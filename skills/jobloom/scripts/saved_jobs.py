#!/usr/bin/env python3
"""Jobs the user looked at and kept for later, recorded before any application exists.

A skip leaves no trace, and that is deliberate rather than a gap to fill: skipping a job
means moving to the next one, so a button labelled "do not apply" would be pressed by
nobody. The one decision worth a press is "not now, but keep it", and that is the only
decision this records.

It follows that the funnel's denominator is jobs *kept*, never jobs *seen*. Reporting a
view rate would require the panel to log every posting the user opens, which is a different
promise from the one it makes today, so nothing here infers one.

Kept jobs are not applications. An application record describes what happened after
something was sent; a kept job has no after. They live in separate tables and are joined on
the job's own URL when the tracker is built, so a job that is later applied to is reported
once from each side rather than counted twice.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import application_core  # noqa: E402

SCHEMA_VERSION = "0.1.0"
# One decision, because it is the only one a person would press a button for.
DECISIONS = {"later"}
MAX_TEXT = 500
MAX_REASON = 1_000


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS saved_jobs (
            job_url TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            employer TEXT NOT NULL,
            location TEXT,
            country TEXT,
            work_arrangement TEXT,
            employment_type TEXT,
            source TEXT,
            ats TEXT,
            apply_url TEXT,
            posted_at TEXT,
            deadline TEXT,
            decision TEXT NOT NULL,
            reason TEXT,
            decided_by TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS saved_jobs_decided_idx ON saved_jobs(decided_at);
    """)
    connection.commit()


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


def _posting_dates(card: dict[str, Any]) -> tuple[str | None, str | None]:
    """When the employer opened the posting, and the deadline if they stated one.

    Both come from the board. Nothing derives a "you should apply by" date: employers state
    a deadline on a small minority of postings, and inventing one for the rest would put a
    number on the card that nobody wrote. Days open is computed at read time from the first
    of these, which is a fact the reader can weigh themselves.
    """
    ats = (card.get("extraction") or {}).get("ats") or {}
    return (_text(ats.get("posted_at")) or None, _text(ats.get("deadline")) or None)


def save(connection: sqlite3.Connection, card: dict[str, Any], *, actor: str,
         decision: str = "later", reason: str | None = None,
         at: datetime | None = None) -> dict[str, Any]:
    """Record a job to come back to. Never creates an application and never needs a review.

    The pre-submission review gate exists to stop an unreviewed card being *sent*. Keeping a
    note of a job sends nothing, so the gate does not apply and is not relaxed to get here.
    """
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of: {', '.join(sorted(DECISIONS))}")
    if not _text(actor):
        raise ValueError("actor is required")
    url = application_core.canonicalize_url(_text(card.get("canonical_url")))
    if not url.startswith("http"):
        raise ValueError("a saved job needs the URL of its posting, so it can be reopened")
    title, employer = _text(card.get("title")), _text(card.get("employer"))
    if not title or not employer:
        raise ValueError("a saved job needs a title and an employer")
    posted_at, deadline = _posting_dates(card)
    ats = (card.get("extraction") or {}).get("ats") or {}
    timestamp = (at or now_utc()).isoformat()
    existing = connection.execute(
        "SELECT decided_at FROM saved_jobs WHERE job_url=?", (url,)).fetchone()
    decided_at = existing["decided_at"] if existing else timestamp
    connection.execute("""
        INSERT INTO saved_jobs (job_url, title, employer, location, country, work_arrangement,
            employment_type, source, ats, apply_url, posted_at, deadline, decision, reason,
            decided_by, decided_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_url) DO UPDATE SET
            title=excluded.title, employer=excluded.employer, location=excluded.location,
            country=excluded.country, work_arrangement=excluded.work_arrangement,
            employment_type=excluded.employment_type, source=excluded.source, ats=excluded.ats,
            apply_url=excluded.apply_url, posted_at=excluded.posted_at, deadline=excluded.deadline,
            decision=excluded.decision, reason=excluded.reason, updated_at=excluded.updated_at
    """, (url, title, employer, _text(card.get("location")), _text(card.get("country")),
          _text(card.get("work_arrangement")), _text(card.get("employment_type")),
          _text(card.get("source")), _text(card.get("ats")), _text(ats.get("apply_url")),
          posted_at, deadline, decision, _text(reason, MAX_REASON), _text(actor),
          decided_at, timestamp))
    connection.commit()
    return {"job_url": url, "decision": decision, "decided_at": decided_at,
            "updated": bool(existing)}


def forget(connection: sqlite3.Connection, job_url: str) -> dict[str, Any]:
    url = application_core.canonicalize_url(_text(job_url))
    cursor = connection.execute("DELETE FROM saved_jobs WHERE job_url=?", (url,))
    connection.commit()
    return {"job_url": url, "removed": cursor.rowcount}


def days_open(posted_at: str | None, today: date | None = None) -> int | None:
    if not posted_at:
        return None
    try:
        opened = date.fromisoformat(str(posted_at)[:10])
    except ValueError:
        return None
    return max(0, ((today or now_utc().date()) - opened).days)


def _applied_urls(connection: sqlite3.Connection) -> set[str]:
    """Saved jobs that now have an application, so neither side double-counts them."""
    found = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='applications'").fetchone()
    if not found:
        return set()
    return {row["canonical_url"] for row in connection.execute("""
        SELECT j.canonical_url FROM applications a JOIN jobs j ON j.job_id=a.job_id
    """)}


def tracker_rows(connection: sqlite3.Connection, *, today: date | None = None) -> list[dict[str, Any]]:
    applied = _applied_urls(connection)
    rows = []
    for row in connection.execute("SELECT * FROM saved_jobs ORDER BY decided_at, job_url"):
        rows.append({
            "saved_time": row["decided_at"],
            "employer": row["employer"],
            "role": row["title"],
            "location": row["location"],
            "work_arrangement": row["work_arrangement"],
            "source": row["source"],
            "ats": row["ats"],
            "job_url": row["job_url"],
            "posted_at": row["posted_at"],
            # Computed on read, not stored: it changes every day, and a stored copy would be
            # wrong by exactly as long as the file has been sitting there.
            "days_open": days_open(row["posted_at"], today),
            # Only when the employer stated one. Blank means unstated, never "no deadline".
            "deadline": row["deadline"],
            "current_status": "Applied" if row["job_url"] in applied else "Saved",
            "reason": row["reason"],
        })
    return rows


def status(connection: sqlite3.Connection, *, today: date | None = None) -> dict[str, Any]:
    rows = tracker_rows(connection, today=today)
    ages = [row["days_open"] for row in rows if row["days_open"] is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "saved": len(rows),
        "since_applied": sum(1 for row in rows if row["current_status"] == "Applied"),
        "with_stated_deadline": sum(1 for row in rows if row["deadline"]),
        "median_days_open": sorted(ages)[len(ages) // 2] if ages else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("status")
    save_parser = commands.add_parser("save")
    save_parser.add_argument("--job-card", required=True, type=Path)
    save_parser.add_argument("--actor", required=True)
    save_parser.add_argument("--reason")
    forget_parser = commands.add_parser("forget")
    forget_parser.add_argument("--job-url", required=True)
    list_parser = commands.add_parser("list")
    list_parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    connection = application_core.connect(args.db)
    try:
        if args.command == "init":
            initialize(connection)
            result = {"status": "initialized"}
        elif args.command == "save":
            result = save(connection, json.loads(args.job_card.read_text(encoding="utf-8")),
                          actor=args.actor, reason=args.reason)
        elif args.command == "forget":
            result = forget(connection, args.job_url)
        elif args.command == "status":
            result = status(connection)
        else:
            rows = tracker_rows(connection)
            if args.output:
                args.output.write_text(json.dumps({"schema_version": SCHEMA_VERSION,
                                                   "row_count": len(rows), "saved_jobs": rows},
                                                  indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
            result = {"row_count": len(rows), "saved_jobs": rows}
    finally:
        connection.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
