#!/usr/bin/env python3
"""Local deterministic Jobloom answer library and freshness gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from _common import answer_issue, context_matches, parse_time  # noqa: E402


IMMIGRATION_CANONICAL_IDS = {
    "work_authorized_now",
    "sponsorship_now",
    "sponsorship_future",
    "employer_action_required",
}
ANSWER_TYPES = {
    "stable_fact", "time_sensitive_fact", "conditional_preference", "company_specific",
    "role_specific", "application_specific", "voluntary_disclosure", "legal_commitment",
    "open_text_template", "derived_answer",
}
VALIDITY_CLASSES = {"stable", "periodic", "event_driven", "per_application"}
SOURCE_TYPES = {
    "user_confirmed", "verified_candidate_fact", "approved_resume", "user_rule", "deterministic_derivation",
}
SCOPE_FIELDS = {"country", "jurisdiction", "company", "role_family", "employment_type", "application_id", "queue_id"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_question(question: str) -> str:
    value = unicodedata.normalize("NFKC", question).casefold().strip()
    value = re.sub(r"\s+", " ", value)
    return value.rstrip(" ?.!")


def connect(path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    initialize(connection)
    if str(path) != ":memory:":
        os.chmod(path, 0o600)
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS answers (
            answer_id TEXT PRIMARY KEY,
            canonical_id TEXT NOT NULL,
            canonical_meaning TEXT NOT NULL,
            answer_json TEXT NOT NULL,
            answer_type TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_ref TEXT,
            confirmation_status TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            effective_from TEXT,
            expires_at TEXT,
            review_after TEXT,
            validity_class TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            preconditions_json TEXT NOT NULL,
            exclusions_json TEXT NOT NULL,
            auto_fill_allowed INTEGER NOT NULL,
            auto_submit_allowed INTEGER NOT NULL,
            sensitivity TEXT NOT NULL,
            invalidation_triggers_json TEXT NOT NULL,
            dependent_fact_ids_json TEXT NOT NULL,
            supersedes_id TEXT,
            status TEXT NOT NULL,
            ambiguity_notes TEXT,
            FOREIGN KEY (supersedes_id) REFERENCES answers(answer_id)
        );
        CREATE INDEX IF NOT EXISTS answers_canonical_idx ON answers(canonical_id, status);

        CREATE TABLE IF NOT EXISTS question_forms (
            normalized_question TEXT NOT NULL,
            canonical_id TEXT NOT NULL,
            match_level TEXT NOT NULL,
            verified_by_user INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (normalized_question, canonical_id)
        );

        CREATE TABLE IF NOT EXISTS authorizations (
            authorization_id TEXT PRIMARY KEY,
            confirmed_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            revoked_at TEXT,
            status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            entity_id TEXT,
            metadata_json TEXT NOT NULL
        );
    """)
    connection.commit()


def audit(connection: sqlite3.Connection, event_type: str, entity_id: str | None, metadata: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO audit_events (created_at, event_type, entity_id, metadata_json) VALUES (?, ?, ?, ?)",
        (now_utc().isoformat(), event_type, entity_id, json.dumps(metadata, sort_keys=True)),
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def add_answer(connection: sqlite3.Connection, entry: dict[str, Any]) -> None:
    required = {
        "answer_id", "canonical_id", "canonical_meaning", "answer", "answer_type", "source_type",
        "confirmation_status", "confirmed_at", "validity_class", "auto_fill_allowed", "auto_submit_allowed",
    }
    missing = sorted(required - set(entry))
    if missing:
        raise ValueError(f"answer entry missing required fields: {', '.join(missing)}")
    if entry["answer_type"] not in ANSWER_TYPES:
        raise ValueError(f"invalid answer_type: {entry['answer_type']}")
    if entry["source_type"] not in SOURCE_TYPES:
        raise ValueError("model inference is not a valid answer source")
    if entry["validity_class"] not in VALIDITY_CLASSES:
        raise ValueError(f"invalid validity_class: {entry['validity_class']}")
    if entry["confirmation_status"] != "confirmed":
        raise ValueError("only user-confirmed answers may enter the active library")
    if not isinstance(entry["auto_fill_allowed"], bool) or not isinstance(entry["auto_submit_allowed"], bool):
        raise ValueError("auto-fill and auto-submit permissions must be explicit booleans")
    if entry["auto_submit_allowed"] and not entry["auto_fill_allowed"]:
        raise ValueError("automatic submission cannot be enabled when automatic filling is disabled")
    scope = entry.get("scope", {})
    unknown_scope = sorted(set(scope) - SCOPE_FIELDS)
    if unknown_scope:
        raise ValueError(f"unsupported scope fields: {', '.join(unknown_scope)}")
    if entry["validity_class"] == "per_application":
        application_id = scope.get("application_id")
        if not isinstance(application_id, str) or not application_id.strip():
            raise ValueError("per_application answers require scope.application_id")
    if entry["answer_type"] in {"legal_commitment", "voluntary_disclosure"} and entry["auto_submit_allowed"]:
        raise ValueError(f"{entry['answer_type']} cannot enable automatic submission in the MVP")
    confirmed = parse_time(entry["confirmed_at"])
    effective = parse_time(entry.get("effective_from"))
    expires = parse_time(entry.get("expires_at"))
    review_after = parse_time(entry.get("review_after"))
    if not confirmed:
        raise ValueError("confirmed_at is required")
    if expires and expires <= (effective or confirmed):
        raise ValueError("answer expiration must be after its effective or confirmation time")
    if review_after and review_after <= confirmed:
        raise ValueError("answer review_after must be after confirmation")
    if entry["validity_class"] == "periodic" and not (review_after or expires):
        raise ValueError("periodic answers require review_after or expires_at")

    connection.execute("""
        INSERT INTO answers (
            answer_id, canonical_id, canonical_meaning, answer_json, answer_type, source_type, source_ref,
            confirmation_status, confirmed_at, effective_from, expires_at, review_after, validity_class,
            scope_json, preconditions_json, exclusions_json, auto_fill_allowed, auto_submit_allowed,
            sensitivity, invalidation_triggers_json, dependent_fact_ids_json, supersedes_id, status, ambiguity_notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entry["answer_id"], entry["canonical_id"], entry["canonical_meaning"], _json(entry["answer"]),
        entry["answer_type"], entry["source_type"], entry.get("source_ref"), entry["confirmation_status"],
        entry["confirmed_at"], entry.get("effective_from"), entry.get("expires_at"), entry.get("review_after"),
        entry["validity_class"], _json(scope), _json(entry.get("preconditions", {})),
        _json(entry.get("exclusions", {})), int(entry["auto_fill_allowed"]), int(entry["auto_submit_allowed"]),
        entry.get("sensitivity", "normal"), _json(entry.get("invalidation_triggers", [])),
        _json(entry.get("dependent_fact_ids", [])), entry.get("supersedes_id"), entry.get("status", "active"),
        entry.get("ambiguity_notes"),
    ))
    if entry.get("supersedes_id"):
        connection.execute("UPDATE answers SET status='superseded' WHERE answer_id=?", (entry["supersedes_id"],))
    audit(connection, "answer_added", entry["answer_id"], {"canonical_id": entry["canonical_id"]})
    connection.commit()


def add_question_form(
    connection: sqlite3.Connection,
    canonical_id: str,
    question: str,
    match_level: str = "exact",
    verified_by_user: bool = True,
) -> None:
    if match_level not in {"exact", "semantic_equivalent"}:
        raise ValueError("match_level must be exact or semantic_equivalent")
    if match_level == "semantic_equivalent" and not verified_by_user:
        raise ValueError("semantic equivalents must be user-verified before reuse")
    normalized = normalize_question(question)
    connection.execute(
        "INSERT INTO question_forms VALUES (?, ?, ?, ?, ?)",
        (normalized, canonical_id, match_level, int(verified_by_user), now_utc().isoformat()),
    )
    question_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    audit(connection, "question_form_added", canonical_id, {"match_level": match_level, "question_hash": question_hash})
    connection.commit()


def authorization_current(
    connection: sqlite3.Connection,
    authorization_id: str | None,
    context: dict[str, Any],
    at: datetime,
) -> tuple[bool, str | None]:
    if not authorization_id:
        return False, "standing_authorization_missing"
    row = connection.execute("SELECT * FROM authorizations WHERE authorization_id=?", (authorization_id,)).fetchone()
    if not row:
        return False, "standing_authorization_unknown"
    if row["status"] != "active" or row["revoked_at"]:
        return False, "standing_authorization_revoked"
    if at >= parse_time(row["expires_at"]):
        return False, "standing_authorization_expired"
    if not context_matches(json.loads(row["scope_json"]), context):
        return False, "standing_authorization_scope_mismatch"
    return True, None


def match_answer(
    connection: sqlite3.Connection,
    question: str,
    context: dict[str, Any],
    authorization_id: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    at = at or now_utc()
    forms = connection.execute(
        "SELECT * FROM question_forms WHERE normalized_question=?",
        (normalize_question(question),),
    ).fetchall()
    canonical_ids = {row["canonical_id"] for row in forms if row["verified_by_user"]}
    if not forms:
        return {"decision": "ask", "reason": "new_question", "auto_fill_ready": False}
    if len(canonical_ids) != 1:
        return {"decision": "conflict", "reason": "question_mapping_conflict", "auto_fill_ready": False}
    canonical_id = next(iter(canonical_ids))
    rows = connection.execute(
        "SELECT * FROM answers WHERE canonical_id=? AND confirmation_status='confirmed'",
        (canonical_id,),
    ).fetchall()
    stale_reasons: list[str] = []
    candidates: list[tuple[int, sqlite3.Row]] = []
    for row in rows:
        scope = json.loads(row["scope_json"])
        validity = answer_issue(row, context, at)
        if validity:
            stale_reasons.append(validity)
            continue
        candidates.append((len(scope), row))
    if not candidates:
        reason = sorted(set(stale_reasons))[0] if stale_reasons else "no_applicable_answer"
        return {"decision": "ask", "reason": reason, "canonical_id": canonical_id, "auto_fill_ready": False}
    best_specificity = max(score for score, _ in candidates)
    best = [row for score, row in candidates if score == best_specificity]
    values = {row["answer_json"] for row in best}
    if len(values) > 1:
        return {"decision": "conflict", "reason": "conflicting_active_answers", "canonical_id": canonical_id, "auto_fill_ready": False}
    selected = max(best, key=lambda row: parse_time(row["confirmed_at"]) or datetime.min.replace(tzinfo=timezone.utc))
    if selected["answer_type"] == "legal_commitment":
        return {"decision": "ask", "reason": "legal_commitment_requires_review", "canonical_id": canonical_id, "answer_id": selected["answer_id"], "auto_fill_ready": False}
    if not selected["auto_fill_allowed"]:
        return {"decision": "ask", "reason": "automatic_fill_not_allowed", "canonical_id": canonical_id, "answer_id": selected["answer_id"], "auto_fill_ready": False}
    authorized, authorization_reason = authorization_current(connection, authorization_id, context, at)
    result = {
        "decision": "use",
        "reason": "verified_answer_match",
        "canonical_id": canonical_id,
        "answer_id": selected["answer_id"],
        "answer": json.loads(selected["answer_json"]),
        "match_level": forms[0]["match_level"],
        "channel_b_fresh": True,
        "channel_a_current": authorized,
        "auto_fill_ready": authorized,
        "auto_submit_ready": bool(authorized and selected["auto_submit_allowed"]),
        "per_application_recheck_required": canonical_id in IMMIGRATION_CANONICAL_IDS,
    }
    if not authorized:
        result["authorization_reason"] = authorization_reason
    audit(connection, "answer_matched", selected["answer_id"], {
        "canonical_id": canonical_id, "decision": result["decision"], "auto_fill_ready": result["auto_fill_ready"],
    })
    connection.commit()
    return result


def add_authorization(connection: sqlite3.Connection, entry: dict[str, Any]) -> None:
    required = {"authorization_id", "confirmed_at", "expires_at", "scope"}
    missing = sorted(required - set(entry))
    if missing:
        raise ValueError(f"authorization missing required fields: {', '.join(missing)}")
    confirmed = parse_time(entry["confirmed_at"])
    expires = parse_time(entry["expires_at"])
    if not confirmed or not expires or expires <= confirmed:
        raise ValueError("authorization expiration must be after confirmation")
    if expires - confirmed > timedelta(days=14):
        raise ValueError("standing authorization may not exceed fourteen days")
    unknown_scope = sorted(set(entry["scope"]) - SCOPE_FIELDS)
    if unknown_scope:
        raise ValueError(f"unsupported authorization scope fields: {', '.join(unknown_scope)}")
    if not entry["scope"]:
        raise ValueError("standing authorization requires a non-empty scope")
    connection.execute(
        "INSERT INTO authorizations VALUES (?, ?, ?, ?, ?, ?)",
        (entry["authorization_id"], entry["confirmed_at"], entry["expires_at"], _json(entry["scope"]), None, "active"),
    )
    audit(connection, "authorization_added", entry["authorization_id"], {"expires_at": entry["expires_at"]})
    connection.commit()


def revoke_authorization(connection: sqlite3.Connection, authorization_id: str, at: datetime | None = None) -> None:
    timestamp = (at or now_utc()).isoformat()
    cursor = connection.execute(
        "UPDATE authorizations SET revoked_at=?, status='revoked' WHERE authorization_id=? AND status='active'",
        (timestamp, authorization_id),
    )
    if cursor.rowcount != 1:
        raise ValueError("active authorization not found")
    audit(connection, "authorization_revoked", authorization_id, {})
    connection.commit()


def invalidate_by_trigger(connection: sqlite3.Connection, trigger: str) -> list[str]:
    rows = connection.execute("SELECT answer_id, invalidation_triggers_json FROM answers WHERE status='active'").fetchall()
    affected = [row["answer_id"] for row in rows if trigger in json.loads(row["invalidation_triggers_json"])]
    for answer_id in affected:
        connection.execute("UPDATE answers SET status='stale' WHERE answer_id=?", (answer_id,))
        audit(connection, "answer_invalidated", answer_id, {"trigger": trigger})
    connection.commit()
    return affected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    add = subparsers.add_parser("add-answer")
    add.add_argument("--entry", required=True, type=Path)
    form = subparsers.add_parser("add-form")
    form.add_argument("--canonical-id", required=True)
    form.add_argument("--question", required=True)
    form.add_argument("--match-level", choices=["exact", "semantic_equivalent"], default="exact")
    form.add_argument("--verified-by-user", action="store_true")
    authorization = subparsers.add_parser("add-authorization")
    authorization.add_argument("--entry", required=True, type=Path)
    revoke = subparsers.add_parser("revoke-authorization")
    revoke.add_argument("--authorization-id", required=True)
    invalidate = subparsers.add_parser("invalidate")
    invalidate.add_argument("--trigger", required=True)
    match = subparsers.add_parser("match")
    match.add_argument("--question", required=True)
    match.add_argument("--context", required=True, type=Path)
    match.add_argument("--authorization-id")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    if args.command == "init":
        result = {"status": "initialized", "db": str(args.db)}
    elif args.command == "add-answer":
        entry = json.loads(args.entry.read_text(encoding="utf-8"))
        add_answer(connection, entry)
        result = {"status": "added", "answer_id": entry["answer_id"]}
    elif args.command == "add-form":
        verified = args.verified_by_user or args.match_level == "exact"
        add_question_form(connection, args.canonical_id, args.question, args.match_level, verified)
        result = {"status": "added", "canonical_id": args.canonical_id}
    elif args.command == "add-authorization":
        entry = json.loads(args.entry.read_text(encoding="utf-8"))
        add_authorization(connection, entry)
        result = {"status": "added", "authorization_id": entry["authorization_id"]}
    elif args.command == "revoke-authorization":
        revoke_authorization(connection, args.authorization_id)
        result = {"status": "revoked", "authorization_id": args.authorization_id}
    elif args.command == "invalidate":
        result = {"status": "invalidated", "answer_ids": invalidate_by_trigger(connection, args.trigger)}
    elif args.command == "match":
        context = json.loads(args.context.read_text(encoding="utf-8"))
        result = match_answer(connection, args.question, context, args.authorization_id)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
