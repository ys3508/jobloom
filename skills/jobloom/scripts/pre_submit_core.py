#!/usr/bin/env python3
"""Deterministic form inventory and user-approved pre-submission summaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_LEGAL_ITEMS = {"standard_attestation", "privacy_notice", "equal_opportunity_notice"}
MANDATORY_PAUSES = {
    "captcha", "assessment", "payment", "identity_document", "tax_document", "banking_document",
    "camera", "microphone", "video", "audio", "biometric", "unapproved_upload", "unsafe_page",
    "arbitration", "non_compete", "ip_assignment", "special_signature", "unknown_legal_term",
}
UPLOAD_KINDS = {"resume", "cover_letter"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


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
        CREATE TABLE IF NOT EXISTS form_inventories (
            inventory_id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            form_url TEXT NOT NULL,
            observed_employer TEXT NOT NULL,
            observed_role TEXT NOT NULL,
            known_form INTEGER NOT NULL,
            required_field_ids_json TEXT NOT NULL,
            legal_items_json TEXT NOT NULL,
            restricted_requests_json TEXT NOT NULL,
            uploads_json TEXT NOT NULL,
            inventory_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            invalidated_at TEXT,
            invalidation_reason TEXT,
            FOREIGN KEY (application_id) REFERENCES applications(application_id)
        );
        CREATE INDEX IF NOT EXISTS form_inventory_application_idx
            ON form_inventories(application_id, status, created_at);

        CREATE TABLE IF NOT EXISTS pre_submit_reviews (
            review_id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            inventory_id TEXT NOT NULL,
            authorization_id TEXT NOT NULL,
            material_lock_id TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            summary_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            approved_at TEXT,
            approved_by TEXT,
            invalidated_at TEXT,
            invalidation_reason TEXT,
            FOREIGN KEY (application_id) REFERENCES applications(application_id),
            FOREIGN KEY (inventory_id) REFERENCES form_inventories(inventory_id)
        );
        CREATE INDEX IF NOT EXISTS pre_submit_review_application_idx
            ON pre_submit_reviews(application_id, status, created_at);

        CREATE TABLE IF NOT EXISTS pre_submit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id TEXT,
            application_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(applications)")}
    if "pre_submit_review_id" not in columns:
        connection.execute("ALTER TABLE applications ADD COLUMN pre_submit_review_id TEXT")
    connection.commit()


def _event(
    connection: sqlite3.Connection,
    application_id: str,
    actor: str,
    event_type: str,
    reason_code: str,
    review_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> None:
    connection.execute(
        "INSERT INTO pre_submit_events (review_id, application_id, created_at, actor, event_type, reason_code, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (review_id, application_id, (at or now_utc()).isoformat(), actor, event_type, reason_code,
         canonical_json(metadata or {})),
    )


def register_inventory(
    connection: sqlite3.Connection,
    inventory_id: str,
    application_id: str,
    form_url: str,
    observed_employer: str,
    observed_role: str,
    known_form: bool,
    required_field_ids: list[str],
    legal_items: list[str],
    restricted_requests: list[str],
    uploads: list[dict[str, str]],
    at: datetime | None = None,
) -> dict[str, Any]:
    application = connection.execute(
        "SELECT state FROM applications WHERE application_id=?", (application_id,)
    ).fetchone()
    if not application:
        raise ValueError("application not found")
    if application["state"] != "filling":
        raise ValueError("form inventory may only be registered during filling")
    if not isinstance(known_form, bool):
        raise ValueError("known_form must be true or false")
    if not form_url.strip() or not observed_employer.strip() or not observed_role.strip():
        raise ValueError("form URL, observed employer, and observed role are required")
    if not isinstance(required_field_ids, list) or not required_field_ids or len(set(required_field_ids)) != len(required_field_ids):
        raise ValueError("required_field_ids must be a non-empty unique list")
    if any(not isinstance(value, str) or not value.strip() for value in required_field_ids):
        raise ValueError("required field IDs must be non-empty strings")
    if not isinstance(legal_items, list) or not all(isinstance(item, str) for item in legal_items):
        raise ValueError("legal_items must be a list of identifiers")
    if not isinstance(restricted_requests, list) or not all(isinstance(item, str) for item in restricted_requests):
        raise ValueError("restricted_requests must be a list of identifiers")
    if not isinstance(uploads, list) or not all(isinstance(item, dict) for item in uploads):
        raise ValueError("uploads must be a list of material mappings")
    unknown_legal = sorted(set(legal_items) - ALLOWED_LEGAL_ITEMS)
    if unknown_legal:
        raise ValueError(f"unsupported legal items require pause: {', '.join(unknown_legal)}")
    unknown_restrictions = sorted(set(restricted_requests) - MANDATORY_PAUSES)
    if unknown_restrictions:
        raise ValueError(f"unknown restricted request types: {', '.join(unknown_restrictions)}")
    seen_uploads: set[str] = set()
    for upload in uploads:
        if set(upload) != {"kind", "version_id"} or upload["kind"] not in UPLOAD_KINDS:
            raise ValueError("uploads must contain an allowlisted kind and version_id")
        if upload["kind"] in seen_uploads:
            raise ValueError("only one upload per material kind is allowed")
        seen_uploads.add(upload["kind"])
    value = {
        "inventory_id": inventory_id, "application_id": application_id, "form_url": form_url,
        "observed_employer": observed_employer, "observed_role": observed_role, "known_form": known_form,
        "required_field_ids": sorted(required_field_ids), "legal_items": sorted(legal_items),
        "restricted_requests": sorted(restricted_requests),
        "uploads": sorted(uploads, key=lambda item: item["kind"]),
    }
    digest = canonical_hash(value)
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE form_inventories SET status='invalidated', invalidated_at=?, invalidation_reason='new_inventory' WHERE application_id=? AND status='active'",
        (timestamp, application_id),
    )
    connection.execute("""
        INSERT INTO form_inventories (
            inventory_id, application_id, form_url, observed_employer, observed_role, known_form,
            required_field_ids_json, legal_items_json, restricted_requests_json, uploads_json,
            inventory_sha256, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
    """, (inventory_id, application_id, form_url, observed_employer, observed_role, int(known_form),
          canonical_json(value["required_field_ids"]), canonical_json(value["legal_items"]),
          canonical_json(value["restricted_requests"]), canonical_json(value["uploads"]), digest, timestamp))
    _event(connection, application_id, "system", "inventory_registered", "form_fields_and_risks_captured",
           metadata={"inventory_id": inventory_id, "required_field_count": len(required_field_ids),
                     "known_form": known_form, "restricted_request_count": len(restricted_requests)}, at=at)
    connection.commit()
    return {"inventory_id": inventory_id, "application_id": application_id, "status": "active",
            "inventory_sha256": digest}


def _context_matches(rules: dict[str, Any], context: dict[str, Any]) -> bool:
    for key, expected in rules.items():
        actual = context.get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _answer_issue(row: sqlite3.Row, context: dict[str, Any], at: datetime) -> str | None:
    if row["status"] != "active" or row["confirmation_status"] != "confirmed":
        return f"answer_{row['status']}"
    effective = parse_time(row["effective_from"])
    expires = parse_time(row["expires_at"])
    review_after = parse_time(row["review_after"])
    if effective and at < effective:
        return "answer_not_effective"
    if expires and at >= expires:
        return "answer_expired"
    if review_after and at >= review_after:
        return "answer_review_due"
    if not _context_matches(json.loads(row["scope_json"]), context):
        return "answer_scope_mismatch"
    if not _context_matches(json.loads(row["preconditions_json"]), context):
        return "answer_precondition_failed"
    exclusions = json.loads(row["exclusions_json"])
    if any(context.get(key) in (values if isinstance(values, list) else [values]) for key, values in exclusions.items()):
        return "answer_excluded"
    if row["answer_type"] == "legal_commitment":
        return "legal_commitment_requires_review"
    return None


def _active_material(connection: sqlite3.Connection, application_id: str) -> sqlite3.Row:
    row = connection.execute("""
        SELECT ml.*, rv.snapshot_path, rv.file_sha256 AS registered_resume_sha256,
               rv.status AS resume_status, rv.claims_manifest_path, rv.claims_manifest_sha256,
               a.resume_version_id AS bound_resume_version_id,
               a.cover_letter_version_id AS bound_cover_letter_version_id,
               a.job_id AS application_job_id
        FROM material_locks ml
        JOIN resume_versions rv ON rv.version_id=ml.resume_version_id
        JOIN applications a ON a.application_id=ml.application_id
        WHERE ml.application_id=? AND ml.invalidated_at IS NULL
    """, (application_id,)).fetchone()
    if not row or row["resume_status"] != "approved":
        raise ValueError("active approved material lock is required")
    if row["resume_version_id"] != row["bound_resume_version_id"]:
        raise ValueError("material lock does not match the bound resume")
    resume = Path(row["snapshot_path"])
    if not resume.is_file() or file_sha256(resume) != row["resume_file_sha256"]:
        raise ValueError("material-locked resume hash mismatch")
    if row["resume_file_sha256"] != row["registered_resume_sha256"]:
        raise ValueError("material lock differs from registered resume hash")
    claims = Path(row["claims_manifest_path"] or "")
    if not claims.is_file() or file_sha256(claims) != row["claims_manifest_sha256"]:
        raise ValueError("resume claims manifest hash mismatch")
    if row["cover_letter_version_id"] != row["bound_cover_letter_version_id"]:
        raise ValueError("material lock does not match the bound cover letter")
    if row["cover_letter_version_id"]:
        cover = connection.execute(
            "SELECT * FROM cover_letter_versions WHERE version_id=?", (row["cover_letter_version_id"],)
        ).fetchone()
        if not cover or cover["status"] != "approved" or cover["file_sha256"] != row["cover_letter_file_sha256"]:
            raise ValueError("cover-letter material lock is stale")
        if cover["kind"] == "application_specific" and (
            cover["application_id"] != application_id or cover["job_id"] != row["application_job_id"]
        ):
            raise ValueError("cover letter is scoped to another application")
        cover_snapshot = Path(cover["snapshot_path"])
        cover_manifest = Path(cover["claims_manifest_path"] or "")
        if not cover_snapshot.is_file() or file_sha256(cover_snapshot) != cover["file_sha256"]:
            raise ValueError("cover-letter snapshot hash mismatch")
        if not cover_manifest.is_file() or file_sha256(cover_manifest) != cover["claims_manifest_sha256"]:
            raise ValueError("cover-letter claims manifest hash mismatch")
    return row


def create_review(
    connection: sqlite3.Connection,
    review_id: str,
    inventory_id: str,
    authorization_id: str,
    authorization_context: dict[str, Any],
    at: datetime | None = None,
) -> dict[str, Any]:
    current_time = at or now_utc()
    if not isinstance(authorization_context, dict):
        raise ValueError("authorization_context must be an object")
    inventory = connection.execute("SELECT * FROM form_inventories WHERE inventory_id=?", (inventory_id,)).fetchone()
    if not inventory or inventory["status"] != "active":
        raise ValueError("active form inventory not found")
    application = connection.execute("""
        SELECT a.*, j.employer, j.title, j.canonical_url, j.job_card_json
        FROM applications a JOIN jobs j ON j.job_id=a.job_id WHERE a.application_id=?
    """, (inventory["application_id"],)).fetchone()
    if application["state"] != "waiting_for_submission_approval":
        raise ValueError("pre-submit review requires a completed fill awaiting approval")
    issues: list[str] = []
    if normalize_text(inventory["observed_employer"]) != normalize_text(application["employer"]):
        issues.append("employer_mismatch")
    if normalize_text(inventory["observed_role"]) != normalize_text(application["title"]):
        issues.append("role_mismatch")
    restrictions = json.loads(inventory["restricted_requests_json"])
    issues.extend(f"mandatory_pause:{item}" for item in restrictions)
    uploads = json.loads(inventory["uploads_json"])
    resume_uploads = [item for item in uploads if item["kind"] == "resume"]
    if len(resume_uploads) != 1 or resume_uploads[0]["version_id"] != application["resume_version_id"]:
        issues.append("incorrect_resume_upload")
    cover_uploads = [item for item in uploads if item["kind"] == "cover_letter"]
    if application["cover_letter_version_id"]:
        if len(cover_uploads) != 1 or cover_uploads[0]["version_id"] != application["cover_letter_version_id"]:
            issues.append("incorrect_cover_letter_upload")
    elif cover_uploads:
        issues.append("unapproved_cover_letter_upload")
    if application["submission_policy"] == "known_forms_only" and not inventory["known_form"]:
        issues.append("unknown_form_blocked_by_policy")

    required_ids = json.loads(inventory["required_field_ids_json"])
    field_rows = connection.execute(
        "SELECT * FROM application_fields WHERE application_id=? ORDER BY field_id",
        (application["application_id"],),
    ).fetchall()
    fields = {row["field_id"]: row for row in field_rows}
    issues.extend(f"required_field_missing:{field_id}" for field_id in required_ids if field_id not in fields)
    context = dict(authorization_context)
    card = json.loads(application["job_card_json"])
    context["application_id"] = application["application_id"]
    context["company"] = application["employer"]
    if card.get("country") is not None:
        context["country"] = card["country"]
    if card.get("employment_type") is not None:
        context["employment_type"] = card["employment_type"]
    for field in field_rows:
        if field["source_kind"] == "fact":
            if field["source_status"] != "locked":
                issues.append(f"fact_not_locked:{field['field_id']}")
        elif field["source_kind"] == "answer":
            answer = connection.execute("SELECT * FROM answers WHERE answer_id=?", (field["source_id"],)).fetchone()
            if not answer:
                issues.append(f"answer_unknown:{field['field_id']}")
            else:
                answer_issue = _answer_issue(answer, context, current_time)
                if answer_issue:
                    issues.append(f"{answer_issue}:{field['field_id']}")
                if answer["answer_json"] != field["value_json"]:
                    issues.append(f"answer_value_changed:{field['field_id']}")
        else:
            issues.append(f"field_source_unknown:{field['field_id']}")

    authorization = connection.execute(
        "SELECT * FROM authorizations WHERE authorization_id=?", (authorization_id,)
    ).fetchone()
    if not authorization or authorization["status"] != "active" or authorization["revoked_at"]:
        issues.append("authorization_not_active")
    else:
        authorization_expires = parse_time(authorization["expires_at"])
        if not authorization_expires or current_time >= authorization_expires:
            issues.append("authorization_expired")
        if not _context_matches(json.loads(authorization["scope_json"]), context):
            issues.append("authorization_scope_mismatch")
    material = _active_material(connection, application["application_id"])
    duplicate_count = connection.execute(
        "SELECT COUNT(*) FROM applications WHERE job_id=?", (application["job_id"],)
    ).fetchone()[0]
    if duplicate_count != 1:
        issues.append("duplicate_application_detected")
    if issues:
        raise ValueError("pre-submit review blocked: " + ", ".join(sorted(set(issues))))

    summary = {
        "schema_version": "0.1.0",
        "review_id": review_id,
        "application_id": application["application_id"],
        "job": {"employer": application["employer"], "role": application["title"],
                "application_url": inventory["form_url"]},
        "materials": {"resume_version_id": application["resume_version_id"],
                      "resume_sha256": material["resume_file_sha256"],
                      "cover_letter_version_id": application["cover_letter_version_id"],
                      "cover_letter_sha256": material["cover_letter_file_sha256"]},
        "fields": [{"field_id": row["field_id"], "source_kind": row["source_kind"],
                    "source_id": row["source_id"], "sensitivity": row["sensitivity"],
                    "value_sha256": hashlib.sha256(row["value_json"].encode("utf-8")).hexdigest(),
                    "status": "fresh"}
                   for row in field_rows],
        "authorization": {"authorization_id": authorization_id,
                          "scope": json.loads(authorization["scope_json"]),
                          "expires_at": authorization["expires_at"]},
        "context": {key: context[key] for key in (
            "country", "jurisdiction", "company", "role_family", "employment_type",
            "application_id", "queue_id"
        ) if key in context},
        "submission_policy": application["submission_policy"],
        "known_form": bool(inventory["known_form"]),
        "legal_items": json.loads(inventory["legal_items_json"]),
        "checks": {"identity_match": True, "required_fields_complete": True,
                   "field_sources_fresh": True, "materials_match": True,
                   "authorization_current_and_scoped": True, "duplicate_check_passed": True,
                   "mandatory_pauses": []},
    }
    summary_hash = canonical_hash(summary)
    timestamp = current_time.isoformat()
    connection.execute(
        "UPDATE pre_submit_reviews SET status='invalidated', invalidated_at=?, invalidation_reason='new_review' WHERE application_id=? AND status IN ('generated','approved')",
        (timestamp, application["application_id"]),
    )
    connection.execute("""
        INSERT INTO pre_submit_reviews (
            review_id, application_id, inventory_id, authorization_id, material_lock_id,
            summary_json, summary_sha256, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'generated', ?)
    """, (review_id, application["application_id"], inventory_id, authorization_id, material["lock_id"],
          canonical_json(summary), summary_hash, timestamp))
    _event(connection, application["application_id"], "system", "review_generated",
           "all_deterministic_checks_passed", review_id,
           {"summary_sha256": summary_hash, "field_count": len(field_rows)}, at)
    connection.commit()
    return {"review_id": review_id, "application_id": application["application_id"],
            "status": "generated", "summary_sha256": summary_hash, "summary": summary}


def approve_review(
    connection: sqlite3.Connection,
    review_id: str,
    actor: str,
    expected_summary_sha256: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    if actor != "user":
        raise ValueError("pre-submit summary approval requires the user actor")
    row = connection.execute("SELECT * FROM pre_submit_reviews WHERE review_id=?", (review_id,)).fetchone()
    if not row or row["status"] != "generated":
        raise ValueError("generated pre-submit review not found")
    if row["summary_sha256"] != expected_summary_sha256:
        raise ValueError("pre-submit summary hash does not match user-reviewed content")
    if canonical_hash(json.loads(row["summary_json"])) != row["summary_sha256"]:
        raise ValueError("stored pre-submit summary hash is invalid")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE pre_submit_reviews SET status='approved', approved_at=?, approved_by='user' WHERE review_id=?",
        (timestamp, review_id),
    )
    _event(connection, row["application_id"], actor, "review_approved", "user_approved_exact_summary",
           review_id, {"summary_sha256": row["summary_sha256"]}, at)
    connection.commit()
    return {"review_id": review_id, "application_id": row["application_id"],
            "status": "approved", "summary_sha256": row["summary_sha256"]}


def status(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute("SELECT status, COUNT(*) AS count FROM pre_submit_reviews GROUP BY status").fetchall()
    return {"inventories": connection.execute("SELECT COUNT(*) FROM form_inventories").fetchone()[0],
            "reviews": sum(row["count"] for row in rows),
            "by_status": {row["status"]: row["count"] for row in rows}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    inventory = commands.add_parser("register-inventory")
    inventory.add_argument("--input", required=True, type=Path)
    review = commands.add_parser("create-review")
    review.add_argument("--input", required=True, type=Path)
    approve = commands.add_parser("approve-review")
    approve.add_argument("--review-id", required=True)
    approve.add_argument("--actor", required=True)
    approve.add_argument("--summary-sha256", required=True)
    commands.add_parser("status")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    if args.command == "init":
        result = {"status": "initialized", "db": str(args.db)}
    elif args.command == "register-inventory":
        result = register_inventory(connection, **json.loads(args.input.read_text(encoding="utf-8")))
    elif args.command == "create-review":
        result = create_review(connection, **json.loads(args.input.read_text(encoding="utf-8")))
    elif args.command == "approve-review":
        result = approve_review(connection, args.review_id, args.actor, args.summary_sha256)
    else:
        result = status(connection)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
