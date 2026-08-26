#!/usr/bin/env python3
"""Initialize Jobloom's private MVP backend and report evidence-based readiness."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import answer_library  # noqa: E402
import application_core  # noqa: E402
import archive_core  # noqa: E402
import candidate_core  # noqa: E402
import cover_letter_core  # noqa: E402
import direction_core  # noqa: E402
import fill_core  # noqa: E402
import outcome_core  # noqa: E402
import pre_submit_core  # noqa: E402
import resume_core  # noqa: E402


REQUIRED_TABLES = {
    "answers", "authorizations", "applications", "application_events", "submission_evidence",
    "resume_versions", "material_locks", "candidate_snapshots", "candidate_facts",
    "search_directions", "search_portfolios", "search_portfolio_directions",
    "portfolio_events", "resume_adaptation_plans", "cover_letter_versions",
    "application_fields", "submission_archives", "outcome_records", "model_usage_events",
    "user_time_events", "form_inventories", "pre_submit_reviews", "fill_sessions",
    "fill_steps", "fill_checkpoints",
}
PRIVATE_DIRECTORIES = (
    "candidates", "resumes", "cover-letters", "search-directions", "archive",
    "action-packages",
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def connect(path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def initialize(connection: sqlite3.Connection, private_root: Path, db_path: Path | None = None) -> dict[str, Any]:
    private_root.mkdir(parents=True, exist_ok=True)
    os.chmod(private_root, 0o700)
    for name in PRIVATE_DIRECTORIES:
        directory = private_root / name
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    for initializer in (
        application_core.initialize, answer_library.initialize, resume_core.initialize,
        candidate_core.initialize, direction_core.initialize, cover_letter_core.initialize,
        archive_core.initialize, outcome_core.initialize, pre_submit_core.initialize,
        fill_core.initialize,
    ):
        initializer(connection)
    connection.commit()
    if db_path and str(db_path) != ":memory:" and db_path.exists():
        os.chmod(db_path, 0o600)
    return {"status": "initialized", "private_root": str(private_root),
            "component_count": 10, "private_directories": list(PRIVATE_DIRECTORIES)}


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}


def _count(connection: sqlite3.Connection, query: str, parameters: tuple[Any, ...] = ()) -> int:
    return int(connection.execute(query, parameters).fetchone()[0])


def _reviewed_jobs(connection: sqlite3.Connection) -> int:
    count = 0
    for row in connection.execute("SELECT job_card_json FROM jobs"):
        try:
            if json.loads(row["job_card_json"]).get("requirements_reviewed") is True:
                count += 1
        except (json.JSONDecodeError, TypeError):
            continue
    return count


def _current_authorizations(connection: sqlite3.Connection, at: datetime) -> int:
    count = 0
    for row in connection.execute(
        "SELECT expires_at FROM authorizations WHERE status='active' AND revoked_at IS NULL"
    ):
        expires = answer_library.parse_time(row["expires_at"])
        if expires and at < expires:
            count += 1
    return count


def _approved_direction_resumes(connection: sqlite3.Connection) -> int:
    rows = connection.execute("""
        SELECT rv.*, cs.snapshot_path AS candidate_snapshot_path,
               cs.file_sha256 AS candidate_file_sha256
        FROM resume_versions rv
        JOIN search_directions sd ON sd.direction_id=rv.direction AND sd.status='approved'
        JOIN search_portfolio_directions spd
          ON spd.direction_id=sd.direction_id AND spd.profile_sha256=sd.profile_sha256
        JOIN search_portfolios sp
          ON sp.portfolio_id=spd.portfolio_id AND sp.status='approved' AND sp.approved_by='user'
        LEFT JOIN resume_adaptation_plans rap ON rap.plan_id=rv.adaptation_plan_id
        LEFT JOIN baseline_plans bp ON bp.plan_id=rv.baseline_plan_id
        JOIN candidate_snapshots cs ON cs.content_sha256=rv.candidate_profile_sha256 AND cs.status='active'
        WHERE rv.status='approved' AND rv.kind='direction'
          AND rv.approved_by='user' AND sd.approved_by='user'
          AND cs.registered_by='user'
          AND rv.direction_profile_sha256=sd.profile_sha256
          AND (
            (
              rv.source_mode='generated'
              AND rap.status='approved' AND rap.approved_by='user'
              AND rap.direction_id=rv.direction AND rap.recommended_kind='direction'
              AND rap.candidate_profile_sha256=rv.candidate_profile_sha256
              AND rap.direction_profile_sha256=sd.profile_sha256
              AND rap.base_resume_version_id=rv.parent_version_id
            )
            OR
            (
              rv.source_mode='direction_baseline'
              AND rv.adaptation_plan_id IS NULL
              AND bp.status='approved' AND bp.approved_by='user'
              AND bp.direction_id=rv.direction
              AND bp.direction_profile_sha256=sd.profile_sha256
              AND bp.candidate_profile_sha256=rv.candidate_profile_sha256
              AND bp.master_version_id=rv.parent_version_id
              AND bp.invalidated_at IS NULL
            )
            OR
            (
              rv.source_mode='user_provided'
              AND rv.adaptation_plan_id IS NULL AND rv.parent_version_id IS NULL
            )
          )
    """).fetchall()
    valid = 0
    for row in rows:
        try:
            resume_core.verify_version_file(row)
            candidate_path = Path(row["candidate_snapshot_path"])
            if (not candidate_path.is_file()
                    or resume_core.file_sha256(candidate_path) != row["candidate_file_sha256"]):
                continue
            valid += 1
        except ValueError:
            continue
    return valid


def _active_candidates(connection: sqlite3.Connection) -> int:
    valid = 0
    for row in connection.execute(
        "SELECT * FROM candidate_snapshots WHERE status='active' AND registered_by='user'"
    ):
        try:
            candidate_core.verify_snapshot_file(row)
            valid += 1
        except ValueError:
            continue
    return valid


def _approved_directions(connection: sqlite3.Connection) -> int:
    valid = 0
    for row in connection.execute(
        "SELECT profile_json, profile_sha256 FROM search_directions "
        "WHERE status='approved' AND approved_by='user'"
    ):
        try:
            if direction_core.canonical_hash(json.loads(row["profile_json"])) == row["profile_sha256"]:
                valid += 1
        except (json.JSONDecodeError, TypeError):
            continue
    return valid


def _approved_portfolios(connection: sqlite3.Connection) -> int:
    valid = 0
    for row in connection.execute(
        "SELECT portfolio_json, portfolio_sha256 FROM search_portfolios "
        "WHERE status='approved' AND approved_by='user'"
    ):
        try:
            value = json.loads(row["portfolio_json"])
            normalized = direction_core.validate_portfolio(value)
            if direction_core.canonical_hash(normalized) == row["portfolio_sha256"]:
                valid += 1
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return valid


def _fill_queue(connection: sqlite3.Connection) -> int:
    valid = 0
    for row in connection.execute(
        "SELECT application_id FROM applications WHERE state IN ('ready_to_fill','filling')"
    ):
        try:
            application_core.require_active_material_lock(connection, row["application_id"])
            valid += 1
        except ValueError:
            continue
    return valid


def status(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = _tables(connection)
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        return {"initialized": False, "missing_tables": missing}
    return {
        "initialized": True,
        "candidate": candidate_core.status(connection),
        "directions": direction_core.status(connection),
        "resumes": resume_core.status(connection),
        "cover_letters": cover_letter_core.status(connection),
        "applications": application_core.status(connection),
        "answers": {
            "entries": _count(connection, "SELECT COUNT(*) FROM answers"),
            "active_entries": _count(connection, "SELECT COUNT(*) FROM answers WHERE status='active'"),
            "authorizations": _count(connection, "SELECT COUNT(*) FROM authorizations"),
        },
        "fill": fill_core.status(connection),
        "pre_submit": pre_submit_core.status(connection),
        "archive": archive_core.status(connection),
        "outcomes": outcome_core.status(connection),
    }


def readiness(connection: sqlite3.Connection, private_root: Path,
              at: datetime | None = None) -> dict[str, Any]:
    current_time = at or now_utc()
    tables = _tables(connection)
    missing_tables = sorted(REQUIRED_TABLES - tables)
    directory_issues = []
    if not private_root.is_dir() or private_root.stat().st_mode & 0o077:
        directory_issues.append("private_root_permissions")
    for name in PRIVATE_DIRECTORIES:
        directory = private_root / name
        if not directory.is_dir() or directory.stat().st_mode & 0o077:
            directory_issues.append(f"private_directory_permissions:{name}")
    implementation_ready = not missing_tables and not directory_issues
    counts = {
        "active_candidate_snapshots": 0,
        "approved_search_portfolios": 0,
        "approved_search_directions": 0,
        "approved_direction_resumes": 0,
        "active_standing_authorizations": 0,
        "reviewed_jobs": 0,
        "fill_queue_applications": 0,
    }
    if not missing_tables:
        counts.update({
            "active_candidate_snapshots": _active_candidates(connection),
            "approved_search_portfolios": _approved_portfolios(connection),
            "approved_search_directions": _approved_directions(connection),
            "approved_direction_resumes": _approved_direction_resumes(connection),
            "active_standing_authorizations": _current_authorizations(connection, current_time),
            "reviewed_jobs": _reviewed_jobs(connection),
            "fill_queue_applications": _fill_queue(connection),
        })
    onboarding_blockers = []
    if counts["active_candidate_snapshots"] != 1:
        onboarding_blockers.append("active_user_registered_candidate_required")
    if counts["approved_search_portfolios"] != 1:
        onboarding_blockers.append("user_approved_search_portfolio_required")
    if counts["approved_direction_resumes"] < 1:
        onboarding_blockers.append("user_approved_direction_resume_required")
    onboarding_ready = implementation_ready and not onboarding_blockers
    live_job_blockers = list(onboarding_blockers)
    if counts["reviewed_jobs"] < 1:
        live_job_blockers.append("real_user_reviewed_job_required")
    live_job_ready = implementation_ready and not live_job_blockers
    fill_blockers = list(live_job_blockers)
    if counts["active_standing_authorizations"] < 1:
        fill_blockers.append("current_standing_authorization_required_scope_rechecked_per_application")
    if counts["fill_queue_applications"] < 1:
        fill_blockers.append("approved_material_locked_application_required")
    fill_queue_ready = implementation_ready and not fill_blockers
    return {
        "schema_version": "0.1.0", "checked_at": current_time.isoformat(),
        "implementation": {"ready": implementation_ready, "missing_tables": missing_tables,
                           "permission_issues": directory_issues},
        "onboarding": {"ready": onboarding_ready, "blockers": onboarding_blockers},
        "live_job_evaluation": {"ready": live_job_ready, "blockers": live_job_blockers},
        "fill_queue": {"ready": fill_queue_ready, "blockers": fill_blockers},
        "counts": counts,
        "safety": {
            "submission_authorized": False,
            "reason": "readiness never grants application or submission approval",
            "requires_real_user_owned_inputs_and_approvals": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--private-root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("status")
    readiness_parser = commands.add_parser("readiness")
    readiness_parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    private_root = args.private_root or args.db.parent
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    if args.command == "init":
        result = initialize(connection, private_root, args.db)
    elif args.command == "status":
        result = status(connection)
    else:
        result = readiness(connection, private_root)
        if args.output:
            if args.output.exists():
                raise ValueError("readiness output already exists")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.chmod(args.output, 0o600)
            result = {"status": "written", "output": str(args.output),
                      "implementation_ready": result["implementation"]["ready"]}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
