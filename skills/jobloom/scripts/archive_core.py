#!/usr/bin/env python3
"""Create and verify immutable, redacted local archives for submitted applications."""

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
from urllib.parse import urlsplit, urlunsplit


ARCHIVABLE_STATES = {
    "submitted", "rejected", "recruiter_response", "screening_call", "interview",
    "final_interview", "offer", "withdrawn", "no_response",
}
FIELD_RECORDING_STATES = {
    "filling", "waiting_for_user_answer", "waiting_for_submission_approval", "pre_submit_ready", "submitting",
}
SENSITIVITY_CLASSES = {
    "normal", "address", "sensitive_personal", "date_of_birth", "identity_document",
    "tax_identifier", "banking", "credential",
}
OMIT_FROM_SNAPSHOT = {"date_of_birth", "identity_document", "tax_identifier", "banking", "credential"}
REDACT_IN_SNAPSHOT = {"address", "sensitive_personal"}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SENSITIVE_FIELD_PATTERN = re.compile(
    r"password|passcode|api.?key|session.?token|social.?security|\bssn\b|tax.?id|bank|routing|account.?number|"
    r"passport|driver.?license|identity.?document|date.?of.?birth|\bdob\b|full.?address",
    re.IGNORECASE,
)
SAFE_EVIDENCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf", ".txt"}
MAX_EVIDENCE_BYTES = 20 * 1024 * 1024


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
        CREATE TABLE IF NOT EXISTS application_fields (
            application_id TEXT NOT NULL,
            field_id TEXT NOT NULL,
            question TEXT NOT NULL,
            value_json TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_status TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (application_id, field_id),
            FOREIGN KEY (application_id) REFERENCES applications(application_id)
        );

        CREATE TABLE IF NOT EXISTS submission_archives (
            archive_id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL UNIQUE,
            archive_path TEXT NOT NULL UNIQUE,
            manifest_path TEXT NOT NULL UNIQUE,
            manifest_sha256 TEXT NOT NULL,
            archived_at TEXT NOT NULL,
            last_verified_at TEXT,
            verification_status TEXT NOT NULL,
            FOREIGN KEY (application_id) REFERENCES applications(application_id)
        );

        CREATE TABLE IF NOT EXISTS archive_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            archive_id TEXT,
            application_id TEXT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );
    """)
    connection.commit()


def _require_safe_id(value: str, label: str) -> None:
    if not SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} must use only letters, numbers, dot, underscore, and hyphen")


def _event(
    connection: sqlite3.Connection,
    event_type: str,
    reason_code: str,
    archive_id: str | None = None,
    application_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> None:
    connection.execute(
        "INSERT INTO archive_events (archive_id, application_id, created_at, event_type, reason_code, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
        (archive_id, application_id, (at or now_utc()).isoformat(), event_type, reason_code,
         canonical_json(metadata or {})),
    )


def record_field(
    connection: sqlite3.Connection,
    application_id: str,
    field_id: str,
    question: str,
    value: Any,
    source_kind: str,
    source_id: str,
    source_status: str,
    sensitivity: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    _require_safe_id(field_id, "field_id")
    if not question.strip():
        raise ValueError("question is required")
    if sensitivity not in SENSITIVITY_CLASSES:
        raise ValueError("invalid sensitivity class")
    if SENSITIVE_FIELD_PATTERN.search(f"{field_id} {question}") and sensitivity == "normal":
        raise ValueError("sensitive field cannot be classified as normal")
    application = connection.execute(
        "SELECT state FROM applications WHERE application_id=?", (application_id,)
    ).fetchone()
    if not application:
        raise ValueError("application not found")
    if application["state"] not in FIELD_RECORDING_STATES:
        raise ValueError("application fields may only be recorded during filling or submission preparation")
    if source_kind == "answer":
        answer_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='answers'"
        ).fetchone()
        if not answer_table:
            raise ValueError("answer library is unavailable")
        answer = connection.execute("SELECT status, answer_json FROM answers WHERE answer_id=?", (source_id,)).fetchone()
        if not answer or answer["status"] != "active" or source_status != "active":
            raise ValueError("answer source must be active when recorded")
        if canonical_json(value) != answer["answer_json"]:
            raise ValueError("recorded field value does not match its answer source")
    elif source_kind == "fact":
        if source_status != "locked":
            raise ValueError("fact source must be locked when recorded")
        candidate_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_facts'"
        ).fetchone()
        if candidate_table:
            fact = connection.execute("""
                SELECT cf.* FROM material_locks ml
                JOIN resume_versions rv ON rv.version_id=ml.resume_version_id
                JOIN candidate_snapshots cs ON cs.content_sha256=rv.candidate_profile_sha256
                JOIN candidate_facts cf ON cf.content_sha256=cs.content_sha256
                WHERE ml.application_id=? AND ml.invalidated_at IS NULL
                  AND cs.status='active' AND cs.registered_by='user' AND cf.fact_id=?
            """, (application_id, source_id)).fetchone()
            if not fact or fact["status"] != "locked" or not fact["locked"]:
                raise ValueError("fact source must exist as a locked active CandidateFact")
            if canonical_json(value) != fact["value_json"]:
                raise ValueError("recorded field value does not match its CandidateFact source")
    else:
        raise ValueError("source_kind must be fact or answer")
    timestamp = (at or now_utc()).isoformat()
    connection.execute("""
        INSERT INTO application_fields (
            application_id, field_id, question, value_json, source_kind, source_id,
            source_status, sensitivity, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(application_id, field_id) DO UPDATE SET
            question=excluded.question, value_json=excluded.value_json, source_kind=excluded.source_kind,
            source_id=excluded.source_id, source_status=excluded.source_status,
            sensitivity=excluded.sensitivity, recorded_at=excluded.recorded_at
    """, (application_id, field_id, question, canonical_json(value), source_kind, source_id,
          source_status, sensitivity, timestamp))
    field_metadata = {"source_kind": source_kind, "sensitivity": sensitivity}
    if sensitivity == "normal":
        field_metadata["field_id"] = field_id
    else:
        field_metadata["field_id_sha256"] = hashlib.sha256(field_id.encode("utf-8")).hexdigest()
    _event(connection, "field_recorded", "verified_source_mapped", application_id=application_id,
           metadata=field_metadata, at=at)
    connection.commit()
    return {"application_id": application_id, "field_id": field_id, "source_kind": source_kind,
            "sensitivity": sensitivity, "status": "recorded"}


def build_redacted_answers(connection: sqlite3.Connection, application_id: str) -> tuple[dict[str, Any], dict[str, int]]:
    rows = connection.execute(
        "SELECT * FROM application_fields WHERE application_id=? ORDER BY field_id", (application_id,)
    ).fetchall()
    included: list[dict[str, Any]] = []
    counts = {"included": 0, "redacted": 0, "omitted": 0}
    for row in rows:
        sensitivity = row["sensitivity"]
        if sensitivity in OMIT_FROM_SNAPSHOT:
            counts["omitted"] += 1
            continue
        item = {
            "field_id": row["field_id"],
            "question": row["question"],
            "source_kind": row["source_kind"],
            "source_id": row["source_id"],
            "source_status_at_submission": row["source_status"],
            "sensitivity": sensitivity,
        }
        if sensitivity in REDACT_IN_SNAPSHOT:
            item["value"] = "[REDACTED]"
            item["redacted"] = True
            counts["redacted"] += 1
        else:
            item["value"] = json.loads(row["value_json"])
            item["redacted"] = False
            counts["included"] += 1
        included.append(item)
    return {"schema_version": "0.1.0", "application_id": application_id, "fields": included,
            "redaction": counts}, counts


def _slug(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").casefold()
    return (normalized[:48] or fallback).strip("-")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _copy_confirmation(evidence: sqlite3.Row, destination: Path) -> Path:
    reference = evidence["reference"]
    if reference:
        source = Path(reference).expanduser()
        if source.is_file():
            suffix = source.suffix.casefold()
            if suffix not in SAFE_EVIDENCE_SUFFIXES:
                raise ValueError("submission evidence file type is not allowlisted")
            if source.stat().st_size > MAX_EVIDENCE_BYTES:
                raise ValueError("submission evidence file exceeds the archive size limit")
            output = destination / f"confirmation{suffix}"
            shutil.copyfile(source, output)
            return output
    output = destination / "confirmation.txt"
    lines = [f"evidence_type: {evidence['evidence_type']}"]
    if evidence["confirmation_id"]:
        lines.append(f"confirmation_id: {evidence['confirmation_id']}")
    if reference:
        try:
            parsed = urlsplit(reference)
        except ValueError:
            parsed = None
        if parsed and parsed.scheme in {"http", "https"} and parsed.netloc:
            lines.append(f"reference_url: {urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))}")
        else:
            lines.append(f"reference_sha256: {hashlib.sha256(reference.encode('utf-8')).hexdigest()}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def _manifest_entry(root: Path, path: Path, artifact_type: str) -> dict[str, Any]:
    return {"artifact_type": artifact_type, "path": str(path.relative_to(root)),
            "sha256": file_sha256(path), "size": path.stat().st_size}


def create_archive(
    connection: sqlite3.Connection,
    archive_root: Path,
    application_id: str,
    archive_id: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    existing = connection.execute(
        "SELECT archive_id FROM submission_archives WHERE application_id=?", (application_id,)
    ).fetchone()
    if existing:
        return verify_archive(connection, existing["archive_id"], at)
    row = connection.execute("""
        SELECT a.*, j.employer, j.title, j.location, j.canonical_url, j.source, j.ats, j.job_card_json,
               rv.snapshot_path, rv.file_sha256, rv.file_size, rv.file_format,
               rv.claims_manifest_path, rv.claims_manifest_sha256,
               ru.file_sha256 AS submitted_resume_sha256
        FROM applications a
        JOIN jobs j ON j.job_id=a.job_id
        JOIN resume_usage ru ON ru.application_id=a.application_id AND ru.use_type='submitted'
        JOIN resume_versions rv ON rv.version_id=ru.version_id
        WHERE a.application_id=?
    """, (application_id,)).fetchone()
    if not row:
        raise ValueError("application has no submitted resume usage record")
    if row["state"] not in ARCHIVABLE_STATES or not row["submitted_at"]:
        raise ValueError("only a positively confirmed submission can be archived")
    cover_letter = None
    if row["cover_letter_version_id"]:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cover_letter_versions'"
        ).fetchone():
            raise ValueError("cover-letter version store is unavailable")
        cover_letter = connection.execute("""
            SELECT cv.*, cu.file_sha256 AS submitted_file_sha256
            FROM cover_letter_versions cv JOIN cover_letter_usage cu
              ON cu.version_id=cv.version_id AND cu.application_id=? AND cu.use_type='submitted'
            WHERE cv.version_id=?
        """, (application_id, row["cover_letter_version_id"])).fetchone()
        if not cover_letter:
            raise ValueError("application has no submitted cover-letter usage record")
        cover_snapshot = Path(cover_letter["snapshot_path"])
        if not cover_snapshot.is_file() or cover_snapshot.stat().st_size != cover_letter["file_size"]:
            raise ValueError("submitted cover-letter snapshot is missing or changed size")
        cover_hash = file_sha256(cover_snapshot)
        if cover_hash != cover_letter["file_sha256"] or cover_hash != cover_letter["submitted_file_sha256"]:
            raise ValueError("submitted cover-letter snapshot hash mismatch")
        cover_manifest = Path(cover_letter["claims_manifest_path"] or "")
        if not cover_manifest.is_file() or file_sha256(cover_manifest) != cover_letter["claims_manifest_sha256"]:
            raise ValueError("submitted cover-letter claims manifest hash mismatch")
    evidence = connection.execute("""
        SELECT * FROM submission_evidence WHERE application_id=?
        ORDER BY CASE evidence_type WHEN 'confirmation_id' THEN 0 WHEN 'success_page' THEN 1
                 WHEN 'account_record' THEN 2 ELSE 3 END, captured_at LIMIT 1
    """, (application_id,)).fetchone()
    if not evidence:
        raise ValueError("submission archive requires positive submission evidence")
    review = connection.execute("""
        SELECT review_id, summary_sha256, authorization_id, status, approved_by
        FROM pre_submit_reviews WHERE review_id=? AND application_id=?
    """, (row["pre_submit_review_id"], application_id)).fetchone()
    if not review or review["status"] != "approved" or review["approved_by"] != "user":
        raise ValueError("submission archive requires its user-approved pre-submit review")
    if not connection.execute(
        "SELECT 1 FROM application_fields WHERE application_id=? LIMIT 1", (application_id,)
    ).fetchone():
        raise ValueError("submission archive requires recorded application fields")
    resume = Path(row["snapshot_path"])
    if not resume.is_file() or resume.stat().st_size != row["file_size"]:
        raise ValueError("submitted resume snapshot is missing or has changed size")
    resume_hash = file_sha256(resume)
    if resume_hash != row["file_sha256"] or resume_hash != row["submitted_resume_sha256"]:
        raise ValueError("submitted resume snapshot hash mismatch")
    manifest_snapshot = Path(row["claims_manifest_path"] or "")
    if not manifest_snapshot.is_file() or file_sha256(manifest_snapshot) != row["claims_manifest_sha256"]:
        raise ValueError("submitted resume claims manifest hash mismatch")

    actual_archive_id = archive_id or f"archive-{uuid.uuid4().hex}"
    _require_safe_id(actual_archive_id, "archive_id")
    submitted_date = row["submitted_at"][:10]
    folder_name = (
        f"{submitted_date}_{_slug(row['employer'], 'employer')}_{_slug(row['title'], 'role')}_"
        f"{_slug(application_id, 'application')}"
    )
    resolved_root = archive_root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)
    os.chmod(resolved_root, 0o700)
    application_dir = resolved_root / "applications" / folder_name
    if application_dir.exists():
        raise ValueError("archive directory already exists without a matching database record")
    application_dir.mkdir(parents=True, mode=0o700)
    artifacts: list[dict[str, Any]] = []
    try:
        resume_output = application_dir / f"resume_used.{row['file_format']}"
        shutil.copyfile(resume, resume_output)
        artifacts.append(_manifest_entry(application_dir, resume_output, "resume"))
        claims_output = application_dir / "resume_claims_manifest.json"
        shutil.copyfile(manifest_snapshot, claims_output)
        artifacts.append(_manifest_entry(application_dir, claims_output, "resume_claims_manifest"))
        if cover_letter is not None:
            cover_output = application_dir / f"cover_letter_used.{cover_letter['file_format']}"
            shutil.copyfile(Path(cover_letter["snapshot_path"]), cover_output)
            artifacts.append(_manifest_entry(application_dir, cover_output, "cover_letter"))
            cover_claims_output = application_dir / "cover_letter_claims_manifest.json"
            shutil.copyfile(Path(cover_letter["claims_manifest_path"]), cover_claims_output)
            artifacts.append(_manifest_entry(
                application_dir, cover_claims_output, "cover_letter_claims_manifest"
            ))

        answers, redaction = build_redacted_answers(connection, application_id)
        answers_output = application_dir / "answers_snapshot.json"
        _write_json(answers_output, answers)
        artifacts.append(_manifest_entry(application_dir, answers_output, "answers_snapshot"))
        job_output = application_dir / "job_card.json"
        _write_json(job_output, json.loads(row["job_card_json"]))
        artifacts.append(_manifest_entry(application_dir, job_output, "job_card"))
        confirmation_output = _copy_confirmation(evidence, application_dir)
        artifacts.append(_manifest_entry(application_dir, confirmation_output, "submission_confirmation"))

        uncertainty_history = connection.execute(
            "SELECT 1 FROM application_events WHERE application_id=? AND to_state='submission_uncertain' LIMIT 1",
            (application_id,),
        ).fetchone() is not None
        timestamp = (at or now_utc()).isoformat()
        archive_manifest = {
            "schema_version": "0.1.0",
            "archive_id": actual_archive_id,
            "application_id": application_id,
            "job_id": row["job_id"],
            "archived_at": timestamp,
            "submission": {
                "submitted_at": row["submitted_at"],
                "application_url": row["canonical_url"],
                "employer": row["employer"],
                "role": row["title"],
                "resume_version_id": row["resume_version_id"],
                "cover_letter_version_id": row["cover_letter_version_id"],
                "submission_policy": row["submission_policy"],
                "pre_submit_review_id": review["review_id"],
                "pre_submit_summary_sha256": review["summary_sha256"],
                "authorization_id": review["authorization_id"],
                "confirmation_id": row["confirmation_id"] or evidence["confirmation_id"],
                "success_evidence_type": evidence["evidence_type"],
                "unresolved_uncertainty": False,
                "uncertainty_history": uncertainty_history,
            },
            "redaction": redaction,
            "artifacts": artifacts,
        }
        archive_manifest_path = application_dir / "archive_manifest.json"
        _write_json(archive_manifest_path, archive_manifest)
        manifest_hash = file_sha256(archive_manifest_path)
        for path in application_dir.iterdir():
            if path.is_file():
                os.chmod(path, 0o400)
        connection.execute("""
            INSERT INTO submission_archives (
                archive_id, application_id, archive_path, manifest_path, manifest_sha256,
                archived_at, last_verified_at, verification_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'verified')
        """, (actual_archive_id, application_id, str(application_dir), str(archive_manifest_path),
              manifest_hash, timestamp, timestamp))
        _event(connection, "archive_created", "confirmed_submission_preserved", actual_archive_id,
               application_id, {"artifact_count": len(artifacts), "redaction": redaction}, at)
        connection.commit()
    except Exception:
        connection.rollback()
        if application_dir.exists():
            for path in application_dir.iterdir():
                if path.is_file():
                    path.unlink()
            application_dir.rmdir()
        raise
    return {"archive_id": actual_archive_id, "application_id": application_id, "status": "verified",
            "archive_path": str(application_dir), "manifest_sha256": manifest_hash,
            "artifact_count": len(artifacts), "redaction": redaction}


def verify_archive(
    connection: sqlite3.Connection,
    archive_id: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM submission_archives WHERE archive_id=?", (archive_id,)).fetchone()
    if not row:
        raise ValueError("submission archive not found")
    root = Path(row["archive_path"])
    manifest_path = Path(row["manifest_path"])
    if not root.is_dir() or not manifest_path.is_file() or file_sha256(manifest_path) != row["manifest_sha256"]:
        raise ValueError("archive manifest is missing or has changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("archive_id") != archive_id or manifest.get("application_id") != row["application_id"]:
        raise ValueError("archive manifest identity mismatch")
    expected = {"archive_manifest.json"}
    for artifact in manifest.get("artifacts", []):
        relative = Path(artifact.get("path", ""))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("archive manifest contains an unsafe artifact path")
        path = (root / relative).resolve()
        if path.parent != root.resolve() or not path.is_file():
            raise ValueError(f"archived artifact is missing: {relative}")
        if path.stat().st_size != artifact.get("size") or file_sha256(path) != artifact.get("sha256"):
            raise ValueError(f"archived artifact hash mismatch: {relative}")
        expected.add(str(relative))
    actual = {path.name for path in root.iterdir() if path.is_file()}
    if actual != expected:
        raise ValueError("archive contains untracked or missing files")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE submission_archives SET last_verified_at=?, verification_status='verified' WHERE archive_id=?",
        (timestamp, archive_id),
    )
    _event(connection, "archive_verified", "all_artifact_hashes_match", archive_id, row["application_id"],
           {"artifact_count": len(expected) - 1}, at)
    connection.commit()
    return {"archive_id": archive_id, "application_id": row["application_id"], "status": "verified",
            "archive_path": str(root), "manifest_sha256": row["manifest_sha256"],
            "artifact_count": len(expected) - 1}


def tracker_source(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute("""
        SELECT sa.archive_id, sa.archive_path, sa.archived_at, a.*, j.employer, j.title, j.location,
               j.canonical_url, j.source, j.ats, j.job_card_json
        FROM submission_archives sa
        JOIN applications a ON a.application_id=sa.application_id
        JOIN jobs j ON j.job_id=a.job_id
        ORDER BY a.submitted_at, a.application_id
    """).fetchall()
    usage_by_application: dict[str, int] = {}
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_usage_events'"
    ).fetchone():
        usage_by_application = {
            usage["application_id"]: usage["tokens"]
            for usage in connection.execute("""
                SELECT application_id, SUM(input_tokens + output_tokens) AS tokens
                FROM model_usage_events WHERE application_id IS NOT NULL GROUP BY application_id
            """)
        }
    output = []
    for row in rows:
        card = json.loads(row["job_card_json"])
        output.append({
            "submission_time": row["submitted_at"],
            "employer": row["employer"],
            "role": row["title"],
            "location": row["location"],
            "work_arrangement": card.get("work_arrangement"),
            "source": row["source"],
            "ats": row["ats"],
            "application_url": row["canonical_url"],
            "resume_version": row["resume_version_id"],
            "cover_letter_version": row["cover_letter_version_id"],
            "category": row["category"],
            "current_status": row["state"],
            "confirmation_id": row["confirmation_id"],
            "follow_up_date": None,
            "model_usage": usage_by_application.get(row["application_id"]),
            "archive_id": row["archive_id"],
            "archive_path": row["archive_path"],
        })
    generated_from = max((row["archived_at"] for row in rows), default=None)
    return {"schema_version": "0.1.0", "generated_from_state_at": generated_from,
            "row_count": len(output), "applications": output}


def write_tracker_source(connection: sqlite3.Connection, output: Path) -> dict[str, Any]:
    value = tracker_source(connection)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output, value)
    os.chmod(output, 0o600)
    return {"status": "written", "output": str(output), "row_count": value["row_count"]}


def status(connection: sqlite3.Connection) -> dict[str, Any]:
    archives = connection.execute("SELECT verification_status, COUNT(*) AS count FROM submission_archives GROUP BY verification_status").fetchall()
    placeholders = ",".join("?" for _ in ARCHIVABLE_STATES)
    pending = connection.execute(
        f"SELECT COUNT(*) FROM applications a WHERE a.state IN ({placeholders}) AND a.submitted_at IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM submission_archives sa WHERE sa.application_id=a.application_id)",
        tuple(sorted(ARCHIVABLE_STATES)),
    ).fetchone()[0]
    return {"archives": sum(row["count"] for row in archives),
            "by_status": {row["verification_status"]: row["count"] for row in archives},
            "pending_submissions": pending,
            "recorded_fields": connection.execute("SELECT COUNT(*) FROM application_fields").fetchone()[0]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--archive-root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    field = commands.add_parser("record-field")
    field.add_argument("--input", required=True, type=Path)
    archive = commands.add_parser("archive")
    archive.add_argument("--application-id", required=True)
    archive.add_argument("--archive-id")
    verify = commands.add_parser("verify")
    verify.add_argument("--archive-id", required=True)
    tracker = commands.add_parser("tracker-source")
    tracker.add_argument("--output", required=True, type=Path)
    commands.add_parser("status")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    archive_root = args.archive_root or args.db.parent / "archive"
    if args.command == "init":
        result = {"status": "initialized", "db": str(args.db), "archive_root": str(archive_root)}
    elif args.command == "record-field":
        value = json.loads(args.input.read_text(encoding="utf-8"))
        result = record_field(connection, **value)
    elif args.command == "archive":
        result = create_archive(connection, archive_root, args.application_id, args.archive_id)
    elif args.command == "verify":
        result = verify_archive(connection, args.archive_id)
    elif args.command == "tracker-source":
        result = write_tracker_source(connection, args.output)
    else:
        result = status(connection)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
