#!/usr/bin/env python3
"""Immutable, user-registered CandidateSnapshot and CandidateFact backend."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import resume_core  # noqa: E402
from _common import require_table  # noqa: E402


FACT_STATUSES = {"confirmed", "locked"}
FORBIDDEN_PROFILE_KEYS = {
    "password", "api_key", "apikey", "session_token", "access_token", "refresh_token",
    "private_key", "secret",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS candidate_snapshots (
            content_sha256 TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            snapshot_path TEXT NOT NULL UNIQUE,
            file_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            registered_at TEXT NOT NULL,
            registered_by TEXT NOT NULL,
            superseded_at TEXT,
            status_reason TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS candidate_snapshot_single_active
            ON candidate_snapshots ((1)) WHERE status='active';

        CREATE TABLE IF NOT EXISTS candidate_facts (
            content_sha256 TEXT NOT NULL,
            fact_id TEXT NOT NULL,
            fact_type TEXT NOT NULL,
            value_json TEXT NOT NULL,
            status TEXT NOT NULL,
            locked INTEGER NOT NULL,
            evidence_strength TEXT NOT NULL,
            expires_at TEXT,
            source_json TEXT NOT NULL,
            keywords_json TEXT NOT NULL,
            confirmed_at TEXT,
            invalidation_triggers_json TEXT NOT NULL,
            fact_sha256 TEXT NOT NULL,
            PRIMARY KEY (content_sha256, fact_id),
            FOREIGN KEY (content_sha256) REFERENCES candidate_snapshots(content_sha256)
        );
        CREATE INDEX IF NOT EXISTS candidate_fact_lookup_idx
            ON candidate_facts(fact_id, content_sha256, status, locked);

        CREATE TABLE IF NOT EXISTS candidate_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );
    """)
    fact_columns = {row[1] for row in connection.execute("PRAGMA table_info(candidate_facts)")}
    for name, definition in (
        ("source_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("keywords_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("confirmed_at", "TEXT"),
        ("invalidation_triggers_json", "TEXT NOT NULL DEFAULT '[]'"),
        # What this fact means, so a form field can reach it by meaning rather than by an
        # internal id no employer page has ever heard of. Nullable: facts written before the
        # Candidate Profile existed carry no canonical meaning, and inventing one for them
        # would be guessing which is which.
        ("canonical_id", "TEXT"),
    ):
        if name not in fact_columns:
            connection.execute(f"ALTER TABLE candidate_facts ADD COLUMN {name} {definition}")
    connection.commit()


def _event(connection: sqlite3.Connection, content_sha256: str, actor: str,
           event_type: str, reason_code: str, metadata: dict[str, Any] | None = None,
           at: datetime | None = None) -> None:
    connection.execute(
        "INSERT INTO candidate_events (content_sha256, created_at, actor, event_type, "
        "reason_code, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
        (content_sha256, (at or now_utc()).isoformat(), actor, event_type, reason_code,
         canonical_json(metadata or {})),
    )


def _validate_candidate(candidate: dict[str, Any]) -> None:
    required_top = {"schema_version", "profile_id", "work_authorization", "search", "facts"}
    if required_top - set(candidate):
        raise ValueError("candidate profile is missing finalized required fields")
    def contains_forbidden_key(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                str(key).casefold() in FORBIDDEN_PROFILE_KEYS or contains_forbidden_key(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(contains_forbidden_key(item) for item in value)
        return False
    if contains_forbidden_key(candidate):
        raise ValueError("credentials and secrets must not be stored in candidate profiles")
    profile_id = candidate.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id.strip() or len(profile_id) > 200:
        raise ValueError("candidate profile_id is required and must be bounded")
    authorization = candidate["work_authorization"]
    auth_fields = {
        "country", "authorized_now", "sponsorship_now", "sponsorship_future",
        "employer_action_required", "confirmed",
    }
    if not isinstance(authorization, dict) or auth_fields - set(authorization):
        raise ValueError("candidate work authorization is incomplete")
    if not authorization["confirmed"] or any(
        not isinstance(authorization[field], bool)
        for field in ("authorized_now", "sponsorship_now", "sponsorship_future",
                      "employer_action_required", "confirmed")
    ):
        raise ValueError("candidate work authorization must be explicitly confirmed")
    if not isinstance(candidate["search"], dict):
        raise ValueError("candidate search settings must be an object")
    facts = candidate.get("facts")
    if not isinstance(facts, list) or not facts or len(facts) > 10_000:
        raise ValueError("candidate profile must contain a bounded non-empty facts list")
    seen: set[str] = set()
    for fact in facts:
        if not isinstance(fact, dict):
            raise ValueError("every CandidateFact must be an object")
        fact_id = fact.get("id")
        if not isinstance(fact_id, str):
            raise ValueError("every CandidateFact requires a stable ID")
        resume_core._require_safe_id(fact_id, "fact_id")
        if fact_id in seen:
            raise ValueError("CandidateFact IDs must be unique")
        seen.add(fact_id)
        if fact.get("status") not in FACT_STATUSES:
            raise ValueError("registered CandidateFacts must be confirmed or locked")
        if not isinstance(fact.get("locked"), bool):
            raise ValueError("CandidateFact locked must be Boolean")
        if (fact["status"] == "locked") != fact["locked"]:
            raise ValueError("CandidateFact status and locked flag disagree")
        if fact.get("evidence_strength") not in resume_core.EVIDENCE_RANK:
            raise ValueError("CandidateFact has invalid evidence strength")
        if "value" not in fact or fact["value"] is None:
            raise ValueError("CandidateFact value is required")


def verify_snapshot_file(row: sqlite3.Row) -> None:
    path = Path(row["snapshot_path"])
    if not path.is_file() or resume_core.file_sha256(path) != row["file_sha256"]:
        raise ValueError("candidate snapshot file hash mismatch")


def _changed_fact_ids(connection: sqlite3.Connection, old_hash: str | None,
                      candidate: dict[str, Any]) -> set[str]:
    if not old_hash:
        return set()
    old = {row["fact_id"]: row["fact_sha256"] for row in connection.execute(
        "SELECT fact_id, fact_sha256 FROM candidate_facts WHERE content_sha256=?", (old_hash,)
    )}
    new = {fact["id"]: resume_core.canonical_hash(fact) for fact in candidate["facts"]}
    return {fact_id for fact_id in set(old) | set(new) if old.get(fact_id) != new.get(fact_id)}


def register_snapshot(
    connection: sqlite3.Connection,
    store: Path,
    candidate_path: Path,
    actor: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    if actor != "user":
        raise ValueError("candidate snapshot registration requires the user actor")
    candidate, content_hash = resume_core.load_valid_candidate(candidate_path)
    _validate_candidate(candidate)
    existing = connection.execute(
        "SELECT * FROM candidate_snapshots WHERE content_sha256=?", (content_hash,)
    ).fetchone()
    if existing:
        if existing["status"] != "active":
            raise ValueError("superseded candidate snapshot cannot be reactivated")
        verify_snapshot_file(existing)
        return {"content_sha256": content_hash, "profile_id": existing["profile_id"],
                "status": "active", "fact_count": connection.execute(
                    "SELECT COUNT(*) FROM candidate_facts WHERE content_sha256=?", (content_hash,)
                ).fetchone()[0]}
    active = connection.execute(
        "SELECT * FROM candidate_snapshots WHERE status='active'"
    ).fetchone()
    changed_ids = _changed_fact_ids(connection, active["content_sha256"] if active else None, candidate)
    resolved_store = store.resolve()
    resolved_store.mkdir(parents=True, exist_ok=True)
    os.chmod(resolved_store, 0o700)
    snapshot_dir = resolved_store / content_hash
    if snapshot_dir.exists():
        raise ValueError("candidate snapshot directory already exists")
    snapshot_dir.mkdir(mode=0o700)
    snapshot = snapshot_dir / "candidate.json"
    try:
        shutil.copyfile(candidate_path, snapshot)
        os.chmod(snapshot, 0o400)
        file_hash = resume_core.file_sha256(snapshot)
        timestamp = (at or now_utc()).isoformat()
        if active:
            connection.execute(
                "UPDATE candidate_snapshots SET status='superseded', superseded_at=?, "
                "status_reason='new_user_registered_snapshot' WHERE content_sha256=?",
                (timestamp, active["content_sha256"]),
            )
        connection.execute("""
            INSERT INTO candidate_snapshots (
                content_sha256, profile_id, snapshot_path, file_sha256, status,
                registered_at, registered_by, status_reason
            ) VALUES (?, ?, ?, ?, 'active', ?, 'user', 'user_registered')
        """, (content_hash, candidate["profile_id"], str(snapshot), file_hash, timestamp))
        for fact in candidate["facts"]:
            connection.execute("""
                INSERT INTO candidate_facts (
                    content_sha256, fact_id, fact_type, value_json, status, locked,
                    evidence_strength, expires_at, source_json, keywords_json, confirmed_at,
                    invalidation_triggers_json, canonical_id, fact_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (content_hash, fact["id"], str(fact.get("type", "unknown")),
                  canonical_json(fact["value"]), fact["status"], int(fact["locked"]),
                  fact["evidence_strength"], fact.get("expires_at"),
                  canonical_json(fact.get("source") or {}), canonical_json(fact.get("keywords") or []),
                  fact.get("confirmed_at"), canonical_json(fact.get("invalidation_triggers") or []),
                  fact.get("canonical_id"),
                  resume_core.canonical_hash(fact)))
        require_table(connection, "material_locks")
        if active:
            connection.execute("""
                UPDATE material_locks SET invalidated_at=?, invalidation_reason='candidate_snapshot_changed'
                WHERE resume_version_id IN (
                    SELECT version_id FROM resume_versions WHERE candidate_profile_sha256!=?
                ) AND invalidated_at IS NULL
            """, (timestamp, content_hash))
        require_table(connection, "answers")
        if changed_ids:
            for answer in connection.execute(
                "SELECT answer_id, dependent_fact_ids_json FROM answers WHERE status='active'"
            ):
                if changed_ids.intersection(json.loads(answer["dependent_fact_ids_json"])):
                    connection.execute(
                        "UPDATE answers SET status='stale' WHERE answer_id=?", (answer["answer_id"],)
                    )
        require_table(connection, "pre_submit_reviews")
        if active:
            connection.execute("""
                UPDATE pre_submit_reviews SET status='invalidated', invalidated_at=?,
                    invalidation_reason='candidate_snapshot_changed'
                WHERE application_id IN (
                    SELECT application_id FROM material_locks
                    WHERE invalidation_reason='candidate_snapshot_changed'
                ) AND status IN ('generated','approved')
            """, (timestamp,))
        _event(connection, content_hash, actor, "registered", "user_registered_exact_candidate_snapshot",
               {"fact_count": len(candidate["facts"]), "changed_fact_count": len(changed_ids),
                "file_sha256": file_hash}, at)
        connection.commit()
    except Exception:
        connection.rollback()
        if snapshot.exists():
            snapshot.unlink()
        if snapshot_dir.exists() and not any(snapshot_dir.iterdir()):
            snapshot_dir.rmdir()
        raise
    return {"content_sha256": content_hash, "profile_id": candidate["profile_id"],
            "status": "active", "fact_count": len(candidate["facts"]),
            "changed_fact_count": len(changed_ids), "snapshot_path": str(snapshot),
            "file_sha256": file_hash}


def require_active_snapshot(connection: sqlite3.Connection, content_sha256: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM candidate_snapshots WHERE content_sha256=? AND status='active' "
        "AND registered_by='user'", (content_sha256,)
    ).fetchone()
    if not row:
        raise ValueError("candidate profile is not the active user-registered snapshot")
    verify_snapshot_file(row)
    return row


def status(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT status, COUNT(*) AS count FROM candidate_snapshots GROUP BY status"
    ).fetchall()
    active = connection.execute(
        "SELECT content_sha256, profile_id FROM candidate_snapshots WHERE status='active'"
    ).fetchone()
    return {"snapshots": sum(row["count"] for row in rows),
            "by_status": {row["status"]: row["count"] for row in rows},
            "active": dict(active) if active else None,
            "active_fact_count": connection.execute(
                "SELECT COUNT(*) FROM candidate_facts WHERE content_sha256=?",
                (active["content_sha256"] if active else "",),
            ).fetchone()[0]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--store", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    register = commands.add_parser("register")
    register.add_argument("--candidate", required=True, type=Path)
    register.add_argument("--actor", required=True)
    commands.add_parser("status")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    store = args.store or args.db.parent / "candidates"
    if args.command == "init":
        result = {"status": "initialized", "db": str(args.db), "store": str(store)}
    elif args.command == "register":
        result = register_snapshot(connection, store, args.candidate, args.actor)
    else:
        result = status(connection)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
