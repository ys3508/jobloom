"""A loopback-only server for the generated semantic replays.

Binds `127.0.0.1` on a port the operating system chooses, serves only pages this process
generated, and makes no outbound request of any kind. Navigation between pages is a request a
test issues; nothing here advances a page on its own, because the v1 bound is that every Next,
Continue and final action belongs to the user.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "jobloom" / "scripts"))

import semantic_replay  # noqa: E402

UPSTREAM = Path(__file__).resolve().parent / "ats-semantic" / "upstream"


class ReplayServer:
    def __init__(self, families=("lever", "greenhouse", "ashby")):
        self.pages: dict[str, str] = {}
        self.final_activations = 0
        for directory in sorted(UPSTREAM.iterdir()):
            if not directory.is_dir():
                continue
            fixture = semantic_replay.load_fixture(directory / "fixture.json")
            family = fixture["platformFamily"]
            if family not in families:
                continue
            last = len(fixture["steps"]) - 1
            for index in range(len(fixture["steps"])):
                self.pages[f"/{family}/{index}"] = semantic_replay.render_page(
                    fixture, index, include_variants=(index == 0), final=(index == last))
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                """The only honest place to count an activation.

                A page-side counter is a variable the server never reads: it reports zero
                whether or not anything was activated. Counting here means the oracle can
                actually fail, which is the only reason to have one.
                """
                if self.path != "/__final_action":
                    self.send_error(404)
                    return
                server.final_activations += 1
                body = b"final action refused"
                self.send_response(403)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                if self.path == "/__state":
                    body = json.dumps({
                        "final_action_activations": server.final_activations,
                        "pages": sorted(server.pages),
                    }).encode("utf-8")
                    content_type = "application/json"
                elif self.path in server.pages:
                    body = server.pages[self.path].encode("utf-8")
                    content_type = "text/html; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *arguments):  # noqa: D102
                return

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.host, self.port = self._httpd.server_address[:2]
        self.origin = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exception):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        return False
