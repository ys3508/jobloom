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
from urllib.parse import parse_qs, urlsplit

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import apply_assist  # noqa: E402
import build_worksheets  # noqa: E402
import candidate_core  # noqa: E402
import candidate_profile  # noqa: E402
import resume_core  # noqa: E402
import resume_migration  # noqa: E402
import sponsorship_triage  # noqa: E402
import submission_record  # noqa: E402
from _common import require_table  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"
LOOPBACK = "127.0.0.1"
ROUND = "onboarding-v1"
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
        # The grouping is the service's, not the page's: which fields belong on one screen is
        # part of what was reviewed, and a page that decided it could put a country code on
        # its own or bury a required field behind an optional one.
        "screens": [{"name": name, "fields": [f for f in fields
                                              if f in candidate_profile.PROFILE_ROUNDS[ROUND]]}
                    for name, fields in candidate_profile.PROFILE_SCREENS],
        "fields_in_round": sorted(candidate_profile.PROFILE_ROUNDS[ROUND]),
        "resolvable": report.get("resolvable", []),
        "unresolved": report.get("unresolved", {}),
        "open_round": open_round,
    }


# ---- the application-assist slice ----------------------------------------------
#
# The second vertical through this service, and it holds the same line the first one does:
# the page asks and renders, and every verdict it shows was returned by the module that owns
# it. `apply_assist` orders `field_policy`, `answer_library` and `evaluate_job`; nothing here
# writes, so a person can open a form, find out where it is stuck, and close the window
# having changed nothing.


def _active_candidate(connection: sqlite3.Connection) -> tuple[dict[str, Any], str]:
    """The one active, user-registered CandidateSnapshot, re-verified before it is read.

    Not `<private_root>/candidate.json`. That file is whatever was last written beside the
    database, and after a profile is registered it can be a superseded snapshot — it was one
    here, and the first version of this slice answered forms from it. The row is the record of
    which profile is in force, and `snapshot_path` on that row is the only path this reads: a
    caller-supplied path would let the page choose which profile answered an employer.

    Three checks before the document is trusted, reusing the functions the fill path already
    runs rather than restating their rules: exactly one active row, the file still hashing to
    `file_sha256`, and the document's own deterministic content hash still equal to
    `content_sha256`. Every refusal is a bare code — a message here could carry a path.
    """
    require_table(connection, "candidate_snapshots")
    rows = connection.execute(
        "SELECT * FROM candidate_snapshots WHERE status='active' AND registered_by='user'"
    ).fetchall()
    if not rows:
        raise AppError("no_active_candidate_snapshot", 409)
    if len(rows) > 1:
        # The schema has a partial unique index for this, so reaching it means the database was
        # edited around the engine. Refusing is the only safe reading: picking one would answer
        # an employer's form from a profile nobody arbitrated.
        raise AppError("multiple_active_candidate_snapshots", 409)
    row = rows[0]
    try:
        candidate_core.verify_snapshot_file(row)
    except (OSError, ValueError):
        raise AppError("candidate_snapshot_file_mismatch", 409) from None
    try:
        candidate, content_hash = resume_core.load_valid_candidate(Path(row["snapshot_path"]))
    except (OSError, ValueError):
        raise AppError("candidate_snapshot_unreadable", 409) from None
    if content_hash != row["content_sha256"]:
        raise AppError("candidate_snapshot_content_mismatch", 409)
    return candidate, row["content_sha256"]


def apply_queue(connection: sqlite3.Connection) -> dict[str, Any]:
    """The applications, with the ones the user has said they finished marked as done.

    The exclusion reads the saved job rather than the application's state. There is no state
    meaning "the user submitted this themselves" and there should not be: `ready_to_fill` is
    still literally true of it, because nothing was filled by Jobloom and nothing will be
    while the worker is fixture-only.
    """
    done = submission_record.pending(connection)
    rows = []
    for row in apply_assist.queue(connection):
        confirmed = done.get(row["application_id"])
        rows.append({**row, "submitted_confirmed_at": confirmed,
                     "pending": confirmed is None})
    return {"applications": rows,
            "pending": sum(1 for row in rows if row["pending"]),
            "confirmed": len(rows) - sum(1 for row in rows if row["pending"])}


# ---- recording a submission the user made by hand -------------------------------
#
# The only write path in this vertical. It climbs two of the three rungs `saved_jobs`
# defines and cannot reach the third: nothing here calls `application_core.transition` or
# writes `submission_evidence`, so the user's word can never open the gate that positive
# employer evidence guards.

SUBMISSION_FIELDS = ("application_id", "employer", "title", "location", "canonical_url",
                     "resume_version_id", "state", "rung", "intended_at", "confirmed_at",
                     "reference_kind", "reference", "reference_kinds", "evidenced",
                     "evidence_unreachable_reason")


def _application_id(payload: dict[str, Any]) -> str:
    application_id = payload.get("application_id")
    if not isinstance(application_id, str) or not application_id:
        raise AppError("bad_application_id")
    return application_id


def submission_state(connection: sqlite3.Connection,
                     payload: dict[str, Any]) -> dict[str, Any]:
    try:
        report = submission_record.state(connection, _application_id(payload))
    except ValueError:
        raise AppError("no_such_application", 404) from None
    except RuntimeError:
        # A required table is absent, which means this database was never initialised. Read
        # paths say so rather than migrating underneath a caller who only wanted to look.
        raise AppError("database_not_initialised", 409) from None
    return {key: report[key] for key in SUBMISSION_FIELDS}


def submission_intend(connection: sqlite3.Connection,
                      payload: dict[str, Any]) -> dict[str, Any]:
    application_id = _application_id(payload)
    try:
        submission_record.intend(connection, application_id)
    except ValueError:
        raise AppError("cannot_record_intention", 409) from None
    return submission_state(connection, payload)


def submission_confirm(connection: sqlite3.Connection,
                       payload: dict[str, Any]) -> dict[str, Any]:
    """Rung 2, and the page may not claim anything beyond it.

    The payload carries a reference kind and the reference itself; it cannot carry the
    timestamp, the rung, or whether the submission is evidenced. Those are the service's, and
    a page able to send them could record a rung-3 claim by typing one.
    """
    application_id = _application_id(payload)
    kind = payload.get("reference_kind")
    reference = payload.get("reference")
    if kind is not None and not isinstance(kind, str):
        raise AppError("bad_reference_kind")
    if reference is not None and not isinstance(reference, str):
        raise AppError("bad_reference")
    try:
        submission_record.confirm(connection, application_id,
                                  reference_kind=kind or None, reference=reference or None)
    except ValueError as error:
        # Mapped to bare codes rather than passed through. These particular messages carry no
        # value, but a code built by reformatting an exception is a code that changes whenever
        # somebody rewords a `raise` — and the next message might not be as harmless.
        text = str(error)
        if "decided to apply" in text:
            raise AppError("intention_not_recorded_first", 409) from None
        if "reference kind" in text:
            raise AppError("unknown_reference_kind", 400) from None
        if "needs the reference" in text or "what kind of thing" in text:
            raise AppError("reference_incomplete", 400) from None
        if "no saved job" in text:
            raise AppError("intention_not_recorded_first", 409) from None
        if "longer than" in text:
            # The reference itself never reaches the response, the log or the exception; only
            # the fact that it was too long to keep whole.
            raise AppError("reference_too_long", 400) from None
        if "must be text" in text:
            raise AppError("bad_reference", 400) from None
        raise AppError("submission_refused", 409) from None
    return submission_state(connection, payload)


def submission_tracker(db_path: Path, private_root: Path) -> dict[str, Any]:
    """Rebuild the tracker from state, never from anything anybody typed into a spreadsheet.

    `build_worksheets` writes the xlsx with `worksheet_writer`, which is stdlib only — it
    exists because `build_application_tracker.mjs` needs a package this repository cannot
    install. So no process is spawned and no dependency is required.
    """
    queues = sorted(Path(private_root).glob("review-queue-*.json"),
                    key=lambda path: path.stat().st_mtime, reverse=True)
    report = build_worksheets.build(queues[0] if queues else None, Path(db_path),
                                    Path(private_root))
    # Paths are not returned: the directory is the user's own private root and naming it
    # back into a page is how a path reaches somewhere it was never meant to go.
    return {"written": [{key: entry[key] for key in entry if key != "source"}
                        for entry in report["written"]],
            "built_at": report["built_at"]}


# What may leave the service for each screen. Written as allowlists rather than by handing back
# whatever a row happens to hold: `applications` and `jobs` gain columns, and a page that
# serialised the row would start publishing them without anybody deciding to.
READINESS_APPLICATION_FIELDS = ("application_id", "job_id", "state", "category",
                               "resume_version_id", "submission_policy",
                               "employer", "title", "location", "canonical_url")
EVALUATION_FIELDS = ("eligibility", "match", "action", "reasons", "hard_filter_failures",
                     "uncertainties", "main_gap", "user_decision_required", "unavailable")
CLASSIFIED_FIELDS = ("question", "lane", "reason", "source", "canonical_id", "domain",
                     "family", "narrative_hint", "answer_exists", "auto_fill_ready",
                     "auto_submit_ready", "authorization_reason", "related_fact_ids",
                     "related_overlap", "facts_considered")


def apply_readiness(connection: sqlite3.Connection,
                    payload: dict[str, Any]) -> dict[str, Any]:
    application_id = payload.get("application_id")
    if not isinstance(application_id, str) or not application_id:
        raise AppError("bad_application_id")
    candidate, _ = _active_candidate(connection)
    try:
        report = apply_assist.readiness(connection, application_id, candidate)
    except ValueError:
        raise AppError("no_such_application", 404) from None
    evaluation = report["evaluation"]
    return {
        "application": {key: report["application"].get(key)
                        for key in READINESS_APPLICATION_FIELDS},
        "sponsorship_statements": report["sponsorship_statements"],
        "evaluation": {key: evaluation[key] for key in EVALUATION_FIELDS if key in evaluation},
    }


def apply_split(payload: dict[str, Any]) -> dict[str, Any]:
    return {"segments": apply_assist.split_questions(payload.get("text") or "")}


def apply_classify(connection: sqlite3.Connection,
                   payload: dict[str, Any]) -> dict[str, Any]:
    """Sort a page of questions. Reads the active profile; writes nothing at all.

    The payload carries questions and an application id, and nothing else is read from it. A
    candidate path, a snapshot hash, a fact id, an answer id or an authorization decision
    arriving from the page would each be the page choosing what answers an employer, so all of
    them are resolved here from rows the service verified itself.
    """
    application_id = payload.get("application_id")
    questions = payload.get("questions")
    if not isinstance(application_id, str) or not application_id:
        raise AppError("bad_application_id")
    if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
        raise AppError("bad_questions")
    candidate, active_sha256 = _active_candidate(connection)
    locked_sha256, lock_reason = apply_assist.application_snapshot(connection, application_id)
    context = {"application_id": application_id,
               "country": candidate.get("work_authorization", {}).get("country")}
    result = apply_assist.classify(
        connection, questions, snapshot_sha256=locked_sha256, context=context,
        facts=apply_assist.snapshot_facts(connection, active_sha256))
    fact_values = {}
    wanted = {fid for item in result["questions"] for fid in item["related_fact_ids"]}
    for fact in apply_assist.snapshot_facts(connection, active_sha256):
        if fact["id"] in wanted:
            fact_values[fact["id"]] = fact["value"]
    return {
        "questions": [{key: item.get(key) for key in CLASSIFIED_FIELDS}
                      for item in result["questions"]],
        "counts": result["counts"],
        "blocking": result["blocking"],
        "fact_values": fact_values,
        "materials_locked": locked_sha256 is not None,
        "materials_reason": lock_reason,
        "writes": False,
    }


def sponsorship_queue(private_root: Path) -> dict[str, Any]:
    """The queued openings a person could settle by reading the employer's own sentence.

    No database at all: the queue is a built file and the cards are the pull it was built
    from, so this endpoint cannot write even by accident.
    """
    return sponsorship_triage.build(Path(private_root))


def sponsorship_posting(private_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise AppError("bad_job_id")
    try:
        return sponsorship_triage.posting(Path(private_root), job_id)
    except ValueError:
        raise AppError("no_such_opening", 404) from None


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
        return {"fields": _fields(_read_worksheet(connection, private_root)), "resumed": True,
                "countries": list(candidate_profile.COUNTRY_NAMES)}
    except AppError:
        pass
    except ValueError:
        # Spent or stale. The file is moved aside rather than deleted: it is the user's, and
        # a worksheet nobody can confirm is still a record of what they were asked.
        path.replace(path.with_suffix(f".superseded-{secrets.token_hex(4)}.json"))
    candidate_profile.propose_profile(
        connection, ROUND,
        sink=lambda sheet: candidate_profile.write_private_document(path, private_root, sheet))
    return {"fields": _fields(_read_worksheet(connection, private_root)), "resumed": False,
            "countries": list(candidate_profile.COUNTRY_NAMES)}


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


# ---- carrying the resumes the new profile left behind --------------------------
#
# The page shows and asks; every judgement stays here. It cannot say a version is migratable,
# and it cannot name a file: no candidate path, no resume path, no manifest path crosses the
# boundary in either direction. The service resolves all three from database rows it has
# already verified.

def migrations(connection: sqlite3.Connection) -> dict[str, Any]:
    """Which approved resumes the active profile left behind, and where each one has got to."""
    try:
        rows = resume_migration.stranded(connection)
    except ValueError:
        # No active snapshot yet, which is simply "nothing to carry" from the window's side.
        return {"stranded": [], "carryable": 0}
    return {"stranded": rows, "carryable": sum(1 for row in rows if row["migratable"])}


def prepare_migration(connection: sqlite3.Connection, store: Path,
                      payload: dict[str, Any]) -> dict[str, Any]:
    predecessor = payload.get("predecessor_version_id")
    if not isinstance(predecessor, str) or not predecessor:
        raise AppError("predecessor_required")
    result = resume_migration.prepare_successor(connection, Path(store), predecessor)
    # The manifest's location is the service's business. What the user needs is that the
    # claims were the ones already approved, and how many of them there are to look at.
    manifest = json.loads(Path(result.pop("claims_manifest_path")).read_text(encoding="utf-8"))
    result["claims"] = [{"claim_id": claim["claim_id"], "claim_text": claim["claim_text"],
                         "evidence_strength": claim["evidence_strength"]}
                        for claim in manifest.get("claims", [])]
    return result


def approve_migration(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """Approve the successor for the profile that is active now.

    The candidate document is the active snapshot's own registered file, read from the row
    rather than accepted from the page. A page that could name a candidate path could approve
    a resume against a profile nobody registered.
    """
    successor = payload.get("successor_version_id")
    if not isinstance(successor, str) or not successor:
        raise AppError("successor_required")
    if not payload.get("materials_reviewed") is True:
        # The first of the two approvals, and the one a page is most likely to skip. Opening
        # the file is not approving it, so the press that says so is its own field.
        raise AppError("materials_not_reviewed", 409)
    require_table(connection, "candidate_snapshots")
    snapshot = connection.execute(
        "SELECT snapshot_path FROM candidate_snapshots WHERE status='active' "
        "AND registered_by='user'").fetchone()
    if not snapshot:
        raise AppError("no_active_profile", 409)
    return resume_migration.approve_successor(
        connection, successor, Path(snapshot["snapshot_path"]), "user")


def bind_migration(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    successor = payload.get("successor_version_id")
    application_id = payload.get("application_id")
    if not isinstance(successor, str) or not successor:
        raise AppError("successor_required")
    if not isinstance(application_id, str) or not application_id:
        raise AppError("application_required")
    return resume_migration.bind_for_application(connection, application_id, successor, "user")


def resume_bytes(connection: sqlite3.Connection, version_id: str) -> tuple[bytes, str]:
    """The actual PDF, for the user to look at before approving it.

    A hash proves two files are the same file; it does not prove the file is the one a person
    wants sent to an employer, and only they can answer that. So the bytes are served - from
    the path the registry holds, verified against the hash the registry holds, never from
    anything the page said. A version outside a live migration is not viewable here: this
    endpoint exists for the document under review and is not a way to read the resume store.
    """
    if not isinstance(version_id, str) or not version_id:
        raise AppError("version_required")
    require_table(connection, "resume_migrations")
    carried = connection.execute(
        "SELECT 1 FROM resume_migrations WHERE (successor_version_id=? OR "
        "predecessor_version_id=?) AND status IN (?, ?)",
        (version_id, version_id, resume_migration.PREPARED, resume_migration.APPROVED)).fetchone()
    if not carried:
        raise AppError("version_not_under_review", 403)
    version = connection.execute(
        "SELECT snapshot_path, file_sha256, file_format FROM resume_versions WHERE version_id=?",
        (version_id,)).fetchone()
    if not version or version["file_format"] != "pdf":
        raise AppError("version_not_viewable", 403)
    path = Path(version["snapshot_path"])
    if not path.is_file() or resume_core.file_sha256(path) != version["file_sha256"]:
        raise AppError("version_changed", 409)
    return path.read_bytes(), version["file_sha256"]


# ---- the local service ---------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "JobloomApp/0.1"
    token = ""
    db_path: Path
    private_root: Path
    store: Path
    resume_store: Path
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

    def _send_page(self, asset: str = "onboarding.html") -> None:
        body = (ASSETS / asset).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The page talks to this origin and loads nothing from anywhere else. Spelled out so a
        # future edit that reaches for a font or a CDN fails here rather than silently sending
        # a request from a window holding somebody's contact details.
        # `blob:` for frames and objects and nothing else: the resume under review is fetched
        # with the session token, turned into a blob and shown inside this window. An
        # `<iframe src="/api/resume-file">` could not carry the token, and a new tab would
        # need it in a URL, where it would land in history.
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; "
                         "style-src 'unsafe-inline'; connect-src 'self'; "
                         "frame-src blob:; object-src blob:")
        self.end_headers()
        self.wfile.write(body)

    def _send_resume(self, raw_path: str) -> None:
        """The PDF itself, inline, from this origin only and never cached.

        A read, so it is a GET - but a GET that does nothing except hand back bytes the
        registry has just re-verified. No migration advances here: opening a document is not
        approving it, and the two are separate presses precisely so that one cannot be
        mistaken for the other.
        """
        query = parse_qs(urlsplit(raw_path).query)
        version_id = (query.get("version_id") or [""])[0]
        connection = self._connection()
        try:
            body, digest = resume_bytes(connection, version_id)
        except AppError as error:
            self._send(error.status, {"error": error.code})
            return
        except Exception:
            self._send(500, {"error": "app_failure"})
            return
        finally:
            connection.close()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(body)))
        # Shown, not saved: the viewer is the window the user already has open, and a copy in
        # a downloads folder is a second place this document lives.
        self.send_header("Content-Disposition", "inline")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Jobloom-File-Sha256", digest)
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
        if path == "/apply":
            self._send_page("apply.html")
            return
        if path == "/triage":
            self._send_page("triage.html")
            return
        if not self._authorised():
            self._send(403, {"error": "bad_token"})
            return
        if path == "/api/state":
            self._run(lambda connection: state(connection, self.private_root))
        elif path == "/api/apply/queue":
            self._run(apply_queue)
        elif path == "/api/sponsorship/queue":
            self._run(lambda _: sponsorship_queue(self.private_root), needs_db=False)
        elif path == "/api/resume-migrations":
            self._run(migrations)
        elif path == "/api/resume-file":
            self._send_resume(self.path)
        else:
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
        elif path == "/api/resume-migrations/prepare":
            self._run(lambda connection: prepare_migration(connection, self.resume_store,
                                                           payload))
        elif path == "/api/resume-migrations/approve":
            self._run(lambda connection: approve_migration(connection, payload))
        elif path == "/api/apply/readiness":
            self._run(lambda connection: apply_readiness(connection, payload))
        elif path == "/api/submission/state":
            self._run(lambda connection: submission_state(connection, payload))
        elif path == "/api/submission/intend":
            self._run(lambda connection: submission_intend(connection, payload))
        elif path == "/api/submission/confirm":
            self._run(lambda connection: submission_confirm(connection, payload))
        elif path == "/api/submission/tracker":
            self._run(lambda _: submission_tracker(self.db_path, self.private_root),
                      needs_db=False)
        elif path == "/api/sponsorship/posting":
            self._run(lambda _: sponsorship_posting(self.private_root, payload), needs_db=False)
        elif path == "/api/apply/split":
            self._run(lambda _: apply_split(payload), needs_db=False)
        elif path == "/api/apply/classify":
            self._run(lambda connection: apply_classify(connection, payload))
        elif path == "/api/resume-migrations/bind":
            self._run(lambda connection: bind_migration(connection, payload))
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
          open_browser: bool = True, resume_store: Path | None = None) -> ThreadingHTTPServer:
    connection = candidate_profile.connect(db_path)
    # Every component the window can reach, not only the first one it needs. Initialising
    # lazily meant the screen after registering was the first thing to touch
    # `resume_migrations`, and it found no such table.
    resume_migration.initialize(connection)
    # The submission slice's schema, brought up to date here rather than on first read.
    # `submission_record.state` is a read path and refuses an uninitialised database instead
    # of migrating it, so this is where the migration has to happen.
    submission_record.initialize(connection)
    connection.close()
    Path(private_root).mkdir(parents=True, exist_ok=True)
    os.chmod(private_root, 0o700)
    Handler.token = secrets.token_urlsafe(32)
    Handler.db_path = Path(db_path)
    Handler.private_root = Path(private_root)
    Handler.store = Path(store)
    # Two stores, because they hold two different things. `store` is the candidate snapshot
    # store, where `register` writes a profile; a resume successor belongs with the other
    # resumes. One path serving both would file resume PDFs inside the profile store, where
    # nothing else expects to find them.
    Handler.resume_store = Path(resume_store) if resume_store else Path(db_path).parent / "resumes"
    server = ThreadingHTTPServer((LOOPBACK, port), Handler)
    Handler.origin = f"http://{LOOPBACK}:{server.server_port}"
    url = f"{Handler.origin}/?token={Handler.token}"
    if open_browser:
        where = _open_window(url)
        print(f"Jobloom is open in a {where}. Leave this running while you use it.")
        print("Nothing here reaches the network; the page talks only to this process.")
    else:
        # Flushed, because this line is the whole point of `--no-browser`: the process then
        # blocks serving, and a buffered URL does not appear until it is killed.
        print(url, flush=True)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Open Jobloom.")
    parser.add_argument("--db", type=Path, default=Path(".jobloom/jobloom.db"))
    parser.add_argument("--private-root", type=Path)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--resume-store", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    private_root = args.private_root or args.db.parent
    store = args.store or args.db.parent / "candidates"
    server = serve(args.db, private_root, store, args.port, not args.no_browser,
                   resume_store=args.resume_store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
