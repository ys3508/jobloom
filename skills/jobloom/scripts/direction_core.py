#!/usr/bin/env python3
"""User-approved search directions and evidence-constrained resume adaptation plans."""

from __future__ import annotations

import argparse
import json
import os
import re
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
from evidence_matcher import match_requirement  # noqa: E402
from _common import require_table  # noqa: E402


PROFILE_KEYS = {
    "schema_version", "direction_id", "name", "role_family", "target_titles",
    "positive_keywords", "negative_keywords", "precision_keywords", "criteria",
    "parent_direction_id", "hard_exclusion_keywords", "discovery_keywords", "auxiliary_titles",
    "warning_keywords",
}
# Optional groups may be absent. A profile registered without one is never rewritten to
# include it, because the profile hash the user approved is computed from exactly these
# keys: defaulting a new group into an old profile would silently change that hash.
OPTIONAL_PROFILE_KEYS = {
    "hard_exclusion_keywords", "discovery_keywords", "auxiliary_titles", "warning_keywords",
}
REQUIRED_PROFILE_KEYS = PROFILE_KEYS - OPTIONAL_PROFILE_KEYS
LEGACY_PROFILE_KEYS = REQUIRED_PROFILE_KEYS
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

        CREATE TABLE IF NOT EXISTS routing_records (
            record_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            job_card_sha256 TEXT NOT NULL,
            direction_id TEXT NOT NULL,
            direction_profile_sha256 TEXT NOT NULL,
            portfolio_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            hard_failures_json TEXT NOT NULL,
            review_reasons_json TEXT NOT NULL,
            field_hit_totals_json TEXT NOT NULL,
            sponsorship_state TEXT NOT NULL,
            sponsorship_priority INTEGER NOT NULL,
            investigation_required INTEGER NOT NULL,
            ranking_score INTEGER NOT NULL,
            entered_pool_at TEXT,
            recorded_at TEXT NOT NULL,
            invalidated_at TEXT,
            invalidation_reason TEXT,
            UNIQUE (job_id, direction_id, job_card_sha256)
        );
        CREATE INDEX IF NOT EXISTS routing_records_pool_idx
            ON routing_records(invalidated_at, entered_pool_at);

        CREATE TABLE IF NOT EXISTS baseline_plans (
            plan_id TEXT PRIMARY KEY,
            direction_id TEXT NOT NULL,
            direction_profile_sha256 TEXT NOT NULL,
            master_version_id TEXT NOT NULL,
            master_file_sha256 TEXT NOT NULL,
            candidate_profile_sha256 TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            plan_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            approved_at TEXT,
            approved_by TEXT,
            invalidated_at TEXT,
            invalidation_reason TEXT,
            FOREIGN KEY (direction_id) REFERENCES search_directions(direction_id),
            FOREIGN KEY (master_version_id) REFERENCES resume_versions(version_id)
        );
        CREATE INDEX IF NOT EXISTS baseline_plans_direction_idx
            ON baseline_plans(direction_id, status);

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
    # Schema-migration guard, not a runtime safety dependency: resume_core owns
    # resume_versions and may not have been initialized yet when this module is used alone.
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
    if not isinstance(profile, dict) or (set(profile) - PROFILE_KEYS) \
            or (REQUIRED_PROFILE_KEYS - set(profile)):
        raise ValueError("direction profile has missing or unknown fields")
    resume_core._require_safe_id(profile["direction_id"], "direction_id")
    if profile["schema_version"] not in {"0.1.0", "0.2.0"}:
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
    normalized.setdefault("hard_exclusion_keywords", [])
    normalized.setdefault("discovery_keywords", [])
    normalized.setdefault("auxiliary_titles", [])
    normalized["name"] = profile["name"].strip()
    normalized["role_family"] = profile["role_family"].strip()
    for field in (
        "target_titles", "positive_keywords", "negative_keywords", "precision_keywords",
        "hard_exclusion_keywords", "discovery_keywords", "auxiliary_titles",
    ):
        normalized[field] = _bounded_strings(normalized[field], field, required=field == "target_titles")
    # Validated but never defaulted in, so an existing profile hash cannot move.
    if "warning_keywords" in normalized:
        normalized["warning_keywords"] = _bounded_strings(
            normalized["warning_keywords"], "warning_keywords")
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




# --- Routing surface -------------------------------------------------------
# Field scope belongs to code; term lists belong to the user-approved profile.
# GROUP_FIELDS is deliberately not derivable from profile JSON: adding a keyword
# group to the profile must never be able to grant itself a wider field scope.
ROUTING_FIELDS = (
    "title", "summary", "responsibilities", "required_skills", "preferred_skills",
    "required_certifications", "preferred_certifications", "employment_type",
    "compensation_structure",
)
# Read for exact-value comparison and domain context only; never keyword-matched,
# so an employer named "Salesforce" can never trip a "sales" term.
CONTEXT_FIELDS = ("employer",)
# The raw JD and its derivatives never reach a routing decision.
ROUTING_DENYLIST = frozenset({
    "description", "description_sha256", "extraction", "canonical_url", "location",
    "country", "salary", "status", "job_id", "employer",
})
assert not set(ROUTING_FIELDS) & ROUTING_DENYLIST

_CONTENT_FIELDS = frozenset({"title", "summary", "responsibilities", "required_skills", "preferred_skills"})
GROUP_FIELDS: dict[str, frozenset[str]] = {
    "target_titles": frozenset({"title"}),
    "auxiliary_titles": frozenset({"title"}),
    "positive_keywords": _CONTENT_FIELDS,
    "precision_keywords": _CONTENT_FIELDS,
    "discovery_keywords": _CONTENT_FIELDS,
    "negative_keywords": _CONTENT_FIELDS,
    # Obligation is read from the field the term was found in, so prose is excluded:
    # "Epic required" and "familiarity with Epic is a plus" are indistinguishable to a
    # token matcher, and only the skill and certification lists state which is which.
    "warning_keywords": frozenset({
        "required_skills", "required_certifications",
        "preferred_skills", "preferred_certifications",
    }),
    # A pay structure is never a "skill", so skills stay out of the only hard path.
    "hard_exclusion_keywords": frozenset({
        "title", "employment_type", "compensation_structure", "required_certifications",
        "summary", "responsibilities",
    }),
}
# Prose carries negation the matcher cannot see ("we do not offer commission-only
# compensation"), so a prose hit is surfaced for triage instead of silently discarding.
HARD_EXCLUSION_DECISIVE_FIELDS = frozenset({
    "title", "employment_type", "compensation_structure", "required_certifications",
})

JOB_FIELD_SHAPES: dict[str, tuple[str, int, int]] = {
    "title": ("str", 300, 0),
    "summary": ("str_or_none", 2000, 0),
    "employment_type": ("str", 100, 0),
    "responsibilities": ("list", 500, 50),
    "required_skills": ("list", 500, 200),
    "preferred_skills": ("list", 500, 200),
    "required_certifications": ("list", 500, 200),
    "preferred_certifications": ("list", 500, 200),
    "compensation_structure": ("list", 300, 20),
    "sponsorship_statements": ("list", 500, 10),
}
FIELD_HIT_CAP = 50

# --- Seniority -------------------------------------------------------------
# Exactly the tokens the user named. Widening this forces guard lists against
# registered target titles, so it stays a deliberate, separate decision.
BLOCKED_SENIORITY_TOKENS = frozenset({"senior", "sr", "snr", "lead", "principal", "staff"})
# A blocked token immediately followed by one of these is domain vocabulary, not rank.
SENIORITY_GUARD_FOLLOWERS = {
    "senior": frozenset({"care", "living", "housing", "services", "health", "citizen", "citizens"}),
    "lead": frozenset({"generation", "optimization", "compound", "scoring", "qualification"}),
    "principal": frozenset({"component", "components", "investigator", "diagnosis"}),
    "staff": frozenset({"nurse", "nurses", "pharmacist", "physician", "augmentation"}),
}
REQUIRE_EXPERIENCE_FOR_UNSPECIFIED_SENIORITY = False
CANDIDATE_MAX_YEARS = 3.0

# --- Duty detection --------------------------------------------------------
# Deliberately excludes bare "sales", "commercial", "business development",
# "commission", "territory", "pipeline", "revenue", "account", "client".
SALES_ROLE_TITLES = (
    "account executive", "sales representative", "sales rep", "territory manager",
    "district sales manager", "inside sales", "outside sales", "field sales",
    "door to door sales", "insurance agent", "sales consultant",
)
SALES_OWNERSHIP_SIGNALS = (
    "carry a quota", "carrying a quota", "quota carrying", "sales quota", "quota attainment",
    "book of business", "cold calling", "cold call", "close deals", "closing deals",
    "on target earnings", "ote", "commission only", "commission based compensation",
    "generate new business", "prospecting new clients", "hunt for new logos",
)
ANALYTICS_COUNTER_SIGNALS = (
    "analysis", "analytics", "analyze", "insights", "dashboard", "reporting", "sql",
    "statistical", "statistics", "modeling", "forecasting", "research", "data quality",
    "study design", "process improvement", "strategy",
)
ERP_CONFIG_SIGNALS = (
    "sap modules", "oracle ebs", "workday configuration", "netsuite configuration",
    "system configuration", "user provisioning", "license administration",
    "erp implementation", "module configuration",
)
IMPLEMENTATION_SIGNALS = ("go live", "go-live", "uat", "user acceptance testing", "cutover", "deployment plan")
ADVANCED_METHOD_TERMS = (
    "pharmacoepidemiology", "propensity score", "causal inference", "survival analysis",
    "health economics", "heor", "cost effectiveness", "markov model", "budget impact model",
)
ADVANCED_DATA_ASSET_TERMS = ("claims data", "administrative claims", "ehr data", "real world data", "rwd")
CAREER_GROWTH_DEMOTION_TITLES = ("clinical data coordinator", "data entry coordinator")
DOMAIN_CONTEXT_TERMS = (
    "healthcare", "health care", "clinical", "pharma", "pharmaceutical", "biotech",
    "life sciences", "hospital", "provider", "payer", "patient", "medical", "cro",
    "health system", "public health", "epidemiology",
)
QUANT_RESEARCH_TRANSFER_TERMS = (
    "conjoint", "maxdiff", "spss", "regression", "survey design", "sampling",
    "experimental design", "segmentation",
)
CONTEXT_REVIEW_MIN_DISTINCT_TERMS = 2

# Industry context is satisfied only by a declared industry or one of its controlled
# surface forms. An ordinary positive keyword must never satisfy it: "commercial
# analytics" or "machine learning" says nothing about the sector, so accepting one
# would let a bare de-qualified title match a retail or technology posting.
INDUSTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "pharmaceuticals": ("pharmaceuticals", "pharmaceutical", "pharma"),
    "biotechnology": ("biotechnology", "biotech"),
    "healthcare": ("healthcare", "health care"),
    "life sciences": ("life sciences", "life science"),
    "clinical research": ("clinical research", "clinical"),
    "hospitals": ("hospitals", "hospital", "health system", "health systems"),
    "public health": ("public health",),
    "research": ("research",),
    "consulting": ("consulting",),
}


def _industry_terms(declared: tuple[str, ...]) -> tuple[str, ...]:
    terms: list[str] = []
    for industry in declared:
        for term in INDUSTRY_ALIASES.get(industry.casefold(), (industry,)):
            if term not in terms:
                terms.append(term)
    return tuple(terms)

# --- Sponsorship -----------------------------------------------------------
SPONSORSHIP_REFUSAL_PATTERNS = (
    "unable to sponsor", "not able to sponsor", "cannot sponsor", "can not sponsor",
    "do not sponsor", "does not sponsor", "will not sponsor", "no visa sponsorship",
    "not provide sponsorship", "not offer sponsorship", "without sponsorship now or in the future",
    "not take over sponsorship", "take over sponsorship of", "unable to provide sponsorship",
)
SPONSORSHIP_SUPPORT_PATTERNS = (
    "visa sponsorship available", "we sponsor", "will sponsor", "able to sponsor",
    "sponsorship is available", "offer visa sponsorship", "h 1b sponsorship available",
)
# "Sponsor" in this portfolio usually means the trial sponsor, not immigration.
SPONSORSHIP_NON_VISA_MARKERS = (
    "sponsor cro", "trial sponsor", "study sponsor", "sponsor site", "investigator initiated",
    "sponsor relationship", "sponsor audits", "clinical sponsor",
)
# Must not be read as refusal.
SPONSORSHIP_NOT_A_REFUSAL = ("no sponsorship required", "sponsorship is not required", "without requiring sponsorship")
SPONSORSHIP_PRIORITY = {
    "supports": 4, "historical_support": 3, "unknown": 2, "conflicting": 1, "does_not_support": 0,
}

# --- Ranking ---------------------------------------------------------------
RANKING_BASE = 100
RANKING_WEIGHTS = {
    "sponsorship": 10, "positive_keyword": 2, "analytics_duty": 1,
    "negative_keyword": -3, "career_growth": -15, "duty_demotion": -10, "stretch": -5,
    "warning_required": -6, "evidence_gap": -12,
}
# Fields whose contents the posting itself states are mandatory.
MANDATORY_OBLIGATION_FIELDS = frozenset({"required_skills", "required_certifications"})
# Below this many stated requirements the coverage ratio is too noisy to act on.
EVIDENCE_SUPPORT_MIN_REQUIREMENTS = 3


def _tokens(value: str) -> list[str]:
    return re.findall(r"[^\W_]+", value.casefold())


def _token_run(needle: list[str], haystack: list[str]) -> int:
    """Index of the first contiguous occurrence of needle in haystack, else -1."""
    if not needle or len(needle) > len(haystack):
        return -1
    first = needle[0]
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start] == first and haystack[start:start + len(needle)] == needle:
            return start
    return -1


def _phrase_hits(phrases: tuple[str, ...], tokens_by_field: dict[str, list[str]],
                 fields: frozenset[str] | tuple[str, ...]) -> list[dict[str, Any]]:
    hits = []
    for phrase in phrases:
        needle = _tokens(phrase)
        for field in fields:
            start = _token_run(needle, tokens_by_field.get(field, []))
            if start >= 0:
                hits.append({"term": phrase, "field": field})
                break
    return hits


def calibrate_direction_keywords(profile: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Report direction terms that do not fire on a measured posting corpus.

    This diagnostic neither edits nor registers a direction. It uses the router's exact
    token-sequence and field-boundary rules, preventing a term from looking alive here while
    remaining dead in routing.
    """
    if not jobs:
        raise ValueError("direction keyword calibration requires at least one job")
    normalized = validate_profile(profile)
    counts = {group: {term: 0 for term in normalized.get(group, [])}
              for group in GROUP_FIELDS}
    jobs_with_hits = {group: 0 for group in GROUP_FIELDS}
    for job in jobs:
        _validate_job_shape(job)
        tokens_by_field = _field_tokens(job)
        for group, allowed_fields in GROUP_FIELDS.items():
            fired = []
            for term in normalized.get(group, []):
                if _phrase_hits((term,), tokens_by_field, allowed_fields):
                    counts[group][term] += 1
                    fired.append(term)
            jobs_with_hits[group] += bool(fired)

    groups = {}
    for group in GROUP_FIELDS:
        term_counts = counts[group]
        ordered = sorted(term_counts.items(), key=lambda item: (-item[1], item[0].casefold()))
        any_hits = jobs_with_hits[group]
        dominant = ordered[0] if ordered and ordered[0][1] else None
        groups[group] = {
            "terms": len(term_counts),
            "jobs_with_any_hit": any_hits,
            "never_fired_terms": sorted(term for term, count in term_counts.items() if count == 0),
            "term_job_counts": {term: count for term, count in ordered},
            "dominant_term": dominant[0] if dominant else None,
            "dominant_term_jobs": dominant[1] if dominant else 0,
            "sole_carrier_term": dominant[0] if dominant and dominant[1] == any_hits else None,
        }
    return {"direction_id": normalized["direction_id"], "corpus_jobs": len(jobs),
            "groups": groups}


def mine_direction_aliases(profile: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure corpus-written subphrases for dead multi-word skill and domain terms.

    Target titles are deliberately excluded: shortening a synthetic title to common title
    components is matcher loosening, not alias discovery. The output is evidence for a
    proposal, never an automatic profile edit.
    """
    audit = calibrate_direction_keywords(profile, jobs)
    normalized = validate_profile(profile)
    raw = []
    for job in jobs:
        text = str(job.get("description") or " ".join(_field_text(job).values()))
        raw.append((_tokens(text), str(job.get("employer") or "unknown")))
    groups = {}
    for group in ("positive_keywords", "precision_keywords", "discovery_keywords"):
        terms = []
        dead = audit["groups"][group]["never_fired_terms"]
        for term in dead:
            tokens = _tokens(term)
            candidates = []
            seen = set()
            for length in range(len(tokens) - 1, 0, -1):
                for start in range(len(tokens) - length + 1):
                    candidate_tokens = tokens[start:start + length]
                    candidate = " ".join(candidate_tokens)
                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    matched = [(employer, index) for index, (haystack, employer) in enumerate(raw)
                               if _token_run(candidate_tokens, haystack) >= 0]
                    if matched:
                        candidates.append({
                            "candidate": candidate,
                            "postings": len({index for _, index in matched}),
                            "employers": len({employer.casefold() for employer, _ in matched}),
                        })
            candidates.sort(key=lambda item: (-len(_tokens(item["candidate"])),
                                               item["postings"], item["candidate"]))
            terms.append({"dead_term": term, "observed_subphrases": candidates})
        groups[group] = terms
    return {
        "direction_id": normalized["direction_id"],
        "corpus_jobs": len(jobs),
        "target_titles": {
            "status": "structurally_dead_do_not_shorten",
            "never_fired_terms": audit["groups"]["target_titles"]["never_fired_terms"],
        },
        "groups": groups,
    }


def _validate_job_shape(job: dict[str, Any]) -> None:
    """Reject a wrong-typed routing field; never coerce it into match text."""
    for field, (kind, max_len, max_items) in JOB_FIELD_SHAPES.items():
        if field not in job or job[field] is None:
            if kind == "str" and field in job and job[field] is None:
                raise ValueError(f"malformed job card field: {field}")
            continue
        value = job[field]
        if kind in {"str", "str_or_none"}:
            if not isinstance(value, str) or len(value) > max_len:
                raise ValueError(f"malformed job card field: {field}")
        else:
            if not isinstance(value, list) or len(value) > max_items:
                raise ValueError(f"malformed job card field: {field}")
            if any(not isinstance(item, str) or len(item) > max_len for item in value):
                raise ValueError(f"malformed job card field: {field}")


MAX_EXCERPT_CHARS = 240


def _field_text(job: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in ROUTING_FIELDS:
        value = job.get(field)
        values[field] = " ".join(value) if isinstance(value, list) else str(value or "")
    return values


def _tokens_with_spans(text: str) -> tuple[list[str], list[tuple[int, int]]]:
    matches = list(re.finditer(r"[^\W_]+", text.casefold()))[:4000]
    return [match.group(0) for match in matches], [match.span() for match in matches]


def _field_tokens(job: dict[str, Any]) -> dict[str, list[str]]:
    return {field: _tokens_with_spans(text)[0] for field, text in _field_text(job).items()}


def _excerpt(text: str, spans: list[tuple[int, int]], token_start: int, token_end: int) -> str:
    """The sentence carrying a hit, so a reviewer can see negation the matcher cannot.

    Bounded and never written to an events table: this is untrusted posting prose.
    """
    if not spans or token_start >= len(spans):
        return ""
    start_char, end_char = spans[token_start][0], spans[min(token_end, len(spans)) - 1][1]
    left = max((text.rfind(mark, 0, start_char) for mark in (". ", "! ", "? ", "\n", ";")), default=-1)
    left = 0 if left < 0 else left + 1
    right_candidates = [index for index in
                        (text.find(mark, end_char) for mark in (". ", "! ", "? ", "\n", ";"))
                        if index != -1]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    sentence = text[left:right].strip()
    if len(sentence) <= MAX_EXCERPT_CHARS:
        return sentence
    keep = MAX_EXCERPT_CHARS // 2
    return (sentence[:keep] + " \u2026 " + sentence[-keep:]).strip()


def _seniority(tokens_by_field, job):
    """Token-level seniority read with domain guards; never a substring scan."""
    title = tokens_by_field.get("title", [])
    markers, suppressed = [], []
    for index, token in enumerate(title):
        if token not in BLOCKED_SENIORITY_TOKENS:
            continue
        follower = title[index + 1] if index + 1 < len(title) else None
        guard = SENIORITY_GUARD_FOLLOWERS.get(token, frozenset())
        if follower in guard:
            suppressed.append({"term": token, "field": "title", "guard": follower})
        else:
            markers.append({"term": token, "field": "title"})
    declared = job.get("seniority") if job.get("requirements_reviewed") else None
    return {
        "band": None,
        "title_derived_band": "blocked" if markers else "unspecified",
        "declared_band": declared if isinstance(declared, str) and declared != "unknown" else None,
        "blocked_tokens": sorted({item["term"] for item in markers}),
        "title_markers": markers,
        "suppressed_markers": suppressed,
        "experience": _experience(job),
    }


def _experience(job):
    raw = job.get("experience")
    if raw is None:
        return {"min_years": None, "max_years": None, "basis": None, "strictness": None,
                "state": "unreviewed" if not job.get("requirements_reviewed") else "unstated"}
    if not isinstance(raw, dict) or set(raw) - {"min_years", "max_years", "basis", "strictness"}:
        return {"min_years": None, "max_years": None, "basis": None, "strictness": None, "state": "malformed"}
    def years(key):
        value = raw.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return "invalid"
        return float(value)
    minimum, maximum = years("min_years"), years("max_years")
    basis, strictness = raw.get("basis"), raw.get("strictness", "required")
    if "invalid" in (minimum, maximum) or basis not in {"range", "minimum", "exact", "unstated", None} \
            or strictness not in {"required", "preferred"}:
        return {"min_years": None, "max_years": None, "basis": None, "strictness": None, "state": "malformed"}
    if minimum is not None and maximum is not None and minimum > maximum:
        return {"min_years": None, "max_years": None, "basis": None, "strictness": None, "state": "malformed"}
    state = "unstated" if minimum is None else (
        "above_range" if minimum > CANDIDATE_MAX_YEARS else "within_reach")
    return {"min_years": minimum, "max_years": maximum, "basis": basis,
            "strictness": strictness, "state": state}


def _normalize_credential(value):
    """Return a stable comparison code, or None when the item is not a credential slug."""
    tokens = _tokens(value)
    if not tokens or len(tokens) > 8:
        return None
    placeholders = ({"none"}, {"n", "a"}, {"na"}, {"not", "applicable"}, {"tbd"})
    if any(set(tokens) == placeholder for placeholder in placeholders):
        return None
    dropped = {"preferred", "or", "equivalent", "active", "current", "license", "licensure",
               "certification", "certified", "a", "an", "the", "in", "state", "of"}
    core = [token for token in tokens if token not in dropped]
    # A single leftover character is punctuation debris, not a credential.
    if not core or "".join(core) == "" or len("".join(core)) < 2:
        return None
    return "".join(core)


CREDENTIAL_ALIASES = {
    "registerednurse": "rn", "rn": "rn",
    "doctorofmedicine": "md", "md": "md",
    "doctorofpharmacy": "pharmd", "pharmd": "pharmd",
    "licensedpracticalnurse": "lpn", "lpn": "lpn",
}


def _credential_code(value):
    slug = _normalize_credential(value)
    if slug is None:
        return None
    return CREDENTIAL_ALIASES.get(slug, slug)


def _credentials(job, candidate):
    """Generalized credential check. No hardcoded license list, and holdings are honored."""
    held_raw = [item for item in candidate.get("certifications", []) if isinstance(item, str)]
    held_exact = {item.casefold() for item in held_raw}
    held_codes = {code for code in (_credential_code(item) for item in held_raw) if code}
    findings, failures, review = [], [], []
    for field, required in (("required_certifications", True), ("preferred_certifications", False)):
        items = job.get(field) or []
        for index, item in enumerate(items):
            code = _credential_code(item)
            if code is None:
                if _tokens(item) and len(_tokens(item)) > 8:
                    status = "unparsed"
                    if required:
                        review.append(f"required_credential_unparsed:{field}.{index}")
                else:
                    continue
                findings.append({"term": item, "field": field, "index": index,
                                 "code": None, "status": status})
                continue
            if "preferred" in _tokens(item) and required:
                findings.append({"term": item, "field": field, "index": index,
                                 "code": code, "status": "preferred_not_held"
                                 if code not in held_codes else "preferred_held"})
                continue
            holds = code in held_codes
            if not required:
                findings.append({"term": item, "field": field, "index": index, "code": code,
                                 "status": "preferred_held" if holds else "preferred_not_held"})
            elif holds:
                # Surface a divergence rather than silently passing what evaluate_job would fail.
                status = "held" if item.casefold() in held_exact else "alias_only"
                if status == "alias_only":
                    review.append(f"credential_alias_not_in_candidate_profile:{code}")
                findings.append({"term": item, "field": field, "index": index,
                                 "code": code, "status": status})
            else:
                failures.append(f"required_credential_not_held:{code}")
                findings.append({"term": item, "field": field, "index": index,
                                 "code": code, "status": "not_held"})
    findings.sort(key=lambda item: (item["field"], item["index"]))
    return findings, failures, review


def _sponsorship(job, candidate):
    """Never substitute one work-authorization answer for another (parent spec 8.1)."""
    statements = job.get("sponsorship_statements") or []
    evidence, refusal, support, non_visa = [], False, False, False
    for index, segment in enumerate(statements):
        segment_tokens = _tokens(segment)
        def present(patterns):
            return [phrase for phrase in patterns if _token_run(_tokens(phrase), segment_tokens) >= 0]
        benign = present(SPONSORSHIP_NOT_A_REFUSAL)
        marks = present(SPONSORSHIP_NON_VISA_MARKERS)
        if marks:
            non_visa = True
            evidence.append({"field": "sponsorship_statements", "segment_index": index,
                             "pattern_id": "non_visa_sense", "matched_text": segment[:120]})
            continue
        if benign:
            evidence.append({"field": "sponsorship_statements", "segment_index": index,
                             "pattern_id": "not_a_refusal", "matched_text": segment[:120]})
            continue
        found_refusal = present(SPONSORSHIP_REFUSAL_PATTERNS)
        found_support = present(SPONSORSHIP_SUPPORT_PATTERNS)
        # "unable to sponsor" contains "able to sponsor": the longer refusal wins.
        if found_refusal:
            refusal = True
            evidence.append({"field": "sponsorship_statements", "segment_index": index,
                             "pattern_id": found_refusal[0], "matched_text": segment[:120]})
        elif found_support:
            support = True
            evidence.append({"field": "sponsorship_statements", "segment_index": index,
                             "pattern_id": found_support[0], "matched_text": segment[:120]})
    detected = ("conflicting" if refusal and support else
                "does_not_support" if refusal else
                "supports" if support else "unknown")
    declared = job.get("sponsorship", "unknown")
    if declared not in SPONSORSHIP_PRIORITY:
        declared = "unknown"
    if detected == "unknown":
        effective = declared
    elif declared == "unknown" or declared == detected:
        effective = detected
    else:
        effective = "conflicting"
    state = {"supports": "explicit_support", "historical_support": "historical_support",
             "unknown": "unknown", "conflicting": "conflicting",
             "does_not_support": "explicit_refusal"}[effective]
    authorization = candidate.get("work_authorization") or {}
    need = {key: bool(authorization.get(key)) for key in
            ("sponsorship_now", "sponsorship_future", "employer_action_required")}
    return {
        "declared": declared, "detected": detected, "effective": effective, "state": state,
        "priority": SPONSORSHIP_PRIORITY[effective], "evidence": evidence,
        "candidate_need": need, "authorized_now": bool(authorization.get("authorized_now")),
        "confirmed": bool(authorization.get("confirmed")),
        "scanned_fields": ["sponsorship_statements"], "non_visa_sense": non_visa,
    }


def _narrow_set(direction_values, candidate_values):
    """A direction may narrow the CandidateProfile, never widen it."""
    direction = {str(item).casefold() for item in direction_values or []}
    candidate = {str(item).casefold() for item in candidate_values or []}
    if not direction:
        return None, False
    if not candidate:
        return direction, False
    return direction & candidate, bool(direction - candidate)


def _criteria(profile, candidate, job, sponsorship_state):
    criteria = profile.get("criteria") or {}
    search = candidate.get("search") or {}
    evaluated = {key: "not_configured" for key in CRITERIA_KEYS}
    failures, review, conflicts = [], [], []
    remote = str(job.get("work_arrangement") or "").casefold() == "remote"

    simple = (
        ("countries", "countries", "country", "direction_country_outside_scope"),
        ("work_arrangements", "work_arrangements", "work_arrangement", "direction_work_arrangement_outside_scope"),
        ("employment_types", "employment_types", "employment_type", "direction_employment_type_outside_scope"),
    )
    for key, search_key, job_key, code in simple:
        effective, widens = _narrow_set(criteria.get(key), search.get(search_key))
        if widens:
            conflicts.append(f"direction_criteria_widens_candidate_profile:{key}")
        if effective is None:
            continue
        if not effective:
            evaluated[key] = "unsatisfiable"
            failures.append(f"direction_criteria_unsatisfiable:{key}")
            continue
        value = str(job.get(job_key) or "unknown").casefold()
        if value == "unknown":
            evaluated[key] = "job_value_unknown"
            continue
        evaluated[key] = "applied"
        if value not in effective:
            failures.append(code)

    effective, widens = _narrow_set(criteria.get("locations"), search.get("locations"))
    if widens:
        conflicts.append("direction_criteria_widens_candidate_profile:locations")
    if effective is not None:
        if not effective:
            evaluated["locations"] = "unsatisfiable"
            failures.append("direction_criteria_unsatisfiable:locations")
        elif remote:
            evaluated["locations"] = "not_applicable_remote"
        else:
            location = str(job.get("location") or "unknown").casefold()
            if location == "unknown":
                evaluated["locations"] = "job_value_unknown"
                review.append("direction_location_unknown")
            else:
                evaluated["locations"] = "applied"
                # Bidirectional containment mirrors evaluate_job for free-text locations.
                if not any(item in location or location in item for item in effective):
                    failures.append("direction_location_outside_scope")

    excluded = {str(item).casefold() for item in criteria.get("excluded_companies") or []}
    if excluded:
        employer = str(job.get("employer") or "unknown").casefold()
        if employer == "unknown":
            evaluated["excluded_companies"] = "job_value_unknown"
        else:
            evaluated["excluded_companies"] = "applied"
            if employer in excluded:
                failures.append("direction_excluded_company")

    floor = criteria.get("salary_floor")
    if floor is not None:
        candidate_floor = search.get("salary_floor")
        if candidate_floor is not None and floor < candidate_floor:
            conflicts.append("direction_criteria_widens_candidate_profile:salary_floor")
            evaluated["salary_floor"] = "deferred_to_candidate_profile"
        else:
            salary = job.get("salary") or {}
            currency = criteria.get("salary_currency")
            if currency and salary.get("currency") and str(salary["currency"]).upper() != str(currency).upper():
                evaluated["salary_floor"] = "currency_conflict"
            elif salary.get("max") is None or str(salary.get("unit", "YEAR")).upper() != "YEAR":
                evaluated["salary_floor"] = "job_value_unknown"
            else:
                evaluated["salary_floor"] = "applied"
                if salary["max"] < floor:
                    failures.append("direction_salary_below_floor")

    travel = criteria.get("travel_limit")
    evaluated["travel_limit"] = "not_configured" if travel is None else "no_jobcard_field"
    for key in ("industries", "company_sizes", "target_companies"):
        if criteria.get(key):
            evaluated[key] = "no_jobcard_field"
    if criteria.get("seniority"):
        # Job families, not bands: inert until a JobCard carries a structured seniority.
        evaluated["seniority"] = "title_token_gate_only"
    if criteria.get("salary_currency"):
        evaluated["salary_currency"] = evaluated.get("salary_floor", "not_configured")
    return evaluated, failures, review, conflicts


# A BaselinePlan records which confirmed facts a direction's standing one-page resume
# carries and in what order. It is a selection record, never a wording record: it holds
# fact IDs and controlled reason codes only, so approving it can never approve prose.
BASELINE_SELECTION_REASONS = {
    "direction_core_evidence", "direction_supporting_evidence", "domain_context",
    "recency", "structural_identity", "structural_education", "structural_skill",
    "structural_certification", "structural_publication",
}
BASELINE_EXCLUSION_REASONS = {
    "outside_direction_scope", "superseded_by_stronger_evidence",
    "space_constrained", "not_relevant_to_direction",
}


def _approved_master(connection: sqlite3.Connection) -> sqlite3.Row:
    rows = connection.execute(
        "SELECT * FROM resume_versions WHERE kind='master_source' AND status='approved' "
        "AND approved_by='user' ORDER BY approved_at DESC"
    ).fetchall()
    if not rows:
        raise ValueError("a user-approved master_source resume is required")
    if len(rows) > 1:
        raise ValueError("more than one approved master_source resume is active")
    return rows[0]


def generate_baseline_plan(connection: sqlite3.Connection, plan_id: str, direction_id: str,
                           candidate_path: Path, selection: list[dict[str, Any]],
                           exclusions: list[dict[str, Any]],
                           unsupported_terms: list[str] | None = None,
                           at: datetime | None = None) -> dict[str, Any]:
    """Build a reviewable selection plan for a direction's standing one-page resume.

    No JobCard is involved: a baseline serves the direction, not a posting. Every fact in
    the candidate profile must be either selected or explicitly excluded, so nothing is
    dropped silently.
    """
    resume_core._require_safe_id(plan_id, "plan_id")
    if connection.execute("SELECT 1 FROM baseline_plans WHERE plan_id=?", (plan_id,)).fetchone():
        raise ValueError("baseline plan already exists")
    direction = connection.execute(
        "SELECT * FROM search_directions WHERE direction_id=?", (direction_id,)).fetchone()
    if not direction or direction["status"] != "approved":
        raise ValueError("approved search direction not found")
    if not _direction_in_active_portfolio(connection, direction_id, direction["profile_sha256"]):
        raise ValueError("search direction is outside the active approved portfolio")
    master = _approved_master(connection)
    resume_core.verify_version_file(master)
    candidate, candidate_hash = resume_core.load_valid_candidate(candidate_path)
    snapshot = connection.execute(
        "SELECT * FROM candidate_snapshots WHERE content_sha256=? AND status='active' "
        "AND registered_by='user'", (candidate_hash,)).fetchone()
    if not snapshot:
        raise ValueError("candidate profile is not the active user-registered snapshot")
    resume_core.verify_snapshot_file_hash(snapshot)
    if master["candidate_profile_sha256"] != candidate_hash:
        raise ValueError("approved master resume was approved against a different candidate profile")
    # A baseline plan carries fact IDs and controlled reason codes only. Free text would
    # be wording, and approving the plan must never approve wording.
    if unsupported_terms:
        raise ValueError("a baseline plan carries no free text; unsupported_terms must be empty")

    usable = {fact["id"] for fact in candidate["facts"]
              if fact.get("status") in {"confirmed", "locked"}}
    selected_ids, orders = [], []
    for item in selection:
        if set(item) - {"fact_id", "order", "reason"}:
            raise ValueError("baseline selection entries carry fact_id, order and reason only")
        fact_id, order, reason = item.get("fact_id"), item.get("order"), item.get("reason")
        if fact_id not in usable:
            raise ValueError(f"baseline plan references an unconfirmed fact: {fact_id}")
        if reason not in BASELINE_SELECTION_REASONS:
            raise ValueError(f"invalid baseline selection reason: {reason}")
        if not isinstance(order, int) or isinstance(order, bool) or order < 0:
            raise ValueError("baseline selection order must be a non-negative integer")
        selected_ids.append(fact_id)
        orders.append(order)
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("baseline selection contains duplicate facts")
    if sorted(orders) != list(range(len(orders))):
        raise ValueError("baseline selection order must be contiguous from zero")

    excluded_ids = []
    for item in exclusions:
        if set(item) - {"fact_id", "reason"}:
            raise ValueError("baseline exclusion entries carry fact_id and reason only")
        fact_id, reason = item.get("fact_id"), item.get("reason")
        if fact_id not in usable:
            raise ValueError(f"baseline plan references an unconfirmed fact: {fact_id}")
        if reason not in BASELINE_EXCLUSION_REASONS:
            raise ValueError(f"invalid baseline exclusion reason: {reason}")
        excluded_ids.append(fact_id)
    if len(set(excluded_ids)) != len(excluded_ids):
        raise ValueError("baseline exclusions contain duplicate facts")
    if set(selected_ids) & set(excluded_ids):
        raise ValueError("a fact cannot be both selected and excluded")
    missing = usable - set(selected_ids) - set(excluded_ids)
    if missing:
        raise ValueError(
            "every confirmed fact must be selected or explicitly excluded; unaccounted: "
            + ", ".join(sorted(missing)[:5]))

    plan = {
        "schema_version": "0.1.0", "plan_id": plan_id, "kind": "direction_baseline",
        "direction_id": direction_id,
        "direction_profile_sha256": direction["profile_sha256"],
        "master_version_id": master["version_id"],
        "master_file_sha256": master["file_sha256"],
        "candidate_profile_sha256": candidate_hash,
        "selection": sorted(({"fact_id": item["fact_id"], "order": item["order"],
                              "reason": item["reason"]} for item in selection),
                            key=lambda item: item["order"]),
        "exclusions": sorted(({"fact_id": item["fact_id"], "reason": item["reason"]}
                              for item in exclusions), key=lambda item: item["fact_id"]),
        "unsupported_terms": [],
        "locked_fact_ids_must_remain_exact": sorted(
            fact["id"] for fact in candidate["facts"]
            if fact.get("locked") and fact["id"] in set(selected_ids)),
        "constraints": {
            "allowed": ["select", "order", "compress", "clarify"],
            "forbidden": ["unsupported_skill", "invented_achievement", "changed_date",
                          "inflated_seniority", "invented_management", "transferable_as_direct",
                          "changed_work_authorization", "promoted_evidence_strength"],
            "evidence_strength_may_be_promoted": False,
            "plan_approves_selection_only": True,
        },
    }
    digest = canonical_hash(plan)
    timestamp = (at or now_utc()).isoformat()
    connection.execute("""
        INSERT INTO baseline_plans (
            plan_id, direction_id, direction_profile_sha256, master_version_id,
            master_file_sha256, candidate_profile_sha256, plan_json, plan_sha256,
            status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'generated', ?)
    """, (plan_id, direction_id, direction["profile_sha256"], master["version_id"],
          master["file_sha256"], candidate_hash, canonical_json(plan), digest, timestamp))
    _event(connection, direction_id, "system", "baseline_plan_generated", "selection_recorded",
           metadata={"plan_id": plan_id, "selected": len(selected_ids),
                     "excluded": len(excluded_ids)}, at=at)
    connection.commit()
    return {"plan_id": plan_id, "status": "generated", "plan_sha256": digest, "plan": plan}


def approve_baseline_plan(connection: sqlite3.Connection, plan_id: str, candidate_path: Path,
                          actor: str, plan_sha256: str, at: datetime | None = None) -> dict[str, Any]:
    """Approve a selection plan. This never approves resume bytes or wording."""
    if actor != "user":
        raise ValueError("baseline plan approval requires the user actor")
    row = connection.execute("SELECT * FROM baseline_plans WHERE plan_id=?", (plan_id,)).fetchone()
    if not row or row["status"] != "generated":
        raise ValueError("generated baseline plan not found")
    if row["plan_sha256"] != plan_sha256:
        raise ValueError("baseline plan hash does not match the reviewed plan")
    _require_baseline_plan_current(connection, row, candidate_path)
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE baseline_plans SET status='approved', approved_at=?, approved_by='user' "
        "WHERE plan_id=?", (timestamp, plan_id))
    _event(connection, row["direction_id"], "user", "baseline_plan_approved",
           "user_approved_exact_baseline_plan", metadata={"plan_id": plan_id}, at=at)
    connection.commit()
    return {"plan_id": plan_id, "status": "approved", "plan_sha256": row["plan_sha256"]}


def _require_baseline_plan_current(connection: sqlite3.Connection, row: sqlite3.Row,
                                   candidate_path: Path | None = None) -> None:
    """Fail closed when anything the plan was reviewed against has moved."""
    if row["invalidated_at"]:
        raise ValueError("baseline plan was invalidated")
    if canonical_hash(json.loads(row["plan_json"])) != row["plan_sha256"]:
        raise ValueError("baseline plan payload does not match its recorded hash")
    direction = connection.execute(
        "SELECT * FROM search_directions WHERE direction_id=?", (row["direction_id"],)).fetchone()
    if not direction or direction["status"] != "approved":
        raise ValueError("baseline plan direction is no longer approved")
    if direction["profile_sha256"] != row["direction_profile_sha256"]:
        raise ValueError("baseline plan direction profile is stale")
    if not _direction_in_active_portfolio(connection, row["direction_id"], direction["profile_sha256"]):
        raise ValueError("baseline plan direction is outside the active approved portfolio")
    master = _approved_master(connection)
    if master["version_id"] != row["master_version_id"] or master["file_sha256"] != row["master_file_sha256"]:
        raise ValueError("baseline plan master resume is stale")
    resume_core.verify_version_file(master)
    if master["candidate_profile_sha256"] != row["candidate_profile_sha256"]:
        raise ValueError("baseline plan master resume was approved against another candidate profile")
    active = connection.execute(
        "SELECT * FROM candidate_snapshots WHERE status='active' AND registered_by='user'"
    ).fetchone()
    if not active or active["content_sha256"] != row["candidate_profile_sha256"]:
        raise ValueError("baseline plan candidate profile is stale")
    resume_core.verify_snapshot_file_hash(active)
    if candidate_path is not None:
        _, candidate_hash = resume_core.load_valid_candidate(candidate_path)
        if candidate_hash != row["candidate_profile_sha256"]:
            raise ValueError("baseline plan candidate profile is stale")


def require_approved_baseline_plan(connection: sqlite3.Connection, plan_id: str | None,
                                   direction_id: str) -> sqlite3.Row:
    """Gate used by resume registration: an approved, still-current plan or nothing."""
    if not plan_id:
        raise ValueError("direction_baseline resume requires an approved baseline plan")
    row = connection.execute("SELECT * FROM baseline_plans WHERE plan_id=?", (plan_id,)).fetchone()
    if not row or row["status"] != "approved" or row["approved_by"] != "user":
        raise ValueError("baseline plan is not user-approved")
    if row["direction_id"] != direction_id:
        raise ValueError("baseline plan belongs to another direction")
    _require_baseline_plan_current(connection, row)
    return row


def cascade_invalidate_baseline_plans(connection: sqlite3.Connection, reason: str,
                                      direction_id: str | None = None,
                                      master_version_id: str | None = None,
                                      at: datetime | None = None) -> list[str]:
    """Retire plans that an authorized revocation has just undermined.

    A cascade may retire a user-approved plan, because the revocation that triggered it
    was itself a user action. Each retirement is recorded individually.
    """
    if not (direction_id or master_version_id):
        raise ValueError("a baseline plan cascade needs a direction or a master version")
    clause, parameter = (("direction_id=?", direction_id) if direction_id
                         else ("master_version_id=?", master_version_id))
    rows = connection.execute(
        f"SELECT plan_id, direction_id, plan_sha256 FROM baseline_plans "
        f"WHERE {clause} AND invalidated_at IS NULL", (parameter,)).fetchall()
    timestamp = (at or now_utc()).isoformat()
    for row in rows:
        connection.execute(
            "UPDATE baseline_plans SET status='invalidated', invalidated_at=?, "
            "invalidation_reason=? WHERE plan_id=?", (timestamp, reason, row["plan_id"]))
        _event(connection, row["direction_id"], "user", "baseline_plan_invalidated", reason,
               metadata={"plan_id": row["plan_id"], "plan_sha256": row["plan_sha256"],
                         "cascaded_from": direction_id or master_version_id}, at=at)
    return [row["plan_id"] for row in rows]


def invalidate_baseline_plan(connection: sqlite3.Connection, plan_id: str, actor: str,
                             reason: str, superseded_by: str | None = None,
                             at: datetime | None = None) -> dict[str, Any]:
    """Retire a baseline plan with an audit trail, rather than editing the table by hand."""
    if actor not in {"user", "system"}:
        raise ValueError("baseline plan invalidation requires a known actor")
    if not reason or not reason.strip():
        raise ValueError("baseline plan invalidation requires a reason")
    row = connection.execute("SELECT * FROM baseline_plans WHERE plan_id=?", (plan_id,)).fetchone()
    if not row:
        raise ValueError("baseline plan not found")
    if row["invalidated_at"]:
        raise ValueError("baseline plan is already invalidated")
    # Only the user may retire what the user approved. An unapproved plan is scaffolding
    # and the system may retire it; an approved one is a decision.
    if row["status"] == "approved" and actor != "user":
        raise ValueError("only the user may invalidate a user-approved baseline plan")
    if superseded_by:
        replacement = connection.execute(
            "SELECT direction_id, invalidated_at FROM baseline_plans WHERE plan_id=?",
            (superseded_by,)).fetchone()
        if not replacement:
            raise ValueError("replacement baseline plan not found")
        if replacement["invalidated_at"]:
            raise ValueError("replacement baseline plan is itself invalidated")
        if replacement["direction_id"] != row["direction_id"]:
            raise ValueError("replacement baseline plan belongs to another direction")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE baseline_plans SET status='invalidated', invalidated_at=?, invalidation_reason=? "
        "WHERE plan_id=?", (timestamp, reason, plan_id))
    _event(connection, row["direction_id"], actor, "baseline_plan_invalidated", reason,
           metadata={"plan_id": plan_id, "plan_sha256": row["plan_sha256"],
                     "superseded_by": superseded_by}, at=at)
    connection.commit()
    return {"plan_id": plan_id, "status": "invalidated", "reason": reason,
            "superseded_by": superseded_by}


def baseline_plan_selected_fact_ids(row: sqlite3.Row) -> set[str]:
    return {item["fact_id"] for item in json.loads(row["plan_json"])["selection"]}


def route_job(profile: dict[str, Any], candidate: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """Route one structured JobCard. A direction may narrow CandidateProfile, never widen it.

    Stage A validates, Stage B derives, Stage C runs every predicate independently, and
    Stage D reduces. Nothing in Stage C short-circuits, so the outcome is order-independent.
    """
    # --- Stage A: validate --------------------------------------------------
    profile = validate_profile(profile)
    _validate_job_shape(job)

    # --- Stage B: derive ----------------------------------------------------
    tokens_by_field = _field_tokens(job)
    failures: list[str] = []
    review: list[str] = []
    notes: list[str] = []

    authorization = candidate.get("work_authorization")
    required_auth = ("country", "authorized_now", "sponsorship_now", "sponsorship_future",
                     "employer_action_required", "confirmed")
    if not isinstance(authorization, dict) or any(key not in authorization for key in required_auth):
        failures.append("work_authorization_incomplete")
        authorization = {}
    elif not authorization.get("confirmed"):
        review.append("work_authorization_unconfirmed_or_stale")

    raw_text = _field_text(job)
    spans_by_field = {field: _tokens_with_spans(text)[1] for field, text in raw_text.items()}
    field_hits: dict[str, list[dict[str, Any]]] = {group: [] for group in GROUP_FIELDS}
    truncated: dict[str, bool] = {group: False for group in GROUP_FIELDS}
    for group, allowed in GROUP_FIELDS.items():
        for term in profile.get(group) or []:
            needle = _tokens(term)
            if not needle:
                continue
            for field in ROUTING_FIELDS:
                if field not in allowed:
                    continue
                start = _token_run(needle, tokens_by_field.get(field, []))
                if start < 0:
                    continue
                if len(field_hits[group]) >= FIELD_HIT_CAP:
                    truncated[group] = True
                    break
                field_hits[group].append({
                    "term": term, "term_tokens": needle, "field": field,
                    "matched_excerpt": _excerpt(raw_text[field], spans_by_field[field],
                                                start, start + len(needle)),
                    "field_tier": "identity" if field == "title" else (
                        "structure" if field in {"employment_type", "compensation_structure",
                                                 "required_certifications", "preferred_certifications"}
                        else "content"),
                    "match_kind": "title_token_sequence" if field == "title" else "token_sequence",
                    "token_start": start, "token_end": start + len(needle),
                    "decisive": field in HARD_EXCLUSION_DECISIVE_FIELDS,
                })

    signal_hits = {
        "sales_role_title": _phrase_hits(SALES_ROLE_TITLES, tokens_by_field, ("title",)),
        "sales_ownership": _phrase_hits(SALES_OWNERSHIP_SIGNALS, tokens_by_field,
                                        ("summary", "responsibilities", "compensation_structure")),
        "analytics_counter": _phrase_hits(ANALYTICS_COUNTER_SIGNALS, tokens_by_field,
                                          ("summary", "responsibilities", "required_skills")),
        "erp_configuration": _phrase_hits(ERP_CONFIG_SIGNALS, tokens_by_field,
                                          ("summary", "responsibilities", "required_skills")),
        "implementation_consulting": _phrase_hits(IMPLEMENTATION_SIGNALS, tokens_by_field,
                                                  ("summary", "responsibilities")),
        "advanced_requirement": [],
        "career_growth_demotion": _phrase_hits(CAREER_GROWTH_DEMOTION_TITLES, tokens_by_field, ("title",)),
        "domain_context": _phrase_hits(DOMAIN_CONTEXT_TERMS, tokens_by_field,
                                       ("title", "summary", "responsibilities", "required_skills")),
        "quantitative_transfer": _phrase_hits(QUANT_RESEARCH_TRANSFER_TERMS, tokens_by_field,
                                              ("summary", "responsibilities", "required_skills")),
    }
    # The title is excluded from the advanced scan or every HEOR target title self-demotes.
    for obligation, field in (("required", "required_skills"), ("preferred", "preferred_skills")):
        for phrase in ADVANCED_METHOD_TERMS + ADVANCED_DATA_ASSET_TERMS:
            needle = _tokens(phrase)
            if _token_run(needle, tokens_by_field.get(field, [])) >= 0:
                signal_hits["advanced_requirement"].append({
                    "term": phrase, "field": field, "obligation": obligation,
                    "covered_by_candidate_fact": match_requirement(
                        phrase, candidate.get("facts") or [])["strength"] != "none",
                })

    seniority_assessment = _seniority(tokens_by_field, job)
    credential_findings, credential_failures, credential_review = _credentials(job, candidate)
    sponsorship_assessment = _sponsorship(job, candidate)
    criteria_evaluated, criteria_failures, criteria_review, criteria_conflicts = _criteria(
        profile, candidate, job, sponsorship_assessment)
    analytics_body_hits = len({hit["term"] for hit in signal_hits["analytics_counter"]})

    # --- Stage C: predicates (independent, accumulating) --------------------
    decisive_exclusions = [hit for hit in field_hits["hard_exclusion_keywords"] if hit["decisive"]]
    if decisive_exclusions:
        failures.append("direction_hard_exclusion")
    elif field_hits["hard_exclusion_keywords"]:
        review.append("hard_exclusion_context_review")

    if seniority_assessment["title_markers"]:
        failures.append("seniority_outside_portfolio")
    if seniority_assessment["suppressed_markers"]:
        notes.append("seniority_token_context_suppressed")
    if not tokens_by_field.get("title"):
        review.append("job_title_unknown")

    failures.extend(credential_failures)
    review.extend(credential_review)

    experience = seniority_assessment["experience"]
    if experience["state"] == "malformed":
        review.append("experience_field_malformed")
    elif experience["state"] == "above_range":
        if experience["strictness"] == "required":
            failures.append("experience_requirement_above_candidate_range")
        else:
            review.append("experience_preference_above_candidate_range")
    elif experience["state"] == "unreviewed":
        notes.append("experience_deferred_pending_job_card_review")
    elif experience["state"] == "unstated" and REQUIRE_EXPERIENCE_FOR_UNSPECIFIED_SENIORITY \
            and not seniority_assessment["declared_band"]:
        review.append("experience_unstated_for_unspecified_seniority")

    if signal_hits["sales_role_title"]:
        failures.append("quota_carrying_sales_title")
    ownership = len({hit["term"] for hit in signal_hits["sales_ownership"]})
    decisive_ownership = len({hit["term"] for hit in signal_hits["sales_ownership"]
                              if hit["field"] == "compensation_structure"})
    if decisive_ownership or ownership >= 2:
        failures.append("quota_carrying_sales_duties")
    elif ownership == 1:
        review.append("sales_duty_signal_requires_review")

    erp = len({hit["term"] for hit in signal_hits["erp_configuration"]})
    if erp >= 2 and analytics_body_hits == 0:
        failures.append("pure_erp_it_configuration")
    elif erp >= 2:
        review.append("erp_configuration_duties_without_analytics")
    elif erp == 1:
        notes.append("erp_configuration_minor_component")
    if signal_hits["implementation_consulting"]:
        notes.append("implementation_consulting_component")
        if analytics_body_hits < 2:
            review.append("implementation_consulting_requires_review")

    mandatory_advanced = [hit for hit in signal_hits["advanced_requirement"]
                          if hit["obligation"] == "required" and not hit["covered_by_candidate_fact"]]
    if mandatory_advanced:
        review.append("advanced_requirement_mandatory")
    elif signal_hits["advanced_requirement"]:
        notes.append("advanced_requirement_preferred_only")

    title_hits = field_hits["target_titles"]
    auxiliary_hits = field_hits["auxiliary_titles"]
    contextual = [hit for group in ("positive_keywords", "precision_keywords", "discovery_keywords")
                  for hit in field_hits[group]]
    distinct_context = len({hit["term"] for hit in contextual})
    employer_tokens = _tokens(str(job.get("employer") or ""))
    # Context is what the direction itself declares, not a fixed global vocabulary:
    # criteria.industries plus its own positive keywords.
    declared_industries = tuple(str(item) for item in (profile.get("criteria") or {}).get("industries") or [])
    industry_terms = _industry_terms(declared_industries)
    industry_hits = _phrase_hits(industry_terms, tokens_by_field,
                                 ("title", "summary", "responsibilities", "required_skills"))
    industry_hits.extend({"term": term, "field": "employer"} for term in industry_terms
                         if _token_run(_tokens(term), employer_tokens) >= 0)
    signal_hits["direction_context"] = industry_hits
    signal_hits["domain_context"].extend(
        {"term": term, "field": "employer"} for term in DOMAIN_CONTEXT_TERMS
        if _token_run(_tokens(term), employer_tokens) >= 0)
    has_domain = bool(signal_hits["domain_context"])
    # Only a declared industry list imposes the requirement; a positive-keyword hit
    # can satisfy it, but keywords alone never impose it.
    context_configured = bool(declared_industries)
    # Only a declared industry satisfies this. A positive keyword is a relevance
    # signal, not a sector claim, and must never stand in for one.
    has_direction_context = bool(industry_hits)
    if title_hits:
        # A target title carries its own context when the title itself names the
        # industry ("Clinical Data Analyst"). A de-qualified bare title ("Sales
        # Operations Analyst") does not, so it must find that context elsewhere
        # before it can auto-match. This is what stops moving a qualifier out of a
        # title from silently widening the direction.
        if context_configured and not has_direction_context:
            review.append("target_title_without_direction_context")
    elif auxiliary_hits:
        if has_domain:
            review.append("auxiliary_title_with_direction_context")
        else:
            review.append("auxiliary_title_without_direction_context")
            if signal_hits["quantitative_transfer"]:
                notes.append("auxiliary_title_transferable_quantitative_only")
    else:
        title_tokens = tokens_by_field.get("title", [])
        order_variant = any(
            set(_tokens(term)) == set(title_tokens) and _tokens(term) != title_tokens
            for term in profile.get("target_titles") or [])
        elsewhere = [hit for group in ("target_titles",) for hit in field_hits[group]]
        contextual_title = any(
            _token_run(_tokens(term), tokens_by_field.get(field, [])) >= 0
            for term in profile.get("target_titles") or []
            for field in ("summary", "responsibilities", "required_skills", "preferred_skills"))
        if order_variant:
            review.append("target_title_order_variant")
        elif contextual_title:
            review.append("contextual_title_reference_only")
        elif distinct_context >= CONTEXT_REVIEW_MIN_DISTINCT_TERMS:
            review.append("direction_context_without_title_match")
        else:
            failures.append("outside_direction_title_scope")

    if field_hits["negative_keywords"]:
        review.append("direction_soft_negative_keyword")

    # A warning term demotes only where the posting makes it mandatory. Listed under
    # "preferred" it is recorded and nothing more: not holding a nice-to-have is not a
    # reason to drop a posting the direction otherwise fits.
    warning_required = sorted({hit["term"] for hit in field_hits["warning_keywords"]
                               if hit["field"] in MANDATORY_OBLIGATION_FIELDS})
    warning_preferred = sorted({hit["term"] for hit in field_hits["warning_keywords"]
                                if hit["field"] not in MANDATORY_OBLIGATION_FIELDS}
                               - set(warning_required))
    if warning_required:
        review.append("warning_term_required")
    if warning_preferred:
        notes.append("warning_term_preferred_only")

    # A title match is an identity signal, not evidence. When the posting states enough
    # mandatory requirements and the candidate's own confirmed facts cover almost none of
    # them, the title is the only thing matching, so it must not carry an auto-match on
    # its own. This stays a review rather than a hard failure: coverage is measured
    # against the fact library, and a thin library is a reason to look, not to discard.
    required_skill_evidence = []
    for requirement in job.get("required_skills") or []:
        evidence_match = match_requirement(str(requirement), candidate.get("facts") or [])
        required_skill_evidence.append({
            "requirement": str(requirement),
            "covered_by_candidate_fact": evidence_match["strength"] != "none",
            "strength": evidence_match["strength"],
            "fact_ids": evidence_match["fact_ids"],
        })
    covered_requirements = sum(1 for item in required_skill_evidence
                               if item["covered_by_candidate_fact"])
    stated_requirements = len(required_skill_evidence)
    evidence_unsupported = bool(
        title_hits
        and stated_requirements >= EVIDENCE_SUPPORT_MIN_REQUIREMENTS
        and covered_requirements * 2 < stated_requirements
    )
    if evidence_unsupported:
        review.append("title_match_without_evidence_support")

    failures.extend(criteria_failures)
    review.extend(criteria_review)

    effective_sponsorship = sponsorship_assessment["effective"]
    need = sponsorship_assessment["candidate_need"]
    needs_employer_support = any(need.values())
    same_country = str(job.get("country") or "").casefold() == str(authorization.get("country") or "").casefold()
    if same_country and needs_employer_support and effective_sponsorship == "does_not_support":
        failures.append("required_sponsorship_not_supported")
        for key, value in need.items():
            if value:
                notes.append(f"sponsorship_required_by:{key}")
    elif needs_employer_support and effective_sponsorship == "unknown":
        review.append("employer_sponsorship_history_investigation_required")
    elif needs_employer_support and effective_sponsorship == "historical_support":
        review.append("sponsorship_historical_only_requires_confirmation")
    if effective_sponsorship == "conflicting":
        review.append("employer_sponsorship_conflict_requires_user_resolution")
        notes.append("sponsorship_signal_conflicting")
    if sponsorship_assessment["declared"] != "unknown" and \
            sponsorship_assessment["detected"] not in {"unknown", sponsorship_assessment["declared"]}:
        review.append("sponsorship_declared_field_conflicts_with_posting_text")
    if sponsorship_assessment.get("non_visa_sense"):
        review.append("sponsorship_statement_non_visa_sense")

    if signal_hits["career_growth_demotion"]:
        notes.append("career_growth_ceiling_coordinator_role")
    missing_surface = [field for field in ("responsibilities", "compensation_structure") if field not in job]
    if missing_surface:
        notes.append("routing_surface_incomplete")

    # --- Stage D: reduce ----------------------------------------------------
    decision = "fail" if failures else ("review" if review else "match")
    if decision == "match" and not job.get("requirements_reviewed"):
        decision = "review"
        review.append("job_card_unreviewed")

    positive_terms = len({hit["term"] for hit in field_hits["positive_keywords"]})
    negative_terms = len({hit["term"] for hit in field_hits["negative_keywords"]})
    career_penalty = 1 if signal_hits["career_growth_demotion"] else 0
    duty_penalty = min(2, erp + (1 if ownership else 0))
    stretch_penalty = 1 if mandatory_advanced else 0
    warning_penalty = min(3, len(warning_required))
    evidence_gap_penalty = 1 if evidence_unsupported else 0
    weights = RANKING_WEIGHTS
    ranking_score = (
        RANKING_BASE
        + weights["sponsorship"] * sponsorship_assessment["priority"]
        + weights["positive_keyword"] * min(positive_terms, 5)
        + weights["analytics_duty"] * min(analytics_body_hits, 5)
        + weights["negative_keyword"] * min(negative_terms, 5)
        + weights["career_growth"] * career_penalty
        + weights["duty_demotion"] * duty_penalty
        + weights["stretch"] * stretch_penalty
        + weights["warning_required"] * warning_penalty
        + weights["evidence_gap"] * evidence_gap_penalty
    )
    penalties = [{"code": code, "weight": weight} for code, weight in (
        ("career_growth_ceiling_coordinator_role", 15 * career_penalty),
        ("duty_demotion", 10 * duty_penalty),
        ("advanced_requirement_mandatory", 5 * stretch_penalty),
        ("warning_term_required", 6 * warning_penalty),
        ("title_match_without_evidence_support", 12 * evidence_gap_penalty),
    ) if weight]

    return {
        "decision": decision,
        "hard_failures": sorted(set(failures)),
        "review_reasons": sorted(set(review)),
        "notes": sorted(set(notes)),
        "field_hits": field_hits,
        "field_hits_truncated": truncated,
        "field_hit_totals": {group: len(hits) for group, hits in field_hits.items()},
        "signal_hits": signal_hits,
        "criteria_evaluated": criteria_evaluated,
        "criteria_conflicts": sorted(set(criteria_conflicts)),
        "seniority_assessment": seniority_assessment,
        "sponsorship_assessment": sponsorship_assessment,
        "credential_findings": credential_findings,
        "required_skill_evidence": required_skill_evidence,
        "ranking_signals": {
            "positive_keyword_hits": positive_terms,
            "positive_keyword_field_hits": len(field_hits["positive_keywords"]),
            "negative_keyword_hits": negative_terms,
            "precision_keyword_terms": sorted({hit["term"] for hit in field_hits["precision_keywords"]}),
            "analytics_duty_hits": analytics_body_hits,
            "sales_ownership_hits": ownership,
            "preferred_credential_hits": sum(
                1 for item in credential_findings if item["status"] == "preferred_held"),
            "career_growth_penalty": career_penalty,
            "duty_demotion_penalty": duty_penalty,
            "stretch_penalty": stretch_penalty,
            "warning_terms_required": warning_required,
            "warning_terms_preferred_only": warning_preferred,
            "warning_penalty": warning_penalty,
            "required_skills_stated": stated_requirements,
            "required_skills_covered_by_facts": covered_requirements,
            "evidence_gap_penalty": evidence_gap_penalty,
            "sponsorship_priority": sponsorship_assessment["priority"],
            "direction_industry_match": None,
            "direction_company_size_match": None,
            "direction_target_company": None,
            "direction_travel_within_limit": None,
        },
        "ranking_penalties": penalties,
        "ranking_score": ranking_score,
        "sponsorship_priority": sponsorship_assessment["priority"],
    }




POOL_DECISIONS = frozenset({"match", "review"})
DEFAULT_POOL_WINDOW = 20


def _active_portfolio(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM search_portfolios WHERE status='approved' ORDER BY approved_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise ValueError("no approved SearchPortfolio is active")
    return row


def record_routing(connection: sqlite3.Connection, record_id: str, direction_id: str,
                   job: dict[str, Any], candidate: dict[str, Any],
                   at: datetime | None = None) -> dict[str, Any]:
    """Route one JobCard against one approved direction and persist the decision.

    Idempotent per (job_id, direction_id, job_card_sha256): re-recording an unchanged
    card returns the stored record untouched. A changed JobCard invalidates the previous
    record for that job and direction rather than mutating it.

    Only `match` and `review` enter the review pool, so a portfolio weight can never
    rescue a hard-filter failure: a failed job is persisted for audit with a null
    entered_pool_at and is invisible to allocation.
    """
    resume_core._require_safe_id(record_id, "record_id")
    portfolio = _active_portfolio(connection)
    direction = connection.execute(
        "SELECT * FROM search_directions WHERE direction_id=?", (direction_id,)
    ).fetchone()
    if not direction or direction["status"] != "approved":
        raise ValueError("routing requires an approved search direction")
    if not _direction_in_active_portfolio(connection, direction_id, direction["profile_sha256"]):
        raise ValueError("search direction is outside the active approved portfolio")

    job_hash = canonical_hash(job)
    existing = connection.execute(
        "SELECT * FROM routing_records WHERE job_id=? AND direction_id=? AND job_card_sha256=?",
        (job["job_id"], direction_id, job_hash),
    ).fetchone()
    if existing:
        if existing["invalidated_at"]:
            raise ValueError("routing record for this JobCard was invalidated; use a new record ID")
        return _routing_row(existing)

    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE routing_records SET invalidated_at=?, invalidation_reason='job_card_changed' "
        "WHERE job_id=? AND direction_id=? AND job_card_sha256<>? AND invalidated_at IS NULL",
        (timestamp, job["job_id"], direction_id, job_hash),
    )
    profile = json.loads(direction["profile_json"])
    routing = route_job(profile, candidate, job)
    sponsorship = routing["sponsorship_assessment"]
    investigation = any(
        reason.startswith("employer_sponsorship_") or
        reason == "sponsorship_historical_only_requires_confirmation"
        for reason in routing["review_reasons"]
    )
    connection.execute("""
        INSERT INTO routing_records (
            record_id, job_id, job_card_sha256, direction_id, direction_profile_sha256,
            portfolio_id, decision, hard_failures_json, review_reasons_json,
            field_hit_totals_json, sponsorship_state, sponsorship_priority,
            investigation_required, ranking_score, entered_pool_at, recorded_at,
            invalidated_at, invalidation_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
    """, (
        record_id, job["job_id"], job_hash, direction_id, direction["profile_sha256"],
        portfolio["portfolio_id"], routing["decision"],
        canonical_json(routing["hard_failures"]), canonical_json(routing["review_reasons"]),
        canonical_json(routing["field_hit_totals"]), sponsorship["state"], sponsorship["priority"],
        int(investigation), routing["ranking_score"],
        timestamp if routing["decision"] in POOL_DECISIONS else None, timestamp,
    ))
    # Events carry counts and hashes only: no matched text, no posting prose.
    _event(connection, direction_id, "system", "job_routed", routing["decision"],
           metadata={"job_id": job["job_id"], "job_card_sha256": job_hash,
                     "hard_failure_count": len(routing["hard_failures"]),
                     "review_reason_count": len(routing["review_reasons"])},
           at=at)
    connection.commit()
    stored = connection.execute(
        "SELECT * FROM routing_records WHERE record_id=?", (record_id,)
    ).fetchone()
    return _routing_row(stored)


def _routing_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "record_id": row["record_id"], "job_id": row["job_id"],
        "job_card_sha256": row["job_card_sha256"], "direction_id": row["direction_id"],
        "direction_profile_sha256": row["direction_profile_sha256"],
        "portfolio_id": row["portfolio_id"], "decision": row["decision"],
        "hard_failures": json.loads(row["hard_failures_json"]),
        "review_reasons": json.loads(row["review_reasons_json"]),
        "field_hit_totals": json.loads(row["field_hit_totals_json"]),
        "sponsorship_state": row["sponsorship_state"],
        "sponsorship_priority": row["sponsorship_priority"],
        "investigation_required": bool(row["investigation_required"]),
        "ranking_score": row["ranking_score"], "entered_pool_at": row["entered_pool_at"],
        "in_review_pool": row["entered_pool_at"] is not None and row["invalidated_at"] is None,
        "invalidated_at": row["invalidated_at"], "invalidation_reason": row["invalidation_reason"],
    }


def review_pool(connection: sqlite3.Connection,
                window_size: int = DEFAULT_POOL_WINDOW) -> list[dict[str, Any]]:
    """The most recent live review-pool records, oldest first. Deterministic ordering."""
    if not isinstance(window_size, int) or isinstance(window_size, bool) or window_size < 1:
        raise ValueError("window_size must be a positive integer")
    rows = connection.execute("""
        SELECT * FROM routing_records
        WHERE invalidated_at IS NULL AND entered_pool_at IS NOT NULL
        ORDER BY entered_pool_at DESC, record_id DESC LIMIT ?
    """, (window_size,)).fetchall()
    return [_routing_row(row) for row in reversed(rows)]


def rolling_allocation_targets(allocations: list[dict[str, Any]], reviewed_direction_ids: list[str],
                               window_size: int = DEFAULT_POOL_WINDOW) -> list[dict[str, Any]]:
    """Return current deficits for a rolling review pool; weights are never daily quotas."""
    recent = reviewed_direction_ids[-window_size:]
    counts = {item["direction_id"]: recent.count(item["direction_id"]) for item in allocations}
    result = []
    for item in allocations:
        target = window_size * item["weight_percent"] / 100
        result.append({"direction_id": item["direction_id"], "weight_percent": item["weight_percent"],
                       "reviewed_count": counts[item["direction_id"]],
                       "target_count": target, "deficit": target - counts[item["direction_id"]]})
    return sorted(result, key=lambda item: (-item["deficit"], item["direction_id"]))


def portfolio_allocation_status(connection: sqlite3.Connection,
                                window_size: int = DEFAULT_POOL_WINDOW) -> dict[str, Any]:
    """Deficits computed from persisted review-pool history, not a caller-supplied list.

    Allocation runs strictly after routing and only orders jobs that already passed it.
    """
    portfolio = _active_portfolio(connection)
    allocations = [
        {"direction_id": row["direction_id"], "weight_percent": row["weight_percent"]}
        for row in connection.execute(
            "SELECT direction_id, weight_percent FROM search_portfolio_directions "
            "WHERE portfolio_id=? ORDER BY direction_id", (portfolio["portfolio_id"],))
    ]
    pool = review_pool(connection, window_size)
    targets = rolling_allocation_targets(
        allocations, [record["direction_id"] for record in pool], window_size)
    return {
        "portfolio_id": portfolio["portfolio_id"], "window_size": window_size,
        "pool_size": len(pool), "targets": targets,
        "underfilled_directions": [item["direction_id"] for item in targets if item["deficit"] > 0],
        "investigation_required_job_ids": sorted(
            {record["job_id"] for record in pool if record["investigation_required"]}),
    }



def variant_allocation_status(connection: sqlite3.Connection, variant_id: str,
                              window_size: int = DEFAULT_POOL_WINDOW) -> dict[str, Any]:
    """Deficits for one resume variant, over its own directions and its own weights.

    Same rolling pool as `portfolio_allocation_status`, restricted to the directions this
    resume actually covers: the portfolio says how the user's applications are split overall,
    the variant says how this one resume's share of them is split.
    """
    require_table(connection, "resume_variants")
    variant = connection.execute(
        "SELECT * FROM resume_variants WHERE variant_id=?", (variant_id,)).fetchone()
    if not variant or variant["status"] != "approved":
        raise ValueError("approved resume variant not found")
    allocations = [
        {"direction_id": row["direction_id"], "weight_percent": row["weight_percent"]}
        for row in connection.execute(
            "SELECT direction_id, weight_percent FROM resume_variant_directions "
            "WHERE variant_id=? ORDER BY direction_id", (variant_id,))
    ]
    covered = {item["direction_id"] for item in allocations}
    pool = [record for record in review_pool(connection, window_size)
            if record["direction_id"] in covered]
    targets = rolling_allocation_targets(
        allocations, [record["direction_id"] for record in pool], window_size)
    return {
        "variant_id": variant_id, "window_size": window_size, "pool_size": len(pool),
        "targets": targets,
        "underfilled_directions": [item["direction_id"] for item in targets if item["deficit"] > 0],
        "investigation_required_job_ids": sorted(
            {record["job_id"] for record in pool if record["investigation_required"]}),
    }


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
    candidate, candidate_hash = resume_core.load_valid_candidate(candidate_path)
    routing = route_job(profile, candidate, job)
    if routing["decision"] != "match":
        blockers = routing["hard_failures"] or routing["review_reasons"]
        raise ValueError(
            "job is outside the approved search direction: " + ", ".join(blockers))
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
    cascade_invalidate_baseline_plans(connection, "direction_revoked",
                                      direction_id=direction_id, at=at)
    require_table(connection, "material_locks")
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
        cascade_invalidate_baseline_plans(connection, "portfolio_revoked",
                                          direction_id=direction_id, at=at)
        require_table(connection, "material_locks")
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
    baseline_parser = commands.add_parser("generate-baseline-plan")
    baseline_parser.add_argument("--plan-id", required=True)
    baseline_parser.add_argument("--direction-id", required=True)
    baseline_parser.add_argument("--candidate", required=True, type=Path)
    baseline_parser.add_argument("--selection", required=True, type=Path,
                                 help="JSON list of {fact_id, order, reason}")
    baseline_parser.add_argument("--exclusions", required=True, type=Path,
                                 help="JSON list of {fact_id, reason}")
    approve_baseline_parser = commands.add_parser("approve-baseline-plan")
    approve_baseline_parser.add_argument("--plan-id", required=True)
    approve_baseline_parser.add_argument("--candidate", required=True, type=Path)
    approve_baseline_parser.add_argument("--actor", required=True)
    approve_baseline_parser.add_argument("--plan-sha256", required=True)
    invalidate_baseline_parser = commands.add_parser("invalidate-baseline-plan")
    invalidate_baseline_parser.add_argument("--plan-id", required=True)
    invalidate_baseline_parser.add_argument("--actor", required=True)
    invalidate_baseline_parser.add_argument("--reason", required=True)
    invalidate_baseline_parser.add_argument("--superseded-by")
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
    elif args.command == "generate-baseline-plan":
        result = generate_baseline_plan(
            connection, args.plan_id, args.direction_id, args.candidate,
            json.loads(args.selection.read_text(encoding="utf-8")),
            json.loads(args.exclusions.read_text(encoding="utf-8")),
        )
    elif args.command == "approve-baseline-plan":
        result = approve_baseline_plan(
            connection, args.plan_id, args.candidate, args.actor, args.plan_sha256)
    elif args.command == "invalidate-baseline-plan":
        result = invalidate_baseline_plan(
            connection, args.plan_id, args.actor, args.reason, args.superseded_by)
    else:
        result = status(connection)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
