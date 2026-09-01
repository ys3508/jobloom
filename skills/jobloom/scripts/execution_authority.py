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

Two endpoints — reserve, then consume — loopback only, a per-run bearer token, and nothing in
a response that is not needed to execute.

**What the token is and is not.** It stops a request that does not carry it, which means a
stray or unauthenticated caller. It does **not** stop a hostile process running as this same
user: such a process can read the 0600 capability file, and no file mode changes that.
Excluding a same-UID attacker needs a different OS identity, a sandbox, or a descriptor
inherited from a parent — not permissions. What the capability file does buy over `--token`
in `argv` is that the value is not exposed in the process list to every other user and every
`ps` on the machine.

**What this does not claim.** A worker trusts the authority it is pointed at, so a caller who
can already choose that address has already chosen everything. The property is narrower and
real: a genuine authority refuses a package it never issued, refuses it twice, refuses it
after expiry or revocation, and hands back the surface parameters rather than believing the
package's own account of them.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


class ExecutionAuthority:
    """Serves grant redemption for the lifetime of one fill run."""

    def __init__(self, connection: sqlite3.Connection, reserve, consume=None, clock=None):
        # The connection must be usable from the serving thread; redemption is serialised
        # here so "consumed exactly once" holds under concurrent requests as well as
        # sequential ones.
        self._connection = connection
        self._reserve = reserve
        self._consume = consume
        self._lock = threading.Lock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.token = secrets.token_hex(24)
        self.redemptions = 0
        authority = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                if self.path not in ("/reserve", "/consume"):
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
                if not isinstance(grant_id, str):
                    self.send_error(400)
                    return
                authority.redemptions += 1
                if self.path == "/reserve":
                    digest = request.get("package_sha256")
                    if not isinstance(digest, str):
                        self.send_error(400)
                        return
                    with authority._lock:
                        answer = authority._reserve(
                            authority._connection, grant_id, digest, authority._clock())
                    granted = answer.get("authorised")
                else:
                    reservation = request.get("reservation")
                    if not isinstance(reservation, str):
                        self.send_error(400)
                        return
                    with authority._lock:
                        answer = authority._consume(
                            authority._connection, grant_id, reservation, authority._clock())
                    granted = answer.get("consumed")
                body = json.dumps(answer).encode("utf-8")
                self.send_response(200 if granted else 403)
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
        self.url = f"{self.origin}/reserve"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def write_capability(self, path: Path) -> Path:
        """Hand the token over as a 0600 file rather than on a command line.

        Process arguments are visible in the process list, so `argv` leaks the value to
        other users and to anything reading `ps`. A 0600 file closes that, and closes access
        by other Unix users. It does not close access by a hostile process running as this
        same user — that one can read the file — and nothing here should be read as claiming
        otherwise.
        """
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"authority_url": self.url, "token": self.token}, stream)
            stream.write("\n")
        return path

    def __enter__(self) -> "ExecutionAuthority":
        self._thread.start()
        return self

    def __exit__(self, *exception) -> bool:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        return False
