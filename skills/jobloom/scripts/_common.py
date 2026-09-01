"""Shared fail-closed helpers for Jobloom's deterministic safety gates."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def context_matches(rules: dict[str, Any], context: dict[str, Any]) -> bool:
    for key, expected in rules.items():
        actual = context.get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def excluded(rules: dict[str, Any], context: dict[str, Any]) -> bool:
    return any(
        context.get(key) in (denied if isinstance(denied, list) else [denied])
        for key, denied in rules.items()
    )


def answer_issue(row: sqlite3.Row, context: dict[str, Any], at: datetime) -> str | None:
    if row["confirmation_status"] != "confirmed":
        return "answer_not_confirmed"
    if row["status"] != "active":
        return f"answer_{row['status']}"
    effective = parse_time(row["effective_from"])
    if effective and at < effective:
        return "answer_not_yet_effective"
    expires = parse_time(row["expires_at"])
    if expires and at >= expires:
        return "answer_expired"
    review_after = parse_time(row["review_after"])
    if review_after and at >= review_after:
        return "answer_review_due"
    if not context_matches(json.loads(row["scope_json"]), context):
        return "answer_scope_mismatch"
    if not context_matches(json.loads(row["preconditions_json"]), context):
        return "answer_precondition_failed"
    if excluded(json.loads(row["exclusions_json"]), context):
        return "answer_excluded"
    if row["answer_type"] == "legal_commitment":
        return "legal_commitment_requires_review"
    return None


def require_table(connection: sqlite3.Connection, table: str) -> None:
    found = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not found:
        raise RuntimeError(f"required safety table is missing: {table}")


PDF_MAGIC = b"%PDF-"


def require_application_material_format(snapshot_path: str | Path, label: str) -> None:
    """Refuse anything but a real PDF once an artifact is chosen as an application material.

    `known-liabilities.md` recorded the path this closes: registration accepts `.pdf`,
    `.docx`, `.txt` and `.md`, and neither binding nor the material lock looked at the
    format, so an approved DOCX walked all the way to `fill_core._plan_upload`. The suffix
    alone is not enough — a DOCX renamed to `.pdf` passes it — so the leading bytes are
    checked too. Registration formats are deliberately left unchanged; this gate fires only
    where a version becomes the artifact an employer would receive.
    """
    snapshot = Path(snapshot_path)
    if snapshot.suffix.casefold() != ".pdf":
        raise ValueError(f"{label} bound to an application must be a .pdf file")
    try:
        with snapshot.open("rb") as handle:
            head = handle.read(len(PDF_MAGIC))
    except OSError:
        raise ValueError(f"{label} snapshot is missing") from None
    if head != PDF_MAGIC:
        raise ValueError(f"{label} bound to an application is not a PDF file")
