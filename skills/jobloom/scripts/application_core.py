#!/usr/bin/env python3
"""Local Jobloom job/application state core with deduplication and guarded transitions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_PARAMETERS = {"source", "src", "ref", "referrer", "trackingid", "gh_src"}
SUCCESS_EVIDENCE_TYPES = {"success_page", "confirmation_id", "account_record", "email_confirmation"}
FAILURE_CODES = {
    "closed_job", "invalid_url", "login_failure", "captcha", "unsupported_form", "upload_failure",
    "new_question", "eligibility_failure", "location_failure", "compensation_failure", "network_failure",
    "website_failure", "safety_restriction", "user_pause", "unknown_failure",
}
APPLICATION_STATES = {
    "discovered", "pending_analysis", "filtered", "needs_user_review", "precision_recommended",
    "broad_recommended", "approved", "materials_in_progress", "ready_to_fill", "filling",
    "waiting_for_user_answer", "waiting_for_submission_approval", "pre_submit_ready", "submitting",
    "submitted", "submission_failed", "submission_uncertain", "waiting_for_user_takeover", "closed",
    "rejected", "recruiter_response", "screening_call", "interview", "final_interview", "offer",
    "withdrawn", "no_response",
}
TRANSITIONS = {
    "discovered": {"pending_analysis", "closed"},
    "pending_analysis": {"filtered", "needs_user_review", "precision_recommended", "broad_recommended", "closed"},
    "filtered": {"pending_analysis", "closed"},
    "needs_user_review": {"pending_analysis", "precision_recommended", "broad_recommended", "filtered", "closed"},
    "precision_recommended": {"approved", "filtered", "closed"},
    "broad_recommended": {"approved", "filtered", "closed"},
    "approved": {"materials_in_progress", "withdrawn"},
    "materials_in_progress": {"ready_to_fill", "waiting_for_user_answer", "withdrawn"},
    "waiting_for_user_answer": {"materials_in_progress", "ready_to_fill", "withdrawn"},
    "ready_to_fill": {"filling", "withdrawn", "closed"},
    "filling": {"waiting_for_user_answer", "waiting_for_submission_approval", "submission_failed", "waiting_for_user_takeover", "withdrawn"},
    "submission_failed": {"ready_to_fill", "waiting_for_user_takeover", "withdrawn"},
    "waiting_for_user_takeover": {"ready_to_fill", "waiting_for_submission_approval", "withdrawn", "closed"},
    "waiting_for_submission_approval": {"pre_submit_ready", "withdrawn"},
    "pre_submit_ready": {"submitting", "waiting_for_user_answer", "withdrawn"},
    "submitting": {"submitted", "submission_failed", "submission_uncertain"},
    "submission_uncertain": {"submitted", "submission_failed"},
    "submitted": {"rejected", "recruiter_response", "withdrawn", "no_response"},
    "recruiter_response": {"screening_call", "rejected", "withdrawn", "no_response"},
    "screening_call": {"interview", "rejected", "withdrawn"},
    "interview": {"final_interview", "rejected", "withdrawn"},
    "final_interview": {"offer", "rejected", "withdrawn"},
    "offer": {"withdrawn"},
    "closed": set(), "rejected": set(), "withdrawn": set(), "no_response": set(),
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def canonicalize_url(value: str) -> str:
    if value in {"", "unknown", "local-file"}:
        return value
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value.rstrip("/")
    query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in TRACKING_PARAMETERS:
            continue
        query.append((key, item))
    host = (parsed.hostname or "").casefold()
    if parsed.port:
        host = f"{host}:{parsed.port}"
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), host, path, urlencode(sorted(query)), ""))


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
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            canonical_url TEXT,
            original_url TEXT,
            employer TEXT NOT NULL,
            title TEXT NOT NULL,
            location TEXT,
            normalized_employer TEXT NOT NULL,
            normalized_title TEXT NOT NULL,
            normalized_location TEXT NOT NULL,
            requisition_id TEXT,
            description_sha256 TEXT,
            source TEXT,
            ats TEXT,
            job_card_json TEXT NOT NULL,
            status TEXT NOT NULL,
            duplicate_of TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (duplicate_of) REFERENCES jobs(job_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS jobs_url_unique ON jobs(canonical_url) WHERE canonical_url NOT IN ('', 'unknown', 'local-file');
        CREATE INDEX IF NOT EXISTS jobs_req_idx ON jobs(normalized_employer, requisition_id);
        CREATE INDEX IF NOT EXISTS jobs_desc_idx ON jobs(description_sha256);
        CREATE INDEX IF NOT EXISTS jobs_identity_idx ON jobs(normalized_employer, normalized_title, normalized_location);

        CREATE TABLE IF NOT EXISTS job_sources (
            source_url TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            discovered_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs(job_id)
        );

        CREATE TABLE IF NOT EXISTS applications (
            application_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            category TEXT NOT NULL,
            submission_policy TEXT NOT NULL,
            resume_version_id TEXT,
            cover_letter_version_id TEXT,
            authorization_id TEXT,
            pre_submit_check_passed INTEGER NOT NULL DEFAULT 0,
            pre_submit_review_id TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            worker_id TEXT,
            lease_expires_at TEXT,
            last_error_code TEXT,
            submitted_at TEXT,
            confirmation_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs(job_id)
        );
        CREATE INDEX IF NOT EXISTS applications_queue_idx ON applications(state, category, created_at);

        CREATE TABLE IF NOT EXISTS application_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY (application_id) REFERENCES applications(application_id)
        );

        CREATE TABLE IF NOT EXISTS submission_evidence (
            evidence_id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            reference TEXT,
            confirmation_id TEXT,
            captured_at TEXT NOT NULL,
            FOREIGN KEY (application_id) REFERENCES applications(application_id)
        );
    """)
    application_columns = {row[1] for row in connection.execute("PRAGMA table_info(applications)")}
    if "pre_submit_review_id" not in application_columns:
        connection.execute("ALTER TABLE applications ADD COLUMN pre_submit_review_id TEXT")
    connection.commit()


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_active_material_lock(connection: sqlite3.Connection, application_id: str) -> sqlite3.Row:
    if not _table_exists(connection, "material_locks") or not _table_exists(connection, "resume_versions"):
        raise ValueError("resume material store is unavailable")
    row = connection.execute("""
        SELECT ml.*, rv.status AS resume_status, rv.snapshot_path, rv.file_sha256 AS current_file_sha256,
               rv.claims_manifest_path, rv.claims_manifest_sha256,
               a.resume_version_id AS bound_resume_version_id,
               a.cover_letter_version_id AS bound_cover_letter_version_id,
               a.job_id AS application_job_id
        FROM material_locks ml
        JOIN resume_versions rv ON rv.version_id=ml.resume_version_id
        JOIN applications a ON a.application_id=ml.application_id
        WHERE ml.application_id=? AND ml.invalidated_at IS NULL
    """, (application_id,)).fetchone()
    if not row:
        raise ValueError("application has no active material lock")
    if row["resume_status"] != "approved":
        raise ValueError("locked resume version is not approved")
    if row["bound_resume_version_id"] != row["resume_version_id"]:
        raise ValueError("material lock does not match the bound resume")
    if row["resume_file_sha256"] != row["current_file_sha256"]:
        raise ValueError("material lock resume hash mismatch")
    if row["bound_cover_letter_version_id"] != row["cover_letter_version_id"]:
        raise ValueError("material lock does not match the bound cover letter")
    snapshot = Path(row["snapshot_path"])
    if not snapshot.is_file() or _hash_file(str(snapshot)) != row["resume_file_sha256"]:
        raise ValueError("locked resume snapshot hash mismatch")
    manifest_path = row["claims_manifest_path"]
    if not manifest_path or not row["claims_manifest_sha256"]:
        raise ValueError("locked resume claims manifest is unavailable")
    manifest = Path(manifest_path)
    if not manifest.is_file() or _hash_file(str(manifest)) != row["claims_manifest_sha256"]:
        raise ValueError("locked resume claims manifest hash mismatch")
    if row["cover_letter_version_id"]:
        if not _table_exists(connection, "cover_letter_versions"):
            raise ValueError("cover-letter version store is unavailable")
        cover = connection.execute(
            "SELECT * FROM cover_letter_versions WHERE version_id=?", (row["cover_letter_version_id"],)
        ).fetchone()
        if not cover or cover["status"] != "approved":
            raise ValueError("locked cover-letter version is not approved")
        if cover["kind"] == "application_specific" and (
            cover["application_id"] != application_id or cover["job_id"] != row["application_job_id"]
        ):
            raise ValueError("locked cover letter is scoped to another application")
        if cover["file_sha256"] != row["cover_letter_file_sha256"]:
            raise ValueError("material lock cover-letter hash mismatch")
        cover_snapshot = Path(cover["snapshot_path"])
        if not cover_snapshot.is_file() or _hash_file(str(cover_snapshot)) != row["cover_letter_file_sha256"]:
            raise ValueError("locked cover-letter snapshot hash mismatch")
        cover_manifest = Path(cover["claims_manifest_path"] or "")
        if not cover_manifest.is_file() or _hash_file(str(cover_manifest)) != cover["claims_manifest_sha256"]:
            raise ValueError("locked cover-letter claims manifest hash mismatch")
    return row


def require_approved_pre_submit_review(
    connection: sqlite3.Connection,
    application_id: str,
    review_id: str | None = None,
    at: datetime | None = None,
) -> sqlite3.Row:
    if not _table_exists(connection, "pre_submit_reviews") or not _table_exists(connection, "form_inventories"):
        raise ValueError("pre-submit review store is unavailable")
    application = connection.execute(
        "SELECT pre_submit_review_id FROM applications WHERE application_id=?", (application_id,)
    ).fetchone()
    actual_review_id = review_id or (application["pre_submit_review_id"] if application else None)
    if not actual_review_id:
        raise ValueError("approved pre-submit review is required")
    review = connection.execute("""
        SELECT psr.*, fi.status AS inventory_status, fi.known_form
        FROM pre_submit_reviews psr JOIN form_inventories fi ON fi.inventory_id=psr.inventory_id
        WHERE psr.review_id=? AND psr.application_id=?
    """, (actual_review_id, application_id)).fetchone()
    if not review or review["status"] != "approved" or review["approved_by"] != "user":
        raise ValueError("pre-submit review is not user-approved")
    if review["inventory_status"] != "active" or review["invalidated_at"]:
        raise ValueError("pre-submit review is stale")
    summary = json.loads(review["summary_json"])
    summary_hash = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if summary_hash != review["summary_sha256"]:
        raise ValueError("pre-submit review summary hash mismatch")
    summary_context = summary.get("context", {})
    for field in summary.get("fields", []):
        stored_field = connection.execute(
            "SELECT * FROM application_fields WHERE application_id=? AND field_id=?",
            (application_id, field.get("field_id")),
        ).fetchone()
        if not stored_field or stored_field["source_kind"] != field.get("source_kind") or stored_field["source_id"] != field.get("source_id"):
            raise ValueError("pre-submit review field source is stale")
        value_hash = hashlib.sha256(stored_field["value_json"].encode("utf-8")).hexdigest()
        if value_hash != field.get("value_sha256"):
            raise ValueError("pre-submit review field value changed")
        if stored_field["source_kind"] == "fact":
            if stored_field["source_status"] != "locked":
                raise ValueError("pre-submit review fact is no longer locked")
            if _table_exists(connection, "candidate_facts"):
                fact = connection.execute("""
                    SELECT cf.value_json, cf.status, cf.locked
                    FROM material_locks ml
                    JOIN resume_versions rv ON rv.version_id=ml.resume_version_id
                    JOIN candidate_snapshots cs ON cs.content_sha256=rv.candidate_profile_sha256
                    JOIN candidate_facts cf ON cf.content_sha256=cs.content_sha256
                    WHERE ml.application_id=? AND ml.invalidated_at IS NULL
                      AND cs.status='active' AND cs.registered_by='user' AND cf.fact_id=?
                """, (application_id, stored_field["source_id"])).fetchone()
                if not fact or fact["status"] != "locked" or not fact["locked"]:
                    raise ValueError("pre-submit review CandidateFact is no longer locked")
                if fact["value_json"] != stored_field["value_json"]:
                    raise ValueError("pre-submit review CandidateFact value changed")
        elif stored_field["source_kind"] == "answer":
            answer = connection.execute("SELECT * FROM answers WHERE answer_id=?", (stored_field["source_id"],)).fetchone()
            if not answer or answer["status"] != "active" or answer["confirmation_status"] != "confirmed":
                raise ValueError("pre-submit review answer is no longer active")
            current_time = at or now_utc()
            effective = parse_time(answer["effective_from"])
            expires = parse_time(answer["expires_at"])
            review_after = parse_time(answer["review_after"])
            if effective and current_time < effective:
                raise ValueError("pre-submit review answer is not effective")
            if expires and current_time >= expires:
                raise ValueError("pre-submit review answer is expired")
            if review_after and current_time >= review_after:
                raise ValueError("pre-submit review answer is due for review")
            if answer["answer_json"] != stored_field["value_json"]:
                raise ValueError("pre-submit review answer value changed")
            for key, expected in json.loads(answer["scope_json"]).items():
                actual = summary_context.get(key)
                if (isinstance(expected, list) and actual not in expected) or (not isinstance(expected, list) and actual != expected):
                    raise ValueError("pre-submit review answer scope is stale")
            if answer["answer_type"] == "legal_commitment":
                raise ValueError("pre-submit review contains a legal commitment")
        else:
            raise ValueError("pre-submit review contains an unknown field source")
    material = require_active_material_lock(connection, application_id)
    if material["lock_id"] != review["material_lock_id"]:
        raise ValueError("pre-submit review material lock is stale")
    summary_materials = summary.get("materials", {})
    if (summary_materials.get("resume_sha256") != material["resume_file_sha256"]
            or summary_materials.get("cover_letter_sha256") != material["cover_letter_file_sha256"]):
        raise ValueError("pre-submit review material hashes are stale")
    authorization = connection.execute(
        "SELECT * FROM authorizations WHERE authorization_id=?", (review["authorization_id"],)
    ).fetchone()
    current_time = at or now_utc()
    if not authorization or authorization["status"] != "active" or authorization["revoked_at"]:
        raise ValueError("pre-submit review authorization is not active")
    if current_time >= parse_time(authorization["expires_at"]):
        raise ValueError("pre-submit review authorization is expired")
    return review


def _event(
    connection: sqlite3.Connection,
    application_id: str,
    actor: str,
    from_state: str | None,
    to_state: str,
    reason_code: str,
    metadata: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> None:
    safe_metadata = metadata or {}
    connection.execute(
        "INSERT INTO application_events (application_id, created_at, actor, from_state, to_state, reason_code, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (application_id, (at or now_utc()).isoformat(), actor, from_state, to_state, reason_code,
         json.dumps(safe_metadata, sort_keys=True, separators=(",", ":"))),
    )


def find_duplicate(connection: sqlite3.Connection, card: dict[str, Any]) -> dict[str, Any]:
    url = canonicalize_url(str(card.get("canonical_url") or ""))
    employer = normalize_text(card.get("employer"))
    title = normalize_text(card.get("title"))
    location = normalize_text(card.get("location"))
    requisition = str(card.get("requisition_id") or "").strip()
    description_hash = str(card.get("description_sha256") or "").strip()

    if url not in {"", "unknown", "local-file"}:
        row = connection.execute("SELECT job_id FROM jobs WHERE canonical_url=?", (url,)).fetchone()
        if row:
            return {"decision": "duplicate", "reason": "canonical_url", "job_id": row["job_id"]}
    if employer and requisition:
        row = connection.execute(
            "SELECT job_id FROM jobs WHERE normalized_employer=? AND requisition_id=?",
            (employer, requisition),
        ).fetchone()
        if row:
            return {"decision": "duplicate", "reason": "employer_requisition", "job_id": row["job_id"]}
    if description_hash:
        row = connection.execute(
            "SELECT job_id FROM jobs WHERE description_sha256=? AND normalized_employer=? AND normalized_title=?",
            (description_hash, employer, title),
        ).fetchone()
        if row:
            return {"decision": "duplicate", "reason": "description_fingerprint", "job_id": row["job_id"]}
    if employer and title and location:
        row = connection.execute(
            "SELECT job_id FROM jobs WHERE normalized_employer=? AND normalized_title=? AND normalized_location=?",
            (employer, title, location),
        ).fetchone()
        if row:
            return {"decision": "review", "reason": "normalized_identity", "job_id": row["job_id"]}
    return {"decision": "new", "reason": None, "job_id": None}


def ingest_job(
    connection: sqlite3.Connection,
    card: dict[str, Any],
    allow_possible_duplicate: bool = False,
    at: datetime | None = None,
) -> dict[str, Any]:
    for field in ("job_id", "canonical_url", "employer", "title"):
        if field not in card:
            raise ValueError(f"job card missing required field: {field}")
    duplicate = find_duplicate(connection, card)
    if duplicate["decision"] == "duplicate":
        source_url = canonicalize_url(str(card.get("canonical_url") or ""))
        if source_url not in {"", "unknown", "local-file"}:
            connection.execute(
                "INSERT OR IGNORE INTO job_sources VALUES (?, ?, ?)",
                (source_url, duplicate["job_id"], (at or now_utc()).isoformat()),
            )
            connection.commit()
        return duplicate
    if duplicate["decision"] == "review" and not allow_possible_duplicate:
        return duplicate
    timestamp = (at or now_utc()).isoformat()
    canonical_url = canonicalize_url(str(card["canonical_url"]))
    original_url = str(card["canonical_url"])
    job_id = str(card["job_id"])
    try:
        connection.execute("""
            INSERT INTO jobs (
                job_id, canonical_url, original_url, employer, title, location,
                normalized_employer, normalized_title, normalized_location, requisition_id,
                description_sha256, source, ats, job_card_json, status, duplicate_of, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, canonical_url, original_url, card["employer"], card["title"], card.get("location"),
            normalize_text(card["employer"]), normalize_text(card["title"]), normalize_text(card.get("location")),
            card.get("requisition_id"), card.get("description_sha256"), card.get("source"), card.get("ats"),
            json.dumps(card, sort_keys=True, separators=(",", ":"), ensure_ascii=False), card.get("status", "unknown"),
            duplicate["job_id"] if duplicate["decision"] == "review" else None, timestamp, timestamp,
        ))
        if canonical_url not in {"", "unknown", "local-file"}:
            connection.execute(
                "INSERT OR IGNORE INTO job_sources VALUES (?, ?, ?)", (canonical_url, job_id, timestamp)
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "decision": "inserted_with_review" if duplicate["decision"] == "review" else "inserted",
        "reason": duplicate["reason"], "job_id": job_id, "possible_duplicate_of": duplicate["job_id"],
    }


def create_application(
    connection: sqlite3.Connection,
    application_id: str,
    job_id: str,
    category: str = "review",
    submission_policy: str = "stop_before_submit",
    at: datetime | None = None,
) -> dict[str, Any]:
    if category not in {"precision", "broad", "review"}:
        raise ValueError("category must be precision, broad, or review")
    if submission_policy not in {"never_submit", "stop_before_submit", "approved_after_summary", "known_forms_only", "approved_queue"}:
        raise ValueError("invalid submission policy")
    if not connection.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone():
        raise ValueError("job not found")
    job = connection.execute("SELECT duplicate_of, job_card_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if json.loads(job["job_card_json"]).get("already_applied"):
        return {"decision": "duplicate_application", "application_id": None, "state": "external_history"}
    related_ids = {job_id}
    if job["duplicate_of"]:
        related_ids.add(job["duplicate_of"])
    related_ids.update(
        row["job_id"] for row in connection.execute("SELECT job_id FROM jobs WHERE duplicate_of=?", (job_id,)).fetchall()
    )
    placeholders = ",".join("?" for _ in related_ids)
    existing = connection.execute(
        f"SELECT application_id, state FROM applications WHERE job_id IN ({placeholders}) LIMIT 1",
        tuple(related_ids),
    ).fetchone()
    if existing:
        return {"decision": "duplicate_application", "application_id": existing["application_id"], "state": existing["state"]}
    timestamp = (at or now_utc()).isoformat()
    connection.execute("""
        INSERT INTO applications (
            application_id, job_id, state, category, submission_policy, created_at, updated_at
        ) VALUES (?, ?, 'discovered', ?, ?, ?, ?)
    """, (application_id, job_id, category, submission_policy, timestamp, timestamp))
    _event(connection, application_id, "system", None, "discovered", "application_created", at=at)
    connection.commit()
    return {"decision": "created", "application_id": application_id, "state": "discovered"}


def transition(
    connection: sqlite3.Connection,
    application_id: str,
    to_state: str,
    actor: str,
    reason_code: str,
    metadata: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    if to_state not in APPLICATION_STATES:
        raise ValueError(f"unknown application state: {to_state}")
    row = connection.execute("SELECT * FROM applications WHERE application_id=?", (application_id,)).fetchone()
    if not row:
        raise ValueError("application not found")
    current = row["state"]
    if to_state not in TRANSITIONS[current]:
        raise ValueError(f"invalid transition: {current} -> {to_state}")
    metadata = metadata or {}
    if to_state == "approved" and actor != "user":
        raise ValueError("application approval requires the user actor")
    if to_state == "filling":
        raise ValueError("use acquire_next to enter the filling state")
    if to_state in {"ready_to_fill", "pre_submit_ready", "submitting"}:
        require_active_material_lock(connection, application_id)
    if to_state == "pre_submit_ready":
        pre_submit_review = require_approved_pre_submit_review(
            connection, application_id, metadata.get("pre_submit_review_id"), at
        )
    if to_state == "submitting":
        if not row["pre_submit_check_passed"]:
            raise ValueError("cannot submit before the pre-submit check passes")
        pre_submit_review = require_approved_pre_submit_review(connection, application_id, at=at)
        authorization_id = metadata.get("authorization_id") or row["authorization_id"]
        if not authorization_id:
            raise ValueError("cannot submit without an authorization ID")
        if authorization_id != pre_submit_review["authorization_id"]:
            raise ValueError("submission authorization does not match the approved pre-submit review")
        authorization_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='authorizations'"
        ).fetchone()
        if not authorization_table:
            raise ValueError("authorization store is unavailable")
        authorization = connection.execute(
            "SELECT * FROM authorizations WHERE authorization_id=?", (authorization_id,)
        ).fetchone()
        transition_time = at or now_utc()
        if not authorization or authorization["status"] != "active" or authorization["revoked_at"]:
            raise ValueError("submission authorization is not active")
        if transition_time >= parse_time(authorization["expires_at"]):
            raise ValueError("submission authorization is expired")
        policy = row["submission_policy"]
        if policy in {"never_submit", "stop_before_submit"}:
            raise ValueError(f"submission policy {policy} blocks submission")
        if policy == "known_forms_only" and not pre_submit_review["known_form"]:
            raise ValueError("submission policy requires a known form")
        if policy == "approved_queue" and not metadata.get("approved_queue"):
            raise ValueError("submission policy requires an approved queue")
    if to_state == "submitted":
        evidence = connection.execute(
            "SELECT confirmation_id FROM submission_evidence WHERE application_id=? AND evidence_type IN ('success_page','confirmation_id','account_record','email_confirmation') LIMIT 1",
            (application_id,),
        ).fetchone()
        if not evidence:
            raise ValueError("submitted state requires positive submission evidence")
        material_lock = require_active_material_lock(connection, application_id)
    if current == "submission_uncertain" and not (actor == "user" and metadata.get("manual_resolution")):
        raise ValueError("submission uncertainty requires explicit user resolution")
    if to_state == "submission_failed":
        error_code = metadata.get("error_code")
        if error_code not in FAILURE_CODES:
            raise ValueError("submission_failed requires a recognized error_code")

    timestamp = (at or now_utc()).isoformat()
    fields = ["state=?", "updated_at=?", "worker_id=NULL", "lease_expires_at=NULL"]
    values: list[Any] = [to_state, timestamp]
    if to_state in {
        "materials_in_progress", "ready_to_fill", "filling", "waiting_for_user_answer",
        "waiting_for_submission_approval", "submission_failed", "waiting_for_user_takeover",
    }:
        fields.append("pre_submit_check_passed=0")
        fields.append("pre_submit_review_id=NULL")
    if to_state == "pre_submit_ready":
        fields.append("pre_submit_check_passed=1")
        fields.append("pre_submit_review_id=?")
        values.append(pre_submit_review["review_id"])
    if to_state == "submitting":
        fields.append("authorization_id=?")
        values.append(metadata.get("authorization_id") or row["authorization_id"])
    if to_state == "submission_failed":
        fields.append("last_error_code=?")
        values.append(metadata["error_code"])
    if to_state == "submitted":
        fields.append("submitted_at=?")
        values.append(timestamp)
        if evidence and evidence["confirmation_id"]:
            fields.append("confirmation_id=?")
            values.append(evidence["confirmation_id"])
    values.append(application_id)
    connection.execute(f"UPDATE applications SET {', '.join(fields)} WHERE application_id=?", values)
    if to_state in {
        "materials_in_progress", "ready_to_fill", "filling", "waiting_for_user_answer",
        "waiting_for_submission_approval", "submission_failed", "waiting_for_user_takeover",
        "withdrawn", "closed",
    } and _table_exists(connection, "pre_submit_reviews"):
        connection.execute("""
            UPDATE pre_submit_reviews SET status='invalidated', invalidated_at=?, invalidation_reason=?
            WHERE application_id=? AND status IN ('generated','approved')
        """, (timestamp, f"application_entered_{to_state}", application_id))
    if to_state == "submitted" and _table_exists(connection, "resume_usage"):
        connection.execute("""
            INSERT OR IGNORE INTO resume_usage (
                version_id, application_id, job_id, use_type, file_sha256, recorded_at
            ) VALUES (?, ?, ?, 'submitted', ?, ?)
        """, (
            material_lock["resume_version_id"], application_id, row["job_id"],
            material_lock["resume_file_sha256"], timestamp,
        ))
    if (to_state == "submitted" and material_lock["cover_letter_version_id"]
            and _table_exists(connection, "cover_letter_usage")):
        connection.execute("""
            INSERT OR IGNORE INTO cover_letter_usage (
                version_id, application_id, job_id, use_type, file_sha256, recorded_at
            ) VALUES (?, ?, ?, 'submitted', ?, ?)
        """, (
            material_lock["cover_letter_version_id"], application_id, row["job_id"],
            material_lock["cover_letter_file_sha256"], timestamp,
        ))
    event_metadata = {
        key: metadata[key] for key in (
            "authorization_id", "error_code", "manual_resolution", "pre_submit_review_id", "approved_queue"
        ) if key in metadata
    }
    _event(connection, application_id, actor, current, to_state, reason_code, event_metadata, at)
    connection.commit()
    return {"application_id": application_id, "from_state": current, "state": to_state}


def record_evidence(
    connection: sqlite3.Connection,
    evidence_id: str,
    application_id: str,
    evidence_type: str,
    reference: str | None = None,
    confirmation_id: str | None = None,
    at: datetime | None = None,
) -> None:
    if evidence_type not in SUCCESS_EVIDENCE_TYPES:
        raise ValueError("unsupported success evidence type")
    if evidence_type == "confirmation_id" and not confirmation_id:
        raise ValueError("confirmation_id evidence requires a confirmation ID")
    application = connection.execute("SELECT state FROM applications WHERE application_id=?", (application_id,)).fetchone()
    if not application:
        raise ValueError("application not found")
    if application["state"] not in {"submitting", "submission_uncertain"}:
        raise ValueError("submission evidence may only be recorded during or after a submission attempt")
    if evidence_type != "confirmation_id" and not reference:
        raise ValueError(f"{evidence_type} evidence requires a reference")
    connection.execute(
        "INSERT INTO submission_evidence VALUES (?, ?, ?, ?, ?, ?)",
        (evidence_id, application_id, evidence_type, reference, confirmation_id, (at or now_utc()).isoformat()),
    )
    connection.commit()


def acquire_next(
    connection: sqlite3.Connection,
    worker_id: str,
    lease_seconds: int = 300,
    at: datetime | None = None,
) -> dict[str, Any] | None:
    if not worker_id.strip():
        raise ValueError("worker_id is required")
    if lease_seconds < 30 or lease_seconds > 1800:
        raise ValueError("lease_seconds must be between 30 and 1800")
    current_time = at or now_utc()
    connection.execute("BEGIN IMMEDIATE")
    try:
        if not _table_exists(connection, "material_locks") or not _table_exists(connection, "resume_versions"):
            connection.rollback()
            raise ValueError("resume material store is unavailable")
        row = connection.execute("""
            SELECT a.* FROM applications a
            JOIN material_locks ml ON ml.application_id=a.application_id AND ml.invalidated_at IS NULL
            JOIN resume_versions rv ON rv.version_id=ml.resume_version_id AND rv.status='approved'
            WHERE a.attempts < a.max_attempts
              AND a.resume_version_id=ml.resume_version_id
              AND ml.resume_file_sha256=rv.file_sha256
              AND (a.state='ready_to_fill' OR (a.state='filling' AND a.lease_expires_at < ?))
            ORDER BY CASE a.category WHEN 'precision' THEN 0 WHEN 'broad' THEN 1 ELSE 2 END, a.created_at
            LIMIT 1
        """, (current_time.isoformat(),)).fetchone()
        if not row:
            connection.rollback()
            return None
        require_active_material_lock(connection, row["application_id"])
        lease_expires = current_time + timedelta(seconds=lease_seconds)
        connection.execute("""
            UPDATE applications
            SET state='filling', worker_id=?, lease_expires_at=?, attempts=attempts+1,
                pre_submit_check_passed=0, updated_at=?
            WHERE application_id=?
        """, (worker_id, lease_expires.isoformat(), current_time.isoformat(), row["application_id"]))
        reason = "fill_lease_acquired" if row["state"] == "ready_to_fill" else "expired_fill_lease_reacquired"
        _event(connection, row["application_id"], worker_id, row["state"], "filling", reason,
               {"lease_seconds": lease_seconds}, current_time)
        connection.commit()
        return {
            "application_id": row["application_id"], "job_id": row["job_id"], "state": "filling",
            "worker_id": worker_id, "lease_expires_at": lease_expires.isoformat(), "attempt": row["attempts"] + 1,
        }
    except Exception:
        connection.rollback()
        raise


def release_lease(
    connection: sqlite3.Connection,
    application_id: str,
    worker_id: str,
    to_state: str,
    reason_code: str,
    error_code: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM applications WHERE application_id=?", (application_id,)).fetchone()
    if not row or row["state"] != "filling" or row["worker_id"] != worker_id:
        raise ValueError("active lease is not owned by this worker")
    metadata = {"error_code": error_code} if error_code else {}
    return transition(connection, application_id, to_state, worker_id, reason_code, metadata, at)


def status(connection: sqlite3.Connection) -> dict[str, Any]:
    jobs = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    rows = connection.execute("SELECT state, COUNT(*) AS count FROM applications GROUP BY state ORDER BY state").fetchall()
    return {"jobs": jobs, "applications": sum(row["count"] for row in rows), "by_state": {row["state"]: row["count"] for row in rows}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    ingest = commands.add_parser("ingest-job")
    ingest.add_argument("--card", required=True, type=Path)
    ingest.add_argument("--allow-possible-duplicate", action="store_true")
    create = commands.add_parser("create-application")
    create.add_argument("--application-id", required=True)
    create.add_argument("--job-id", required=True)
    create.add_argument("--category", choices=["precision", "broad", "review"], default="review")
    create.add_argument("--submission-policy", default="stop_before_submit")
    move = commands.add_parser("transition")
    move.add_argument("--application-id", required=True)
    move.add_argument("--to", required=True)
    move.add_argument("--actor", required=True)
    move.add_argument("--reason-code", required=True)
    move.add_argument("--metadata", type=Path)
    acquire = commands.add_parser("acquire")
    acquire.add_argument("--worker-id", required=True)
    acquire.add_argument("--lease-seconds", type=int, default=300)
    release = commands.add_parser("release")
    release.add_argument("--application-id", required=True)
    release.add_argument("--worker-id", required=True)
    release.add_argument("--to", required=True)
    release.add_argument("--reason-code", required=True)
    release.add_argument("--error-code")
    evidence = commands.add_parser("record-evidence")
    evidence.add_argument("--evidence-id", required=True)
    evidence.add_argument("--application-id", required=True)
    evidence.add_argument("--type", required=True)
    evidence.add_argument("--reference")
    evidence.add_argument("--confirmation-id")
    commands.add_parser("status")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    if args.command == "init":
        result = {"status": "initialized", "db": str(args.db)}
    elif args.command == "ingest-job":
        result = ingest_job(connection, json.loads(args.card.read_text(encoding="utf-8")), args.allow_possible_duplicate)
    elif args.command == "create-application":
        result = create_application(connection, args.application_id, args.job_id, args.category, args.submission_policy)
    elif args.command == "transition":
        metadata = json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else {}
        result = transition(connection, args.application_id, args.to, args.actor, args.reason_code, metadata)
    elif args.command == "acquire":
        result = acquire_next(connection, args.worker_id, args.lease_seconds)
    elif args.command == "release":
        result = release_lease(connection, args.application_id, args.worker_id, args.to, args.reason_code, args.error_code)
    elif args.command == "record-evidence":
        record_evidence(connection, args.evidence_id, args.application_id, args.type, args.reference, args.confirmation_id)
        result = {"status": "recorded", "evidence_id": args.evidence_id}
    else:
        result = status(connection)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
