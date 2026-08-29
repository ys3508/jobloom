#!/usr/bin/env python3
"""Local bridge between the browser assistant and Jobloom.

The assistant is a passenger, not a driver. It answers questions about a page the user
already has open; it never opens one. Everything here is therefore request/response over
loopback: the extension sends what is on the user's screen, this process answers from the
local registries, and nothing initiates a fetch of its own.

Boundaries enforced here rather than trusted to the extension:

- Loopback only. The server refuses to bind anything but 127.0.0.1, so nothing on the
  network can reach it.
- A shared token, kept in the private root at 0600 and reused across restarts, so it is
  pasted into the panel once rather than after every start. Without it every request is
  refused, so another page in the same browser cannot call the bridge. `--rotate-token`
  replaces it when it has been shown to someone.
- Read-only by default. Storing a JobCard is a separate, explicitly enabled endpoint,
  because the difference between "help me read this page" and "keep a copy of every page I
  scrolled past" is the difference between assistance and collection.
- No page text is written anywhere unless the user stores that job on purpose.
"""

from __future__ import annotations

import argparse
import json
import hashlib
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
import posting_sections  # noqa: E402
import quantity_extractor  # noqa: E402
import resume_core  # noqa: E402
from evidence_matcher import EVIDENCE_ORDER  # noqa: E402

LOOPBACK = "127.0.0.1"
MAX_PAGE_TEXT = 60_000
SUPPORTED_HOSTS = ("linkedin.com", "indeed.com")


TOKEN_FILENAME = "assist-token"
# The files whose behaviour a running bridge has baked in. Reported so a start attempt can
# say "an older copy is serving" instead of "nothing to do", which is the wrong answer when
# this code has moved since that copy began.
VERSIONED_SOURCES = ("assist_bridge.py", "posting_sections.py", "evidence_matcher.py")


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for name in VERSIONED_SOURCES:
        path = Path(__file__).resolve().parent / name
        digest.update(path.read_bytes() if path.is_file() else b"")
    return digest.hexdigest()[:12]


def load_or_create_token(private_root: Path, *, rotate: bool = False) -> str:
    """One token per install, not per run.

    Rotating on every start meant re-pasting into the panel after each restart, which is
    friction with no security to show for it: the token exists to stop other pages in the
    browser calling the bridge, and that does not need it to change.
    """
    path = private_root / TOKEN_FILENAME
    if not rotate and path.is_file():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = secrets.token_urlsafe(24)
    private_root.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    path.chmod(0o600)
    return token


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
    # The page arrives as prose. Section extraction turns the parts the posting states
    # outright into JobCard fields; anything the caller supplied explicitly still wins,
    # because that came from the page's own markup rather than from reading its text.
    card.update(posting_sections.extract(text, title=page.get("title")))
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


# Four ways a requirement can stand, and each one asks for a different move. Merging them
# into "keywords you are missing" is what makes a keyword counter reward padding: it cannot
# tell work you did but left off a page from work you never did.
CLASSES = ("covered", "hidden_strength", "transferable", "evidence_gap", "real_gap")


def resume_fact_ids(connection: sqlite3.Connection) -> set[str]:
    """Fact IDs the user's approved direction resume actually carries.

    A requirement met by a fact the resume never mentions is a hidden strength: real, and
    addable, because it is the user's own confirmed work. Without this set the engine
    cannot tell that apart from something they simply worded badly.
    """
    covered: set[str] = set()
    for row in connection.execute(
        "SELECT claims_manifest_path FROM resume_versions "
        "WHERE status='approved' AND kind='direction' AND claims_manifest_path IS NOT NULL"
    ):
        path = Path(row["claims_manifest_path"] or "")
        if not path.is_file():
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        covered.update(str(fact_id) for claim in manifest.get("claims", [])
                       for fact_id in claim.get("fact_ids", []))
    return covered


def classify_requirement(match: dict[str, Any], facts: dict[str, Any],
                         on_resume: set[str], *, preferred: bool) -> dict[str, Any]:
    """Sort one stated requirement into the move it calls for."""
    supporting = [facts[fid] for fid in match.get("fact_ids") or [] if fid in facts]
    strength = match.get("strength", "none")
    if strength == "none" or not supporting:
        kind = "real_gap"
    elif EVIDENCE_ORDER[strength] < EVIDENCE_ORDER["strongly_related"]:
        # Transferable and mention-only stay where they are. Nothing downstream may read
        # them as direct experience, whatever else is true of the posting.
        kind = "transferable"
    elif not any(fact["id"] in on_resume for fact in supporting):
        kind = "hidden_strength"
    elif not any(quantity_extractor.is_quantified(str(fact.get("value", "")))
                 for fact in supporting):
        kind = "evidence_gap"
    else:
        kind = "covered"
    return {
        "requirement": match["requirement"],
        "class": kind,
        "strength": strength,
        "obligation": "preferred" if preferred else "required",
        "evidence": [{"fact_id": fact["id"], "text": str(fact.get("value", ""))[:180],
                      "on_resume": fact["id"] in on_resume,
                      "quantified": quantity_extractor.is_quantified(str(fact.get("value", "")))}
                     for fact in supporting[:3]],
    }


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
    matches = (evaluation or {}).get("evidence_matches", [])
    facts = {str(fact.get("id")): fact for fact in candidate.get("facts") or []}
    on_resume = resume_fact_ids(connection)
    preferred_terms = set(card.get("preferred_skills") or [])
    classified = [classify_requirement(match, facts, on_resume,
                                       preferred=match["requirement"] in preferred_terms)
                  for match in matches]
    buckets = {name: [item for item in classified if item["class"] == name] for name in CLASSES}
    covered = buckets["covered"] + buckets["hidden_strength"] + buckets["evidence_gap"]
    gaps = buckets["real_gap"]
    required_gaps = [item for item in gaps if item["obligation"] == "required"]
    unassessed_by_kind = card.get("extraction", {}).get("unassessed_requirements", {})
    unassessed = [
        {"requirement": line,
         "obligation": "preferred" if kind == "preferred_skills" else "required"}
        for kind in ("required_skills", "preferred_skills")
        for line in unassessed_by_kind.get(kind, [])
    ]
    required_unassessed = [item for item in unassessed if item["obligation"] == "required"]
    line_groups = card.get("extraction", {}).get("requirement_lines", {})
    stated_requirements = [
        {"requirement": item["text"], "recognized_terms": item.get("recognized_terms", []),
         "obligation": "preferred" if kind == "preferred_skills" else "required"}
        for kind in ("required_skills", "preferred_skills")
        for item in line_groups.get(kind, [])
    ]
    best = directions[0] if directions else None
    blocking = [term for direction in directions
                for term in direction.get("warning_terms_required", [])]
    # A posting nobody could read is not a posting nobody should apply to. Saying "skip"
    # here passes off a parsing failure as a judgement about the user.
    stated = ((card.get("required_skills_stated") or [])
              + (card.get("preferred_skills_stated") or [])
              + (card.get("responsibilities") or []))
    if not stated:
        return {
            "verdict": {"call": "unreadable",
                        "because": "this page did not give up a requirements section, "
                                   "so there is nothing here to judge you against",
                        "direction": None, "covered": 0, "stated": 0},
            "lead_with": [], "gaps": [], "classified": {name: [] for name in CLASSES},
            "resume_shows": len(on_resume),
            "job": {key: card.get(key) for key in ("title", "employer", "location", "country",
                                                   "work_arrangement", "employment_type",
                                                   "sponsorship", "salary")},
            "directions": directions,
            "evidence": {"matches": [], "main_gap": None, "eligibility": None,
                         "match": None, "action": None},
            "notice": "nothing was read from this page and nothing was stored",
        }
    # Whether the user can do this job is a question about their evidence. Whether it sits
    # inside a registered direction is a question about how they are budgeting applications.
    # Letting the second answer the first is how a genomics role they are well matched to
    # comes back as "skip" because its title was not on a list.
    outside_directions = bool(best) and best.get("decision") == "fail"
    if required_unassessed:
        verdict, because = "review", (
            f"{len(required_unassessed)} required lines still need a human evidence check; "
            "the recognised terms alone are not the whole posting")
    elif not covered and not buckets["transferable"]:
        verdict, because = "skip", "none of the stated requirements resolve to your evidence"
    elif len(required_gaps) > len(covered):
        verdict, because = "stretch", (
            f"{len(required_gaps)} required things you have no evidence for, "
            f"against {len(covered)} you do")
    elif outside_directions:
        verdict, because = "review", (
            "your evidence fits, but this sits outside the directions you registered — "
            "worth a look, and worth asking whether the direction should widen")
    elif blocking:
        verdict, because = "review", (
            "required terms you have no evidence for: "
            + ", ".join(sorted(set(blocking))[:3]))
    elif buckets["hidden_strength"]:
        verdict, because = "apply", (
            f"{len(buckets['hidden_strength'])} of these you have done but this resume "
            "does not show — add them before applying")
    else:
        verdict, because = "apply", "your evidence covers most of what this posting states"
    return {
        "verdict": {"call": verdict, "because": because,
                    "direction": (best or {}).get("name"),
                    "covered": len(covered), "stated": len(matches),
                    "unassessed": len(unassessed), "lines_read": len(stated_requirements)},
        "lead_with": covered,
        "gaps": gaps,
        "classified": {name: buckets[name] for name in CLASSES},
        "unassessed_requirements": unassessed,
        "stated_requirements": stated_requirements,
        "resume_shows": len(on_resume),
        "job": {key: card.get(key) for key in ("title", "employer", "location", "country",
                                               "work_arrangement", "employment_type",
                                               "sponsorship", "salary")},
        "directions": directions,
        "evidence": {
            "matches": matches,
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
            self._send(200, {"status": "ok", "store_enabled": self.allow_store,
                             "source_fingerprint": source_fingerprint()})
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


def _running_bridge(port: int) -> dict[str, Any] | None:
    """The health of whatever holds the port, when it is an assist bridge."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{LOOPBACK}:{port}/health", timeout=2) as response:
            body = json.load(response)
    except (urllib.error.URLError, ValueError, OSError):
        return None
    return body if body.get("status") == "ok" else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--allow-store", action="store_true",
                        help="permit the extension to store a job card the user reviewed; "
                             "off by default so browsing never accumulates a job database")
    parser.add_argument("--private-root", type=Path,
                        help="where the token is kept; defaults to the database's directory")
    parser.add_argument("--rotate-token", action="store_true",
                        help="replace the stored token, for when it has been shown to someone")
    args = parser.parse_args()
    private_root = args.private_root or args.db.resolve().parent
    token = load_or_create_token(private_root, rotate=args.rotate_token)
    try:
        server = serve(db_path=args.db, candidate_path=args.candidate, port=args.port,
                       allow_store=args.allow_store, token=token)
    except OSError as error:
        # A stack trace here says nothing useful: the port is either held by an older copy
        # of this bridge, which is fine to leave alone, or by something else, which the
        # user has to decide about.
        if getattr(error, "errno", None) != 48:
            raise
        running = _running_bridge(args.port)
        current = source_fingerprint()
        if running is None:
            detail, code = "another program holds this port; stop it or pass --port", 1
        elif running.get("source_fingerprint") == current:
            detail, code = "this bridge is already serving on this port; nothing to do", 0
        else:
            detail, code = ("an older copy of this bridge is serving; stop it and start "
                            "again to pick up the current code"), 1
        print(json.dumps({
            "error": "port_in_use", "port": args.port, "detail": detail,
            "running_fingerprint": (running or {}).get("source_fingerprint"),
            "current_fingerprint": current,
            "stop_it_with": f"lsof -ti:{args.port} | xargs kill",
        }, indent=2))
        raise SystemExit(code)
    print(json.dumps({"listening": f"http://{LOOPBACK}:{args.port}",
                      "token": token, "token_file": str(private_root / TOKEN_FILENAME),
                      "token_reused": not args.rotate_token,
                      "store_enabled": args.allow_store}, indent=2))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
