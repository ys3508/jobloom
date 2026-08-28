#!/usr/bin/env python3
"""Local bridge between the browser assistant and Jobloom.

The assistant is a passenger, not a driver. It answers questions about a page the user
already has open; it never opens one. Everything here is therefore request/response over
loopback: the extension sends what is on the user's screen, this process answers from the
local registries, and nothing initiates a fetch of its own.

Boundaries enforced here rather than trusted to the extension:

- Loopback only. The server refuses to bind anything but 127.0.0.1, so nothing on the
  network can reach it.
- A shared token, generated per run and printed once. Without it every request is refused,
  so another page in the same browser cannot call it.
- Read-only by default. Storing a JobCard is a separate, explicitly enabled endpoint,
  because the difference between "help me read this page" and "keep a copy of every page I
  scrolled past" is the difference between assistance and collection.
- No page text is written anywhere unless the user stores that job on purpose.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import direction_core  # noqa: E402
import evaluate_job  # noqa: E402
import ingest_job  # noqa: E402
import resume_core  # noqa: E402

LOOPBACK = "127.0.0.1"
MAX_PAGE_TEXT = 60_000
SUPPORTED_HOSTS = ("linkedin.com", "indeed.com")


class BridgeError(Exception):
    """A request the bridge refuses, reported to the extension as a stable code."""

    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _load_candidate(candidate_path: Path) -> dict[str, Any]:
    candidate, _ = resume_core.load_valid_candidate(candidate_path)
    return candidate


def _approved_directions(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute("""
        SELECT sd.direction_id, sd.profile_json
        FROM search_directions sd
        JOIN search_portfolio_directions spd
          ON spd.direction_id = sd.direction_id AND spd.profile_sha256 = sd.profile_sha256
        JOIN search_portfolios sp
          ON sp.portfolio_id = spd.portfolio_id AND sp.status = 'approved'
        WHERE sd.status = 'approved'
        ORDER BY sd.direction_id
    """).fetchall()
    return [json.loads(row["profile_json"]) for row in rows]


def build_card(page: dict[str, Any]) -> dict[str, Any]:
    """Structure the text the user is already looking at. No fetching."""
    text = str(page.get("text") or "")
    if not text.strip():
        raise BridgeError("page_text_empty")
    if len(text) > MAX_PAGE_TEXT:
        raise BridgeError("page_text_too_large")
    url = str(page.get("url") or "")
    if not url.startswith("https://"):
        raise BridgeError("page_url_not_https")
    card = ingest_job.build_card(text, url, "text")
    for key in ("title", "employer", "location", "country", "work_arrangement",
                "employment_type", "seniority", "sponsorship"):
        value = page.get(key)
        if isinstance(value, str) and value.strip():
            card[key] = value.strip()
    for key in ("required_skills", "preferred_skills", "responsibilities"):
        value = page.get(key)
        if isinstance(value, list):
            card[key] = [str(item).strip() for item in value if str(item).strip()][:200]
    # The card is a draft until the user reviews it, whatever the page said.
    card["requirements_reviewed"] = False
    return card


def positioning(card: dict[str, Any], candidate: dict[str, Any],
                connection: sqlite3.Connection) -> dict[str, Any]:
    """What the side panel shows: which direction, what evidence, what is missing.

    This is the part that touches nothing outside the user's own registries. It reads the
    page's structured fields and the user's own facts, and returns a judgement.
    """
    directions = []
    for profile in _approved_directions(connection):
        try:
            routed = direction_core.route_job(profile, candidate, card)
        except ValueError as error:
            directions.append({"direction_id": profile["direction_id"],
                               "decision": "unavailable", "reason": str(error)})
            continue
        signals = routed["ranking_signals"]
        directions.append({
            "direction_id": profile["direction_id"],
            "name": profile.get("name") or profile["direction_id"],
            "decision": routed["decision"],
            "ranking_score": routed["ranking_score"],
            "hard_failures": routed["hard_failures"],
            "review_reasons": routed["review_reasons"],
            "required_skill_evidence": routed.get("required_skill_evidence", []),
            "warning_terms_required": signals.get("warning_terms_required", []),
            "warning_terms_preferred_only": signals.get("warning_terms_preferred_only", []),
        })
    directions.sort(key=lambda item: (item.get("decision") != "match",
                                      -(item.get("ranking_score") or 0),
                                      item["direction_id"]))
    evaluation = None
    try:
        evaluation = evaluate_job.evaluate(candidate, {**card, "requirements_reviewed": True})
    except (ValueError, KeyError) as error:
        evaluation = {"eligibility": "unavailable", "reason": str(error)}
    return {
        "job": {key: card.get(key) for key in ("title", "employer", "location", "country",
                                               "work_arrangement", "employment_type",
                                               "sponsorship", "salary")},
        "directions": directions,
        "evidence": {
            "matches": (evaluation or {}).get("evidence_matches", []),
            "main_gap": (evaluation or {}).get("main_gap"),
            "eligibility": (evaluation or {}).get("eligibility"),
            "match": (evaluation or {}).get("match"),
            "action": (evaluation or {}).get("action"),
        },
        "notice": "draft judgement on an unreviewed job card; nothing was stored",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "JobloomAssistBridge/1.0"
    token = ""
    candidate_path: Path
    db_path: Path
    allow_store = False

    def log_message(self, *args: Any) -> None:  # noqa: D102 - quiet by default
        pass

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Jobloom-Token")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, {})

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"status": "ok", "store_enabled": self.allow_store})
            return
        self._send(404, {"error": "unknown_endpoint"})

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("X-Jobloom-Token") != self.token:
            self._send(403, {"error": "bad_token"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (TypeError, ValueError):
            self._send(400, {"error": "bad_request_body"})
            return
        try:
            if self.path == "/positioning":
                self._send(200, self._positioning(payload))
            elif self.path == "/store":
                self._send(200, self._store(payload))
            else:
                self._send(404, {"error": "unknown_endpoint"})
        except BridgeError as error:
            self._send(error.status, {"error": error.code})
        except Exception as error:  # surfaced as a code, never as a stack trace
            self._send(500, {"error": "bridge_failure", "detail": str(error)[:200]})

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _positioning(self, payload: dict[str, Any]) -> dict[str, Any]:
        card = build_card(payload)
        candidate = _load_candidate(self.candidate_path)
        connection = self._connection()
        try:
            return {**positioning(card, candidate, connection), "job_card": card}
        finally:
            connection.close()

    def _store(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.allow_store:
            raise BridgeError("storing_disabled", 403)
        card = payload.get("job_card")
        if not isinstance(card, dict):
            raise BridgeError("job_card_required")
        if not card.get("requirements_reviewed"):
            raise BridgeError("job_card_unreviewed")
        import application_core  # local: keeps the read-only path free of write imports

        connection = self._connection()
        try:
            result = application_core.ingest_job(connection, card)
        finally:
            connection.close()
        return {**result, "stored_at": datetime.now(timezone.utc).isoformat()}


def serve(*, db_path: Path, candidate_path: Path, port: int, allow_store: bool,
          token: str | None = None) -> ThreadingHTTPServer:
    Handler.token = token or secrets.token_urlsafe(24)
    Handler.candidate_path = candidate_path
    Handler.db_path = db_path
    Handler.allow_store = allow_store
    return ThreadingHTTPServer((LOOPBACK, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--allow-store", action="store_true",
                        help="permit the extension to store a job card the user reviewed; "
                             "off by default so browsing never accumulates a job database")
    args = parser.parse_args()
    server = serve(db_path=args.db, candidate_path=args.candidate, port=args.port,
                   allow_store=args.allow_store)
    print(json.dumps({"listening": f"http://{LOOPBACK}:{args.port}",
                      "token": Handler.token, "store_enabled": args.allow_store}, indent=2))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
