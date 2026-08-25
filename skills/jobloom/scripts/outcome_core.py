#!/usr/bin/env python3
"""Verified application outcomes, model usage, user time, and cautious funnel analytics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


OUTCOME_TYPES = {
    "recruiter_response", "screening_call", "interview", "final_interview",
    "offer", "rejected", "withdrawn", "no_response",
}
OUTCOME_SOURCE_TYPES = {"user_confirmation", "email", "ats_record", "recruiter_message"}
MODEL_TIERS = {"none", "low_cost", "high_capability"}
USAGE_WORKFLOWS = {
    "candidate_extraction", "job_ingestion", "job_evaluation", "question_matching",
    "resume_adaptation", "form_filling", "critical_review", "other",
}
USER_TIME_ACTIVITIES = {
    "candidate_review", "settings_confirmation", "resume_review", "job_review",
    "answer_review", "submission_review", "manual_takeover", "interview_tracking", "other",
}
USER_TIME_SOURCES = {"timer", "user_reported"}
SMALL_SAMPLE_THRESHOLD = 30
SAFE_OPERATION = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


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
        CREATE TABLE IF NOT EXISTS outcome_records (
            outcome_id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            outcome_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_reference_sha256 TEXT,
            verified_by_user INTEGER NOT NULL,
            resume_version_id TEXT,
            application_category TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (application_id) REFERENCES applications(application_id)
        );
        CREATE INDEX IF NOT EXISTS outcomes_application_idx
            ON outcome_records(application_id, outcome_type, occurred_at);

        CREATE TABLE IF NOT EXISTS model_usage_events (
            usage_id TEXT PRIMARY KEY,
            workflow TEXT NOT NULL,
            operation TEXT NOT NULL,
            model_tier TEXT NOT NULL,
            model_name TEXT,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cached_tokens INTEGER NOT NULL,
            cost_microusd INTEGER,
            latency_ms INTEGER,
            cache_hit INTEGER NOT NULL,
            application_id TEXT,
            job_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (application_id) REFERENCES applications(application_id),
            FOREIGN KEY (job_id) REFERENCES jobs(job_id)
        );
        CREATE INDEX IF NOT EXISTS model_usage_application_idx ON model_usage_events(application_id, created_at);

        CREATE TABLE IF NOT EXISTS user_time_events (
            time_event_id TEXT PRIMARY KEY,
            activity TEXT NOT NULL,
            duration_seconds INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            application_id TEXT,
            job_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (application_id) REFERENCES applications(application_id),
            FOREIGN KEY (job_id) REFERENCES jobs(job_id)
        );
        CREATE INDEX IF NOT EXISTS user_time_application_idx ON user_time_events(application_id, created_at);

        CREATE TABLE IF NOT EXISTS outcome_audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            entity_id TEXT,
            metadata_json TEXT NOT NULL
        );
    """)
    connection.commit()


def _audit(
    connection: sqlite3.Connection,
    event_type: str,
    entity_id: str,
    metadata: dict[str, Any],
    at: datetime | None = None,
) -> None:
    connection.execute(
        "INSERT INTO outcome_audit_events (created_at, event_type, entity_id, metadata_json) VALUES (?, ?, ?, ?)",
        ((at or now_utc()).isoformat(), event_type, entity_id, canonical_json(metadata)),
    )


def _require_nonnegative_int(value: Any, label: str, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def record_outcome(
    connection: sqlite3.Connection,
    outcome_id: str,
    application_id: str,
    outcome_type: str,
    occurred_at: str,
    source_type: str,
    actor: str,
    source_reference: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    if outcome_type not in OUTCOME_TYPES:
        raise ValueError("invalid outcome type")
    if source_type not in OUTCOME_SOURCE_TYPES:
        raise ValueError("invalid outcome source type")
    if actor != "user" and not source_reference:
        raise ValueError("non-user outcome records require a source reference")
    if source_type == "user_confirmation" and actor != "user":
        raise ValueError("user_confirmation outcomes require the user actor")
    occurred = parse_time(occurred_at)
    current_time = at or now_utc()
    if occurred > current_time + timedelta(minutes=5):
        raise ValueError("outcome timestamp cannot be in the future")
    application = connection.execute(
        "SELECT * FROM applications WHERE application_id=?", (application_id,)
    ).fetchone()
    if not application:
        raise ValueError("application not found")
    if not application["submitted_at"] and outcome_type not in {"withdrawn"}:
        raise ValueError("post-application outcomes require a submitted application")
    state_event = connection.execute(
        "SELECT 1 FROM application_events WHERE application_id=? AND to_state=? LIMIT 1",
        (application_id, outcome_type),
    ).fetchone()
    if not state_event:
        raise ValueError("outcome must first appear in guarded application state history")
    if outcome_type != "withdrawn" and not application["resume_version_id"]:
        raise ValueError("outcome cannot be attributed without a resume version")
    reference_hash = hashlib.sha256(source_reference.encode("utf-8")).hexdigest() if source_reference else None
    connection.execute("""
        INSERT INTO outcome_records (
            outcome_id, application_id, outcome_type, occurred_at, source_type,
            source_reference_sha256, verified_by_user, resume_version_id,
            application_category, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (outcome_id, application_id, outcome_type, occurred.isoformat(), source_type, reference_hash,
          int(actor == "user"), application["resume_version_id"], application["category"],
          current_time.isoformat()))
    _audit(connection, "outcome_recorded", outcome_id, {
        "application_id": application_id, "outcome_type": outcome_type,
        "source_type": source_type, "verified_by_user": actor == "user",
        "has_source_reference": bool(source_reference),
    }, at)
    connection.commit()
    return {"outcome_id": outcome_id, "application_id": application_id, "outcome_type": outcome_type,
            "occurred_at": occurred.isoformat(), "verified_by_user": actor == "user"}


def record_model_usage(
    connection: sqlite3.Connection,
    usage_id: str,
    workflow: str,
    operation: str,
    model_tier: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    model_name: str | None = None,
    cost_microusd: int | None = None,
    latency_ms: int | None = None,
    cache_hit: bool = False,
    application_id: str | None = None,
    job_id: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    if workflow not in USAGE_WORKFLOWS:
        raise ValueError("invalid model-usage workflow")
    if not SAFE_OPERATION.fullmatch(operation):
        raise ValueError("model-usage operation must be a stable snake_case identifier")
    if model_tier not in MODEL_TIERS:
        raise ValueError("invalid model tier")
    input_value = _require_nonnegative_int(input_tokens, "input_tokens")
    output_value = _require_nonnegative_int(output_tokens, "output_tokens")
    cached_value = _require_nonnegative_int(cached_tokens, "cached_tokens")
    cost_value = _require_nonnegative_int(cost_microusd, "cost_microusd", allow_none=True)
    latency_value = _require_nonnegative_int(latency_ms, "latency_ms", allow_none=True)
    if not isinstance(cache_hit, bool):
        raise ValueError("cache_hit must be true or false")
    if cached_value > input_value:
        raise ValueError("cached_tokens cannot exceed input_tokens")
    if model_tier == "none" and (cache_hit or any(
        (model_name, input_value, output_value, cached_value, cost_value, latency_value)
    )):
        raise ValueError("no-model events cannot report model usage")
    if model_tier != "none" and not model_name:
        raise ValueError("model-backed usage requires model_name")
    if application_id and not connection.execute(
        "SELECT 1 FROM applications WHERE application_id=?", (application_id,)
    ).fetchone():
        raise ValueError("application not found")
    if job_id and not connection.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone():
        raise ValueError("job not found")
    connection.execute("""
        INSERT INTO model_usage_events (
            usage_id, workflow, operation, model_tier, model_name, input_tokens, output_tokens,
            cached_tokens, cost_microusd, latency_ms, cache_hit, application_id, job_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (usage_id, workflow, operation, model_tier, model_name, input_value, output_value,
          cached_value, cost_value, latency_value, int(cache_hit), application_id, job_id,
          (at or now_utc()).isoformat()))
    _audit(connection, "model_usage_recorded", usage_id, {
        "workflow": workflow, "model_tier": model_tier, "cache_hit": cache_hit,
        "application_id": application_id, "job_id": job_id,
    }, at)
    connection.commit()
    return {"usage_id": usage_id, "status": "recorded", "total_tokens": input_value + output_value,
            "cached_tokens": cached_value}


def record_user_time(
    connection: sqlite3.Connection,
    time_event_id: str,
    activity: str,
    duration_seconds: int,
    source_type: str,
    application_id: str | None = None,
    job_id: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    if activity not in USER_TIME_ACTIVITIES:
        raise ValueError("invalid user-time activity")
    if source_type not in USER_TIME_SOURCES:
        raise ValueError("invalid user-time source type")
    duration = _require_nonnegative_int(duration_seconds, "duration_seconds")
    if not duration or duration > 86400:
        raise ValueError("duration_seconds must be between 1 and 86400")
    if application_id and not connection.execute(
        "SELECT 1 FROM applications WHERE application_id=?", (application_id,)
    ).fetchone():
        raise ValueError("application not found")
    if job_id and not connection.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone():
        raise ValueError("job not found")
    connection.execute("""
        INSERT INTO user_time_events (
            time_event_id, activity, duration_seconds, source_type, application_id, job_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (time_event_id, activity, duration, source_type, application_id, job_id,
          (at or now_utc()).isoformat()))
    _audit(connection, "user_time_recorded", time_event_id, {
        "activity": activity, "source_type": source_type,
        "application_id": application_id, "job_id": job_id,
    }, at)
    connection.commit()
    return {"time_event_id": time_event_id, "status": "recorded", "duration_seconds": duration}


def _distinct_transition_count(connection: sqlite3.Connection, state: str) -> int:
    return connection.execute(
        "SELECT COUNT(DISTINCT application_id) FROM application_events WHERE to_state=?", (state,)
    ).fetchone()[0]


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator,
            "rate": round(numerator / denominator, 4) if denominator else None}


def _dimension_rows(connection: sqlite3.Connection, expression: str) -> list[dict[str, Any]]:
    rows = connection.execute(f"""
        SELECT COALESCE({expression}, 'unknown') AS dimension_value,
               COUNT(DISTINCT a.application_id) AS submitted,
               COUNT(DISTINCT CASE WHEN interview.application_id IS NOT NULL THEN a.application_id END) AS interviews,
               COUNT(DISTINCT CASE WHEN response.application_id IS NOT NULL THEN a.application_id END) AS responses,
               COUNT(DISTINCT CASE WHEN offer.application_id IS NOT NULL THEN a.application_id END) AS offers
        FROM applications a
        JOIN jobs j ON j.job_id=a.job_id
        LEFT JOIN resume_versions rv ON rv.version_id=a.resume_version_id
        LEFT JOIN application_events interview
            ON interview.application_id=a.application_id AND interview.to_state='interview'
        LEFT JOIN application_events response
            ON response.application_id=a.application_id AND response.to_state='recruiter_response'
        LEFT JOIN application_events offer
            ON offer.application_id=a.application_id AND offer.to_state='offer'
        WHERE a.submitted_at IS NOT NULL
        GROUP BY COALESCE({expression}, 'unknown')
        ORDER BY submitted DESC, dimension_value
    """).fetchall()
    return [{"value": row["dimension_value"], "submitted": row["submitted"],
             "responses": row["responses"], "interviews": row["interviews"], "offers": row["offers"],
             "interview_rate": round(row["interviews"] / row["submitted"], 4) if row["submitted"] else None}
            for row in rows]


def report(connection: sqlite3.Connection) -> dict[str, Any]:
    discovered = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    precision = _distinct_transition_count(connection, "precision_recommended")
    broad = _distinct_transition_count(connection, "broad_recommended")
    recommended = connection.execute("""
        SELECT COUNT(DISTINCT application_id) FROM application_events
        WHERE to_state IN ('precision_recommended', 'broad_recommended')
    """).fetchone()[0]
    approved = _distinct_transition_count(connection, "approved")
    submitted = connection.execute(
        "SELECT COUNT(*) FROM applications WHERE submitted_at IS NOT NULL"
    ).fetchone()[0]
    responses = _distinct_transition_count(connection, "recruiter_response")
    screenings = _distinct_transition_count(connection, "screening_call")
    interviews = _distinct_transition_count(connection, "interview")
    finals = _distinct_transition_count(connection, "final_interview")
    offers = _distinct_transition_count(connection, "offer")
    precision_submitted = connection.execute(
        "SELECT COUNT(*) FROM applications WHERE submitted_at IS NOT NULL AND category='precision'"
    ).fetchone()[0]
    broad_submitted = connection.execute(
        "SELECT COUNT(*) FROM applications WHERE submitted_at IS NOT NULL AND category='broad'"
    ).fetchone()[0]
    precision_interviews = connection.execute("""
        SELECT COUNT(DISTINCT a.application_id) FROM applications a JOIN application_events e
        ON e.application_id=a.application_id AND e.to_state='interview'
        WHERE a.submitted_at IS NOT NULL AND a.category='precision'
    """).fetchone()[0]
    broad_interviews = connection.execute("""
        SELECT COUNT(DISTINCT a.application_id) FROM applications a JOIN application_events e
        ON e.application_id=a.application_id AND e.to_state='interview'
        WHERE a.submitted_at IS NOT NULL AND a.category='broad'
    """).fetchone()[0]
    usage = connection.execute("""
        SELECT COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
               SUM(cost_microusd) AS cost_microusd,
               COUNT(*) AS events
        FROM model_usage_events
    """).fetchone()
    user_seconds = connection.execute("SELECT COALESCE(SUM(duration_seconds), 0) FROM user_time_events").fetchone()[0]
    return {
        "schema_version": "0.1.0",
        "funnel": {
            "jobs_discovered": discovered,
            "jobs_passing_hard_filters": recommended,
            "jobs_recommended": recommended,
            "precision_recommended": precision,
            "broad_recommended": broad,
            "jobs_approved": approved,
            "applications_submitted": submitted,
            "employer_responses": responses,
            "screening_calls": screenings,
            "interviews": interviews,
            "final_interviews": finals,
            "offers": offers,
        },
        "metrics": {
            "valid_application_rate": _rate(recommended, discovered),
            "successful_submission_rate": _rate(submitted, approved),
            "response_rate": _rate(responses, submitted),
            "screening_rate": _rate(screenings, submitted),
            "interview_rate": _rate(interviews, submitted),
            "offer_rate": _rate(offers, submitted),
            "precision_interview_rate": _rate(precision_interviews, precision_submitted),
            "broad_interview_rate": _rate(broad_interviews, broad_submitted),
            "applications_per_interview": round(submitted / interviews, 2) if interviews else None,
            "model_tokens_per_interview": round((usage["input_tokens"] + usage["output_tokens"]) / interviews, 2) if interviews else None,
            "user_minutes_per_interview": round((user_seconds / 60) / interviews, 2) if interviews else None,
        },
        "usage": {
            "events": usage["events"], "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"], "cached_tokens": usage["cached_tokens"],
            "cost_microusd": usage["cost_microusd"], "user_time_seconds": user_seconds,
        },
        "dimensions": {
            "category": _dimension_rows(connection, "a.category"),
            "resume_version": _dimension_rows(connection, "a.resume_version_id"),
            "direction": _dimension_rows(connection, "rv.direction"),
            "source": _dimension_rows(connection, "j.source"),
            "ats": _dimension_rows(connection, "j.ats"),
        },
        "statistical_caution": {
            "sample_size": submitted,
            "threshold": SMALL_SAMPLE_THRESHOLD,
            "status": "insufficient_sample" if submitted < SMALL_SAMPLE_THRESHOLD else "descriptive_only",
            "message": "Report trends only; do not infer causation or change strategy without user approval.",
        },
    }


def write_report(connection: sqlite3.Connection, output: Path) -> dict[str, Any]:
    value = report(connection)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)
    return {"status": "written", "output": str(output),
            "submitted": value["funnel"]["applications_submitted"]}


def status(connection: sqlite3.Connection) -> dict[str, Any]:
    return {
        "outcomes": connection.execute("SELECT COUNT(*) FROM outcome_records").fetchone()[0],
        "model_usage_events": connection.execute("SELECT COUNT(*) FROM model_usage_events").fetchone()[0],
        "user_time_events": connection.execute("SELECT COUNT(*) FROM user_time_events").fetchone()[0],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    outcome = commands.add_parser("record-outcome")
    outcome.add_argument("--input", required=True, type=Path)
    model = commands.add_parser("record-model-usage")
    model.add_argument("--input", required=True, type=Path)
    user_time = commands.add_parser("record-user-time")
    user_time.add_argument("--input", required=True, type=Path)
    analytics = commands.add_parser("report")
    analytics.add_argument("--output", type=Path)
    commands.add_parser("status")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    if args.command == "init":
        result = {"status": "initialized", "db": str(args.db)}
    elif args.command == "record-outcome":
        result = record_outcome(connection, **json.loads(args.input.read_text(encoding="utf-8")))
    elif args.command == "record-model-usage":
        result = record_model_usage(connection, **json.loads(args.input.read_text(encoding="utf-8")))
    elif args.command == "record-user-time":
        result = record_user_time(connection, **json.loads(args.input.read_text(encoding="utf-8")))
    elif args.command == "report":
        result = write_report(connection, args.output) if args.output else report(connection)
    else:
        result = status(connection)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
