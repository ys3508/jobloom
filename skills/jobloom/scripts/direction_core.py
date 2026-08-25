#!/usr/bin/env python3
"""User-approved search directions and evidence-constrained resume adaptation plans."""

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
import evaluate_job  # noqa: E402
import resume_core  # noqa: E402


PROFILE_KEYS = {
    "schema_version", "direction_id", "name", "role_family", "target_titles",
    "positive_keywords", "negative_keywords", "precision_keywords", "criteria",
    "parent_direction_id",
}
CRITERIA_KEYS = {
    "countries", "locations", "work_arrangements", "employment_types", "industries",
    "target_companies", "excluded_companies", "salary_floor", "salary_currency",
    "seniority", "travel_limit", "company_sizes",
}
LIST_CRITERIA = CRITERIA_KEYS - {"salary_floor", "salary_currency", "travel_limit"}
PORTFOLIO_KEYS = {"schema_version", "portfolio_id", "name", "allocations"}
ALLOCATION_KEYS = {"direction_id", "profile_sha256", "weight_percent"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(value: Any) -> str:
    return resume_core.canonical_hash(value)


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
        CREATE TABLE IF NOT EXISTS search_directions (
            direction_id TEXT PRIMARY KEY,
            parent_direction_id TEXT,
            name TEXT NOT NULL,
            role_family TEXT NOT NULL,
            profile_json TEXT NOT NULL,
            profile_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            approved_at TEXT,
            approved_by TEXT,
            revoked_at TEXT,
            status_reason TEXT,
            FOREIGN KEY (parent_direction_id) REFERENCES search_directions(direction_id)
        );
        CREATE INDEX IF NOT EXISTS search_direction_status_idx
            ON search_directions(status, approved_at);

        CREATE TABLE IF NOT EXISTS search_portfolios (
            portfolio_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            portfolio_json TEXT NOT NULL,
            portfolio_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            approved_at TEXT,
            approved_by TEXT,
            revoked_at TEXT,
            status_reason TEXT
        );
        CREATE INDEX IF NOT EXISTS search_portfolio_status_idx
            ON search_portfolios(status, approved_at);

        CREATE TABLE IF NOT EXISTS search_portfolio_directions (
            portfolio_id TEXT NOT NULL,
            direction_id TEXT NOT NULL,
            profile_sha256 TEXT NOT NULL,
            weight_percent INTEGER NOT NULL,
            PRIMARY KEY (portfolio_id, direction_id),
            FOREIGN KEY (portfolio_id) REFERENCES search_portfolios(portfolio_id),
            FOREIGN KEY (direction_id) REFERENCES search_directions(direction_id)
        );

        CREATE TABLE IF NOT EXISTS portfolio_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS resume_adaptation_plans (
            plan_id TEXT PRIMARY KEY,
            direction_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            base_resume_version_id TEXT NOT NULL,
            candidate_profile_sha256 TEXT NOT NULL,
            direction_profile_sha256 TEXT NOT NULL,
            job_card_sha256 TEXT NOT NULL,
            recommended_kind TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            plan_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            approved_at TEXT,
            approved_by TEXT,
            invalidated_at TEXT,
            invalidation_reason TEXT,
            FOREIGN KEY (direction_id) REFERENCES search_directions(direction_id),
            FOREIGN KEY (job_id) REFERENCES jobs(job_id),
            FOREIGN KEY (base_resume_version_id) REFERENCES resume_versions(version_id)
        );
        CREATE INDEX IF NOT EXISTS adaptation_plan_selection_idx
            ON resume_adaptation_plans(direction_id, job_id, status, created_at);

        CREATE TABLE IF NOT EXISTS direction_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            direction_id TEXT NOT NULL,
            plan_id TEXT,
            created_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );
    """)
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resume_versions'"
    ).fetchone():
        columns = {row[1] for row in connection.execute("PRAGMA table_info(resume_versions)")}
        if "adaptation_plan_id" not in columns:
            connection.execute("ALTER TABLE resume_versions ADD COLUMN adaptation_plan_id TEXT")
        if "direction_profile_sha256" not in columns:
            connection.execute("ALTER TABLE resume_versions ADD COLUMN direction_profile_sha256 TEXT")
    connection.commit()


def _event(
    connection: sqlite3.Connection,
    direction_id: str,
    actor: str,
    event_type: str,
    reason_code: str,
    plan_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> None:
    connection.execute(
        "INSERT INTO direction_events (direction_id, plan_id, created_at, actor, event_type, "
        "reason_code, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (direction_id, plan_id, (at or now_utc()).isoformat(), actor, event_type, reason_code,
         canonical_json(metadata or {})),
    )


def _portfolio_event(
    connection: sqlite3.Connection,
    portfolio_id: str,
    actor: str,
    event_type: str,
    reason_code: str,
    metadata: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> None:
    connection.execute(
        "INSERT INTO portfolio_events (portfolio_id, created_at, actor, event_type, "
        "reason_code, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
        (portfolio_id, (at or now_utc()).isoformat(), actor, event_type, reason_code,
         canonical_json(metadata or {})),
    )


def _bounded_strings(value: Any, label: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list) or (required and not value) or len(value) > 100:
        raise ValueError(f"{label} must be a bounded list" + (" with at least one value" if required else ""))
    if any(not isinstance(item, str) or not item.strip() or len(item) > 200 for item in value):
        raise ValueError(f"{label} values must be non-empty strings up to 200 characters")
    normalized = [item.strip() for item in value]
    if len({item.casefold() for item in normalized}) != len(normalized):
        raise ValueError(f"{label} values must be unique")
    return normalized


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict) or set(profile) != PROFILE_KEYS:
        raise ValueError("direction profile has missing or unknown fields")
    resume_core._require_safe_id(profile["direction_id"], "direction_id")
    if profile["schema_version"] != "0.1.0":
        raise ValueError("unsupported direction profile schema_version")
    for field in ("name", "role_family"):
        if not isinstance(profile[field], str) or not profile[field].strip() or len(profile[field]) > 200:
            raise ValueError(f"direction {field} is required and must be bounded")
    parent = profile["parent_direction_id"]
    if parent is not None:
        resume_core._require_safe_id(parent, "parent_direction_id")
        if parent == profile["direction_id"]:
            raise ValueError("direction cannot be its own parent")
    criteria = profile["criteria"]
    if not isinstance(criteria, dict) or set(criteria) - CRITERIA_KEYS:
        raise ValueError("direction criteria contains unsupported fields")
    normalized_criteria: dict[str, Any] = {}
    for key, item in criteria.items():
        if key in LIST_CRITERIA:
            normalized_criteria[key] = _bounded_strings(item, f"criteria.{key}")
        elif key in {"salary_floor", "travel_limit"}:
            if item is not None and (isinstance(item, bool) or not isinstance(item, (int, float)) or item < 0):
                raise ValueError(f"criteria.{key} must be a non-negative number or null")
            normalized_criteria[key] = item
        else:
            if item is not None and (not isinstance(item, str) or not item.strip() or len(item) > 16):
                raise ValueError("criteria.salary_currency must be a short string or null")
            normalized_criteria[key] = item.strip().upper() if isinstance(item, str) else None
    normalized = dict(profile)
    normalized["name"] = profile["name"].strip()
    normalized["role_family"] = profile["role_family"].strip()
    for field in ("target_titles", "positive_keywords", "negative_keywords", "precision_keywords"):
        normalized[field] = _bounded_strings(profile[field], field, required=field == "target_titles")
    normalized["criteria"] = normalized_criteria
    return normalized


def register_direction(connection: sqlite3.Connection, profile: dict[str, Any],
                       at: datetime | None = None) -> dict[str, Any]:
    value = validate_profile(profile)
    direction_id = value["direction_id"]
    if connection.execute(
        "SELECT 1 FROM search_directions WHERE direction_id=?", (direction_id,)
    ).fetchone():
        raise ValueError("search direction already exists")
    if value["parent_direction_id"]:
        parent = connection.execute(
            "SELECT status FROM search_directions WHERE direction_id=?", (value["parent_direction_id"],)
        ).fetchone()
        if not parent or parent["status"] != "approved":
            raise ValueError("parent search direction must be approved")
    digest = canonical_hash(value)
    timestamp = (at or now_utc()).isoformat()
    connection.execute("""
        INSERT INTO search_directions (
            direction_id, parent_direction_id, name, role_family, profile_json,
            profile_sha256, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)
    """, (direction_id, value["parent_direction_id"], value["name"], value["role_family"],
          canonical_json(value), digest, timestamp))
    _event(connection, direction_id, "system", "registered", "immutable_direction_profile_created",
           metadata={"profile_sha256": digest}, at=at)
    connection.commit()
    return {"direction_id": direction_id, "status": "draft", "profile_sha256": digest}


def approve_direction(connection: sqlite3.Connection, direction_id: str, actor: str,
                      expected_profile_sha256: str, at: datetime | None = None) -> dict[str, Any]:
    if actor != "user":
        raise ValueError("search direction approval requires the user actor")
    row = connection.execute(
        "SELECT * FROM search_directions WHERE direction_id=?", (direction_id,)
    ).fetchone()
    if not row or row["status"] != "draft":
        raise ValueError("draft search direction not found")
    if row["profile_sha256"] != expected_profile_sha256:
        raise ValueError("direction profile hash does not match user-reviewed content")
    if canonical_hash(json.loads(row["profile_json"])) != row["profile_sha256"]:
        raise ValueError("stored direction profile hash is invalid")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE search_directions SET status='approved', approved_at=?, approved_by='user', "
        "status_reason='user_approved' WHERE direction_id=?",
        (timestamp, direction_id),
    )
    _event(connection, direction_id, actor, "approved", "user_approved_exact_profile",
           metadata={"profile_sha256": row["profile_sha256"]}, at=at)
    connection.commit()
    return {"direction_id": direction_id, "status": "approved",
            "profile_sha256": row["profile_sha256"]}


def validate_portfolio(portfolio: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(portfolio, dict) or set(portfolio) != PORTFOLIO_KEYS:
        raise ValueError("search portfolio has missing or unknown fields")
    if portfolio.get("schema_version") != "0.1.0":
        raise ValueError("unsupported search portfolio schema_version")
    portfolio_id = portfolio.get("portfolio_id")
    resume_core._require_safe_id(portfolio_id, "portfolio_id")
    name = portfolio.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ValueError("search portfolio name is required and must be bounded")
    allocations = portfolio.get("allocations")
    if not isinstance(allocations, list) or not 1 <= len(allocations) <= 20:
        raise ValueError("search portfolio requires between one and twenty allocations")
    normalized_allocations = []
    seen = set()
    total = 0
    for allocation in allocations:
        if not isinstance(allocation, dict) or set(allocation) != ALLOCATION_KEYS:
            raise ValueError("portfolio allocation has missing or unknown fields")
        direction_id = allocation.get("direction_id")
        resume_core._require_safe_id(direction_id, "direction_id")
        if direction_id in seen:
            raise ValueError("portfolio direction IDs must be unique")
        seen.add(direction_id)
        profile_sha256 = allocation.get("profile_sha256")
        if (not isinstance(profile_sha256, str) or len(profile_sha256) != 64
                or any(character not in "0123456789abcdef" for character in profile_sha256)):
            raise ValueError("portfolio allocation requires a lowercase SHA-256")
        weight = allocation.get("weight_percent")
        if isinstance(weight, bool) or not isinstance(weight, int) or not 1 <= weight <= 100:
            raise ValueError("portfolio allocation weight_percent must be an integer from 1 to 100")
        total += weight
        normalized_allocations.append({
            "direction_id": direction_id,
            "profile_sha256": profile_sha256,
            "weight_percent": weight,
        })
    if total != 100:
        raise ValueError("search portfolio weights must total exactly 100")
    return {
        "schema_version": "0.1.0",
        "portfolio_id": portfolio_id,
        "name": name.strip(),
        "allocations": normalized_allocations,
    }


def register_portfolio(connection: sqlite3.Connection, portfolio: dict[str, Any],
                       at: datetime | None = None) -> dict[str, Any]:
    value = validate_portfolio(portfolio)
    portfolio_id = value["portfolio_id"]
    if connection.execute(
        "SELECT 1 FROM search_portfolios WHERE portfolio_id=?", (portfolio_id,)
    ).fetchone():
        raise ValueError("search portfolio already exists")
    direction_rows = {}
    for allocation in value["allocations"]:
        row = connection.execute(
            "SELECT * FROM search_directions WHERE direction_id=?",
            (allocation["direction_id"],),
        ).fetchone()
        if not row or row["status"] not in {"draft", "approved"}:
            raise ValueError("portfolio directions must exist as draft or approved profiles")
        if row["profile_sha256"] != allocation["profile_sha256"]:
            raise ValueError("portfolio direction hash does not match the registered profile")
        if canonical_hash(json.loads(row["profile_json"])) != row["profile_sha256"]:
            raise ValueError("portfolio direction profile hash is invalid")
        direction_rows[allocation["direction_id"]] = row
    digest = canonical_hash(value)
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "INSERT INTO search_portfolios (portfolio_id, name, portfolio_json, portfolio_sha256, "
        "status, created_at) VALUES (?, ?, ?, ?, 'draft', ?)",
        (portfolio_id, value["name"], canonical_json(value), digest, timestamp),
    )
    for allocation in value["allocations"]:
        connection.execute(
            "INSERT INTO search_portfolio_directions (portfolio_id, direction_id, "
            "profile_sha256, weight_percent) VALUES (?, ?, ?, ?)",
            (portfolio_id, allocation["direction_id"], allocation["profile_sha256"],
             allocation["weight_percent"]),
        )
    _portfolio_event(
        connection, portfolio_id, "system", "registered", "immutable_search_portfolio_created",
        {"portfolio_sha256": digest, "direction_count": len(direction_rows)}, at,
    )
    connection.commit()
    return {"portfolio_id": portfolio_id, "status": "draft", "portfolio_sha256": digest,
            "direction_count": len(direction_rows), "total_weight_percent": 100}


def approve_portfolio(connection: sqlite3.Connection, portfolio_id: str, actor: str,
                      expected_portfolio_sha256: str,
                      at: datetime | None = None) -> dict[str, Any]:
    if actor != "user":
        raise ValueError("search portfolio approval requires the user actor")
    row = connection.execute(
        "SELECT * FROM search_portfolios WHERE portfolio_id=?", (portfolio_id,)
    ).fetchone()
    if not row or row["status"] != "draft":
        raise ValueError("draft search portfolio not found")
    if row["portfolio_sha256"] != expected_portfolio_sha256:
        raise ValueError("search portfolio hash does not match user-reviewed content")
    value = json.loads(row["portfolio_json"])
    if canonical_hash(value) != row["portfolio_sha256"]:
        raise ValueError("stored search portfolio hash is invalid")
    allocations = connection.execute(
        "SELECT * FROM search_portfolio_directions WHERE portfolio_id=? ORDER BY direction_id",
        (portfolio_id,),
    ).fetchall()
    expected = {item["direction_id"]: item for item in value["allocations"]}
    if len(allocations) != len(expected):
        raise ValueError("stored search portfolio allocations are incomplete")
    direction_rows = []
    for allocation in allocations:
        item = expected.get(allocation["direction_id"])
        if (not item or item["profile_sha256"] != allocation["profile_sha256"]
                or item["weight_percent"] != allocation["weight_percent"]):
            raise ValueError("stored search portfolio allocation does not match its hash")
        direction = connection.execute(
            "SELECT * FROM search_directions WHERE direction_id=?",
            (allocation["direction_id"],),
        ).fetchone()
        if (not direction or direction["status"] not in {"draft", "approved"}
                or direction["profile_sha256"] != allocation["profile_sha256"]
                or canonical_hash(json.loads(direction["profile_json"])) != direction["profile_sha256"]):
            raise ValueError("search portfolio contains a stale direction")
        direction_rows.append(direction)
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE search_portfolios SET status='superseded', status_reason='new_portfolio_approved' "
        "WHERE status='approved' AND portfolio_id<>?", (portfolio_id,),
    )
    for direction in direction_rows:
        if direction["status"] == "draft":
            connection.execute(
                "UPDATE search_directions SET status='approved', approved_at=?, approved_by='user', "
                "status_reason='user_approved_via_portfolio' WHERE direction_id=?",
                (timestamp, direction["direction_id"]),
            )
            _event(
                connection, direction["direction_id"], actor, "approved",
                "user_approved_exact_profile_via_portfolio",
                metadata={"profile_sha256": direction["profile_sha256"],
                          "portfolio_id": portfolio_id}, at=at,
            )
    connection.execute(
        "UPDATE search_portfolios SET status='approved', approved_at=?, approved_by='user', "
        "status_reason='user_approved_exact_portfolio' WHERE portfolio_id=?",
        (timestamp, portfolio_id),
    )
    _portfolio_event(
        connection, portfolio_id, actor, "approved", "user_approved_exact_portfolio",
        {"portfolio_sha256": row["portfolio_sha256"], "direction_count": len(direction_rows)}, at,
    )
    connection.commit()
    return {"portfolio_id": portfolio_id, "status": "approved",
            "portfolio_sha256": row["portfolio_sha256"],
            "direction_count": len(direction_rows), "total_weight_percent": 100}


def _direction_in_active_portfolio(connection: sqlite3.Connection, direction_id: str,
                                   profile_sha256: str) -> bool:
    portfolio_count = connection.execute("SELECT COUNT(*) FROM search_portfolios").fetchone()[0]
    if portfolio_count == 0:
        return True
    return connection.execute("""
        SELECT 1 FROM search_portfolio_directions pd
        JOIN search_portfolios p ON p.portfolio_id=pd.portfolio_id
        WHERE p.status='approved' AND pd.direction_id=? AND pd.profile_sha256=?
    """, (direction_id, profile_sha256)).fetchone() is not None


def _title_in_direction(profile: dict[str, Any], title: str) -> bool:
    actual = " ".join(title.casefold().split())
    return any(
        (target := " ".join(item.casefold().split())) in actual or actual in target
        for item in profile["target_titles"]
    )


def _base_resume(connection: sqlite3.Connection, direction_id: str) -> sqlite3.Row:
    row = connection.execute("""
        SELECT * FROM resume_versions
        WHERE direction=? AND kind='direction' AND status='approved'
        ORDER BY approved_at DESC LIMIT 1
    """, (direction_id,)).fetchone()
    if row:
        resume_core.verify_version_file(row)
        resume_core._require_resume_authorized_for_application(connection, row)
        return row
    row = connection.execute("""
        SELECT * FROM resume_versions WHERE kind='master_source' AND status='approved'
        ORDER BY approved_at DESC LIMIT 1
    """).fetchone()
    if not row:
        raise ValueError("an approved master resume is required")
    resume_core.verify_version_file(row)
    return row


def generate_plan(
    connection: sqlite3.Connection,
    plan_id: str,
    direction_id: str,
    job_id: str,
    candidate_path: Path,
    at: datetime | None = None,
) -> dict[str, Any]:
    resume_core._require_safe_id(plan_id, "plan_id")
    if connection.execute("SELECT 1 FROM resume_adaptation_plans WHERE plan_id=?", (plan_id,)).fetchone():
        raise ValueError("resume adaptation plan already exists")
    direction = connection.execute(
        "SELECT * FROM search_directions WHERE direction_id=?", (direction_id,)
    ).fetchone()
    if not direction or direction["status"] != "approved":
        raise ValueError("approved search direction not found")
    if not _direction_in_active_portfolio(
        connection, direction_id, direction["profile_sha256"]
    ):
        raise ValueError("search direction is outside the active approved portfolio")
    job_row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if not job_row:
        raise ValueError("job not found")
    job = json.loads(job_row["job_card_json"])
    if not job.get("requirements_reviewed"):
        raise ValueError("resume planning requires a user-reviewed JobCard")
    profile = json.loads(direction["profile_json"])
    if not _title_in_direction(profile, job["title"]):
        raise ValueError("job title is outside the approved search direction")
    candidate, candidate_hash = resume_core.load_valid_candidate(candidate_path)
    evaluation = evaluate_job.evaluate(candidate, job)
    if evaluation["eligibility"] != "pass" or evaluation["action"] not in {"broad", "precision"}:
        raise ValueError("resume adaptation is blocked until job eligibility and evidence are resolved")
    base = _base_resume(connection, direction_id)
    claims_text = ""
    if base["claims_manifest_path"]:
        manifest = json.loads(Path(base["claims_manifest_path"]).read_text(encoding="utf-8"))
        claims_text = " ".join(claim["claim_text"].casefold() for claim in manifest["claims"])
    evidence = evaluation["evidence_matches"]
    supported = [item for item in evidence if item["strength"] in {"direct", "strongly_related"}]
    transferable = [item for item in evidence if item["strength"] == "transferable"]
    unsupported = [item for item in evidence if item["strength"] in {"none", "mention_only"}]
    missing_supported_terms = [
        item["requirement"] for item in supported if item["requirement"].casefold() not in claims_text
    ]
    if base["kind"] == "master_source":
        recommended_kind = "direction"
    elif evaluation["action"] == "precision":
        recommended_kind = "precision"
    elif missing_supported_terms:
        recommended_kind = "lightweight"
    else:
        recommended_kind = "direct_reuse"
    reorder_fact_ids = list(dict.fromkeys(
        fact_id for item in supported for fact_id in item["fact_ids"]
    ))
    plan = {
        "schema_version": "0.1.0", "plan_id": plan_id, "direction_id": direction_id,
        "job_id": job_id, "base_resume_version_id": base["version_id"],
        "recommended_kind": recommended_kind,
        "job_identity": {"employer": job["employer"], "title": job["title"]},
        "evidence": evidence,
        "proposed_changes": {
            "reorder_or_emphasize_fact_ids": reorder_fact_ids,
            "supported_terminology": [item["requirement"] for item in supported],
            "terms_missing_from_base": missing_supported_terms,
            "transferable_only": [item["requirement"] for item in transferable],
            "unsupported_or_mention_only": [item["requirement"] for item in unsupported],
            "removed_or_compressed_content": [],
        },
        "review": {
            "material_change_required": recommended_kind != "direct_reuse",
            "claims_requiring_attention": [item["requirement"] for item in transferable + unsupported],
            "locked_fact_ids_must_remain_exact": sorted(
                fact["id"] for fact in candidate["facts"] if fact.get("locked")
            ),
        },
        "constraints": {
            "allowed": ["reorder", "emphasize", "compress", "clarify", "supported_terminology"],
            "forbidden": ["unsupported_skill", "invented_achievement", "changed_date",
                          "inflated_seniority", "invented_management", "transferable_as_direct",
                          "changed_work_authorization"],
        },
    }
    digest = canonical_hash(plan)
    timestamp = (at or now_utc()).isoformat()
    connection.execute("""
        INSERT INTO resume_adaptation_plans (
            plan_id, direction_id, job_id, base_resume_version_id, candidate_profile_sha256,
            direction_profile_sha256, job_card_sha256, recommended_kind, plan_json,
            plan_sha256, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'generated', ?)
    """, (plan_id, direction_id, job_id, base["version_id"], candidate_hash,
          direction["profile_sha256"], canonical_hash(job), recommended_kind,
          canonical_json(plan), digest, timestamp))
    _event(connection, direction_id, "system", "plan_generated", "deterministic_evidence_plan_created",
           plan_id, {"plan_sha256": digest, "recommended_kind": recommended_kind,
                     "evidence_count": len(evidence)}, at)
    connection.commit()
    return {"plan_id": plan_id, "status": "generated", "plan_sha256": digest, "plan": plan}


def approve_plan(connection: sqlite3.Connection, plan_id: str, candidate_path: Path,
                 actor: str, expected_plan_sha256: str,
                 at: datetime | None = None) -> dict[str, Any]:
    if actor != "user":
        raise ValueError("resume adaptation approval requires the user actor")
    plan = connection.execute(
        "SELECT * FROM resume_adaptation_plans WHERE plan_id=?", (plan_id,)
    ).fetchone()
    if not plan or plan["status"] != "generated":
        raise ValueError("generated resume adaptation plan not found")
    if plan["plan_sha256"] != expected_plan_sha256:
        raise ValueError("adaptation plan hash does not match user-reviewed content")
    if canonical_hash(json.loads(plan["plan_json"])) != plan["plan_sha256"]:
        raise ValueError("stored adaptation plan hash is invalid")
    candidate, candidate_hash = resume_core.load_valid_candidate(candidate_path)
    if candidate_hash != plan["candidate_profile_sha256"]:
        raise ValueError("adaptation plan candidate profile is stale")
    direction = connection.execute(
        "SELECT * FROM search_directions WHERE direction_id=?", (plan["direction_id"],)
    ).fetchone()
    if (not direction or direction["status"] != "approved"
            or direction["profile_sha256"] != plan["direction_profile_sha256"]):
        raise ValueError("adaptation plan direction is stale")
    if not _direction_in_active_portfolio(
        connection, plan["direction_id"], plan["direction_profile_sha256"]
    ):
        raise ValueError("adaptation plan direction is outside the active approved portfolio")
    job = connection.execute("SELECT job_card_json FROM jobs WHERE job_id=?", (plan["job_id"],)).fetchone()
    if not job or canonical_hash(json.loads(job["job_card_json"])) != plan["job_card_sha256"]:
        raise ValueError("adaptation plan JobCard is stale")
    base = connection.execute(
        "SELECT * FROM resume_versions WHERE version_id=?", (plan["base_resume_version_id"],)
    ).fetchone()
    if not base or base["status"] != "approved":
        raise ValueError("adaptation plan base resume is no longer approved")
    resume_core.verify_version_file(base)
    if base["kind"] != "master_source":
        resume_core._require_resume_authorized_for_application(connection, base)
    if any(fact.get("status") not in {"confirmed", "locked"} for fact in candidate["facts"]):
        raise ValueError("candidate profile contains unusable facts")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE resume_adaptation_plans SET status='approved', approved_at=?, approved_by='user' "
        "WHERE plan_id=?", (timestamp, plan_id),
    )
    _event(connection, plan["direction_id"], actor, "plan_approved", "user_approved_exact_plan",
           plan_id, {"plan_sha256": plan["plan_sha256"]}, at)
    connection.commit()
    return {"plan_id": plan_id, "status": "approved", "recommended_kind": plan["recommended_kind"],
            "plan_sha256": plan["plan_sha256"]}


def revoke_direction(connection: sqlite3.Connection, direction_id: str, actor: str,
                     reason: str, at: datetime | None = None) -> dict[str, Any]:
    if actor != "user":
        raise ValueError("search direction revocation requires the user actor")
    if not reason.strip():
        raise ValueError("revocation reason is required")
    row = connection.execute(
        "SELECT status FROM search_directions WHERE direction_id=?", (direction_id,)
    ).fetchone()
    if not row or row["status"] != "approved":
        raise ValueError("approved search direction not found")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE search_directions SET status='revoked', revoked_at=?, status_reason=? "
        "WHERE direction_id=?", (timestamp, reason, direction_id),
    )
    connection.execute(
        "UPDATE resume_adaptation_plans SET status='invalidated', invalidated_at=?, "
        "invalidation_reason='direction_revoked' WHERE direction_id=? AND status IN ('generated','approved')",
        (timestamp, direction_id),
    )
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='material_locks'"
    ).fetchone():
        connection.execute("""
            UPDATE material_locks SET invalidated_at=?, invalidation_reason='direction_revoked'
            WHERE resume_version_id IN (
                SELECT version_id FROM resume_versions WHERE direction=?
            ) AND invalidated_at IS NULL
        """, (timestamp, direction_id))
    _event(connection, direction_id, actor, "revoked", reason, at=at)
    connection.commit()
    return {"direction_id": direction_id, "status": "revoked"}


def revoke_portfolio(connection: sqlite3.Connection, portfolio_id: str, actor: str,
                     reason: str, at: datetime | None = None) -> dict[str, Any]:
    if actor != "user":
        raise ValueError("search portfolio revocation requires the user actor")
    if not reason.strip():
        raise ValueError("revocation reason is required")
    row = connection.execute(
        "SELECT status FROM search_portfolios WHERE portfolio_id=?", (portfolio_id,)
    ).fetchone()
    if not row or row["status"] != "approved":
        raise ValueError("approved search portfolio not found")
    direction_ids = [item[0] for item in connection.execute(
        "SELECT direction_id FROM search_portfolio_directions WHERE portfolio_id=?",
        (portfolio_id,),
    )]
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE search_portfolios SET status='revoked', revoked_at=?, status_reason=? "
        "WHERE portfolio_id=?", (timestamp, reason, portfolio_id),
    )
    for direction_id in direction_ids:
        connection.execute(
            "UPDATE resume_adaptation_plans SET status='invalidated', invalidated_at=?, "
            "invalidation_reason='portfolio_revoked' WHERE direction_id=? "
            "AND status IN ('generated','approved')", (timestamp, direction_id),
        )
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='material_locks'"
        ).fetchone():
            connection.execute("""
                UPDATE material_locks SET invalidated_at=?, invalidation_reason='portfolio_revoked'
                WHERE resume_version_id IN (
                    SELECT version_id FROM resume_versions WHERE direction=?
                ) AND invalidated_at IS NULL
            """, (timestamp, direction_id))
    _portfolio_event(connection, portfolio_id, actor, "revoked", reason,
                     {"direction_count": len(direction_ids)}, at)
    connection.commit()
    return {"portfolio_id": portfolio_id, "status": "revoked",
            "direction_count": len(direction_ids)}


def status(connection: sqlite3.Connection) -> dict[str, Any]:
    directions = connection.execute(
        "SELECT status, COUNT(*) AS count FROM search_directions GROUP BY status"
    ).fetchall()
    plans = connection.execute(
        "SELECT status, COUNT(*) AS count FROM resume_adaptation_plans GROUP BY status"
    ).fetchall()
    portfolios = connection.execute(
        "SELECT status, COUNT(*) AS count FROM search_portfolios GROUP BY status"
    ).fetchall()
    return {
        "directions": sum(row["count"] for row in directions),
        "directions_by_status": {row["status"]: row["count"] for row in directions},
        "portfolios": sum(row["count"] for row in portfolios),
        "portfolios_by_status": {row["status"]: row["count"] for row in portfolios},
        "plans": sum(row["count"] for row in plans),
        "plans_by_status": {row["status"]: row["count"] for row in plans},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    register = commands.add_parser("register-direction")
    register.add_argument("--input", required=True, type=Path)
    approve = commands.add_parser("approve-direction")
    approve.add_argument("--direction-id", required=True)
    approve.add_argument("--actor", required=True)
    approve.add_argument("--profile-sha256", required=True)
    register_portfolio_parser = commands.add_parser("register-portfolio")
    register_portfolio_parser.add_argument("--input", required=True, type=Path)
    approve_portfolio_parser = commands.add_parser("approve-portfolio")
    approve_portfolio_parser.add_argument("--portfolio-id", required=True)
    approve_portfolio_parser.add_argument("--actor", required=True)
    approve_portfolio_parser.add_argument("--portfolio-sha256", required=True)
    revoke_portfolio_parser = commands.add_parser("revoke-portfolio")
    revoke_portfolio_parser.add_argument("--portfolio-id", required=True)
    revoke_portfolio_parser.add_argument("--actor", required=True)
    revoke_portfolio_parser.add_argument("--reason", required=True)
    revoke = commands.add_parser("revoke-direction")
    revoke.add_argument("--direction-id", required=True)
    revoke.add_argument("--actor", required=True)
    revoke.add_argument("--reason", required=True)
    generate = commands.add_parser("generate-plan")
    generate.add_argument("--plan-id", required=True)
    generate.add_argument("--direction-id", required=True)
    generate.add_argument("--job-id", required=True)
    generate.add_argument("--candidate", required=True, type=Path)
    approve_plan_parser = commands.add_parser("approve-plan")
    approve_plan_parser.add_argument("--plan-id", required=True)
    approve_plan_parser.add_argument("--candidate", required=True, type=Path)
    approve_plan_parser.add_argument("--actor", required=True)
    approve_plan_parser.add_argument("--plan-sha256", required=True)
    commands.add_parser("status")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    if args.command == "init":
        result = {"status": "initialized", "db": str(args.db)}
    elif args.command == "register-direction":
        result = register_direction(connection, json.loads(args.input.read_text(encoding="utf-8")))
    elif args.command == "approve-direction":
        result = approve_direction(
            connection, args.direction_id, args.actor, args.profile_sha256
        )
    elif args.command == "register-portfolio":
        result = register_portfolio(
            connection, json.loads(args.input.read_text(encoding="utf-8"))
        )
    elif args.command == "approve-portfolio":
        result = approve_portfolio(
            connection, args.portfolio_id, args.actor, args.portfolio_sha256
        )
    elif args.command == "revoke-portfolio":
        result = revoke_portfolio(
            connection, args.portfolio_id, args.actor, args.reason
        )
    elif args.command == "revoke-direction":
        result = revoke_direction(connection, args.direction_id, args.actor, args.reason)
    elif args.command == "generate-plan":
        result = generate_plan(
            connection, args.plan_id, args.direction_id, args.job_id, args.candidate
        )
    elif args.command == "approve-plan":
        result = approve_plan(
            connection, args.plan_id, args.candidate, args.actor, args.plan_sha256
        )
    else:
        result = status(connection)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
