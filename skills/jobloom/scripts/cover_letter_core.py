#!/usr/bin/env python3
"""Immutable, evidence-linked, user-approved Jobloom cover-letter versions."""

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
from _common import require_application_material_format, require_table  # noqa: E402


COVER_LETTER_KINDS = {"reusable_template", "application_specific"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


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
        CREATE TABLE IF NOT EXISTS cover_letter_versions (
            version_id TEXT PRIMARY KEY,
            parent_version_id TEXT,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            job_id TEXT,
            application_id TEXT,
            snapshot_path TEXT NOT NULL UNIQUE,
            file_sha256 TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            file_format TEXT NOT NULL,
            candidate_profile_sha256 TEXT,
            claims_manifest_path TEXT,
            claims_manifest_sha256 TEXT,
            created_at TEXT NOT NULL,
            approved_at TEXT,
            approved_by TEXT,
            revoked_at TEXT,
            status_reason TEXT,
            FOREIGN KEY (parent_version_id) REFERENCES cover_letter_versions(version_id),
            FOREIGN KEY (job_id) REFERENCES jobs(job_id),
            FOREIGN KEY (application_id) REFERENCES applications(application_id)
        );
        CREATE INDEX IF NOT EXISTS cover_letter_selection_idx
            ON cover_letter_versions(application_id, job_id, status, approved_at);

        CREATE TABLE IF NOT EXISTS cover_letter_usage (
            usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            use_type TEXT NOT NULL,
            file_sha256 TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (version_id) REFERENCES cover_letter_versions(version_id),
            FOREIGN KEY (application_id) REFERENCES applications(application_id),
            FOREIGN KEY (job_id) REFERENCES jobs(job_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS cover_letter_usage_unique
            ON cover_letter_usage(version_id, application_id, use_type);

        CREATE TABLE IF NOT EXISTS cover_letter_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id TEXT NOT NULL,
            application_id TEXT,
            created_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY (version_id) REFERENCES cover_letter_versions(version_id)
        );
    """)
    # Schema-migration guard, not a runtime safety dependency: application_core owns
    # material_locks and may not have been initialized yet when this module is used alone.
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='material_locks'"
    ).fetchone():
        columns = {row[1] for row in connection.execute("PRAGMA table_info(material_locks)")}
        if "cover_letter_file_sha256" not in columns:
            connection.execute("ALTER TABLE material_locks ADD COLUMN cover_letter_file_sha256 TEXT")
    connection.commit()


def _event(
    connection: sqlite3.Connection,
    version_id: str,
    actor: str,
    event_type: str,
    reason_code: str,
    application_id: str | None = None,
    at: datetime | None = None,
) -> None:
    connection.execute(
        "INSERT INTO cover_letter_events (version_id, application_id, created_at, actor, event_type, reason_code, metadata_json) VALUES (?, ?, ?, ?, ?, ?, '{}')",
        (version_id, application_id, (at or now_utc()).isoformat(), actor, event_type, reason_code),
    )


def _validate_parent(connection: sqlite3.Connection, parent_version_id: str | None) -> None:
    if not parent_version_id:
        return
    parent = connection.execute(
        "SELECT status FROM cover_letter_versions WHERE version_id=?", (parent_version_id,)
    ).fetchone()
    if not parent:
        raise ValueError("parent cover-letter version not found")
    if parent["status"] != "approved":
        raise ValueError("parent cover-letter version must be approved")


def verify_version_file(row: sqlite3.Row) -> None:
    snapshot = Path(row["snapshot_path"])
    if not snapshot.is_file() or snapshot.stat().st_size != row["file_size"]:
        raise ValueError("cover-letter snapshot is missing or changed size")
    if resume_core.file_sha256(snapshot) != row["file_sha256"]:
        raise ValueError("cover-letter snapshot hash mismatch")
    if row["status"] == "approved":
        manifest = Path(row["claims_manifest_path"] or "")
        if not manifest.is_file() or resume_core.file_sha256(manifest) != row["claims_manifest_sha256"]:
            raise ValueError("cover-letter claims manifest hash mismatch")


def register_version(
    connection: sqlite3.Connection,
    store: Path,
    source_file: Path,
    version_id: str,
    kind: str,
    parent_version_id: str | None = None,
    application_id: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    resume_core._require_safe_id(version_id, "version_id")
    if kind not in COVER_LETTER_KINDS:
        raise ValueError("invalid cover-letter kind")
    if not source_file.is_file():
        raise ValueError("cover-letter source file not found")
    if connection.execute("SELECT 1 FROM cover_letter_versions WHERE version_id=?", (version_id,)).fetchone():
        raise ValueError("cover-letter version already exists")
    _validate_parent(connection, parent_version_id)
    job_id = None
    if kind == "application_specific":
        if not application_id:
            raise ValueError("application_specific cover letter requires application_id")
        application = connection.execute(
            "SELECT job_id, state FROM applications WHERE application_id=?", (application_id,)
        ).fetchone()
        if not application:
            raise ValueError("application not found")
        if application["state"] not in {"approved", "materials_in_progress"}:
            raise ValueError("application-specific cover letter must be registered during material preparation")
        job_id = application["job_id"]
    elif application_id:
        raise ValueError("reusable_template must not be scoped to an application")
    suffix = source_file.suffix.casefold()
    if suffix not in {".pdf", ".docx", ".txt", ".md"}:
        raise ValueError("cover-letter format must be PDF, DOCX, TXT, or Markdown")
    resolved_store = store.resolve()
    resolved_store.mkdir(parents=True, exist_ok=True)
    os.chmod(resolved_store, 0o700)
    version_dir = resolved_store / version_id
    if version_dir.exists():
        raise ValueError("cover-letter snapshot directory already exists")
    version_dir.mkdir(mode=0o700)
    snapshot = version_dir / f"cover-letter{suffix}"
    try:
        shutil.copyfile(source_file, snapshot)
        os.chmod(snapshot, 0o400)
        digest = resume_core.file_sha256(snapshot)
        size = snapshot.stat().st_size
        timestamp = (at or now_utc()).isoformat()
        connection.execute("""
            INSERT INTO cover_letter_versions (
                version_id, parent_version_id, kind, status, job_id, application_id,
                snapshot_path, file_sha256, file_size, file_format, created_at
            ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)
        """, (version_id, parent_version_id, kind, job_id, application_id, str(snapshot),
              digest, size, suffix[1:], timestamp))
        _event(connection, version_id, "system", "registered", "immutable_snapshot_created", application_id, at)
        connection.commit()
    except Exception:
        connection.rollback()
        if snapshot.exists():
            snapshot.unlink()
        if version_dir.exists() and not any(version_dir.iterdir()):
            version_dir.rmdir()
        raise
    return {"version_id": version_id, "kind": kind, "status": "draft", "application_id": application_id,
            "job_id": job_id, "snapshot_path": str(snapshot), "file_sha256": digest, "file_size": size}


def approve_version(
    connection: sqlite3.Connection,
    version_id: str,
    candidate_path: Path,
    manifest_path: Path,
    actor: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    if actor != "user":
        raise ValueError("cover-letter approval requires the user actor")
    row = connection.execute("SELECT * FROM cover_letter_versions WHERE version_id=?", (version_id,)).fetchone()
    if not row or row["status"] != "draft":
        raise ValueError("draft cover-letter version not found")
    verify_version_file(row)
    candidate, candidate_hash = resume_core.load_valid_candidate(candidate_path)
    manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
    resume_core.validate_claims_manifest(manifest_value, candidate)
    manifest_snapshot = Path(row["snapshot_path"]).parent / "claims-manifest.json"
    if manifest_snapshot.exists():
        raise ValueError("cover-letter claims manifest snapshot already exists")
    manifest_snapshot.write_text(
        json.dumps(manifest_value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.chmod(manifest_snapshot, 0o400)
    manifest_hash = resume_core.file_sha256(manifest_snapshot)
    timestamp = (at or now_utc()).isoformat()
    connection.execute("""
        UPDATE cover_letter_versions SET status='approved', candidate_profile_sha256=?,
            claims_manifest_path=?, claims_manifest_sha256=?, approved_at=?, approved_by='user',
            status_reason='user_approved' WHERE version_id=?
    """, (candidate_hash, str(manifest_snapshot), manifest_hash, timestamp, version_id))
    _event(connection, version_id, actor, "approved", "claims_and_file_verified", row["application_id"], at)
    connection.commit()
    return {"version_id": version_id, "status": "approved", "file_sha256": row["file_sha256"],
            "candidate_profile_sha256": candidate_hash, "claims_manifest_sha256": manifest_hash}


def bind_version(
    connection: sqlite3.Connection,
    application_id: str,
    version_id: str,
    actor: str = "system",
    at: datetime | None = None,
) -> dict[str, Any]:
    application = connection.execute("SELECT * FROM applications WHERE application_id=?", (application_id,)).fetchone()
    if not application or application["state"] not in {"approved", "materials_in_progress"}:
        raise ValueError("cover letter can only be bound during material preparation")
    version = connection.execute("SELECT * FROM cover_letter_versions WHERE version_id=?", (version_id,)).fetchone()
    if not version or version["status"] != "approved":
        raise ValueError("application requires an approved cover-letter version")
    if version["kind"] == "application_specific" and (
        version["application_id"] != application_id or version["job_id"] != application["job_id"]
    ):
        raise ValueError("application-specific cover letter is scoped to another application")
    verify_version_file(version)
    require_application_material_format(version["snapshot_path"], "cover letter")
    timestamp = (at or now_utc()).isoformat()
    require_table(connection, "material_locks")
    connection.execute(
        "UPDATE material_locks SET invalidated_at=?, invalidation_reason='cover_letter_rebound' WHERE application_id=? AND invalidated_at IS NULL",
        (timestamp, application_id),
    )
    connection.execute(
        "UPDATE applications SET cover_letter_version_id=?, pre_submit_check_passed=0, pre_submit_review_id=NULL, updated_at=? WHERE application_id=?",
        (version_id, timestamp, application_id),
    )
    require_table(connection, "pre_submit_reviews")
    connection.execute(
        "UPDATE pre_submit_reviews SET status='invalidated', invalidated_at=?, "
        "invalidation_reason='cover_letter_rebound' WHERE application_id=? "
        "AND status IN ('generated','approved') AND invalidated_at IS NULL",
        (timestamp, application_id),
    )
    connection.execute("""
        INSERT OR IGNORE INTO cover_letter_usage (
            version_id, application_id, job_id, use_type, file_sha256, recorded_at
        ) VALUES (?, ?, ?, 'prepared', ?, ?)
    """, (version_id, application_id, application["job_id"], version["file_sha256"], timestamp))
    _event(connection, version_id, actor, "bound", "application_material_selected", application_id, at)
    connection.commit()
    return {"application_id": application_id, "cover_letter_version_id": version_id,
            "file_sha256": version["file_sha256"]}


def revoke_version(
    connection: sqlite3.Connection,
    version_id: str,
    actor: str,
    reason: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    if actor != "user":
        raise ValueError("cover-letter revocation requires the user actor")
    if not reason.strip():
        raise ValueError("revocation reason is required")
    row = connection.execute("SELECT * FROM cover_letter_versions WHERE version_id=?", (version_id,)).fetchone()
    if not row or row["status"] != "approved":
        raise ValueError("approved cover-letter version not found")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE cover_letter_versions SET status='revoked', revoked_at=?, status_reason=? WHERE version_id=?",
        (timestamp, reason, version_id),
    )
    require_table(connection, "material_locks")
    connection.execute(
        "UPDATE material_locks SET invalidated_at=?, invalidation_reason='cover_letter_revoked' WHERE cover_letter_version_id=? AND invalidated_at IS NULL",
        (timestamp, version_id),
    )
    require_table(connection, "pre_submit_reviews")
    connection.execute(
        "UPDATE pre_submit_reviews SET status='invalidated', invalidated_at=?, "
        "invalidation_reason='cover_letter_revoked' WHERE application_id IN "
        "(SELECT application_id FROM applications WHERE cover_letter_version_id=?) "
        "AND status IN ('generated','approved') AND invalidated_at IS NULL",
        (timestamp, version_id),
    )
    _event(connection, version_id, actor, "revoked", reason, row["application_id"], at)
    connection.commit()
    return {"version_id": version_id, "status": "revoked"}


def status(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute("SELECT status, COUNT(*) AS count FROM cover_letter_versions GROUP BY status").fetchall()
    return {"versions": sum(row["count"] for row in rows),
            "by_status": {row["status"]: row["count"] for row in rows}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--store", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    register = commands.add_parser("register")
    register.add_argument("--file", required=True, type=Path)
    register.add_argument("--version-id", required=True)
    register.add_argument("--kind", required=True, choices=sorted(COVER_LETTER_KINDS))
    register.add_argument("--parent")
    register.add_argument("--application-id")
    approve = commands.add_parser("approve")
    approve.add_argument("--version-id", required=True)
    approve.add_argument("--candidate", required=True, type=Path)
    approve.add_argument("--manifest", required=True, type=Path)
    approve.add_argument("--actor", required=True)
    bind = commands.add_parser("bind")
    bind.add_argument("--application-id", required=True)
    bind.add_argument("--version-id", required=True)
    bind.add_argument("--actor", default="system")
    revoke = commands.add_parser("revoke")
    revoke.add_argument("--version-id", required=True)
    revoke.add_argument("--actor", required=True)
    revoke.add_argument("--reason", required=True)
    commands.add_parser("status")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    store = args.store or args.db.parent / "cover-letters"
    if args.command == "init":
        result = {"status": "initialized", "db": str(args.db), "store": str(store)}
    elif args.command == "register":
        result = register_version(connection, store, args.file, args.version_id, args.kind,
                                  args.parent, args.application_id)
    elif args.command == "approve":
        result = approve_version(connection, args.version_id, args.candidate, args.manifest, args.actor)
    elif args.command == "bind":
        result = bind_version(connection, args.application_id, args.version_id, args.actor)
    elif args.command == "revoke":
        result = revoke_version(connection, args.version_id, args.actor, args.reason)
    else:
        result = status(connection)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
