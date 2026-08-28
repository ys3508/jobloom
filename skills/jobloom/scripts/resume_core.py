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
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from _common import require_table  # noqa: E402
from evidence_matcher import EVIDENCE_ORDER  # noqa: E402


RESUME_KINDS = {"master_source", "direction", "lightweight", "precision"}
RESUME_STATUSES = {"draft", "approved", "revoked", "superseded"}
SOURCE_MODES = {"generated", "direction_baseline", "user_provided"}
# Backward-compatible name for callers that imported the old local constant.
# The object itself is owned by evidence_matcher and has one definition only.
EVIDENCE_RANK = EVIDENCE_ORDER
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
            source_mode TEXT NOT NULL DEFAULT 'generated',
            baseline_plan_id TEXT,
            rendered_page_count INTEGER,
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

        CREATE TABLE IF NOT EXISTS resume_variants (
            variant_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            coverage_json TEXT NOT NULL,
            coverage_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            approved_at TEXT,
            approved_by TEXT,
            revoked_at TEXT,
            status_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS resume_variant_directions (
            variant_id TEXT NOT NULL,
            direction_id TEXT NOT NULL,
            profile_sha256 TEXT NOT NULL,
            weight_percent INTEGER NOT NULL,
            PRIMARY KEY (variant_id, direction_id),
            FOREIGN KEY (variant_id) REFERENCES resume_variants(variant_id)
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
    if "source_mode" not in version_columns:
        connection.execute(
            "ALTER TABLE resume_versions ADD COLUMN source_mode TEXT NOT NULL DEFAULT 'generated'"
        )
    if "baseline_plan_id" not in version_columns:
        connection.execute("ALTER TABLE resume_versions ADD COLUMN baseline_plan_id TEXT")
    if "rendered_page_count" not in version_columns:
        connection.execute("ALTER TABLE resume_versions ADD COLUMN rendered_page_count INTEGER")
    if "variant_id" not in version_columns:
        connection.execute("ALTER TABLE resume_versions ADD COLUMN variant_id TEXT")
    connection.commit()


def _require_safe_id(value: str, label: str) -> None:
    if not SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} must use only letters, numbers, dot, underscore, and hyphen")


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    # Two distinct uses: fail-closed checks that raise on False, and bootstrap gates
    # that legitimately skip enforcement before a store exists. Each call site says which.
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _require_baseline_plan(connection: sqlite3.Connection, plan_id: str | None, direction: str):
    if not _table_exists(connection, "baseline_plans"):
        raise RuntimeError("required safety table is missing: baseline_plans")
    import direction_core  # local import: direction_core imports resume_core at module load
    return direction_core.require_approved_baseline_plan(connection, plan_id, direction)


def _active_direction(connection: sqlite3.Connection, direction: str) -> sqlite3.Row | None:
    # Bootstrap gate, not a fail-open safety skip: before direction enforcement is
    # initialized there is no portfolio to enforce, and master-source resumes are still
    # legal. Once search_directions exists, every check below is mandatory.
    if not _table_exists(connection, "search_directions"):
        return None
    row = connection.execute(
        "SELECT * FROM search_directions WHERE direction_id=?", (direction,)
    ).fetchone()
    if not row or row["status"] != "approved":
        raise ValueError("resume direction is not user-approved")
    # Bootstrap gate: portfolio enforcement begins with the first registered portfolio.
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
    # Bootstrap gate: adaptation-plan enforcement begins once direction_core is initialized.
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
    require_table(connection, "candidate_snapshots")
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
    if version["source_mode"] == "generated":
        _approved_adaptation_plan(
            connection, version["adaptation_plan_id"], version["direction"], version["kind"],
            require_fresh_job=False,
        )
    elif version["source_mode"] == "direction_baseline":
        _require_baseline_plan(connection, version["baseline_plan_id"], version["direction"])
        # A direction baseline is one page by definition. The rule lives here so readiness
        # and fill acquisition cannot drift apart.
        if version["status"] == "approved" and version["rendered_page_count"] != 1:
            raise ValueError("approved direction_baseline resume is not recorded as a single page")


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


def _validate_parent(
    connection: sqlite3.Connection,
    kind: str,
    direction: str,
    parent_version_id: str | None,
    source_mode: str,
) -> None:
    if kind == "master_source":
        if parent_version_id:
            raise ValueError("master_source must not have a parent")
        return
    if source_mode == "user_provided":
        if kind != "direction":
            raise ValueError("only direction resumes may use user_provided source mode")
        if parent_version_id:
            raise ValueError("user-provided direction resume must not claim a generated parent")
        return
    if source_mode == "direction_baseline" and kind != "direction":
        raise ValueError("only direction resumes may use direction_baseline source mode")
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
    source_mode: str | None = None,
    baseline_plan_id: str | None = None,
) -> dict[str, Any]:
    _require_safe_id(version_id, "version_id")
    if kind not in RESUME_KINDS:
        raise ValueError("invalid resume kind")
    actual_source_mode = source_mode or ("user_provided" if kind == "master_source" else "generated")
    if actual_source_mode not in SOURCE_MODES:
        raise ValueError("invalid resume source mode")
    if kind == "master_source" and actual_source_mode != "user_provided":
        raise ValueError("master_source must use user_provided source mode")
    if actual_source_mode == "direction_baseline" and kind != "direction":
        raise ValueError("only direction resumes may use direction_baseline source mode")
    if not direction.strip():
        raise ValueError("direction is required")
    if not source_file.is_file():
        raise ValueError("resume source file not found")
    if connection.execute("SELECT 1 FROM resume_versions WHERE version_id=?", (version_id,)).fetchone():
        raise ValueError("resume version already exists")
    _validate_parent(connection, kind, direction, parent_version_id, actual_source_mode)
    direction_row = None
    plan = None
    if kind != "master_source":
        direction_row = _active_direction(connection, direction)
        if actual_source_mode == "generated":
            plan = _approved_adaptation_plan(connection, adaptation_plan_id, direction, kind)
            if plan and plan["base_resume_version_id"] != parent_version_id:
                raise ValueError("resume parent does not match the approved adaptation plan")
        elif actual_source_mode == "direction_baseline":
            if adaptation_plan_id:
                raise ValueError("direction_baseline resume must not bind a JobCard adaptation plan")
            baseline = _require_baseline_plan(connection, baseline_plan_id, direction)
            if baseline["master_version_id"] != parent_version_id:
                raise ValueError("direction_baseline parent must be the planned master resume")
        elif adaptation_plan_id:
            raise ValueError("user-provided direction resume must not bind an adaptation plan")
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
                direction_profile_sha256, source_mode, baseline_plan_id
            ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (version_id, parent_version_id, kind, direction, str(snapshot), digest, size,
              suffix[1:], timestamp, adaptation_plan_id,
              direction_row["profile_sha256"] if direction_row else None, actual_source_mode,
              baseline_plan_id))
        _event(
            connection, version_id, actor, "registered", "immutable_snapshot_created",
            metadata={"source_mode": actual_source_mode}, at=at,
        )
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
        "source_mode": actual_source_mode,
        "baseline_plan_id": baseline_plan_id,
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


def verify_snapshot_file_hash(row: sqlite3.Row) -> None:
    """A registered CandidateSnapshot must still be the exact file that was registered."""
    path = Path(row["snapshot_path"])
    if not path.is_file() or file_sha256(path) != row["file_sha256"]:
        raise ValueError("registered candidate snapshot hash mismatch")


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


def _approve_version(
    connection: sqlite3.Connection,
    version_id: str,
    candidate_path: Path,
    manifest_path: Path,
    actor: str,
    at: datetime | None = None,
    rendered_page_count: int | None = None,
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
    require_table(connection, "candidate_snapshots")
    snapshot = connection.execute(
        "SELECT * FROM candidate_snapshots WHERE content_sha256=? AND status='active' "
        "AND registered_by='user'", (candidate_hash,)
    ).fetchone()
    if not snapshot:
        raise ValueError("candidate profile is not the active user-registered snapshot")
    snapshot_path = Path(snapshot["snapshot_path"])
    if not snapshot_path.is_file() or file_sha256(snapshot_path) != snapshot["file_sha256"]:
        raise ValueError("registered candidate snapshot hash mismatch")
    # Bootstrap gate on search_directions; every check inside is mandatory once it exists.
    if row["kind"] != "master_source" and _table_exists(connection, "search_directions"):
        direction_row = _active_direction(connection, row["direction"])
        if row["direction_profile_sha256"] != direction_row["profile_sha256"]:
            raise ValueError("resume direction profile changed after registration")
        if row["source_mode"] == "generated":
            plan = _approved_adaptation_plan(
                connection, row["adaptation_plan_id"], row["direction"], row["kind"]
            )
            if not plan or plan["candidate_profile_sha256"] != candidate_hash:
                raise ValueError("adaptation plan candidate profile is stale")
        elif row["source_mode"] == "direction_baseline":
            baseline = _require_baseline_plan(connection, row["baseline_plan_id"], row["direction"])
            if baseline["candidate_profile_sha256"] != candidate_hash:
                raise ValueError("baseline plan candidate profile is stale")
            # One page is what a direction baseline is, and page count is a property of the
            # rendered file, so it is confirmed here rather than assumed from the plan.
            if rendered_page_count != 1:
                raise ValueError(
                    "direction_baseline approval requires a confirmed rendered_page_count of 1")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_claims_manifest(manifest, candidate)
    if row["source_mode"] == "direction_baseline":
        import direction_core  # local import: direction_core imports resume_core at module load
        planned = direction_core.baseline_plan_selected_fact_ids(baseline)
        claimed = {fact_id for claim in manifest["claims"] for fact_id in claim["fact_ids"]}
        if claimed != planned:
            raise ValueError(
                "claims manifest does not match the approved baseline selection; missing: "
                f"{sorted(planned - claimed)[:5] or 'none'}; not selected: "
                f"{sorted(claimed - planned)[:5] or 'none'}")
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
            claims_manifest_sha256=?, approved_at=?, approved_by=?, status_reason='user_approved',
            rendered_page_count=COALESCE(?, rendered_page_count)
        WHERE version_id=?
    """, (candidate_hash, str(manifest_snapshot), manifest_hash, timestamp, actor,
          rendered_page_count, version_id))
    _event(connection, version_id, actor, "approved", "claims_and_file_verified", at=at)
    return {"version_id": version_id, "status": "approved", "candidate_profile_sha256": candidate_hash,
            "claims_manifest_sha256": manifest_hash, "file_sha256": row["file_sha256"],
            "claims_manifest_path": str(manifest_snapshot)}


def approve_version(
    connection: sqlite3.Connection,
    version_id: str,
    candidate_path: Path,
    manifest_path: Path,
    actor: str,
    at: datetime | None = None,
    rendered_page_count: int | None = None,
) -> dict[str, Any]:
    """Approve one ResumeVersion. A variant approves its members in one transaction instead."""
    result = _approve_version(connection, version_id, candidate_path, manifest_path, actor,
                             at, rendered_page_count)
    connection.commit()
    return result


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
    if _table_exists(connection, "baseline_plans"):
        import direction_core  # local import: direction_core imports resume_core at module load
        direction_core.cascade_invalidate_baseline_plans(
            connection, "master_resume_revoked", master_version_id=version_id, at=at)
    _event(connection, version_id, actor, "revoked", reason, at=at)
    connection.commit()
    return {"version_id": version_id, "status": "revoked"}


VARIANT_COVERAGE_KEYS = {"direction_id", "profile_sha256", "weight_percent"}


def validate_variant_coverage(coverage: Any) -> list[dict[str, Any]]:
    """One resume, several directions, its own weights.

    A portfolio allocates applications across every direction the user pursues. A variant
    allocates one resume's own attention across the subset it can honestly answer, so the two
    weightings are separate and need not agree.
    """
    if not isinstance(coverage, list) or not 1 <= len(coverage) <= 20:
        raise ValueError("resume variant coverage requires between one and twenty directions")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for entry in coverage:
        if not isinstance(entry, dict) or set(entry) != VARIANT_COVERAGE_KEYS:
            raise ValueError("resume variant coverage entry has missing or unknown fields")
        direction_id = entry["direction_id"]
        _require_safe_id(direction_id, "direction_id")
        if direction_id in seen:
            raise ValueError("resume variant coverage direction IDs must be unique")
        seen.add(direction_id)
        digest = entry["profile_sha256"]
        if (not isinstance(digest, str) or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)):
            raise ValueError("resume variant coverage requires a lowercase SHA-256")
        weight = entry["weight_percent"]
        if isinstance(weight, bool) or not isinstance(weight, int) or not 1 <= weight <= 100:
            raise ValueError("resume variant weight_percent must be an integer from 1 to 100")
        total += weight
        normalized.append({"direction_id": direction_id, "profile_sha256": digest,
                           "weight_percent": weight})
    if total != 100:
        raise ValueError("resume variant weights must total 100")
    return sorted(normalized, key=lambda item: item["direction_id"])


def _member_version_id(variant_id: str, direction_id: str) -> str:
    version_id = f"{variant_id}--{direction_id}"
    if not SAFE_ID.fullmatch(version_id):
        raise ValueError("variant and direction IDs are too long to form a member version ID")
    return version_id


def register_variant(
    connection: sqlite3.Connection,
    variant_id: str,
    name: str,
    coverage: list[dict[str, Any]],
    source_file: Path,
    store: Path,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Register one user-provided resume as a draft member version per covered direction.

    Every member is a physical snapshot of the same bytes, so each direction keeps its own
    immutable artifact and every downstream selection, binding and lock stays per-direction.
    """
    _require_safe_id(variant_id, "variant_id")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ValueError("resume variant name is required and must be bounded")
    value = validate_variant_coverage(coverage)
    if connection.execute(
        "SELECT 1 FROM resume_variants WHERE variant_id=?", (variant_id,)
    ).fetchone():
        raise ValueError("resume variant already exists")
    # A variant is defined in terms of directions, so the registry is a hard dependency.
    require_table(connection, "search_directions")
    for entry in value:
        # Raises unless the direction is user-approved and inside the active portfolio.
        direction_row = _active_direction(connection, entry["direction_id"])
        if direction_row["profile_sha256"] != entry["profile_sha256"]:
            raise ValueError(f"covered direction profile has moved: {entry['direction_id']}")
    digest = canonical_hash(value)
    timestamp = (at or now_utc()).isoformat()
    members = []
    try:
        connection.execute("""
            INSERT INTO resume_variants (
                variant_id, name, coverage_json, coverage_sha256, status, created_at
            ) VALUES (?, ?, ?, ?, 'draft', ?)
        """, (variant_id, name.strip(), json.dumps(value, sort_keys=True, separators=(",", ":")),
              digest, timestamp))
        for entry in value:
            connection.execute("""
                INSERT INTO resume_variant_directions (
                    variant_id, direction_id, profile_sha256, weight_percent
                ) VALUES (?, ?, ?, ?)
            """, (variant_id, entry["direction_id"], entry["profile_sha256"],
                  entry["weight_percent"]))
            version_id = _member_version_id(variant_id, entry["direction_id"])
            register_version(
                connection, version_id=version_id, source_file=source_file, kind="direction",
                direction=entry["direction_id"], store=store, at=at, source_mode="user_provided",
            )
            connection.execute(
                "UPDATE resume_versions SET variant_id=? WHERE version_id=?",
                (variant_id, version_id))
            members.append(version_id)
        connection.commit()
    except Exception:
        connection.rollback()
        for version_id in members:
            directory = store.resolve() / version_id
            for path in sorted(directory.glob("*")) if directory.exists() else []:
                path.unlink()
            if directory.exists():
                directory.rmdir()
        raise
    return {"variant_id": variant_id, "status": "draft", "coverage_sha256": digest,
            "member_version_ids": members}


def approve_variant(
    connection: sqlite3.Connection,
    variant_id: str,
    candidate_path: Path,
    manifest_path: Path,
    actor: str,
    expected_coverage_sha256: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Approve every member of a variant in one transaction, or none of them.

    Each member still runs the full per-version approval: active candidate snapshot, unchanged
    direction profile, and a claims manifest whose every claim resolves to an available fact.
    """
    if actor != "user":
        raise ValueError("resume variant approval requires the user actor")
    row = connection.execute(
        "SELECT * FROM resume_variants WHERE variant_id=?", (variant_id,)).fetchone()
    if not row or row["status"] != "draft":
        raise ValueError("draft resume variant not found")
    if row["coverage_sha256"] != expected_coverage_sha256:
        raise ValueError("resume variant coverage hash does not match user-reviewed content")
    if canonical_hash(json.loads(row["coverage_json"])) != row["coverage_sha256"]:
        raise ValueError("stored resume variant coverage hash is invalid")
    members = [item["version_id"] for item in connection.execute(
        "SELECT version_id FROM resume_versions WHERE variant_id=? ORDER BY version_id",
        (variant_id,))]
    if not members:
        raise ValueError("resume variant has no member versions")
    written: list[Path] = []
    timestamp = (at or now_utc()).isoformat()
    try:
        results = []
        for version_id in members:
            result = _approve_version(connection, version_id, candidate_path, manifest_path,
                                      actor, at)
            written.append(Path(result["claims_manifest_path"]))
            results.append(result)
        connection.execute("""
            UPDATE resume_variants SET status='approved', approved_at=?, approved_by=?,
                status_reason='user_approved' WHERE variant_id=?
        """, (timestamp, actor, variant_id))
        connection.commit()
    except Exception:
        connection.rollback()
        for path in written:
            if path.exists():
                path.chmod(0o600)
                path.unlink()
        raise
    return {"variant_id": variant_id, "status": "approved",
            "coverage_sha256": row["coverage_sha256"],
            "approved_version_ids": members,
            "candidate_profile_sha256": results[0]["candidate_profile_sha256"]}


def revoke_variant(
    connection: sqlite3.Connection,
    variant_id: str,
    actor: str,
    reason: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Revoke a variant and every approved member with it, so coverage cannot outlive it."""
    if actor != "user":
        raise ValueError("resume variant revocation requires the user actor")
    if not reason.strip():
        raise ValueError("revocation reason is required")
    row = connection.execute(
        "SELECT * FROM resume_variants WHERE variant_id=?", (variant_id,)).fetchone()
    if not row or row["status"] != "approved":
        raise ValueError("approved resume variant not found")
    timestamp = (at or now_utc()).isoformat()
    revoked = []
    for item in connection.execute(
        "SELECT version_id FROM resume_versions WHERE variant_id=? AND status='approved' "
        "ORDER BY version_id", (variant_id,)
    ).fetchall():
        connection.execute("""
            UPDATE resume_versions SET status='revoked', revoked_at=?, status_reason=?
            WHERE version_id=?
        """, (timestamp, reason.strip(), item["version_id"]))
        _event(connection, item["version_id"], actor, "revoked", "variant_revoked", at=at)
        revoked.append(item["version_id"])
    connection.execute("""
        UPDATE resume_variants SET status='revoked', revoked_at=?, status_reason=?
        WHERE variant_id=?
    """, (timestamp, reason.strip(), variant_id))
    connection.commit()
    return {"variant_id": variant_id, "status": "revoked", "revoked_version_ids": revoked}


def select_approved(connection: sqlite3.Connection, direction: str, kind: str | None = None) -> dict[str, Any] | None:
    # Bootstrap gate on search_directions; see _active_direction.
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
        require_table(connection, "cover_letter_versions")
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
    register.add_argument("--baseline-plan-id")
    register.add_argument("--source-mode", choices=sorted(SOURCE_MODES))
    approve = commands.add_parser("approve")
    approve.add_argument("--version-id", required=True)
    approve.add_argument("--candidate", required=True, type=Path)
    approve.add_argument("--manifest", required=True, type=Path)
    approve.add_argument("--actor", required=True)
    approve.add_argument("--rendered-page-count", type=int)
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
    register_variant_parser = commands.add_parser("register-variant")
    register_variant_parser.add_argument("--file", required=True, type=Path)
    register_variant_parser.add_argument("--variant-id", required=True)
    register_variant_parser.add_argument("--name", required=True)
    register_variant_parser.add_argument("--coverage", required=True, type=Path)
    approve_variant_parser = commands.add_parser("approve-variant")
    approve_variant_parser.add_argument("--variant-id", required=True)
    approve_variant_parser.add_argument("--candidate", required=True, type=Path)
    approve_variant_parser.add_argument("--manifest", required=True, type=Path)
    approve_variant_parser.add_argument("--actor", required=True)
    approve_variant_parser.add_argument("--expected-coverage-sha256", required=True)
    revoke_variant_parser = commands.add_parser("revoke-variant")
    revoke_variant_parser.add_argument("--variant-id", required=True)
    revoke_variant_parser.add_argument("--actor", required=True)
    revoke_variant_parser.add_argument("--reason", required=True)
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
            source_mode=args.source_mode, baseline_plan_id=args.baseline_plan_id,
        )
    elif args.command == "approve":
        result = approve_version(connection, args.version_id, args.candidate, args.manifest,
                                 args.actor, rendered_page_count=args.rendered_page_count)
    elif args.command == "revoke":
        result = revoke_version(connection, args.version_id, args.actor, args.reason)
    elif args.command == "register-variant":
        coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
        result = register_variant(connection, args.variant_id, args.name,
                                  coverage.get("coverage", coverage), args.file, store)
    elif args.command == "approve-variant":
        result = approve_variant(connection, args.variant_id, args.candidate, args.manifest,
                                 args.actor, args.expected_coverage_sha256)
    elif args.command == "revoke-variant":
        result = revoke_variant(connection, args.variant_id, args.actor, args.reason)
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
