"""A loopback-only server for the generated semantic replays.

Binds `127.0.0.1` on a port the operating system chooses, serves only pages this process
generated, and makes no outbound request of any kind. Navigation between pages is a request a
test issues; nothing here advances a page on its own, because the v1 bound is that every Next,
Continue and final action belongs to the user.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "jobloom" / "scripts"))

import field_policy  # noqa: E402
import semantic_replay  # noqa: E402

UPSTREAM = Path(__file__).resolve().parent / "ats-semantic" / "upstream"


class ReplayServer:
    """Starts a replay and, when given a connection, registers the surface it is serving.

    The nonce is generated here and handed to the renderer. Nothing else on the machine has
    it, which is what separates "Jobloom started this server" from "something is listening on
    loopback".
    """

    def __init__(self, families=("lever", "greenhouse", "ashby"), connection=None,
                 clock=None, lifetime_hours=1):
        # A default of "now" and not a fixed date: a surface stamped with a historical time
        # is expired the moment it is created, and the earlier version only looked correct
        # because the tests happened to share its constant. Tests inject a clock instead.
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.pages: dict[str, str] = {}
        self.final_activations = 0
        self.nonce = secrets.token_hex(24)
        self._source_bytes: list[bytes] = []
        for directory in sorted(UPSTREAM.iterdir()):
            if not directory.is_dir():
                continue
            fixture = semantic_replay.load_fixture(directory / "fixture.json")
            family = fixture["platformFamily"]
            if family not in families:
                continue
            self._source_bytes.append((directory / "fixture.json").read_bytes())
            if family == "lever":
                # A two-page arrangement of the same reviewed controls. Upstream puts them
                # all on one step with the final action alone on the next, so a flow with two
                # packages needs a split; the controls are unchanged and only the pagination
                # is Jobloom's.
                controls = list(enumerate(fixture["steps"][0]["controls"]))
                head = [(index, control) for index, control in controls
                        if index in (0, 1, 5)]
                tail = [(index, control) for index, control in controls if index == 20]
                self.pages["/lever/split/0"] = semantic_replay.render_controls(
                    family, 0, head, self.nonce, next_path="/lever/split/1")
                self.pages["/lever/split/1"] = semantic_replay.render_controls(
                    family, 1, tail, self.nonce, final=True)
                # The same page as `/lever/split/0` with exactly one thing wrong with it.
                # One hazard per page: a page carrying all four at once can only show that
                # the observer refused it, never that it refuses each of them.
                for hazard in semantic_replay.OBSERVER_HAZARDS:
                    self.pages[f"/refuse/{hazard}"] = semantic_replay.render_controls(
                        family, 0, head, self.nonce, next_path="/lever/split/1",
                        hazard=hazard)
            last = len(fixture["steps"]) - 1
            for index in range(len(fixture["steps"])):
                self.pages[f"/{family}/{index}"] = semantic_replay.render_page(
                    fixture, index, self.nonce,
                    include_variants=(index == 0), final=(index == last))
        server = self

        class Handler(BaseHTTPRequestHandler):
            HAZARDS = {
                # Two pages a real ATS could serve, used only to prove the guard sees them.
                # `/hazard/get-submit` submits by navigating with method="GET"; nothing about
                # being same-origin makes that safe. `/hazard/location` leaves the page from
                # an input handler.
                "/hazard/get-submit": (
                    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    '<title>get submit</title></head><body><form id="application" '
                    'method="get" action="/__final_action">'
                    '<label for="h-1">Full name</label>'
                    '<input type="text" id="h-1" data-test-id="h-1" name="h-1">'
                    '<label for="h-2">Second</label>'
                    '<input type="text" id="h-2" data-test-id="h-2" name="h-2">'
                    '<label for="h-3">Third</label>'
                    '<input type="text" id="h-3" data-test-id="h-3" name="h-3">'
                    '<input type="submit" id="final-action" data-test-id="final-action" '
                    'value="Submit"></form>'
                    '<script>document.getElementById("h-1").addEventListener("input",'
                    'function(){document.getElementById("application").submit();});</script>'
                    "</body></html>\n"),
                # A side effect that lands well after any static wait a worker might use.
                # 400ms is not special; the point is that no fixed number is a completion
                # boundary, so the guard has to outlive the actions rather than a timer.
                "/hazard/delayed-submit": (
                    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    '<title>delayed submit</title></head><body><form id="application" '
                    'method="post" action="/__final_action">'
                    '<label for="h-1">Full name</label>'
                    '<input type="text" id="h-1" data-test-id="h-1" name="h-1">'
                    '<input type="submit" id="final-action" data-test-id="final-action" '
                    'value="Submit"></form>'
                    '<script>document.getElementById("h-1").addEventListener("input",'
                    'function(){setTimeout(function(){'
                    'document.getElementById("application").submit();},400);});</script>'
                    "</body></html>\n"),
                "/hazard/delayed-get": (
                    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    '<title>delayed get</title></head><body>'
                    '<label for="h-1">Full name</label>'
                    '<input type="text" id="h-1" data-test-id="h-1">'
                    '<script>document.getElementById("h-1").addEventListener("input",'
                    'function(){setTimeout(function(){'
                    'window.location = "/__final_action?late=1";},400);});</script>'
                    "</body></html>\n"),
                "/hazard/location": (
                    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    '<title>location</title></head><body>'
                    '<label for="h-1">Full name</label>'
                    '<input type="text" id="h-1" data-test-id="h-1">'
                    '<script>document.getElementById("h-1").addEventListener("input",'
                    'function(){window.location = "/lever/1";});</script>'
                    "</body></html>\n"),
            }

            def do_POST(self):  # noqa: N802
                """The only honest place to count an activation.

                A page-side counter is a variable the server never reads: it reports zero
                whether or not anything was activated. Counting here means the oracle can
                actually fail, which is the only reason to have one.
                """
                if self.path.split("?", 1)[0] != "/__final_action":
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
                elif self.path.split("?", 1)[0] == "/__final_action":
                    # A GET form submit reaches the same place a POST would.
                    server.final_activations += 1
                    self.send_error(403)
                    return
                elif self.path in self.HAZARDS:
                    body = self.HAZARDS[self.path].encode("utf-8")
                    content_type = "text/html; charset=utf-8"
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

        self._handler_class = Handler
        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.host, self.port = self._httpd.server_address[:2]
        self.origin = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._connection = connection
        self.surface_id = f"replay-{self.nonce[:12]}"
        if connection is not None:
            issued = self._clock()
            field_policy.register_replay_surface(
                connection, self.surface_id, self.origin, self.nonce,
                self.content_sha256(), semantic_replay.RENDERER_VERSION, issued,
                issued + timedelta(hours=lifetime_hours),
                page_digests=self.page_digests())

    def __enter__(self):
        self._thread.start()
        return self

    def page_digests(self) -> dict[str, str]:
        """The digest of each page as served, so a worker can check the page it actually got."""
        digests = {path: hashlib.sha256(body.encode("utf-8")).hexdigest()
                   for path, body in self.pages.items()}
        digests.update({path: hashlib.sha256(body.encode("utf-8")).hexdigest()
                        for path, body in self._handler_class.HAZARDS.items()})
        return digests

    def content_sha256(self) -> str:
        """What this surface is actually serving.

        Hashing the path names told nobody anything: changing a fixture's labels, choices or
        the rendered markup left `/lever/0` unchanged and the digest identical. This binds the
        vendored fixture bytes, the renderer version, and the bytes of every page served.
        """
        digest = hashlib.sha256()
        digest.update(semantic_replay.RENDERER_VERSION.encode("utf-8"))
        for raw in self._source_bytes:
            digest.update(hashlib.sha256(raw).digest())
        for path in sorted(self.pages):
            digest.update(path.encode("utf-8"))
            digest.update(hashlib.sha256(self.pages[path].encode("utf-8")).digest())
        return digest.hexdigest()

    def __exit__(self, *exception):
        if self._connection is not None:
            # A surface outlives nothing: when the server stops, the trust it carried stops.
            field_policy.revoke_replay_surface(
                self._connection, self.surface_id, self._clock())
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        return False
