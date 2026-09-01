"""Test-only: put a page into the state a verified import would have left.

`fill_core` has no way to do this, on purpose. `complete_step` and the
`require_verified_import` switch were removed because a boolean bypass in production would
have made the whole result-import path optional — which is the opposite of what Task 5 was
for. The suites that predate the import path still need a completed page to test other
things (page chains, non-disclosure policy, session finishing), so the shortcut lives here,
outside the shipped code, and says plainly that it is a fixture.

It writes the same rows a successful import writes and nothing else. It is not a second
implementation of verification: nothing here checks a hash, and no production module can
call it. The real path is exercised end to end, with a real browser, in
`tests/test_fill_worker.py`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime


def complete_page_as_if_imported(fill_core, connection: sqlite3.Connection, session_id: str,
                                 worker_id: str, page_id: str, at: datetime) -> None:
    """Mark every pending step of one page complete and record a stand-in import row."""
    session = connection.execute(
        "SELECT * FROM fill_sessions WHERE session_id=?", (session_id,)).fetchone()
    steps = connection.execute(
        "SELECT * FROM fill_steps WHERE session_id=? AND page_id=? AND status='pending' "
        "ORDER BY ordinal", (session_id, page_id)).fetchall()
    for step in steps:
        fill_core._apply_step(connection, session, worker_id, step, at)
    if steps:
        package = connection.execute(
            "SELECT package_sha256 FROM exported_packages WHERE session_id=? AND page_id=? "
            "ORDER BY exported_at DESC LIMIT 1", (session_id, page_id)).fetchone()
        digest = package["package_sha256"] if package else hashlib.sha256(
            json.dumps([step["step_id"] for step in steps], sort_keys=True).encode()
        ).hexdigest()
        connection.execute(
            "INSERT OR IGNORE INTO exported_packages (session_id, page_id, package_sha256, "
            "exported_at) VALUES (?, ?, ?, ?)",
            (session_id, page_id, digest, at.isoformat()))
        connection.execute(
            "INSERT OR REPLACE INTO imported_results (result_sha256, session_id, page_id, "
            "grant_id, package_sha256, status, imported_at) "
            "VALUES (?, ?, ?, ?, ?, 'verified', ?)",
            (hashlib.sha256(f"fixture:{session_id}:{page_id}".encode()).hexdigest(),
             session_id, page_id, f"fixture-{page_id}", digest, at.isoformat()))
    connection.commit()
