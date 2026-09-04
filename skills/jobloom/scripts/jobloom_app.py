#!/usr/bin/env python3
"""The Jobloom app: the window a person uses, and the local service behind it.

Every component before this one is a command. That is the right shape for an engine and the
wrong shape for a product: a person looking for work should not meet a Python invocation, a
JSON file, a database path or a SHA-256 to copy. This is the surface that replaces them, and
`fill-profile` at the terminal becomes what it always should have been — a developer's way in
to the same rules.

**It is the UI layer of the desktop app, not yet the desktop app.** The page is served over
loopback and opened in a window of the user's browser, which is exactly the arrangement a
Tauri or Electron shell wraps later: the same local service, the same HTML, inside a frame
that carries an icon and an installer. What is missing is the packaging — a signed `.dmg`, an
`.exe`, an updater, and a private data directory outside the repository. Those are named in
`references/desktop-app.md` and none of them changes a rule in here.

Boundaries, kept where the bridge keeps them rather than trusted to the page:

- Loopback only. The server refuses to bind anything but 127.0.0.1.
- A session token, generated per run and never written to disk. It reaches the page in the
  URL this process opens, so nobody types or pastes anything; every API call carries it back.
  A page in another tab, on another origin, does not have it.
- Origin-checked. A request whose `Origin` is not this server's own is refused, so a website
  the user happens to have open cannot post to it even by guessing the port.
- No value is logged. The request log is off, and errors return a code.

What it does *not* do is decide anything. Every rule it applies lives in `candidate_profile`
and is the same rule the terminal path runs; this file asks the questions and renders the
answers.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import candidate_profile  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"
LOOPBACK = "127.0.0.1"
ROUND = "required-v1"
# One worksheet per round, named for the round rather than for the moment it was made, so
# reopening the app finds the round in progress instead of starting a second one beside it.
WORKSHEET_NAME = f"profile-{ROUND}.json"


class AppError(Exception):
    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


# ---- what the app knows before it asks anything --------------------------------

def state(connection: sqlite3.Connection, private_root: Path) -> dict[str, Any]:
    """Where the user is, in the terms the wizard shows. No value crosses this boundary."""
    report = candidate_profile.status(connection)
    worksheet_path = Path(private_root) / WORKSHEET_NAME
    open_round = None
    if worksheet_path.is_file():
        try:
            worksheet = json.loads(worksheet_path.read_text(encoding="utf-8"))
            candidate_profile.check_worksheet(connection, worksheet)
            open_round = worksheet["round"]
        except (OSError, ValueError):
            # A worksheet whose proposal is spent, or one from an older profile. Neither is an
            # error to show: the wizard offers a fresh round and this one is replaced.
            open_round = None
    return {
        "has_profile": report["active_snapshot"] is not None,
        "round": ROUND,
        "fields_in_round": sorted(candidate_profile.PROFILE_ROUNDS[ROUND]),
        "resolvable": report.get("resolvable", []),
        "unresolved": report.get("unresolved", {}),
        "open_round": open_round,
    }


def _worksheet_path(private_root: Path) -> Path:
    return Path(private_root) / WORKSHEET_NAME


def _read_worksheet(connection: sqlite3.Connection, private_root: Path) -> dict[str, Any]:
    path = _worksheet_path(private_root)
    if not path.is_file():
        raise AppError("no_open_round", 409)
    worksheet = json.loads(path.read_text(encoding="utf-8"))
    candidate_profile.check_worksheet(connection, worksheet)
    return worksheet


def start_round(connection: sqlite3.Connection, private_root: Path) -> dict[str, Any]:
    """Open a round, or hand back the one already open.

    Proposing twice would leave two worksheets and two live proposals for one round, and the
    second would quietly be the one confirmed. Reopening the app is not a decision to start
    over.
    """
    path = _worksheet_path(private_root)
    try:
        return {"fields": _fields(_read_worksheet(connection, private_root)), "resumed": True}
    except AppError:
        pass
    except ValueError:
        # Spent or stale. The file is moved aside rather than deleted: it is the user's, and
        # a worksheet nobody can confirm is still a record of what they were asked.
        path.replace(path.with_suffix(f".superseded-{secrets.token_hex(4)}.json"))
    candidate_profile.propose_profile(
        connection, ROUND,
        sink=lambda sheet: candidate_profile.write_private_document(path, private_root, sheet))
    return {"fields": _fields(_read_worksheet(connection, private_root)), "resumed": False}


def _fields(worksheet: dict[str, Any]) -> list[dict[str, Any]]:
    """The questions, as the wizard needs them. Carries values: this reaches the user's own
    screen and nowhere else."""
    return [{
        "canonical_id": entry["canonical_id"],
        "group": entry["group"],
        "what_it_is": entry["what_it_is"],
        "required": entry["required_where_present"],
        "value": entry["value"],
        "proposed": entry["value"] is not None,
        "value_source": entry["value_source"],
        "confirmed": entry["confirmed_by_user"],
        "autofill": entry["autofill_allowed_by_user"],
    } for entry in worksheet["entries"]]


def check(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate one field as it is typed, with the same rule the prompt loop uses."""
    canonical_id = payload.get("canonical_id")
    if canonical_id not in candidate_profile.PROFILE_V1:
        raise AppError("unknown_field")
    return candidate_profile.check_value(canonical_id, payload.get("value") or "")


def save(connection: sqlite3.Connection, private_root: Path,
         payload: dict[str, Any]) -> dict[str, Any]:
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise AppError("answers_required")
    return candidate_profile.apply_answers(
        _worksheet_path(private_root), private_root, connection, answers)


def draft(connection: sqlite3.Connection, private_root: Path) -> dict[str, Any]:
    """Prepare the snapshot and price the switch. Nothing is activated."""
    worksheet = _read_worksheet(connection, private_root)
    return candidate_profile.confirm_profile(connection, worksheet, private_root)


def register(connection: sqlite3.Connection, private_root: Path, store: Path,
             payload: dict[str, Any]) -> dict[str, Any]:
    """Activate the draft the user just read.

    The exact-hash approval is kept and moved off the person: the button carries the hash of
    the draft whose impact was on the screen, so an approval still names one specific set of
    facts rather than whatever happens to be pending. What a person can no longer do is check
    that hash against the one they were shown — that is the trade a window makes for not
    asking anyone to compare 64 characters, and it is recorded rather than glossed.
    """
    draft_sha256 = payload.get("draft_sha256")
    if not isinstance(draft_sha256, str) or not draft_sha256:
        raise AppError("draft_required")
    return candidate_profile.register_profile(
        connection, draft_sha256, Path(store), "user")


# ---- the local service ---------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "JobloomApp/0.1"
    token = ""
    db_path: Path
    private_root: Path
    store: Path
    origin = ""

    def log_message(self, *args: Any) -> None:  # noqa: D102 - a request log is a value log
        pass

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_page(self) -> None:
        body = (ASSETS / "onboarding.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The page talks to this origin and loads nothing from anywhere else. Spelled out so a
        # future edit that reaches for a font or a CDN fails here rather than silently sending
        # a request from a window holding somebody's contact details.
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; "
                         "style-src 'unsafe-inline'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        if not secrets.compare_digest(self.headers.get("X-Jobloom-Token") or "", self.token):
            return False
        sent = self.headers.get("Origin")
        # Absent is fine — same-origin fetches may omit it. Present and wrong is not: that is
        # another page trying its luck against a port it guessed.
        return sent is None or sent == self.origin

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send_page()
            return
        if path == "/api/state":
            if not self._authorised():
                self._send(403, {"error": "bad_token"})
                return
            self._run(lambda connection: state(connection, self.private_root))
            return
        self._send(404, {"error": "unknown_endpoint"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorised():
            self._send(403, {"error": "bad_token"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (TypeError, ValueError):
            self._send(400, {"error": "bad_request_body"})
            return
        path = self.path.split("?", 1)[0]
        if path == "/api/check":
            self._run(lambda _: check(payload), needs_db=False)
        elif path == "/api/round":
            self._run(lambda connection: start_round(connection, self.private_root))
        elif path == "/api/answers":
            self._run(lambda connection: save(connection, self.private_root, payload))
        elif path == "/api/draft":
            self._run(lambda connection: draft(connection, self.private_root))
        elif path == "/api/register":
            self._run(lambda connection: register(connection, self.private_root, self.store,
                                                  payload))
        else:
            self._send(404, {"error": "unknown_endpoint"})

    def _run(self, work, needs_db: bool = True) -> None:
        connection = self._connection() if needs_db else None
        try:
            self._send(200, work(connection))
        except AppError as error:
            self._send(error.status, {"error": error.code})
        except ValueError as error:
            # The refusals from `candidate_profile` are written for a person and name no
            # value; they are the most useful thing the wizard can show.
            self._send(409, {"error": "refused", "detail": str(error)[:300]})
        except Exception:
            # Deliberately bare: anything else may carry a path or a value in its message.
            self._send(500, {"error": "app_failure"})
        finally:
            if connection is not None:
                connection.close()


def _open_window(url: str) -> str:
    """Open the page in its own window where the browser has one, a tab where it does not.

    An address bar over a form asking for someone's phone number makes it look like a website
    they should be suspicious of, which is the wrong instinct to teach about a local page — so
    Chrome's app mode is tried first. Nothing depends on it working.
    """
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("msedge"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            try:
                subprocess.Popen([candidate, f"--app={url}"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return "window"
            except OSError:
                break
    webbrowser.open(url)
    return "tab"


def serve(db_path: Path, private_root: Path, store: Path, port: int = 0,
          open_browser: bool = True) -> ThreadingHTTPServer:
    connection = candidate_profile.connect(db_path)
    connection.close()
    Path(private_root).mkdir(parents=True, exist_ok=True)
    os.chmod(private_root, 0o700)
    Handler.token = secrets.token_urlsafe(32)
    Handler.db_path = Path(db_path)
    Handler.private_root = Path(private_root)
    Handler.store = Path(store)
    server = ThreadingHTTPServer((LOOPBACK, port), Handler)
    Handler.origin = f"http://{LOOPBACK}:{server.server_port}"
    url = f"{Handler.origin}/?token={Handler.token}"
    if open_browser:
        where = _open_window(url)
        print(f"Jobloom is open in a {where}. Leave this running while you use it.")
        print("Nothing here reaches the network; the page talks only to this process.")
    else:
        print(url)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Open Jobloom.")
    parser.add_argument("--db", type=Path, default=Path(".jobloom/jobloom.db"))
    parser.add_argument("--private-root", type=Path)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    private_root = args.private_root or args.db.parent
    store = args.store or args.db.parent / "candidates"
    server = serve(args.db, private_root, store, args.port, not args.no_browser)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
