"""Test-only: put a page into the state a verified import would have left.

`fill_core` has no way to do this, on purpose. `complete_step` and the
`require_verified_import` switch were removed because a boolean bypass in production would
have made the whole result-import path optional. The suites that predate the import path
still need a completed page to test other things — page chains, non-disclosure policy,
session finishing — so the shortcut lives here, outside the shipped code.

**It writes its rows itself.** An earlier version called `fill_core._apply_step`, which made
the implementation under test also the thing producing the expected state: a defect in
`_apply_step` would have moved production and the oracle together and been invisible. The SQL
below is deliberately a second, dumber description of the same end state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime


def complete_page_as_if_imported(fill_core, connection: sqlite3.Connection, session_id: str,
                                 worker_id: str, page_id: str, at: datetime) -> None:
    """Mark every pending step of one page complete and record a stand-in import row."""
    timestamp = at.isoformat()
    application_id = connection.execute(
        "SELECT application_id FROM fill_sessions WHERE session_id=?",
        (session_id,)).fetchone()["application_id"]
    steps = connection.execute(
        "SELECT * FROM fill_steps WHERE session_id=? AND page_id=? AND status='pending' "
        "ORDER BY ordinal", (session_id, page_id)).fetchall()
    for step in steps:
        if step["source_kind"] == "nondisclosure_policy":
            connection.execute(
                "INSERT INTO nondisclosure_handling (application_id, field_id, marker, "
                "evidence_kind, evidence_ref, recorded_at) "
                "VALUES (?, ?, 'policy_declined', 'verified_policy_step', ?, ?) "
                "ON CONFLICT(application_id, field_id) DO UPDATE SET marker=excluded.marker",
                (application_id, step["field_id"], step["source_id"], timestamp))
        elif step["operation"] in {"fill", "select", "check", "uncheck"}:
            connection.execute(
                "INSERT INTO application_fields (application_id, field_id, question, "
                "value_json, source_kind, source_id, source_status, sensitivity, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(application_id, field_id) DO UPDATE SET "
                "value_json=excluded.value_json, recorded_at=excluded.recorded_at",
                (application_id, step["field_id"], step["question"], step["value_json"],
                 step["source_kind"], step["source_id"], step["source_status"],
                 step["sensitivity"], timestamp))
        connection.execute(
            "UPDATE fill_steps SET status='completed', completed_at=? WHERE step_id=?",
            (timestamp, step["step_id"]))
    if steps:
        package = connection.execute(
            "SELECT package_sha256 FROM exported_packages WHERE session_id=? AND page_id=? "
            "ORDER BY exported_at DESC LIMIT 1", (session_id, page_id)).fetchone()
        digest = package["package_sha256"] if package else hashlib.sha256(
            json.dumps([step["step_id"] for step in steps], sort_keys=True).encode()
        ).hexdigest()
        connection.execute(
            "INSERT OR IGNORE INTO exported_packages (session_id, page_id, package_sha256, "
            "exported_at) VALUES (?, ?, ?, ?)", (session_id, page_id, digest, timestamp))
        connection.execute(
            "INSERT OR REPLACE INTO imported_results (result_sha256, session_id, page_id, "
            "grant_id, package_sha256, status, imported_at) "
            "VALUES (?, ?, ?, ?, ?, 'verified', ?)",
            (hashlib.sha256(f"fixture:{session_id}:{page_id}".encode()).hexdigest(),
             session_id, page_id, f"fixture-{page_id}", digest, timestamp))
    connection.commit()
