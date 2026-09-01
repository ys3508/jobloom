#!/usr/bin/env python3
"""A deliberately tiny local service: the only thing that can say a grant exists.

The worker reads no database, so something has to answer "was this package authorised?" The
previous attempt put an HMAC secret in the same file as the signature it verified, which is
not authenticity at all — anyone could pick a secret, sign a package of their own, and hand
over both. The verifying secret cannot sit beside the object it verifies.

So verification moved here. The worker sends a grant id and the digest of the package it
holds; this service consults the authority's own database, consumes the grant atomically, and
returns the parameters the run is allowed to use. A package this authority never exported has
no grant, and no amount of local file writing creates one.

One endpoint, loopback only, a per-run bearer token so another process on the machine cannot
drain grants, and nothing in a response that is not needed to execute.

**What this does not claim.** A worker trusts the authority it is pointed at, so a caller who
can already choose that address has already chosen everything. The property is narrower and
real: a genuine authority refuses a package it never issued, refuses it twice, refuses it
after expiry or revocation, and hands back the surface parameters rather than believing the
package's own account of them.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


class ExecutionAuthority:
    """Serves grant redemption for the lifetime of one fill run."""

    def __init__(self, connection: sqlite3.Connection, redeem, clock=None):
        # The connection must be usable from the serving thread; redemption is serialised
        # here so "consumed exactly once" holds under concurrent requests as well as
        # sequential ones.
        self._connection = connection
        self._redeem = redeem
        self._lock = threading.Lock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.token = secrets.token_hex(24)
        self.redemptions = 0
        authority = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                if self.path != "/redeem":
                    self.send_error(404)
                    return
                if self.headers.get("Authorization") != f"Bearer {authority.token}":
                    # Another local process must not be able to spend someone's grant.
                    self.send_error(403)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    request = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    self.send_error(400)
                    return
                grant_id = request.get("grant_id")
                digest = request.get("package_sha256")
                if not isinstance(grant_id, str) or not isinstance(digest, str):
                    self.send_error(400)
                    return
                authority.redemptions += 1
                with authority._lock:
                    answer = authority._redeem(
                        authority._connection, grant_id, digest, authority._clock())
                body = json.dumps(answer).encode("utf-8")
                self.send_response(200 if answer.get("authorised") else 403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                self.send_error(405)

            def log_message(self, *arguments):  # noqa: D102
                return

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._httpd.server_address[:2]
        self.origin = f"http://{host}:{port}"
        self.url = f"{self.origin}/redeem"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self) -> "ExecutionAuthority":
        self._thread.start()
        return self

    def __exit__(self, *exception) -> bool:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        return False
