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

One further mode lives here: **extension-controlled separate guarded worker**. The panel can
ask for one Fill-Only page to be run, but it does not run it and it is not driving the tab the
user is looking at. The bridge resolves an opaque execution ID against its own protected
state, the execution authority supplies the target, the allowed origin and the page identity,
and `fill_worker` opens that target in its own headed, guarded Chromium window. The extension
never learns a path, a value, a capability or a grant token, and nothing it sends is treated as
authorization material. Production ATS adapters remain unimplemented: this runs against the
local semantic replay only.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import hashlib
import os
import secrets
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
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
from evidence_matcher import EVIDENCE_ORDER, match_requirement_prose  # noqa: E402

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
    elif match.get("quantification_expected", True) and not any(
            quantity_extractor.is_quantified(str(fact.get("value", "")))
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
    extraction = card.get("extraction") or {}
    usable_facts = [fact for fact in candidate.get("facts") or []
                    if fact.get("status") in {"confirmed", "locked"}]
    if not usable_facts:
        return {
            "verdict": {"call": "evidence_unavailable", "because": "candidate_fact_store_empty",
                        "direction": None, "covered": 0, "stated": 0},
            "lead_with": [], "gaps": [], "classified": {name: [] for name in CLASSES},
            "unassessed_requirements": [], "stated_requirements": [], "resume_shows": 0,
            "job": {key: card.get(key) for key in ("title", "employer", "location", "country",
                                                   "work_arrangement", "employment_type",
                                                   "sponsorship", "salary")},
            "directions": [], "evidence": {"matches": [], "main_gap": None,
                                             "eligibility": None, "match": None, "action": None},
            "notice": "candidate fact store is empty and nothing was stored",
        }
    if extraction.get("read_status") == "partial":
        return {
            "verdict": {"call": "partial", "because": "posting_read_incomplete",
                        "direction": None, "covered": 0, "stated": 0},
            # No judgement, but the reading itself still travels. The user has to see how
            # little arrived to know the panel is asking them to open the full posting
            # rather than failing for reasons of its own.
            "lead_with": [], "gaps": [], "classified": {name: [] for name in CLASSES},
            "unassessed_requirements": [],
            "stated_requirements": [{"text": line, "recognized_terms": []}
                                    for line in (card.get("required_skills_stated") or [])],
            "resume_shows": len(resume_fact_ids(connection)),
            "job": {key: card.get(key) for key in ("title", "employer", "location", "country",
                                                   "work_arrangement", "employment_type",
                                                   "sponsorship", "salary")},
            "directions": [],
            "evidence": {"matches": [], "main_gap": None, "eligibility": None,
                         "match": None, "action": None},
            "notice": "the posting was only partly read and nothing was stored",
        }

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
    decision_order = {"match": 0, "review": 1, "fail": 2, "unavailable": 3}
    directions.sort(key=lambda item: (decision_order.get(item.get("decision"), 4),
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
    unassessed_by_kind = card.get("extraction", {}).get("unassessed_requirements", {})
    prose_matches = [
        {**match_requirement_prose(line, usable_facts),
         "obligation": "preferred" if kind == "preferred_skills" else "required"}
        for kind in ("required_skills", "preferred_skills")
        for line in unassessed_by_kind.get(kind, [])
    ]
    classified.extend(classify_requirement(match, facts, on_resume,
                                            preferred=match["obligation"] == "preferred")
                      for match in prose_matches if match["recognized"])
    buckets = {name: [item for item in classified if item["class"] == name] for name in CLASSES}
    covered = buckets["covered"] + buckets["hidden_strength"] + buckets["evidence_gap"]
    gaps = buckets["real_gap"]
    required_gaps = [item for item in gaps if item["obligation"] == "required"]
    unassessed = [{"requirement": item["requirement"], "obligation": item["obligation"]}
                  for item in prose_matches if not item["recognized"]]
    resolved_prose = [item for item in prose_matches if item["recognized"]]
    required_unassessed = [item for item in unassessed if item["obligation"] == "required"]
    line_groups = card.get("extraction", {}).get("requirement_lines", {})
    prose_by_text = {item["requirement"]: item for item in prose_matches}
    stated_requirements = [
        {"requirement": item["text"], "recognized_terms": item.get("recognized_terms", []),
         "evidence_status": (
             "matched" if prose_by_text.get(item["text"], {}).get("strength") not in {None, "none"}
             else "missing" if prose_by_text.get(item["text"], {}).get("recognized")
             else "manual"
         ),
         "obligation": "preferred" if kind == "preferred_skills" else "required"}
        for kind in ("required_skills", "preferred_skills")
        for item in line_groups.get(kind, [])
    ]
    best = directions[0] if directions else None
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
    # Refusing to judge until *every* required line resolves to a controlled term sounds
    # careful and is a constant: measured over 1,199 live postings, 100% left at least one
    # required line unrecognised, median seven, so "apply" was unreachable and every posting
    # came back "worth a look" however well it matched.
    #
    # The line that actually separates a judgement from a guess is whether anything was
    # assessed at all. Across the postings where the user has direct evidence, every one had
    # at least one required line recognised; across all 1,199, 71% had none. So nothing
    # recognised means there is genuinely nothing to compare and the verdict stays "review";
    # anything recognised is judged on what was assessed, with the unassessed count carried
    # into every reason so no verdict claims to have read more than it did.
    assessed = [item for item in stated_requirements
                if item["obligation"] == "required" and item["recognized_terms"]]
    caveat = (f" {len(required_unassessed)} other required lines name nothing this can check "
              "and were not assessed" if required_unassessed else "")
    if not assessed:
        verdict, because = "review", (
            f"none of the {len(required_unassessed)} required lines name anything that can be "
            "checked against your evidence, so this needs your own read")
    elif not covered and not buckets["transferable"]:
        verdict, because = "skip", (
            "none of the stated requirements resolve to your evidence." + caveat)
    elif len(required_gaps) > len(covered):
        verdict, because = "stretch", (
            f"{len(required_gaps)} required things you have no evidence for, "
            f"against {len(covered)} you do." + caveat)
    elif buckets["hidden_strength"]:
        verdict, because = "apply", (
            f"{len(buckets['hidden_strength'])} of these you have done but this resume "
            "does not show — add them before applying." + caveat)
    else:
        verdict, because = "apply", (
            "your evidence covers what this posting states and can be checked." + caveat)
    return {
        "verdict": {"call": verdict, "because": because,
                    "direction": ((best or {}).get("name")
                                  if (best or {}).get("decision") in {"match", "review"} else None),
                    "covered": len(covered), "stated": len(matches) + len(resolved_prose),
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
            "matches": matches + resolved_prose,
            "main_gap": (evaluation or {}).get("main_gap"),
            "eligibility": (evaluation or {}).get("eligibility"),
            "match": (evaluation or {}).get("match"),
            "action": (evaluation or {}).get("action"),
        },
        "notice": "draft judgement on an unreviewed job card; nothing was stored",
    }


# ---- one-page Fill-Only control ---------------------------------------------------------
#
# The extension is a control surface and nothing more. It presses a button and holds an
# opaque execution ID; it does not name a page, a target, an origin, a tab, a path or a
# value, and nothing it sends is read as authorization material. Everything the run is
# authorised to touch comes from the execution authority and from this process's own
# protected state, which is why a panel that lied about what it was looking at would change
# nothing about what runs.

# Closed field sets. Anything else in the body — an origin, a target, a tab id, a path
# helpfully supplied by a caller — is a refusal rather than an ignored extra, because a
# field that is quietly dropped today is a field someone wires up tomorrow.
FILL_PREPARE_FIELDS = {"application_id"}
FILL_EXECUTE_FIELDS = {"execution_id"}
FILL_ID_LIMIT = 128
# A prepared run is a held grant, so it does not sit around. The grant's own expiry is the
# boundary that matters; this only stops the panel offering a button that cannot work.
FILL_PREPARED_TTL_SECONDS = 900

# One authority per page, ever, as a property of the database rather than of a process.
#
# `FillControl`'s lock is per-process, and the CLI takes `--port`, so two bridges on one
# database each hold their own lock and their own idea of what is prepared. Both could read
# "nothing live" and both could then issue, and the page would carry two authorities that
# were each single-use and each perfectly valid. A partial unique index states the invariant
# where both processes can see it.
#
# The predicate is `revoked_at IS NULL` and deliberately says nothing about `consumed_at`.
# An earlier version excluded consumed grants too, which looks tighter and is much weaker:
# `fill_worker` spends the grant *before* it opens a browser, so the slot came free while the
# run was still starting. A second bridge asking during that window found no live grant, was
# issued one of its own, and filled the same page again — and the process-local `running`
# flag that would have stopped it belonged to the first bridge, which the second one cannot
# see. A consumed grant therefore keeps the page's one slot for good: a spent package must
# not come back through a freshly minted grant. A run that failed is retried through a new
# reviewed page or session, not by re-authorising the same pending steps.
#
# Created here rather than in `fill_core.initialize` because that file belongs to a released
# task; beside the table it constrains is where it should eventually live.
LIVE_GRANT_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS execution_grant_one_authority_per_page "
    "ON execution_grants(session_id, page_id) WHERE revoked_at IS NULL")
FILL_PREPARE_LOCK = ".prepare.lock"

try:  # POSIX only; the index above is the guarantee, this only orders the queue.
    import fcntl
except ImportError:  # pragma: no cover - not the platform this runs on
    fcntl = None


class _PreparedRun:
    """One prepared page, addressable only by an opaque id.

    Everything the extension must never see lives here and nowhere else: the package and
    result paths, the grant id, the session and page identity. `summary` is the only part
    that is ever serialised outward, and it is built field by field rather than filtered,
    so a new column in `fill_steps` cannot leak by being forgotten about.
    """

    __slots__ = ("execution_id", "application_id", "session_id", "page_id", "worker_id",
                 "grant_id", "package_path", "result_path", "capability_path", "summary",
                 "expires_at", "state", "outcome")

    def __init__(self, **fields: Any) -> None:
        for name in self.__slots__:
            setattr(self, name, fields.get(name))


def _closed_request(payload: Any, allowed: set[str], code: str) -> dict[str, str]:
    """Exactly these fields, each a bounded non-empty string. No extras, none missing."""
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise BridgeError(code)
    values = {}
    for name in sorted(allowed):
        value = payload[name]
        if not isinstance(value, str):
            raise BridgeError(code)
        value = value.strip()
        if not value or len(value) > FILL_ID_LIMIT:
            raise BridgeError(code)
        values[name] = value
    return values


def _fill_counts(rows: list[sqlite3.Row], column: str) -> dict[str, int]:
    counted: dict[str, int] = {}
    for row in rows:
        counted[row[column]] = counted.get(row[column], 0) + 1
    return dict(sorted(counted.items()))


class FillControl:
    """Prepared runs, and the one place a double press is turned into one execution.

    Three layers refuse a second run and they are not redundant. The panel disables its own
    button, which handles the ordinary double-click; this class holds the whole preparation
    under a lock and moves a run out of `prepared` under the same one, which handles requests
    that raced past the panel; and the execution authority consumes the grant exactly once,
    which is the layer that actually holds if both of the others are wrong or absent. Only
    the last one is a safety boundary — the other two are there so the user is told, rather
    than left reading a stack of identical refusals.

    Consuming a grant exactly once is not the same as there being one grant. An earlier
    version de-duplicated on `execution_id` alone, so two presses of *Prepare* before either
    was run produced two executions with two grants, each single-use and each perfectly
    valid: `prepare, prepare, execute(A), execute(B)` filled the page twice, and the test
    that looked like it covered this only prepared again *after* a run, when the steps were
    already completed and any second attempt would have been refused anyway. So the
    invariant is stated per page rather than per execution: at most one live prepared run,
    and at most one live grant, for one `(session_id, page_id)`.
    """

    def __init__(self) -> None:
        # Re-entrant: `prepare` calls back into the caller's builder while holding the lock,
        # which is the point — the export and the grant must not straddle it.
        self._lock = threading.RLock()
        self._runs: dict[str, _PreparedRun] = {}
        self._by_page: dict[tuple[str, str], str] = {}

    def prepare(self, key: tuple[str, str], now: datetime, build) -> tuple[_PreparedRun, bool]:
        """The one live prepared run for this page, building it only if there is none.

        Returns `(run, created)`. `build` is called under the lock and must revoke whatever
        authority the page already carries before issuing more.
        """
        with self._lock:
            previous_id = self._by_page.get(key)
            previous = self._runs.get(previous_id) if previous_id else None
            if previous is not None:
                if previous.state == "running":
                    # A second authority while the first is in a browser is the exact shape
                    # of the bug this guards.
                    raise BridgeError("fill_execution_in_progress", 409)
                if previous.state == "prepared" and now < previous.expires_at:
                    # Idempotent. The page has not moved, so the answer has not either.
                    return previous, False
                if previous.state == "prepared":
                    previous.state = "expired"
            run = build()
            self._runs[run.execution_id] = run
            self._by_page[key] = run.execution_id
            return run, True

    def claim(self, execution_id: str, now: datetime) -> _PreparedRun:
        """Hand back a run once, and only while it is still prepared."""
        with self._lock:
            run = self._runs.get(execution_id)
            if run is None:
                raise BridgeError("execution_unknown", 404)
            if run.state != "prepared":
                # Includes the second half of a double press. Deliberately not the first
                # run's result: two presses must not read as two successes.
                raise BridgeError("execution_already_started", 409)
            if now >= run.expires_at:
                run.state = "expired"
                raise BridgeError("execution_expired", 409)
            run.state = "running"
            return run

    def finish(self, run: _PreparedRun, state: str, outcome: dict[str, Any] | None) -> None:
        with self._lock:
            run.state = state
            run.outcome = outcome


class Handler(BaseHTTPRequestHandler):
    server_version = "JobloomAssistBridge/1.0"
    token = ""
    candidate_path: Path
    db_path: Path
    allow_store = False
    # Headed by default and set once, at `serve`: a run the user cannot see is a run they
    # cannot stop, and that is the whole reason the separate window is acceptable at all.
    fill_headed = True
    fill_control = FillControl()

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
            try:
                candidate = _load_candidate(self.candidate_path)
                fact_count = sum(fact.get("status") in {"confirmed", "locked"}
                                 for fact in candidate.get("facts") or [])
            except (OSError, ValueError, KeyError):
                fact_count = 0
            self._send(200, {"status": "ok", "store_enabled": self.allow_store,
                             "fact_store_ready": fact_count > 0, "fact_count": fact_count,
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
            elif self.path == "/save":
                self._send(200, self._save(payload))
            elif self.path == "/confirm-submitted":
                self._send(200, self._confirm_submitted(payload))
            elif self.path == "/store":
                self._send(200, self._store(payload))
            elif self.path == "/fill/prepare":
                self._send(200, self._fill_prepare(payload))
            elif self.path == "/fill/execute":
                self._send(200, self._fill_execute(payload))
            else:
                self._send(404, {"error": "unknown_endpoint"})
        except BridgeError as error:
            self._send(error.status, {"error": error.code})
        except Exception as error:  # surfaced as a code, never as a stack trace
            if self.path.startswith("/fill/"):
                # Second line of the same defence. The fill routes convert their own
                # exceptions, and this is what holds if one ever escapes: `detail` is
                # `str(error)`, and on this path that routinely means a local path.
                self._send(500, {"error": "bridge_failure"})
            else:
                self._send(500, {"error": "bridge_failure", "detail": str(error)[:200]})

    def _connection(self, *, shared: bool = False) -> sqlite3.Connection:
        # `shared` is for the fill run only: the execution authority redeems grants from its
        # own serving thread, and single-use consumption has to be decided on the same
        # connection that the run is being verified against.
        connection = sqlite3.connect(str(self.db_path), check_same_thread=not shared)
        connection.row_factory = sqlite3.Row
        return connection

    @property
    def private_root(self) -> Path:
        """Where packages, results and capabilities are written. Never sent anywhere."""
        return self.db_path.parent

    def _positioning(self, payload: dict[str, Any]) -> dict[str, Any]:
        card = build_card(payload)
        try:
            candidate = _load_candidate(self.candidate_path)
        except (OSError, ValueError, KeyError):
            candidate = {"facts": []}
        connection = self._connection()
        try:
            return {**positioning(card, candidate, connection), "job_card": card}
        finally:
            connection.close()

    def _save(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Keep a note of a job to come back to.

        Deliberately not routed through `/store`. That path refuses an unreviewed card
        because the pre-submission review gate stands between a card and being *sent*, and
        relaxing it so a note could be filed would weaken a submission safeguard for a
        bookkeeping errand. Keeping a note sends nothing and creates no application, so it
        needs no review and gets its own door.
        """
        if not self.allow_store:
            raise BridgeError("storing_disabled", 403)
        card = payload.get("job_card")
        if not isinstance(card, dict):
            raise BridgeError("job_card_required")
        import saved_jobs  # local: keeps the read-only path free of write imports

        connection = self._connection()
        try:
            saved_jobs.initialize(connection)
            return saved_jobs.save(connection, card,
                                   actor=str(payload.get("actor") or "user"),
                                   decision=str(payload.get("decision") or saved_jobs.LATER),
                                   judgement=payload.get("judgement"),
                                   reason=payload.get("reason"))
        except ValueError as error:
            raise BridgeError(str(error)) from error
        finally:
            connection.close()

    def _confirm_submitted(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The user saying, after the fact, that they finished the employer's form.

        A separate press from the one that recorded the decision, because it answers a
        different question: the first was pressed before the form opened. Nothing here
        observes the submission — this is still the user's word — but the word is now
        given about something that happened.
        """
        if not self.allow_store:
            raise BridgeError("storing_disabled", 403)
        job_url = str(payload.get("job_url") or "").strip()
        if not job_url:
            raise BridgeError("job_url_required")
        import saved_jobs  # local: keeps the read-only path free of write imports

        connection = self._connection()
        try:
            saved_jobs.initialize(connection)
            return saved_jobs.confirm_submitted(connection, job_url)
        except ValueError as error:
            raise BridgeError(str(error)) from error
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


    # ---- one-page Fill-Only control -----------------------------------------------

    def _fill_state(self, connection, application_id: str, now: datetime):
        """The session and page a run would act on, resolved here and never by the caller.

        The extension names an application and stops there. Which session, which page, which
        steps and which target are all read out of protected state, so the worst a wrong or
        malicious panel can do is name an application that is not ready.
        """
        session = connection.execute(
            "SELECT * FROM fill_sessions WHERE application_id=? AND status='active' "
            "ORDER BY updated_at DESC LIMIT 1", (application_id,)).fetchone()
        if not session:
            raise BridgeError("fill_session_not_active", 409)
        page = connection.execute(
            "SELECT * FROM fill_pages WHERE session_id=? AND status='active' "
            "ORDER BY page_index DESC LIMIT 1", (session["session_id"],)).fetchone()
        if not page:
            # Observation is upstream of this control and stays there: the replay observer
            # is a test fixture, not a production adapter, and Task 7 does not make one.
            raise BridgeError("fill_page_not_observed", 409)
        steps = connection.execute(
            "SELECT * FROM fill_steps WHERE session_id=? AND page_id=? AND status='pending' "
            "ORDER BY ordinal", (session["session_id"], page["page_id"])).fetchall()
        if not steps:
            raise BridgeError("fill_page_has_no_pending_steps", 409)
        import fill_core  # local: keeps the read-only path free of write imports

        try:
            fill_core._require_lease(connection, application_id, session["worker_id"], now)
        except ValueError as error:
            raise BridgeError("fill_lease_not_held", 409) from error
        return session, page, steps

    def _fill_summary(self, session, page, steps, grant, execution_id: str) -> dict[str, Any]:
        """Everything the panel is allowed to know, built by naming each field.

        Built up rather than filtered down. A summary made by deleting known-bad keys grows
        a leak the day a column is added, and `fill_steps` holds the question text, the
        selector and the value itself.
        """
        return {
            "execution_id": execution_id,
            "status": "prepared",
            "application": {
                "application_id": session["application_id"],
                "employer": session["observed_employer"],
                "role": session["observed_role"],
            },
            "page": {
                "page_index": page["page_index"],
                "final_page": bool(page["final_page"]),
                "submit_control_seen": bool(page["submit_control_seen"]),
            },
            "actions": {
                "count": len(steps),
                "controls": _fill_counts(steps, "control"),
                "operations": _fill_counts(steps, "operation"),
                "sources": _fill_counts(steps, "source_kind"),
                "sensitivities": _fill_counts(steps, "sensitivity"),
            },
            "risks": {
                "legal_items": json.loads(page["legal_items_json"]),
                "restricted_requests": json.loads(page["restricted_requests_json"]),
            },
            "expires_at": grant["expires_at"],
            # Not a rendering choice. `stop_before_submit` fails closed in the protocol and
            # no submission action exists to call, so the panel is stating a property of the
            # protocol rather than promising to behave.
            "stops_before_submit": True,
            "runs_in_separate_window": True,
        }

    def _revoke_live_grants(self, connection, fill_core, session_id: str, page_id: str,
                            now: datetime) -> None:
        """Withdraw every authority this page still carries, before issuing another.

        Read out of the database rather than out of this process's memory on purpose. A
        bridge that restarted between the two presses would remember nothing while the
        grants it issued were still perfectly valid, so an in-memory invariant would hold
        only for as long as the process did.
        """
        live = connection.execute(
            "SELECT grant_id FROM execution_grants WHERE session_id=? AND page_id=? "
            "AND consumed_at IS NULL AND revoked_at IS NULL", (session_id, page_id)
        ).fetchall()
        for row in live:
            fill_core.revoke_execution_grant(connection, row["grant_id"], now)

    def _ensure_authority_index(self, connection) -> None:
        """Put the invariant in the database, or refuse to prepare without it.

        A database written before this constraint existed can already hold two unrevoked
        grants for one page — that is the bug, and its traces do not disappear because the
        rule arrived. Creating the index then fails, and the honest answer is to stop rather
        than to carry on with the guarantee quietly absent or to revoke history until it
        fits. The refusal names a code and leaves the rows for a person to look at.
        """
        try:
            connection.execute(LIVE_GRANT_INDEX)
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise BridgeError("fill_authority_index_unavailable", 409) from error

    @contextlib.contextmanager
    def _across_processes(self):
        """Hold the preparation critical section against another bridge on this database.

        `fill_core`'s calls commit as they go, so an outer transaction cannot span the
        revoke and the issue — the first inner commit would end it. A file lock can span
        them. It is an ordering device, not the guarantee: the guarantee is the unique index,
        which holds even where this lock does not exist.
        """
        if fcntl is None:  # pragma: no cover - not the platform this runs on
            yield
            return
        root = self.private_root / "fill-runs"
        root.mkdir(parents=True, exist_ok=True)
        handle = os.open(root / FILL_PREPARE_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
        finally:
            os.close(handle)

    def _fill_prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _closed_request(payload, FILL_PREPARE_FIELDS, "fill_request_invalid")
        import fill_core  # local: keeps the read-only path free of write imports

        now = datetime.now(timezone.utc)
        connection = self._connection()
        try:
            session, page, steps = self._fill_state(
                connection, request["application_id"], now)

            self._ensure_authority_index(connection)

            def build() -> _PreparedRun:
                # Under `FillControl`'s lock, the file lock, and the unique index — in that
                # order of increasing authority. The first orders this process, the second
                # orders the bridges on this machine, and the third is what actually holds.
                with self._across_processes():
                    self._revoke_live_grants(connection, fill_core, session["session_id"],
                                             page["page_id"], now)
                    execution_id = uuid.uuid4().hex
                    root = self.private_root / "fill-runs" / execution_id
                    root.mkdir(parents=True, exist_ok=True)
                    root.chmod(0o700)
                    package_path = root / "actions.json"
                    try:
                        fill_core.export_page(connection, session["session_id"],
                                              session["worker_id"], page["page_id"],
                                              package_path, now)
                        grant = fill_core.issue_execution_grant(
                            connection, session["session_id"], page["page_id"],
                            package_path, now)
                    except sqlite3.IntegrityError as error:
                        # Another bridge got there first and its authority is still live.
                        # Refused rather than queued: whichever page this is, someone else is
                        # already holding the one grant it may have.
                        raise BridgeError("fill_page_already_authorised", 409) from error
                    except ValueError as error:
                        raise BridgeError("fill_page_not_exportable", 409) from error
                return _PreparedRun(
                    execution_id=execution_id, application_id=session["application_id"],
                    session_id=session["session_id"], page_id=page["page_id"],
                    worker_id=session["worker_id"], grant_id=grant["grant_id"],
                    package_path=package_path, result_path=root / "result.json",
                    capability_path=root / "capability.json",
                    summary=self._fill_summary(session, page, steps, grant, execution_id),
                    expires_at=now + timedelta(seconds=FILL_PREPARED_TTL_SECONDS),
                    state="prepared", outcome=None)

            run, _created = self.fill_control.prepare(
                (session["session_id"], page["page_id"]), now, build)
            return run.summary
        except BridgeError:
            raise
        except Exception as error:
            # Nothing from here is allowed to describe itself. `mkdir`, `chmod`, the export
            # and the grant all name the private root in their messages, and a failure
            # response is as much a part of this API as a successful one.
            raise BridgeError("fill_preparation_failed", 409) from error
        finally:
            connection.close()

    def _fill_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _closed_request(payload, FILL_EXECUTE_FIELDS, "fill_request_invalid")
        now = datetime.now(timezone.utc)
        run = self.fill_control.claim(request["execution_id"], now)
        import execution_authority  # local: keeps the read-only path free of write imports
        import fill_core
        import fill_worker

        connection = self._connection(shared=True)
        try:
            # Re-verified here and not carried over from prepare. Between the two presses a
            # lease can lapse, a page can be checkpointed and a grant can be revoked, and
            # the run must find that out before a browser exists rather than after.
            session, page, _ = self._fill_state(connection, run.application_id, now)
            if (session["session_id"] != run.session_id
                    or page["page_id"] != run.page_id
                    or session["worker_id"] != run.worker_id):
                raise BridgeError("fill_identity_changed", 409)
            grant = connection.execute(
                "SELECT * FROM execution_grants WHERE grant_id=?", (run.grant_id,)).fetchone()
            if not grant:
                raise BridgeError("fill_grant_unknown", 409)
            if grant["consumed_at"]:
                raise BridgeError("fill_grant_already_consumed", 409)
            if grant["revoked_at"]:
                raise BridgeError("fill_grant_revoked", 409)
            expires = datetime.fromisoformat(grant["expires_at"])
            if now >= expires:
                raise BridgeError("fill_grant_expired", 409)

            with execution_authority.ExecutionAuthority(
                connection, fill_core.reserve_execution_grant,
                consume=fill_core.consume_execution_grant,
            ) as authority:
                capability = authority.write_capability(run.capability_path)
                url, capability_token = fill_worker.read_capability(capability)
                # The worker opens the authority's target in its own window. No URL, origin
                # or tab from the extension reaches this call, because none was accepted.
                fill_worker.run(run.package_path, run.result_path, url, capability_token,
                                run.grant_id, headed=self.fill_headed)
            imported = fill_core.import_result(
                connection, run.session_id, run.page_id, run.worker_id, run.result_path,
                run.grant_id, now)
            outcome = self._fill_outcome(connection, run, imported, now)
            self.fill_control.finish(run, "finished", outcome)
            return outcome
        except BridgeError:
            self.fill_control.finish(run, "failed", None)
            raise
        except Exception as error:
            # The grant may or may not have been spent, so the run is over either way: a
            # retry that could replay a consumed package is exactly what must not exist.
            #
            # A stable code and nothing else. The worker, the authority and the import all
            # raise messages that can carry a path, a target or a field value, and handing
            # `str(error)` to the panel would put them there — a value-free success response
            # beside a talkative failure response is not a value-free API.
            self.fill_control.finish(run, "failed", None)
            raise BridgeError("fill_execution_failed", 409) from error
        finally:
            connection.close()

    def _fill_outcome(self, connection, run: _PreparedRun, imported: dict[str, Any],
                      now: datetime) -> dict[str, Any]:
        """Counts and stable codes. Never a claim that anything was sent."""
        steps = connection.execute(
            "SELECT status FROM fill_steps WHERE session_id=? AND page_id=?",
            (run.session_id, run.page_id)).fetchall()
        counted: dict[str, int] = {}
        for row in steps:
            counted[row["status"]] = counted.get(row["status"], 0) + 1
        session = connection.execute(
            "SELECT status, pause_reasons_json FROM fill_sessions WHERE session_id=?",
            (run.session_id,)).fetchone()
        grant = connection.execute(
            "SELECT consumed_at, expires_at FROM execution_grants WHERE grant_id=?",
            (run.grant_id,)).fetchone()
        reasons = json.loads(session["pause_reasons_json"]) if session else []
        return {
            "execution_id": run.execution_id,
            "status": imported.get("status", "unknown"),
            "verified": counted.get("completed", 0),
            "pending": counted.get("pending", 0),
            "paused": counted.get("paused", 0),
            "session_status": session["status"] if session else "unknown",
            # Stable codes only. `_field_reason` hashes the field, so nothing here can name
            # the question, let alone what was put in it.
            "reasons": [str(reason).split(":", 1)[0] for reason in reasons],
            "package_consumed": bool(grant and grant["consumed_at"]),
            "package_expired": bool(
                grant and now >= datetime.fromisoformat(grant["expires_at"])),
            # The submit control is a boundary that was looked at, never a control that was
            # acted on: no submission action exists in the protocol to act on it with.
            "submit_boundary": "observed_not_acted_on",
            "stops_before_submit": True,
            "runs_in_separate_window": True,
        }


def serve(*, db_path: Path, candidate_path: Path, port: int, allow_store: bool,
          token: str | None = None, fill_headed: bool = True) -> ThreadingHTTPServer:
    """One server, with its own settings and its own prepared runs.

    A subclass per server rather than attributes on `Handler` itself. Setting them on the
    shared class meant a second `serve` silently replaced the first one's `FillControl`, so
    two bridges in one process were not two bridges at all — they were one, wearing two
    ports, which is precisely the arrangement a test of two bridges must not accidentally
    create. The handler class is reachable as `server.RequestHandlerClass`.
    """
    bound = type("BoundHandler", (Handler,), {
        "token": token or secrets.token_urlsafe(24),
        "candidate_path": candidate_path,
        "db_path": db_path,
        "allow_store": allow_store,
        # Headed unless a test says otherwise. The separate window is the thing that makes a
        # run the user did not press stop on still a run they watched.
        "fill_headed": fill_headed,
        "fill_control": FillControl(),
    })
    return ThreadingHTTPServer((LOOPBACK, port), bound)


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
