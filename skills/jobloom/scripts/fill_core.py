#!/usr/bin/env python3
"""Deterministic, checkpointed, fill-only form execution for Jobloom."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import answer_library  # noqa: E402
import application_core  # noqa: E402
import archive_core  # noqa: E402
import field_policy  # noqa: E402
import pre_submit_core  # noqa: E402
import resume_core  # noqa: E402
import worker_protocol  # noqa: E402
from _common import require_application_material_format, require_table  # noqa: E402


ALLOWED_CONTROLS = {"text", "textarea", "select", "radio", "checkbox", "file",
                    "standard_attestation", "submit"}
VALUE_CONTROLS = {"text", "textarea", "select", "radio", "checkbox"}
USER_ANSWER_PAUSES = {
    "new_question", "question_mapping_conflict", "no_applicable_answer", "answer_expired",
    "answer_review_due", "answer_not_yet_effective", "conflicting_active_answers",
    "automatic_fill_not_allowed", "legal_commitment_requires_review", "immigration_recheck_required",
    "standing_authorization_missing",
    "standing_authorization_unknown", "standing_authorization_revoked",
    "standing_authorization_expired", "standing_authorization_scope_mismatch",
    "candidate_fact_unknown", "candidate_fact_not_locked", "candidate_fact_expired",
    "sponsorship_meaning_ambiguous", "discovery_source_not_user_confirmed",
}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(value: Any) -> str:
    return resume_core.canonical_hash(value)


def _field_reason(code: str, field_id: str, sensitivity: str = "normal") -> str:
    suffix = field_id if sensitivity == "normal" else f"sensitive-{canonical_hash(field_id)[:16]}"
    return f"{code}:{suffix}"


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a safe stable identifier")


def _safe_url(value: str, label: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{label} must be an HTTP(S) URL without embedded credentials")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{label} has an invalid port") from error
    host = (parsed.hostname or "").casefold()
    netloc = f"{host}:{port}" if port else host
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "/", "", ""))


def _same_origin(first: str, second: str) -> bool:
    left, right = urlsplit(first), urlsplit(second)
    return (left.scheme.casefold(), left.hostname, left.port) == (right.scheme.casefold(), right.hostname, right.port)


def connect(path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    initialize(connection)
    if str(path) != ":memory:":
        os.chmod(path, 0o600)
    return connection


def _add_missing_columns(connection: sqlite3.Connection) -> None:
    existing = {row["name"] for row in connection.execute("PRAGMA table_info(fill_pages)")}
    for name, definition in (("final_page", "INTEGER NOT NULL DEFAULT 0"),
                             ("submit_control_seen", "INTEGER NOT NULL DEFAULT 0"),
                             ("predecessor_checkpoint_sha256", "TEXT")):
        if existing and name not in existing:
            connection.execute(f"ALTER TABLE fill_pages ADD COLUMN {name} {definition}")


def initialize(connection: sqlite3.Connection) -> None:
    field_policy.initialize(connection)
    _add_missing_columns(connection)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS fill_sessions (
            session_id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            form_url TEXT NOT NULL,
            observed_employer TEXT NOT NULL,
            observed_role TEXT NOT NULL,
            known_form INTEGER NOT NULL,
            authorization_id TEXT,
            authorization_context_json TEXT NOT NULL,
            status TEXT NOT NULL,
            current_page_id TEXT,
            submit_control_seen INTEGER NOT NULL DEFAULT 0,
            pause_reasons_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (application_id) REFERENCES applications(application_id)
        );
        CREATE INDEX IF NOT EXISTS fill_session_application_idx
            ON fill_sessions(application_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS fill_pages (
            session_id TEXT NOT NULL,
            page_id TEXT NOT NULL,
            page_index INTEGER NOT NULL,
            page_url TEXT NOT NULL,
            observation_json TEXT NOT NULL,
            observation_sha256 TEXT NOT NULL,
            legal_items_json TEXT NOT NULL,
            restricted_requests_json TEXT NOT NULL,
            status TEXT NOT NULL,
            final_page INTEGER NOT NULL DEFAULT 0,
            submit_control_seen INTEGER NOT NULL DEFAULT 0,
            predecessor_checkpoint_sha256 TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            PRIMARY KEY (session_id, page_id),
            UNIQUE (session_id, page_index),
            FOREIGN KEY (session_id) REFERENCES fill_sessions(session_id)
        );

        CREATE TABLE IF NOT EXISTS fill_steps (
            step_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            page_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            field_id TEXT NOT NULL,
            question TEXT NOT NULL,
            selector TEXT NOT NULL,
            control TEXT NOT NULL,
            required INTEGER NOT NULL,
            operation TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_status TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            value_json TEXT NOT NULL,
            expected_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            completed_at TEXT,
            UNIQUE (session_id, page_id, field_id),
            FOREIGN KEY (session_id, page_id) REFERENCES fill_pages(session_id, page_id)
        );
        CREATE INDEX IF NOT EXISTS fill_step_next_idx
            ON fill_steps(session_id, page_id, status, ordinal);

        CREATE TABLE IF NOT EXISTS fill_checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            page_id TEXT NOT NULL,
            checkpoint_sha256 TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            UNIQUE (session_id, page_id),
            FOREIGN KEY (session_id, page_id) REFERENCES fill_pages(session_id, page_id)
        );

        CREATE TABLE IF NOT EXISTS fill_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES fill_sessions(session_id)
        );
    """)
    connection.commit()


def _event(
    connection: sqlite3.Connection,
    session: sqlite3.Row | dict[str, Any],
    actor: str,
    event_type: str,
    reason_code: str,
    metadata: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> None:
    connection.execute(
        "INSERT INTO fill_events (session_id, application_id, created_at, actor, event_type, "
        "reason_code, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session["session_id"], session["application_id"], (at or now_utc()).isoformat(), actor,
         event_type, reason_code, canonical_json(metadata or {})),
    )


def _application_context(connection: sqlite3.Connection, application_id: str,
                         supplied: dict[str, Any]) -> tuple[sqlite3.Row, dict[str, Any]]:
    if not isinstance(supplied, dict):
        raise ValueError("authorization context must be an object")
    unknown_context = set(supplied) - answer_library.SCOPE_FIELDS
    if unknown_context:
        raise ValueError("authorization context contains unsupported fields")
    application = connection.execute("""
        SELECT a.*, j.employer, j.title, j.job_card_json
        FROM applications a JOIN jobs j ON j.job_id=a.job_id WHERE a.application_id=?
    """, (application_id,)).fetchone()
    if not application:
        raise ValueError("application not found")
    card = json.loads(application["job_card_json"])
    context = dict(supplied)
    context["application_id"] = application_id
    context["company"] = application["employer"]
    if card.get("country") is not None:
        context["country"] = card["country"]
    if card.get("employment_type") is not None:
        context["employment_type"] = card["employment_type"]
    return application, context


def _require_lease(connection: sqlite3.Connection, application_id: str, worker_id: str,
                   at: datetime | None = None) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM applications WHERE application_id=?", (application_id,)).fetchone()
    current = at or now_utc()
    if not row or row["state"] != "filling" or row["worker_id"] != worker_id:
        raise ValueError("active fill lease is not owned by this worker")
    lease_expires = parse_time(row["lease_expires_at"])
    if not lease_expires or lease_expires <= current:
        raise ValueError("fill lease is expired")
    application_core.require_active_material_lock(connection, application_id)
    return row


def _pause(
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    reasons: list[str],
    actor: str,
    page_id: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    unique = sorted(set(reasons))
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE fill_sessions SET status='paused', pause_reasons_json=?, updated_at=? WHERE session_id=?",
        (canonical_json(unique), timestamp, session["session_id"]),
    )
    if page_id:
        connection.execute(
            "UPDATE fill_pages SET status='paused' WHERE session_id=? AND page_id=?",
            (session["session_id"], page_id),
        )
    _event(connection, session, actor, "paused", unique[0],
           {"reason_codes": unique, "page_id": page_id}, at)
    answer_pause = all(reason.split(":", 1)[0] in USER_ANSWER_PAUSES for reason in unique)
    target = "waiting_for_user_answer" if answer_pause else "waiting_for_user_takeover"
    application_core.release_lease(
        connection, session["application_id"], session["worker_id"], target,
        "fill_paused_for_user" if answer_pause else "fill_paused_for_takeover", at=at,
    )
    return {"session_id": session["session_id"], "status": "paused", "state": target,
            "reasons": unique, "completed_checkpoints_preserved": True}


def start_session(
    connection: sqlite3.Connection,
    session_id: str,
    application_id: str,
    worker_id: str,
    form_url: str,
    observed_employer: str,
    observed_role: str,
    known_form: bool,
    authorization_id: str | None,
    authorization_context: dict[str, Any],
    at: datetime | None = None,
) -> dict[str, Any]:
    _require_id(session_id, "session_id")
    sanitized_form_url = _safe_url(form_url, "form_url")
    if not isinstance(known_form, bool) or not isinstance(authorization_context, dict):
        raise ValueError("known_form must be Boolean and authorization_context must be an object")
    if connection.execute("SELECT 1 FROM fill_sessions WHERE session_id=?", (session_id,)).fetchone():
        raise ValueError("fill session already exists")
    _require_lease(connection, application_id, worker_id, at)
    application, context = _application_context(connection, application_id, authorization_context)
    timestamp = (at or now_utc()).isoformat()
    reasons = []
    if pre_submit_core.normalize_text(observed_employer) != pre_submit_core.normalize_text(application["employer"]):
        reasons.append("employer_mismatch")
    if pre_submit_core.normalize_text(observed_role) != pre_submit_core.normalize_text(application["title"]):
        reasons.append("role_mismatch")
    connection.execute("""
        INSERT INTO fill_sessions (
            session_id, application_id, worker_id, mode, form_url, observed_employer,
            observed_role, known_form, authorization_id, authorization_context_json,
            status, pause_reasons_json, created_at, updated_at
        ) VALUES (?, ?, ?, 'fill_only', ?, ?, ?, ?, ?, ?, 'active', '[]', ?, ?)
    """, (session_id, application_id, worker_id, sanitized_form_url, observed_employer, observed_role,
          int(known_form), authorization_id, canonical_json(context), timestamp, timestamp))
    session = connection.execute("SELECT * FROM fill_sessions WHERE session_id=?", (session_id,)).fetchone()
    _event(connection, session, worker_id, "started", "fill_only_session_started",
           {"known_form": known_form, "mode": "fill_only"}, at)
    connection.commit()
    if reasons:
        return _pause(connection, session, reasons, worker_id, at=at)
    return {"session_id": session_id, "application_id": application_id, "status": "active",
            "mode": "fill_only", "final_submission_allowed": False}


def _active_session(connection: sqlite3.Connection, session_id: str, worker_id: str,
                    at: datetime | None = None) -> sqlite3.Row:
    session = connection.execute("SELECT * FROM fill_sessions WHERE session_id=?", (session_id,)).fetchone()
    if not session or session["status"] != "active":
        raise ValueError("active fill session not found")
    if session["worker_id"] != worker_id:
        raise ValueError("fill session is owned by another worker")
    _require_lease(connection, session["application_id"], worker_id, at)
    return session


def _candidate_facts(connection: sqlite3.Connection, application_id: str,
                     candidate_path: Path) -> dict[str, dict[str, Any]]:
    candidate, candidate_hash = resume_core.load_valid_candidate(candidate_path)
    require_table(connection, "candidate_snapshots")
    snapshot = connection.execute(
        "SELECT * FROM candidate_snapshots WHERE content_sha256=? AND status='active' "
        "AND registered_by='user'", (candidate_hash,)
    ).fetchone()
    if not snapshot:
        raise ValueError("candidate profile is not the active registered snapshot")
    snapshot_path = Path(snapshot["snapshot_path"])
    if not snapshot_path.is_file() or resume_core.file_sha256(snapshot_path) != snapshot["file_sha256"]:
        raise ValueError("registered candidate snapshot hash mismatch")
    material = connection.execute("""
        SELECT rv.candidate_profile_sha256
        FROM material_locks ml JOIN resume_versions rv ON rv.version_id=ml.resume_version_id
        WHERE ml.application_id=? AND ml.invalidated_at IS NULL
    """, (application_id,)).fetchone()
    if not material or material["candidate_profile_sha256"] != candidate_hash:
        raise ValueError("candidate profile does not match the active material lock")
    stored = {row["fact_id"]: row for row in connection.execute(
        "SELECT * FROM candidate_facts WHERE content_sha256=?", (candidate_hash,)
    )}
    for fact in candidate["facts"]:
        row = stored.get(fact["id"])
        if not row or row["fact_sha256"] != resume_core.canonical_hash(fact):
            raise ValueError("candidate fact registry does not match candidate.json")
    return {fact["id"]: fact for fact in candidate["facts"]}


def _require_chain_link(connection: sqlite3.Connection, session_id: str, page_index: int,
                        predecessor: str | None) -> None:
    """A page joins the chain only behind the checkpointed page before it.

    Without this, a session could hold one page at index 49 and satisfy every completeness
    check `finish_session` performs. Coverage of what was seen is not coverage of the form.
    """
    if page_index == 0:
        if predecessor is not None:
            raise ValueError("the first page cannot name a predecessor checkpoint")
        return
    if predecessor is None:
        raise ValueError("a later page must name the checkpoint of the page before it")
    previous = connection.execute(
        "SELECT p.status, c.checkpoint_sha256 FROM fill_pages p "
        "LEFT JOIN fill_checkpoints c ON c.session_id=p.session_id AND c.page_id=p.page_id "
        "WHERE p.session_id=? AND p.page_index=?", (session_id, page_index - 1),
    ).fetchone()
    if not previous:
        raise ValueError("page indexes must be consecutive from the first page")
    if previous["status"] != "completed" or not previous["checkpoint_sha256"]:
        raise ValueError("the previous page must be checkpointed before the next is observed")
    if previous["checkpoint_sha256"] != predecessor:
        raise ValueError("predecessor checkpoint does not match the previous page")
    final = connection.execute(
        "SELECT 1 FROM fill_pages WHERE session_id=? AND final_page=1", (session_id,)
    ).fetchone()
    if final:
        raise ValueError("no page follows the final page")


def _apply_domain_rule(connection, domain, field, field_id, ordinal, sensitivity, locale,
                       context, current_time, planned, eeo_handling, form_url):
    """Apply the rule for a classified domain. Returns a pause reason, a marker, or None.

    `None` means the field falls through to the ordinary source dispatch;
    `"continue_to_source"` is never returned for a domain that must not.
    """
    domain_kind, family = domain
    if domain_kind == "voluntary_eeo":
        # Protected-characteristic questions are the user's alone. The reason code hashes the
        # field regardless of what the page declared, so neither the question nor an answer is
        # legible in the pause record.
        resolved = field_policy.resolve_nondisclosure(
            connection, family, locale or "",
            field_policy.normalize_options(field.get("options")), context, current_time,
            form_url=form_url)  # trust comes from the issued surface record, not this URL
        if not resolved["applied"]:
            eeo_handling[field_id] = resolved["reason"]
            return _field_reason(resolved["reason"], field_id, "sensitive_personal")
        # Selecting a reviewed "prefer not to answer" option discloses nothing, which is the
        # whole reason it may be automated while a value may not. The submitted value is the
        # page's own opaque option value, not the reviewed label.
        planned.append({"ordinal": ordinal, "field": field, "operation": "fill",
                        "source_kind": "nondisclosure_policy",
                        "source_id": resolved["policy_id"],
                        "source_status": "active", "sensitivity": "sensitive_personal",
                        "value": resolved["submitted_value"],
                        "expected_sha256": canonical_hash(resolved["submitted_value"])})
        eeo_handling[field_id] = "policy_declined"
        return "continue_to_source"
    if domain_kind == "compensation":
        # Employer-defined brackets exist only in page text, and the direction criteria
        # `salary_floor` is a search filter in another subsystem. Neither may pick a band.
        return _field_reason("employer_defined_compensation_manual", field_id, sensitivity)
    if domain_kind == "employer_conflict":
        derivation = field_policy.conflict_derivation(connection, family, context, current_time)
        if not derivation["derivable"]:
            return _field_reason(derivation["reason"], field_id, sensitivity)
        return None
    if domain_kind == "sponsorship":
        # One broad control cannot stand in for four separate canonical meanings, and page
        # wording may not settle which one it is.
        return _field_reason("sponsorship_meaning_ambiguous", field_id, sensitivity)
    return None


def _discovery_answer_allowed(connection: sqlite3.Connection, answer_id: str) -> bool:
    """How the user heard about a role is their statement, never an inference.

    Nothing may derive it from the posting URL, the ATS host, or which collector surfaced the
    opening — those record how Jobloom found the job, which is a different fact about a
    different actor. Only a user-confirmed application-specific or conditional answer counts.
    """
    row = connection.execute(
        "SELECT answer_type, source_type FROM answers WHERE answer_id=?", (answer_id,)
    ).fetchone()
    return bool(row) and (row["answer_type"] in field_policy.DISCOVERY_ANSWER_TYPES
                          and row["source_type"] == "user_confirmed")


def _plan_upload(connection: sqlite3.Connection, session: sqlite3.Row,
                 upload_kind: str) -> tuple[str, str, dict[str, Any], str] | None:
    lock = application_core.require_active_material_lock(connection, session["application_id"])
    if upload_kind == "resume":
        version = connection.execute(
            "SELECT snapshot_path FROM resume_versions WHERE version_id=?", (lock["resume_version_id"],)
        ).fetchone()
        require_application_material_format(version["snapshot_path"], "resume")
        return "resume", lock["resume_version_id"], {
            "version_id": lock["resume_version_id"], "path": version["snapshot_path"],
            "file_sha256": lock["resume_file_sha256"],
        }, lock["resume_file_sha256"]
    if upload_kind == "cover_letter" and lock["cover_letter_version_id"]:
        version = connection.execute(
            "SELECT snapshot_path FROM cover_letter_versions WHERE version_id=?",
            (lock["cover_letter_version_id"],),
        ).fetchone()
        require_application_material_format(version["snapshot_path"], "cover letter")
        return "cover_letter", lock["cover_letter_version_id"], {
            "version_id": lock["cover_letter_version_id"], "path": version["snapshot_path"],
            "file_sha256": lock["cover_letter_file_sha256"],
        }, lock["cover_letter_file_sha256"]
    return None


def observe_page(
    connection: sqlite3.Connection,
    session_id: str,
    worker_id: str,
    candidate_path: Path,
    observation: dict[str, Any],
    at: datetime | None = None,
) -> dict[str, Any]:
    session = _active_session(connection, session_id, worker_id, at)
    required = {"page_id", "page_index", "page_url", "fields", "legal_items", "restricted_requests"}
    optional = {"locale", "final_page", "predecessor_checkpoint_sha256"}
    if (set(observation) - (required | optional)) or (required - set(observation)):
        raise ValueError("page observation has missing or unknown fields")
    final_page = observation.get("final_page", False)
    if not isinstance(final_page, bool):
        raise ValueError("final_page must be Boolean")
    predecessor = observation.get("predecessor_checkpoint_sha256")
    if predecessor is not None and (
        not isinstance(predecessor, str) or not worker_protocol.SHA256.fullmatch(predecessor)
    ):
        raise ValueError("predecessor_checkpoint_sha256 must be a sha256 digest")
    locale = observation.get("locale")
    if locale is not None:
        field_policy.require_locale(locale, "page locale")
    page_id = observation["page_id"]
    _require_id(page_id, "page_id")
    sanitized_page_url = _safe_url(observation["page_url"], "page_url")
    if (not isinstance(observation["page_index"], int) or observation["page_index"] < 0
            or observation["page_index"] > 49):
        raise ValueError("page_index must be an integer from 0 through 49")
    if not isinstance(observation["fields"], list) or not isinstance(observation["legal_items"], list):
        raise ValueError("fields and legal_items must be lists")
    if not isinstance(observation["restricted_requests"], list):
        raise ValueError("restricted_requests must be a list")
    if not observation["fields"] or len(observation["fields"]) > 250:
        raise ValueError("a page must contain between 1 and 250 observed fields")
    if (len(observation["legal_items"]) > 50 or len(observation["restricted_requests"]) > 50
            or not all(isinstance(item, str) and 0 < len(item) <= 128
                       for item in observation["legal_items"] + observation["restricted_requests"])):
        raise ValueError("legal and restricted identifiers must be bounded strings")
    if connection.execute(
        "SELECT 1 FROM fill_pages WHERE session_id=? AND page_id=?", (session_id, page_id)
    ).fetchone():
        raise ValueError("page was already observed")
    _require_chain_link(connection, session_id, observation["page_index"], predecessor)
    facts = _candidate_facts(connection, session["application_id"], candidate_path)
    current_time = at or now_utc()
    reasons: list[str] = []
    legal_items = set(observation["legal_items"])
    restrictions = set(observation["restricted_requests"])
    if not _same_origin(session["form_url"], sanitized_page_url):
        reasons.append("unexpected_navigation")
    unknown_legal = legal_items - pre_submit_core.ALLOWED_LEGAL_ITEMS
    if unknown_legal:
        reasons.append("unknown_legal_item")
    unknown_restrictions = restrictions - pre_submit_core.MANDATORY_PAUSES
    if unknown_restrictions:
        reasons.append("unknown_restricted_request")
    reasons.extend(sorted(restrictions & pre_submit_core.MANDATORY_PAUSES))
    context = json.loads(session["authorization_context_json"])
    planned: list[dict[str, Any]] = []
    eeo_handling: dict[str, str] = {}
    seen_fields: set[str] = set()
    submit_seen = False
    for ordinal, field in enumerate(observation["fields"]):
        if not isinstance(field, dict):
            raise ValueError("each observed field must be an object")
        allowed = {"field_id", "question", "selector", "control", "required", "sensitivity",
                   "source_kind", "source_id", "upload_kind", "options"}
        if set(field) - allowed:
            raise ValueError("observed field has unknown properties")
        for name in ("field_id", "question", "selector", "control"):
            if not isinstance(field.get(name), str) or not field[name].strip():
                raise ValueError(f"observed field {name} is required")
        if len(field["question"]) > 2000 or len(field["selector"]) > 2000:
            raise ValueError("observed field question and selector must be at most 2000 characters")
        field_id = field["field_id"]
        _require_id(field_id, "field_id")
        if field_id in seen_fields:
            raise ValueError("field IDs must be unique within a page")
        seen_fields.add(field_id)
        if not isinstance(field.get("required"), bool):
            raise ValueError("observed field required must be Boolean")
        options = field_policy.normalize_options(field.get("options"))
        if options is not None and len(options) > 100:
            raise ValueError("observed field options must be at most 100 entries")
        control = field["control"]
        if control not in ALLOWED_CONTROLS:
            reasons.append(_field_reason("unsupported_form", field_id, field.get("sensitivity", "normal")))
            continue
        if control == "submit":
            submit_seen = True
            continue
        if control == "standard_attestation":
            legal_items.add("standard_attestation")
            continue
        sensitivity = field.get("sensitivity", "normal")
        if sensitivity not in archive_core.SENSITIVITY_CLASSES:
            reasons.append(_field_reason("invalid_sensitivity", field_id))
            continue
        if archive_core.SENSITIVE_FIELD_PATTERN.search(f"{field_id} {field['question']}") and sensitivity == "normal":
            reasons.append(_field_reason("sensitive_field_misclassified", field_id, "sensitive_personal"))
            continue
        # Classification runs before every source branch, including uploads. A control
        # declared `file` with `upload_kind: "resume"` and a question asking for an identity
        # document would otherwise have been planned as a resume upload without ever being
        # classified — the upload branch used to `continue` before this ran.
        domain = field_policy.classify(field_id, field["question"])
        if domain:
            outcome = _apply_domain_rule(connection, domain, field, field_id, ordinal,
                                         sensitivity, locale, context, current_time,
                                         planned, eeo_handling, session["form_url"])
            if outcome:
                if outcome != "continue_to_source":
                    reasons.append(outcome)
                continue
        if control == "file":
            upload_kind = field.get("upload_kind")
            upload = _plan_upload(connection, session, upload_kind)
            if not upload:
                reasons.append(_field_reason("unapproved_upload", field_id, sensitivity))
                continue
            source_kind, source_id, value, expected = upload
            planned.append({"ordinal": ordinal, "field": field, "operation": "upload",
                            "source_kind": source_kind, "source_id": source_id,
                            "source_status": "locked", "sensitivity": sensitivity,
                            "value": value, "expected_sha256": expected})
            continue
        source_kind = field.get("source_kind")
        if source_kind == "fact":
            fact = facts.get(field.get("source_id"))
            if not fact:
                reasons.append(_field_reason("candidate_fact_unknown", field_id, sensitivity))
                continue
            if fact.get("status") != "locked" or not fact.get("locked"):
                reasons.append(_field_reason("candidate_fact_not_locked", field_id, sensitivity))
                continue
            expires = parse_time(fact.get("expires_at"))
            if expires and current_time >= expires:
                reasons.append(_field_reason("candidate_fact_expired", field_id, sensitivity))
                continue
            value = fact.get("value")
            if isinstance(value, (dict, type(None))):
                reasons.append(_field_reason("unsupported_fact_value", field_id, sensitivity))
                continue
            planned.append({"ordinal": ordinal, "field": field, "operation": "fill",
                            "source_kind": "fact", "source_id": fact["id"],
                            "source_status": "locked", "sensitivity": sensitivity,
                            "value": value, "expected_sha256": canonical_hash(value)})
        elif source_kind in {None, "answer"}:
            match = answer_library.match_answer(
                connection, field["question"], context, session["authorization_id"], current_time
            )
            if not match.get("auto_fill_ready"):
                reasons.append(_field_reason(match["reason"], field_id, sensitivity))
                continue
            if domain and domain[0] == "discovery_source" and not _discovery_answer_allowed(
                connection, match["answer_id"]
            ):
                reasons.append(_field_reason(
                    "discovery_source_not_user_confirmed", field_id, sensitivity))
                continue
            planned.append({"ordinal": ordinal, "field": field, "operation": "fill",
                            "source_kind": "answer", "source_id": match["answer_id"],
                            "source_status": "active", "sensitivity": sensitivity,
                            "value": match["answer"], "expected_sha256": canonical_hash(match["answer"])})
        else:
            reasons.append(_field_reason("unsupported_source", field_id, sensitivity))
    timestamp = current_time.isoformat()
    sanitized_observation = dict(observation)
    sanitized_observation["page_url"] = sanitized_page_url
    sanitized_observation["fields"] = [
        {**{key: value for key, value in item.items() if key != "options"},
         **({"options_count": len(item["options"])} if isinstance(item.get("options"), list) else {})}
        for item in observation["fields"]
    ]
    stored_observation = canonical_json(sanitized_observation)
    connection.execute("""
        INSERT INTO fill_pages (
            session_id, page_id, page_index, page_url, final_page, submit_control_seen,
            predecessor_checkpoint_sha256, observation_json, observation_sha256,
            legal_items_json, restricted_requests_json, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
    """, (session_id, page_id, observation["page_index"], sanitized_page_url,
          # Two independent observations. Folding them together would erase the one case
          # worth catching: an observer that declared a page final without seeing the submit
          # control, which is a contradiction, not a page that is merely not final.
          int(final_page), int(submit_seen), predecessor, stored_observation,
          canonical_hash(sanitized_observation), canonical_json(sorted(legal_items)),
          canonical_json(sorted(restrictions)), timestamp))
    connection.execute(
        "UPDATE fill_sessions SET current_page_id=?, submit_control_seen=MAX(submit_control_seen, ?), "
        "updated_at=? WHERE session_id=?",
        (page_id, int(submit_seen), timestamp, session_id),
    )
    if reasons:
        connection.commit()
        session = connection.execute("SELECT * FROM fill_sessions WHERE session_id=?", (session_id,)).fetchone()
        return _pause(connection, session, reasons, worker_id, page_id, at)
    for item in planned:
        field = item["field"]
        step_id = f"{session_id}:{page_id}:{field['field_id']}"
        if len(step_id) > 128:
            step_id = f"step-{uuid.uuid5(uuid.NAMESPACE_URL, step_id).hex}"
        connection.execute("""
            INSERT INTO fill_steps (
                step_id, session_id, page_id, ordinal, field_id, question, selector, control,
                required, operation, source_kind, source_id, source_status, sensitivity,
                value_json, expected_sha256, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (step_id, session_id, page_id, item["ordinal"], field["field_id"], field["question"],
              field["selector"], field["control"], int(field["required"]), item["operation"],
              item["source_kind"], item["source_id"], item["source_status"], item["sensitivity"],
              canonical_json(item["value"]), item["expected_sha256"]))
    session = connection.execute("SELECT * FROM fill_sessions WHERE session_id=?", (session_id,)).fetchone()
    _event(connection, session, worker_id, "page_planned", "verified_sources_resolved",
           {"page_id": page_id, "step_count": len(planned), "submit_control_seen": submit_seen}, at)
    connection.commit()
    return {"session_id": session_id, "page_id": page_id, "status": "active",
            "step_count": len(planned), "submit_control_seen": submit_seen,
            "final_submission_allowed": False}


def export_page(connection: sqlite3.Connection, session_id: str, worker_id: str,
                page_id: str, output: Path, at: datetime | None = None) -> dict[str, Any]:
    session = _active_session(connection, session_id, worker_id, at)
    page = connection.execute(
        "SELECT * FROM fill_pages WHERE session_id=? AND page_id=? AND status='active'",
        (session_id, page_id),
    ).fetchone()
    if not page:
        raise ValueError("active planned page not found")
    steps = connection.execute(
        "SELECT * FROM fill_steps WHERE session_id=? AND page_id=? AND status='pending' ORDER BY ordinal",
        (session_id, page_id),
    ).fetchall()
    actions = [{
        "step_id": row["step_id"], "field_id": row["field_id"], "selector": row["selector"],
        "control": row["control"], "operation": row["operation"],
        "value": json.loads(row["value_json"]), "expected_sha256": row["expected_sha256"],
    } for row in steps]
    # The worker never reads this database. Everything it is allowed to check about the
    # surface travels with the package, and the nonce deliberately does not: a package is a
    # file on disk and the worker has no use for the secret.
    # `None` when no surface was issued for this page. Export stays possible — a future
    # production adapter will have a different attestation — but the worker refuses to act on
    # a package that carries none, so the gate sits at execution rather than at planning.
    attestation = field_policy.surface_attestation(
        connection, page["page_url"], at or now_utc())
    package = {
        "schema_version": "0.1.0", "mode": "fill_only", "session_id": session_id,
        "application_id": session["application_id"], "page_id": page_id, "page_url": page["page_url"],
        "surface": attestation,
        "actions": actions, "stop_before_submit": True, "submission_action": None,
    }
    if output.exists():
        raise ValueError("action package output already exists")
    parent_existed = output.parent.exists()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not parent_existed:
        os.chmod(output.parent, 0o700)
    output.write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)
    digest = resume_core.file_sha256(output)
    _event(connection, session, worker_id, "page_exported", "private_action_package_written",
           {"page_id": page_id, "action_count": len(actions), "package_sha256": digest}, at)
    connection.commit()
    return {"session_id": session_id, "page_id": page_id, "output": str(output),
            "package_sha256": digest, "action_count": len(actions), "contains_submission_action": False}


def complete_step(connection: sqlite3.Connection, session_id: str, worker_id: str,
                  step_id: str, observed_sha256: str, at: datetime | None = None) -> dict[str, Any]:
    session = _active_session(connection, session_id, worker_id, at)
    step = connection.execute(
        "SELECT * FROM fill_steps WHERE step_id=? AND session_id=?", (step_id, session_id)
    ).fetchone()
    if not step or step["status"] != "pending":
        raise ValueError("pending fill step not found")
    if observed_sha256 != step["expected_sha256"]:
        return _pause(connection, session, [_field_reason(
            "incorrect_autofill", step["field_id"], step["sensitivity"]
        )],
                      worker_id, step["page_id"], at)
    if step["operation"] == "fill" and step["source_kind"] == "nondisclosure_policy":
        # An ApplicationField stores what was entered. For a protected-characteristic control
        # Jobloom must not be able to say, so only the handling marker is kept.
        field_policy.record_handling(
            connection, session["application_id"], step["field_id"], "policy_declined",
            step["source_id"], at or now_utc())
    elif step["operation"] == "fill":
        archive_core.record_field(
            connection, session["application_id"], step["field_id"], step["question"],
            json.loads(step["value_json"]), step["source_kind"], step["source_id"],
            step["source_status"], step["sensitivity"], at,
        )
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE fill_steps SET status='completed', completed_at=? WHERE step_id=?",
        (timestamp, step_id),
    )
    step_metadata = {"page_id": step["page_id"], "operation": step["operation"]}
    if step["sensitivity"] == "normal":
        step_metadata["step_id"] = step_id
    else:
        step_metadata["step_id_sha256"] = canonical_hash(step_id)
    _event(connection, session, worker_id, "step_completed", "observed_value_hash_matched",
           step_metadata, at)
    connection.commit()
    return {"session_id": session_id, "step_id": step_id, "status": "completed"}


def checkpoint_page(connection: sqlite3.Connection, session_id: str, worker_id: str,
                    page_id: str, checkpoint_id: str, at: datetime | None = None) -> dict[str, Any]:
    _require_id(checkpoint_id, "checkpoint_id")
    session = _active_session(connection, session_id, worker_id, at)
    page = connection.execute(
        "SELECT * FROM fill_pages WHERE session_id=? AND page_id=? AND status='active'",
        (session_id, page_id),
    ).fetchone()
    if not page:
        raise ValueError("active page not found")
    pending = connection.execute(
        "SELECT COUNT(*) FROM fill_steps WHERE session_id=? AND page_id=? AND status!='completed'",
        (session_id, page_id),
    ).fetchone()[0]
    if pending:
        raise ValueError("cannot checkpoint a page with incomplete steps")
    steps = connection.execute(
        "SELECT step_id, expected_sha256 FROM fill_steps WHERE session_id=? AND page_id=? ORDER BY ordinal",
        (session_id, page_id),
    ).fetchall()
    digest = canonical_hash([dict(row) for row in steps])
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "INSERT INTO fill_checkpoints VALUES (?, ?, ?, ?, ?)",
        (checkpoint_id, session_id, page_id, digest, timestamp),
    )
    connection.execute(
        "UPDATE fill_pages SET status='completed', completed_at=? WHERE session_id=? AND page_id=?",
        (timestamp, session_id, page_id),
    )
    _event(connection, session, worker_id, "page_checkpointed", "all_planned_steps_verified",
           {"page_id": page_id, "checkpoint_sha256": digest, "step_count": len(steps)}, at)
    connection.commit()
    return {"session_id": session_id, "page_id": page_id, "checkpoint_id": checkpoint_id,
            "status": "completed", "checkpoint_sha256": digest}


def resume_session(connection: sqlite3.Connection, session_id: str, worker_id: str,
                   authorization_id: str | None, authorization_context: dict[str, Any],
                   candidate_path: Path, at: datetime | None = None,
                   observation: dict[str, Any] | None = None) -> dict[str, Any]:
    session = connection.execute("SELECT * FROM fill_sessions WHERE session_id=?", (session_id,)).fetchone()
    if not session or session["status"] != "paused":
        raise ValueError("paused fill session not found")
    _require_lease(connection, session["application_id"], worker_id, at)
    _, context = _application_context(connection, session["application_id"], authorization_context)
    page = connection.execute(
        "SELECT * FROM fill_pages WHERE session_id=? AND status='paused' ORDER BY page_index DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if not page:
        raise ValueError("session-level identity pause requires a new fill session")
    stored = json.loads(page["observation_json"])
    needs_live_page = any(
        "options_count" in field for field in stored["fields"] if isinstance(field, dict))
    if observation is None:
        if needs_live_page:
            # Option strings are page text and were never stored, so the record cannot say
            # what this control offers. Replanning from it would silently skip a policy the
            # user has just registered, and would answer a question about the current page
            # using yesterday's page. The worker must look again.
            raise ValueError("resuming this page requires a new live observation")
        observation = stored
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE fill_sessions SET worker_id=?, authorization_id=?, authorization_context_json=?, "
        "status='active', pause_reasons_json='[]', updated_at=? WHERE session_id=?",
        (worker_id, authorization_id, canonical_json(context), timestamp, session_id),
    )
    session = connection.execute("SELECT * FROM fill_sessions WHERE session_id=?", (session_id,)).fetchone()
    _event(connection, session, worker_id, "resumed", "user_resolved_pause", {}, at)
    connection.commit()
    filled_ids = [row[0] for row in connection.execute(
        "SELECT field_id FROM fill_steps WHERE session_id=? AND page_id=? AND operation='fill'",
        (session_id, page["page_id"]),
    )]
    if filled_ids:
        placeholders = ",".join("?" for _ in filled_ids)
        connection.execute(
            f"DELETE FROM application_fields WHERE application_id=? AND field_id IN ({placeholders})",
            [session["application_id"], *filled_ids],
        )
    connection.execute(
        "DELETE FROM fill_steps WHERE session_id=? AND page_id=?", (session_id, page["page_id"])
    )
    connection.execute(
        "DELETE FROM fill_pages WHERE session_id=? AND page_id=?", (session_id, page["page_id"])
    )
    _event(connection, session, worker_id, "page_replanned", "paused_page_sources_revalidated",
           {"page_id": page["page_id"], "checkpointed_pages_preserved": True}, at)
    connection.commit()
    return observe_page(connection, session_id, worker_id, candidate_path, observation, at)


def finish_session(connection: sqlite3.Connection, session_id: str, worker_id: str,
                   inventory_id: str, at: datetime | None = None) -> dict[str, Any]:
    _require_id(inventory_id, "inventory_id")
    session = _active_session(connection, session_id, worker_id, at)
    pages = connection.execute(
        "SELECT * FROM fill_pages WHERE session_id=? ORDER BY page_index", (session_id,)
    ).fetchall()
    if not pages or any(page["status"] != "completed" for page in pages):
        raise ValueError("all observed pages require completed checkpoints")
    fields = connection.execute(
        "SELECT * FROM fill_steps WHERE session_id=? ORDER BY ordinal", (session_id,)
    ).fetchall()
    if not fields or any(row["status"] != "completed" for row in fields):
        raise ValueError("all fill steps must be completed")
    required_field_ids = sorted({row["field_id"] for row in fields
                                 if row["operation"] == "fill" and row["required"]})
    if not required_field_ids:
        raise ValueError("form inventory requires at least one required filled field")
    uploads = sorted({(row["source_kind"], row["source_id"]) for row in fields
                      if row["operation"] == "upload"})
    # Every page has been observed and checkpointed above, so this is the one moment at which
    # "this form has no voluntary-disclosure control" is a claim the record can support.
    checkpoints = {row["page_id"]: row["checkpoint_sha256"] for row in connection.execute(
        "SELECT page_id, checkpoint_sha256 FROM fill_checkpoints WHERE session_id=?", (session_id,))}
    observed_eeo = sorted({
        field["field_id"]
        for page in pages
        for field in json.loads(page["observation_json"])["fields"]
        if isinstance(field, dict) and field_policy.disposition(
            field["field_id"], field["question"], field["control"],
            field.get("source_kind"))[1] == "voluntary_eeo"
    })
    chain = [{
        "page_index": page["page_index"], "status": page["status"],
        "final_page": bool(page["final_page"]),
        "predecessor_checkpoint_sha256": page["predecessor_checkpoint_sha256"],
        "checkpoint_sha256": checkpoints.get(page["page_id"]),
        "submit_control_seen": bool(page["submit_control_seen"]),
    } for page in pages]
    issue = worker_protocol.chain_issue(chain)
    if issue:
        raise ValueError(f"form coverage is incomplete: {issue}")
    if not session["submit_control_seen"]:
        raise ValueError("final submit control has not been observed")
    field_policy.finalize_handling(
        connection, session["application_id"], observed_eeo,
        f"{worker_protocol.COVERAGE_BASIS}:"
        f"{canonical_hash([page['observation_sha256'] for page in pages])}", at or now_utc())
    legal_items = sorted({item for page in pages for item in json.loads(page["legal_items_json"])})
    restrictions = sorted({item for page in pages for item in json.loads(page["restricted_requests_json"])})
    pre_submit_core.register_inventory(
        connection, inventory_id, session["application_id"], session["form_url"],
        session["observed_employer"], session["observed_role"], bool(session["known_form"]),
        required_field_ids, legal_items, restrictions,
        [{"kind": kind, "version_id": version_id} for kind, version_id in uploads], at,
    )
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE fill_sessions SET status='completed', completed_at=?, updated_at=? WHERE session_id=?",
        (timestamp, timestamp, session_id),
    )
    _event(connection, session, worker_id, "completed", "stopped_before_submission",
           {"page_count": len(pages), "field_count": len(fields), "inventory_id": inventory_id,
            "submission_action_performed": False}, at)
    application_core.release_lease(
        connection, session["application_id"], worker_id, "waiting_for_submission_approval",
        "fill_only_completed", at=at,
    )
    return {"session_id": session_id, "status": "completed", "inventory_id": inventory_id,
            "application_state": "waiting_for_submission_approval", "submission_performed": False}


def status(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute("SELECT status, COUNT(*) AS count FROM fill_sessions GROUP BY status").fetchall()
    return {"sessions": sum(row["count"] for row in rows),
            "by_status": {row["status"]: row["count"] for row in rows},
            "checkpoints": connection.execute("SELECT COUNT(*) FROM fill_checkpoints").fetchone()[0]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    start = commands.add_parser("start")
    start.add_argument("--input", required=True, type=Path)
    observe = commands.add_parser("observe-page")
    observe.add_argument("--session-id", required=True)
    observe.add_argument("--worker-id", required=True)
    observe.add_argument("--candidate", required=True, type=Path)
    observe.add_argument("--input", required=True, type=Path)
    export = commands.add_parser("export-page")
    export.add_argument("--session-id", required=True)
    export.add_argument("--worker-id", required=True)
    export.add_argument("--page-id", required=True)
    export.add_argument("--output", required=True, type=Path)
    complete = commands.add_parser("complete-step")
    complete.add_argument("--session-id", required=True)
    complete.add_argument("--worker-id", required=True)
    complete.add_argument("--step-id", required=True)
    complete.add_argument("--observed-sha256", required=True)
    checkpoint = commands.add_parser("checkpoint")
    checkpoint.add_argument("--session-id", required=True)
    checkpoint.add_argument("--worker-id", required=True)
    checkpoint.add_argument("--page-id", required=True)
    checkpoint.add_argument("--checkpoint-id", required=True)
    resume = commands.add_parser("resume")
    resume.add_argument("--session-id", required=True)
    resume.add_argument("--worker-id", required=True)
    resume.add_argument("--candidate", required=True, type=Path)
    resume.add_argument("--authorization-id")
    resume.add_argument("--context", required=True, type=Path)
    finish = commands.add_parser("finish")
    finish.add_argument("--session-id", required=True)
    finish.add_argument("--worker-id", required=True)
    finish.add_argument("--inventory-id", required=True)
    commands.add_parser("status")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    if args.command == "init":
        result = {"status": "initialized", "db": str(args.db)}
    elif args.command == "start":
        value = json.loads(args.input.read_text(encoding="utf-8"))
        result = start_session(connection, **value)
    elif args.command == "observe-page":
        value = json.loads(args.input.read_text(encoding="utf-8"))
        result = observe_page(connection, args.session_id, args.worker_id, args.candidate, value)
    elif args.command == "export-page":
        result = export_page(connection, args.session_id, args.worker_id, args.page_id, args.output)
    elif args.command == "complete-step":
        result = complete_step(
            connection, args.session_id, args.worker_id, args.step_id, args.observed_sha256
        )
    elif args.command == "checkpoint":
        result = checkpoint_page(
            connection, args.session_id, args.worker_id, args.page_id, args.checkpoint_id
        )
    elif args.command == "resume":
        context = json.loads(args.context.read_text(encoding="utf-8"))
        result = resume_session(
            connection, args.session_id, args.worker_id, args.authorization_id, context, args.candidate
        )
    elif args.command == "finish":
        result = finish_session(
            connection, args.session_id, args.worker_id, args.inventory_id
        )
    else:
        result = status(connection)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
