#!/usr/bin/env python3
"""Immutable local resume versions, evidence manifests, approvals, and material locks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RESUME_KINDS = {"master_source", "direction", "lightweight", "precision"}
RESUME_STATUSES = {"draft", "approved", "revoked", "superseded"}
EVIDENCE_RANK = {
    "none": 0,
    "mention_only": 1,
    "transferable": 2,
    "strongly_related": 3,
    "direct": 4,
}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        CREATE TABLE IF NOT EXISTS resume_versions (
            version_id TEXT PRIMARY KEY,
            parent_version_id TEXT,
            kind TEXT NOT NULL,
            direction TEXT NOT NULL,
            status TEXT NOT NULL,
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
            adaptation_plan_id TEXT,
            direction_profile_sha256 TEXT,
            FOREIGN KEY (parent_version_id) REFERENCES resume_versions(version_id)
        );
        CREATE INDEX IF NOT EXISTS resume_versions_selection_idx
            ON resume_versions(direction, kind, status, approved_at);

        CREATE TABLE IF NOT EXISTS material_locks (
            lock_id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            resume_version_id TEXT NOT NULL,
            resume_file_sha256 TEXT NOT NULL,
            cover_letter_version_id TEXT,
            cover_letter_file_sha256 TEXT,
            locked_at TEXT NOT NULL,
            invalidated_at TEXT,
            invalidation_reason TEXT,
            FOREIGN KEY (application_id) REFERENCES applications(application_id),
            FOREIGN KEY (resume_version_id) REFERENCES resume_versions(version_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS material_locks_active_application
            ON material_locks(application_id) WHERE invalidated_at IS NULL;

        CREATE TABLE IF NOT EXISTS resume_usage (
            usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            use_type TEXT NOT NULL,
            file_sha256 TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (version_id) REFERENCES resume_versions(version_id),
            FOREIGN KEY (application_id) REFERENCES applications(application_id),
            FOREIGN KEY (job_id) REFERENCES jobs(job_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS resume_usage_unique
            ON resume_usage(version_id, application_id, use_type);

        CREATE TABLE IF NOT EXISTS resume_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id TEXT NOT NULL,
            application_id TEXT,
            created_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY (version_id) REFERENCES resume_versions(version_id)
        );
    """)
    lock_columns = {row[1] for row in connection.execute("PRAGMA table_info(material_locks)")}
    if "cover_letter_file_sha256" not in lock_columns:
        connection.execute("ALTER TABLE material_locks ADD COLUMN cover_letter_file_sha256 TEXT")
    version_columns = {row[1] for row in connection.execute("PRAGMA table_info(resume_versions)")}
    if "adaptation_plan_id" not in version_columns:
        connection.execute("ALTER TABLE resume_versions ADD COLUMN adaptation_plan_id TEXT")
    if "direction_profile_sha256" not in version_columns:
        connection.execute("ALTER TABLE resume_versions ADD COLUMN direction_profile_sha256 TEXT")
    connection.commit()


def _require_safe_id(value: str, label: str) -> None:
    if not SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} must use only letters, numbers, dot, underscore, and hyphen")


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _active_direction(connection: sqlite3.Connection, direction: str) -> sqlite3.Row | None:
    if not _table_exists(connection, "search_directions"):
        return None
    row = connection.execute(
        "SELECT * FROM search_directions WHERE direction_id=?", (direction,)
    ).fetchone()
    if not row or row["status"] != "approved":
        raise ValueError("resume direction is not user-approved")
    if _table_exists(connection, "search_portfolios"):
        portfolio_count = connection.execute(
            "SELECT COUNT(*) FROM search_portfolios"
        ).fetchone()[0]
        if portfolio_count and not connection.execute("""
            SELECT 1 FROM search_portfolio_directions pd
            JOIN search_portfolios p ON p.portfolio_id=pd.portfolio_id
            WHERE p.status='approved' AND pd.direction_id=? AND pd.profile_sha256=?
        """, (direction, row["profile_sha256"])).fetchone():
            raise ValueError("resume direction is outside the active approved portfolio")
    return row


def _approved_adaptation_plan(
    connection: sqlite3.Connection,
    plan_id: str | None,
    direction: str,
    kind: str,
    require_fresh_job: bool = True,
) -> sqlite3.Row | None:
    if not _table_exists(connection, "resume_adaptation_plans"):
        return None
    if not plan_id:
        raise ValueError("derived resume requires an approved adaptation plan")
    plan = connection.execute(
        "SELECT * FROM resume_adaptation_plans WHERE plan_id=?", (plan_id,)
    ).fetchone()
    if not plan or plan["status"] != "approved" or plan["direction_id"] != direction:
        raise ValueError("approved adaptation plan not found for this direction")
    if plan["recommended_kind"] != kind:
        raise ValueError("resume kind does not match the approved adaptation plan")
    if require_fresh_job:
        job = connection.execute("SELECT job_card_json FROM jobs WHERE job_id=?", (plan["job_id"],)).fetchone()
        if not job or canonical_hash(json.loads(job["job_card_json"])) != plan["job_card_sha256"]:
            raise ValueError("adaptation plan job card is stale")
    return plan


def _require_resume_authorized_for_application(
    connection: sqlite3.Connection, version: sqlite3.Row
) -> None:
    if _table_exists(connection, "candidate_snapshots"):
        snapshot = connection.execute(
            "SELECT * FROM candidate_snapshots WHERE content_sha256=? AND status='active' "
            "AND registered_by='user'", (version["candidate_profile_sha256"],)
        ).fetchone()
        if not snapshot:
            raise ValueError("resume candidate profile is not the active registered snapshot")
        path = Path(snapshot["snapshot_path"])
        if not path.is_file() or file_sha256(path) != snapshot["file_sha256"]:
            raise ValueError("registered candidate snapshot hash mismatch")
    if not _table_exists(connection, "search_directions"):
        return
    if version["kind"] == "master_source":
        raise ValueError("master-source resume cannot be used after direction enforcement is initialized")
    direction = _active_direction(connection, version["direction"])
    if version["direction_profile_sha256"] != direction["profile_sha256"]:
        raise ValueError("resume direction profile is stale")
    _approved_adaptation_plan(
        connection, version["adaptation_plan_id"], version["direction"], version["kind"],
        require_fresh_job=False,
    )


def _event(
    connection: sqlite3.Connection,
    version_id: str,
    actor: str,
    event_type: str,
    reason_code: str,
    application_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> None:
    connection.execute(
        "INSERT INTO resume_events (version_id, application_id, created_at, actor, event_type, reason_code, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            version_id,
            application_id,
            (at or now_utc()).isoformat(),
            actor,
            event_type,
            reason_code,
            json.dumps(metadata or {}, sort_keys=True, separators=(",", ":")),
        ),
    )


def _validate_parent(connection: sqlite3.Connection, kind: str, direction: str, parent_version_id: str | None) -> None:
    if kind == "master_source":
        if parent_version_id:
            raise ValueError("master_source must not have a parent")
        return
    if not parent_version_id:
        raise ValueError(f"{kind} requires an approved parent version")
    parent = connection.execute("SELECT * FROM resume_versions WHERE version_id=?", (parent_version_id,)).fetchone()
    if not parent:
        raise ValueError("parent resume version not found")
    if parent["status"] != "approved":
        raise ValueError("parent resume version must be approved")
    if kind == "direction" and parent["kind"] not in {"master_source", "direction"}:
        raise ValueError("direction resume must derive from a master_source or direction resume")
    if kind in {"lightweight", "precision"} and parent["kind"] not in {"direction", "lightweight", "precision"}:
        raise ValueError(f"{kind} resume must derive from an approved direction resume chain")
    if parent["kind"] != "master_source" and parent["direction"] != direction:
        raise ValueError("child resume direction must match its parent")


def register_version(
    connection: sqlite3.Connection,
    store: Path,
    source_file: Path,
    version_id: str,
    kind: str,
    direction: str,
    parent_version_id: str | None = None,
    actor: str = "system",
    at: datetime | None = None,
    adaptation_plan_id: str | None = None,
) -> dict[str, Any]:
    _require_safe_id(version_id, "version_id")
    if kind not in RESUME_KINDS:
        raise ValueError("invalid resume kind")
    if not direction.strip():
        raise ValueError("direction is required")
    if not source_file.is_file():
        raise ValueError("resume source file not found")
    if connection.execute("SELECT 1 FROM resume_versions WHERE version_id=?", (version_id,)).fetchone():
        raise ValueError("resume version already exists")
    _validate_parent(connection, kind, direction, parent_version_id)
    direction_row = None
    plan = None
    if kind != "master_source":
        direction_row = _active_direction(connection, direction)
        plan = _approved_adaptation_plan(connection, adaptation_plan_id, direction, kind)
        if plan and plan["base_resume_version_id"] != parent_version_id:
            raise ValueError("resume parent does not match the approved adaptation plan")
    suffix = source_file.suffix.casefold()
    if suffix not in {".pdf", ".docx", ".txt", ".md"}:
        raise ValueError("resume format must be PDF, DOCX, TXT, or Markdown")
    resolved_store = store.resolve()
    resolved_store.mkdir(parents=True, exist_ok=True)
    os.chmod(resolved_store, 0o700)
    version_dir = resolved_store / version_id
    snapshot = version_dir / f"resume{suffix}"
    if version_dir.exists():
        raise ValueError("resume snapshot directory already exists")
    version_dir.mkdir(parents=True, mode=0o700)
    try:
        shutil.copyfile(source_file, snapshot)
        os.chmod(snapshot, 0o400)
        digest = file_sha256(snapshot)
        size = snapshot.stat().st_size
        timestamp = (at or now_utc()).isoformat()
        connection.execute("""
            INSERT INTO resume_versions (
                version_id, parent_version_id, kind, direction, status, snapshot_path,
                file_sha256, file_size, file_format, created_at, adaptation_plan_id,
                direction_profile_sha256
            ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)
        """, (version_id, parent_version_id, kind, direction, str(snapshot), digest, size,
              suffix[1:], timestamp, adaptation_plan_id,
              direction_row["profile_sha256"] if direction_row else None))
        _event(connection, version_id, actor, "registered", "immutable_snapshot_created", at=at)
        connection.commit()
    except Exception:
        connection.rollback()
        if snapshot.exists():
            snapshot.unlink()
        if version_dir.exists() and not any(version_dir.iterdir()):
            version_dir.rmdir()
        raise
    return {
        "version_id": version_id,
        "kind": kind,
        "direction": direction,
        "status": "draft",
        "file_sha256": digest,
        "file_size": size,
        "snapshot_path": str(snapshot),
        "adaptation_plan_id": adaptation_plan_id,
    }


def load_valid_candidate(path: Path) -> tuple[dict[str, Any], str]:
    candidate = json.loads(path.read_text(encoding="utf-8"))
    stored_hash = candidate.get("content_sha256")
    if not isinstance(stored_hash, str) or not stored_hash:
        raise ValueError("candidate profile has no content_sha256")
    unhashed = dict(candidate)
    unhashed.pop("content_sha256", None)
    if canonical_hash(unhashed) != stored_hash:
        raise ValueError("candidate profile content hash is invalid")
    return candidate, stored_hash


def validate_claims_manifest(manifest: dict[str, Any], candidate: dict[str, Any]) -> None:
    claims = manifest.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("claims manifest must contain at least one claim")
    facts = {fact.get("id"): fact for fact in candidate.get("facts", [])}
    seen: set[str] = set()
    for claim in claims:
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id or claim_id in seen:
            raise ValueError("every claim requires a unique claim_id")
        seen.add(claim_id)
        if not isinstance(claim.get("claim_text"), str) or not claim["claim_text"].strip():
            raise ValueError(f"claim {claim_id} requires claim_text")
        fact_ids = claim.get("fact_ids")
        if not isinstance(fact_ids, list) or not fact_ids:
            raise ValueError(f"claim {claim_id} has no supporting facts")
        supporting = []
        for fact_id in fact_ids:
            fact = facts.get(fact_id)
            if not fact or fact.get("status") not in {"confirmed", "locked"}:
                raise ValueError(f"claim {claim_id} references an unavailable fact: {fact_id}")
            supporting.append(fact)
        strength = claim.get("evidence_strength")
        if strength not in EVIDENCE_RANK:
            raise ValueError(f"claim {claim_id} has invalid evidence_strength")
        max_strength = max(EVIDENCE_RANK.get(fact.get("evidence_strength"), 0) for fact in supporting)
        if EVIDENCE_RANK[strength] > max_strength:
            raise ValueError(f"claim {claim_id} inflates its supporting evidence")
        if any(fact.get("locked") or fact.get("status") == "locked" for fact in supporting):
            if claim.get("exact_locked_value_preserved") is not True:
                raise ValueError(f"claim {claim_id} must preserve exact locked values")


def verify_version_file(row: sqlite3.Row) -> None:
    snapshot = Path(row["snapshot_path"])
    if not snapshot.is_file():
        raise ValueError("resume snapshot is missing")
    if snapshot.stat().st_size != row["file_size"] or file_sha256(snapshot) != row["file_sha256"]:
        raise ValueError("resume snapshot hash mismatch")
    if row["status"] == "approved":
        manifest_path = row["claims_manifest_path"]
        manifest_hash = row["claims_manifest_sha256"]
        if not manifest_path or not manifest_hash:
            raise ValueError("approved resume has no claims manifest snapshot")
        manifest = Path(manifest_path)
        if not manifest.is_file() or file_sha256(manifest) != manifest_hash:
            raise ValueError("claims manifest snapshot hash mismatch")


def approve_version(
    connection: sqlite3.Connection,
    version_id: str,
    candidate_path: Path,
    manifest_path: Path,
    actor: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    if actor != "user":
        raise ValueError("resume approval requires the user actor")
    row = connection.execute("SELECT * FROM resume_versions WHERE version_id=?", (version_id,)).fetchone()
    if not row:
        raise ValueError("resume version not found")
    if row["status"] != "draft":
        raise ValueError("only a draft resume can be approved")
    verify_version_file(row)
    candidate, candidate_hash = load_valid_candidate(candidate_path)
    if _table_exists(connection, "candidate_snapshots"):
        snapshot = connection.execute(
            "SELECT * FROM candidate_snapshots WHERE content_sha256=? AND status='active' "
            "AND registered_by='user'", (candidate_hash,)
        ).fetchone()
        if not snapshot:
            raise ValueError("candidate profile is not the active user-registered snapshot")
        snapshot_path = Path(snapshot["snapshot_path"])
        if not snapshot_path.is_file() or file_sha256(snapshot_path) != snapshot["file_sha256"]:
            raise ValueError("registered candidate snapshot hash mismatch")
    if row["kind"] != "master_source" and _table_exists(connection, "search_directions"):
        direction_row = _active_direction(connection, row["direction"])
        if row["direction_profile_sha256"] != direction_row["profile_sha256"]:
            raise ValueError("resume direction profile changed after registration")
        plan = _approved_adaptation_plan(
            connection, row["adaptation_plan_id"], row["direction"], row["kind"]
        )
        if not plan or plan["candidate_profile_sha256"] != candidate_hash:
            raise ValueError("adaptation plan candidate profile is stale")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_claims_manifest(manifest, candidate)
    manifest_snapshot = Path(row["snapshot_path"]).parent / "claims-manifest.json"
    if manifest_snapshot.exists():
        raise ValueError("claims manifest snapshot already exists")
    manifest_snapshot.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(manifest_snapshot, 0o400)
    manifest_hash = file_sha256(manifest_snapshot)
    timestamp = (at or now_utc()).isoformat()
    connection.execute("""
        UPDATE resume_versions
        SET status='approved', candidate_profile_sha256=?, claims_manifest_path=?,
            claims_manifest_sha256=?, approved_at=?, approved_by=?, status_reason='user_approved'
        WHERE version_id=?
    """, (candidate_hash, str(manifest_snapshot), manifest_hash, timestamp, actor, version_id))
    _event(connection, version_id, actor, "approved", "claims_and_file_verified", at=at)
    connection.commit()
    return {"version_id": version_id, "status": "approved", "candidate_profile_sha256": candidate_hash,
            "claims_manifest_sha256": manifest_hash, "file_sha256": row["file_sha256"]}


def revoke_version(
    connection: sqlite3.Connection,
    version_id: str,
    actor: str,
    reason: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    if actor != "user":
        raise ValueError("resume revocation requires the user actor")
    if not reason.strip():
        raise ValueError("revocation reason is required")
    row = connection.execute("SELECT status FROM resume_versions WHERE version_id=?", (version_id,)).fetchone()
    if not row:
        raise ValueError("resume version not found")
    if row["status"] != "approved":
        raise ValueError("only an approved resume can be revoked")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE resume_versions SET status='revoked', revoked_at=?, status_reason=? WHERE version_id=?",
        (timestamp, reason, version_id),
    )
    connection.execute(
        "UPDATE material_locks SET invalidated_at=?, invalidation_reason='resume_revoked' WHERE resume_version_id=? AND invalidated_at IS NULL",
        (timestamp, version_id),
    )
    _event(connection, version_id, actor, "revoked", reason, at=at)
    connection.commit()
    return {"version_id": version_id, "status": "revoked"}


def select_approved(connection: sqlite3.Connection, direction: str, kind: str | None = None) -> dict[str, Any] | None:
    if _table_exists(connection, "search_directions"):
        _active_direction(connection, direction)
    parameters: list[Any] = [direction]
    kind_filter = ""
    if kind:
        if kind not in RESUME_KINDS:
            raise ValueError("invalid resume kind")
        kind_filter = " AND kind=?"
        parameters.append(kind)
    row = connection.execute(
        f"SELECT * FROM resume_versions WHERE direction=? AND status='approved'{kind_filter} ORDER BY approved_at DESC LIMIT 1",
        parameters,
    ).fetchone()
    if not row:
        return None
    _require_resume_authorized_for_application(connection, row)
    verify_version_file(row)
    return {key: row[key] for key in (
        "version_id", "parent_version_id", "kind", "direction", "status", "snapshot_path",
        "file_sha256", "candidate_profile_sha256", "claims_manifest_sha256", "approved_at",
    )}


def bind_version(
    connection: sqlite3.Connection,
    application_id: str,
    version_id: str,
    actor: str = "system",
    at: datetime | None = None,
) -> dict[str, Any]:
    application = connection.execute("SELECT * FROM applications WHERE application_id=?", (application_id,)).fetchone()
    if not application:
        raise ValueError("application not found")
    if application["state"] not in {"approved", "materials_in_progress"}:
        raise ValueError("resume can only be bound during material preparation")
    version = connection.execute("SELECT * FROM resume_versions WHERE version_id=?", (version_id,)).fetchone()
    if not version or version["status"] != "approved":
        raise ValueError("application requires an approved resume version")
    _require_resume_authorized_for_application(connection, version)
    verify_version_file(version)
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE material_locks SET invalidated_at=?, invalidation_reason='resume_rebound' WHERE application_id=? AND invalidated_at IS NULL",
        (timestamp, application_id),
    )
    connection.execute(
        "UPDATE applications SET resume_version_id=?, pre_submit_check_passed=0, updated_at=? WHERE application_id=?",
        (version_id, timestamp, application_id),
    )
    connection.execute(
        "INSERT OR IGNORE INTO resume_usage (version_id, application_id, job_id, use_type, file_sha256, recorded_at) VALUES (?, ?, ?, 'prepared', ?, ?)",
        (version_id, application_id, application["job_id"], version["file_sha256"], timestamp),
    )
    _event(connection, version_id, actor, "bound", "application_material_selected", application_id, at=at)
    connection.commit()
    return {"application_id": application_id, "resume_version_id": version_id, "file_sha256": version["file_sha256"]}


def lock_materials(
    connection: sqlite3.Connection,
    application_id: str,
    actor: str = "system",
    lock_id: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    application = connection.execute("SELECT * FROM applications WHERE application_id=?", (application_id,)).fetchone()
    if not application:
        raise ValueError("application not found")
    if application["state"] != "materials_in_progress":
        raise ValueError("materials can only be locked while preparation is in progress")
    if not application["resume_version_id"]:
        raise ValueError("no resume version is bound to the application")
    version = connection.execute(
        "SELECT * FROM resume_versions WHERE version_id=?", (application["resume_version_id"],)
    ).fetchone()
    if not version or version["status"] != "approved":
        raise ValueError("bound resume version is not approved")
    _require_resume_authorized_for_application(connection, version)
    verify_version_file(version)
    cover_letter = None
    if application["cover_letter_version_id"]:
        cover_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cover_letter_versions'"
        ).fetchone()
        if not cover_table:
            raise ValueError("cover-letter version store is unavailable")
        cover_letter = connection.execute(
            "SELECT * FROM cover_letter_versions WHERE version_id=?",
            (application["cover_letter_version_id"],),
        ).fetchone()
        if not cover_letter or cover_letter["status"] != "approved":
            raise ValueError("bound cover-letter version is not approved")
        if cover_letter["kind"] == "application_specific" and (
            cover_letter["application_id"] != application_id or cover_letter["job_id"] != application["job_id"]
        ):
            raise ValueError("bound cover letter is scoped to another application")
        cover_snapshot = Path(cover_letter["snapshot_path"])
        if not cover_snapshot.is_file() or cover_snapshot.stat().st_size != cover_letter["file_size"]:
            raise ValueError("cover-letter snapshot is missing or changed size")
        if file_sha256(cover_snapshot) != cover_letter["file_sha256"]:
            raise ValueError("cover-letter snapshot hash mismatch")
        cover_manifest = Path(cover_letter["claims_manifest_path"] or "")
        if not cover_manifest.is_file() or file_sha256(cover_manifest) != cover_letter["claims_manifest_sha256"]:
            raise ValueError("cover-letter claims manifest hash mismatch")
    existing = connection.execute(
        "SELECT lock_id FROM material_locks WHERE application_id=? AND invalidated_at IS NULL", (application_id,)
    ).fetchone()
    if existing:
        raise ValueError("application materials are already locked")
    actual_lock_id = lock_id or f"lock-{uuid.uuid4().hex}"
    _require_safe_id(actual_lock_id, "lock_id")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "INSERT INTO material_locks (lock_id, application_id, resume_version_id, resume_file_sha256, cover_letter_version_id, cover_letter_file_sha256, locked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (actual_lock_id, application_id, version["version_id"], version["file_sha256"],
         cover_letter["version_id"] if cover_letter else None,
         cover_letter["file_sha256"] if cover_letter else None, timestamp),
    )
    connection.execute(
        "INSERT OR IGNORE INTO resume_usage (version_id, application_id, job_id, use_type, file_sha256, recorded_at) VALUES (?, ?, ?, 'locked', ?, ?)",
        (version["version_id"], application_id, application["job_id"], version["file_sha256"], timestamp),
    )
    if cover_letter is not None:
        connection.execute("""
            INSERT OR IGNORE INTO cover_letter_usage (
                version_id, application_id, job_id, use_type, file_sha256, recorded_at
            ) VALUES (?, ?, ?, 'locked', ?, ?)
        """, (cover_letter["version_id"], application_id, application["job_id"],
              cover_letter["file_sha256"], timestamp))
    _event(connection, version["version_id"], actor, "locked", "application_materials_frozen", application_id, at=at)
    connection.commit()
    return {"lock_id": actual_lock_id, "application_id": application_id,
            "resume_version_id": version["version_id"], "resume_file_sha256": version["file_sha256"],
            "cover_letter_version_id": cover_letter["version_id"] if cover_letter else None,
            "cover_letter_file_sha256": cover_letter["file_sha256"] if cover_letter else None}


def status(connection: sqlite3.Connection) -> dict[str, Any]:
    versions = connection.execute(
        "SELECT status, COUNT(*) AS count FROM resume_versions GROUP BY status ORDER BY status"
    ).fetchall()
    active_locks = connection.execute("SELECT COUNT(*) FROM material_locks WHERE invalidated_at IS NULL").fetchone()[0]
    return {"versions": sum(row["count"] for row in versions),
            "by_status": {row["status"]: row["count"] for row in versions}, "active_material_locks": active_locks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--store", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    register = commands.add_parser("register")
    register.add_argument("--file", required=True, type=Path)
    register.add_argument("--version-id", required=True)
    register.add_argument("--kind", required=True, choices=sorted(RESUME_KINDS))
    register.add_argument("--direction", required=True)
    register.add_argument("--parent")
    register.add_argument("--adaptation-plan-id")
    approve = commands.add_parser("approve")
    approve.add_argument("--version-id", required=True)
    approve.add_argument("--candidate", required=True, type=Path)
    approve.add_argument("--manifest", required=True, type=Path)
    approve.add_argument("--actor", required=True)
    revoke = commands.add_parser("revoke")
    revoke.add_argument("--version-id", required=True)
    revoke.add_argument("--actor", required=True)
    revoke.add_argument("--reason", required=True)
    select = commands.add_parser("select")
    select.add_argument("--direction", required=True)
    select.add_argument("--kind", choices=sorted(RESUME_KINDS))
    bind = commands.add_parser("bind")
    bind.add_argument("--application-id", required=True)
    bind.add_argument("--version-id", required=True)
    bind.add_argument("--actor", default="system")
    lock = commands.add_parser("lock")
    lock.add_argument("--application-id", required=True)
    lock.add_argument("--actor", default="system")
    lock.add_argument("--lock-id")
    commands.add_parser("status")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    store = args.store or args.db.parent / "resumes"
    if args.command == "init":
        result = {"status": "initialized", "db": str(args.db), "store": str(store)}
    elif args.command == "register":
        result = register_version(
            connection, store, args.file, args.version_id, args.kind, args.direction,
            args.parent, adaptation_plan_id=args.adaptation_plan_id,
        )
    elif args.command == "approve":
        result = approve_version(connection, args.version_id, args.candidate, args.manifest, args.actor)
    elif args.command == "revoke":
        result = revoke_version(connection, args.version_id, args.actor, args.reason)
    elif args.command == "select":
        result = select_approved(connection, args.direction, args.kind)
    elif args.command == "bind":
        result = bind_version(connection, args.application_id, args.version_id, args.actor)
    elif args.command == "lock":
        result = lock_materials(connection, args.application_id, args.actor, args.lock_id)
    else:
        result = status(connection)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
