#!/usr/bin/env python3
"""Jobs the user looked at, kept for later, or applied to outside this system.

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

`applied` here is the user saying they applied. It is **not** the `submitted` state in
`application_core`, which requires positive submission evidence — a confirmation page, a
confirmation id, an account record — and a material lock. Nothing here has seen any of
that. The two must never be read as the same claim, so this one is reported as
self-reported wherever it is shown.

Recording it at all exists because applications made by hand are otherwise invisible: the
tracker derived "Applied" by joining the applications table, so a job applied to outside
the fill flow stayed "Saved" forever and the funnel collected nothing. Every question this
project has deferred — what `ranking_score` drives, whether an "apply within N days"
interval exists, whether the tailoring suggestion fires too often — waits on reply data
that cannot start accumulating until the applying is recorded at all.
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
import outcome_core  # noqa: E402

SCHEMA_VERSION = "0.2.0"
# Two decisions worth a press. A skip still records nothing: skipping a job means moving to
# the next one, so a control meaning "do not apply" would be pressed by nobody.
LATER, APPLIED = "later", "applied"
DECISIONS = {LATER, APPLIED}
# Borrowed rather than redefined, so the two halves of the funnel cannot drift apart. The
# `outcome_records` table itself cannot be reused: it has a foreign key to an application,
# and an application made by hand has no row there.
OUTCOMES = outcome_core.OUTCOME_TYPES
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
            applied_at TEXT,
            verdict TEXT,
            verdict_reason TEXT,
            direction TEXT,
            covered INTEGER,
            stated INTEGER,
            hidden_strength INTEGER,
            evidence_gap INTEGER,
            suggested_choice TEXT,
            outcome TEXT,
            outcome_at TEXT,
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


JUDGEMENT_KEYS = ("verdict", "verdict_reason", "direction", "covered", "stated",
                  "hidden_strength", "evidence_gap", "suggested_choice")


def _judgement(value: Any) -> dict[str, Any]:
    """The judgement as it was shown, kept verbatim rather than recomputed later.

    Direction profiles are revised, the ontology is recalibrated, and the evidence library
    grows. Re-deriving a verdict months later would answer "what would we say now", and the
    question the funnel needs answered is "was what we said then borne out" — so this is a
    copy, in the same spirit as an archive holding the bytes of a resume rather than a
    pointer to a version that will have moved.

    The vocabulary lives at the bridge, which owns the verdict. It is deliberately not
    re-declared here: a second copy is a second thing to drift. A value that was never a
    verdict shows up as its own group in any analysis, which is visible rather than silent.
    """
    found = value if isinstance(value, dict) else {}
    kept: dict[str, Any] = {}
    for key in JUDGEMENT_KEYS:
        item = found.get(key)
        if key in {"covered", "stated", "hidden_strength", "evidence_gap"}:
            kept[key] = int(item) if isinstance(item, (int, float)) else None
        else:
            kept[key] = _text(item) or None
    return kept


def save(connection: sqlite3.Connection, card: dict[str, Any], *, actor: str,
         decision: str = "later", reason: str | None = None,
         judgement: dict[str, Any] | None = None,
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
    judged = _judgement(judgement)
    ats = (card.get("extraction") or {}).get("ats") or {}
    timestamp = (at or now_utc()).isoformat()
    existing = connection.execute(
        "SELECT decided_at, applied_at FROM saved_jobs WHERE job_url=?", (url,)).fetchone()
    decided_at = existing["decided_at"] if existing else timestamp
    # When the user first said they applied. A second press does not move it, and going back
    # to "later" does not erase it: an application already made is not undone by a change of
    # mind about the next one.
    applied_at = (existing["applied_at"] if existing else None)
    if decision == APPLIED and not applied_at:
        applied_at = timestamp
    connection.execute("""
        INSERT INTO saved_jobs (job_url, title, employer, location, country, work_arrangement,
            employment_type, source, ats, apply_url, posted_at, deadline, decision, applied_at,
            verdict, verdict_reason, direction, covered, stated, hidden_strength, evidence_gap,
            suggested_choice, reason, decided_by, decided_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_url) DO UPDATE SET
            title=excluded.title, employer=excluded.employer, location=excluded.location,
            country=excluded.country, work_arrangement=excluded.work_arrangement,
            employment_type=excluded.employment_type, source=excluded.source, ats=excluded.ats,
            apply_url=excluded.apply_url, posted_at=excluded.posted_at, deadline=excluded.deadline,
            decision=excluded.decision, applied_at=excluded.applied_at,
            -- The judgement shown at the first decision is what the funnel tests. A later
            -- press records the later decision without rewriting the judgement it was
            -- first weighed against, unless there was none to begin with.
            verdict=COALESCE(saved_jobs.verdict, excluded.verdict),
            verdict_reason=COALESCE(saved_jobs.verdict_reason, excluded.verdict_reason),
            direction=COALESCE(saved_jobs.direction, excluded.direction),
            covered=COALESCE(saved_jobs.covered, excluded.covered),
            stated=COALESCE(saved_jobs.stated, excluded.stated),
            hidden_strength=COALESCE(saved_jobs.hidden_strength, excluded.hidden_strength),
            evidence_gap=COALESCE(saved_jobs.evidence_gap, excluded.evidence_gap),
            suggested_choice=COALESCE(saved_jobs.suggested_choice, excluded.suggested_choice),
            reason=excluded.reason, updated_at=excluded.updated_at
    """, (url, title, employer, _text(card.get("location")), _text(card.get("country")),
          _text(card.get("work_arrangement")), _text(card.get("employment_type")),
          _text(card.get("source")), _text(card.get("ats")), _text(ats.get("apply_url")),
          posted_at, deadline, decision, applied_at,
          *(judged[key] for key in JUDGEMENT_KEYS),
          _text(reason, MAX_REASON), _text(actor), decided_at, timestamp))
    connection.commit()
    return {"job_url": url, "decision": decision, "decided_at": decided_at,
            "applied_at": applied_at, "updated": bool(existing)}


def record_outcome(connection: sqlite3.Connection, job_url: str, outcome: str,
                   *, at: datetime | None = None) -> dict[str, Any]:
    """What came back. The vocabulary is `outcome_core`'s, so the two halves of the funnel
    describe the same things by the same names.

    Only a job the user said they applied to can have an outcome. A reply to an application
    that was never recorded is a bookkeeping gap, not a reply to a job merely kept.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of: {', '.join(sorted(OUTCOMES))}")
    url = application_core.canonicalize_url(_text(job_url))
    row = connection.execute(
        "SELECT decision FROM saved_jobs WHERE job_url=?", (url,)).fetchone()
    if row is None:
        raise ValueError("no saved job with that URL")
    if row["decision"] != APPLIED:
        raise ValueError("an outcome belongs to a job you applied to; mark it applied first")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE saved_jobs SET outcome=?, outcome_at=?, updated_at=? WHERE job_url=?",
        (outcome, timestamp, timestamp, url))
    connection.commit()
    return {"job_url": url, "outcome": outcome, "outcome_at": timestamp}


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


def _tracked_application_urls(connection: sqlite3.Connection) -> set[str]:
    """Saved jobs that also have an application record, so neither side double-counts them."""
    found = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='applications'").fetchone()
    if not found:
        return set()
    return {row["canonical_url"] for row in connection.execute("""
        SELECT j.canonical_url FROM applications a JOIN jobs j ON j.job_id=a.job_id
    """)}


def tracker_rows(connection: sqlite3.Connection, *, today: date | None = None) -> list[dict[str, Any]]:
    tracked = _tracked_application_urls(connection)
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
            # Derived from both halves. Deriving it only from the applications table left a
            # job applied to by hand reading "Saved" forever, which is most of them.
            "current_status": ("Applied" if row["decision"] == APPLIED
                                            or row["job_url"] in tracked else "Saved"),
            # Whether the applying was seen or only stated. `application_core`'s `submitted`
            # requires positive submission evidence; this does not, and must not borrow its
            # authority.
            "applied_evidence": ("tracked application" if row["job_url"] in tracked
                                 else "self-reported" if row["decision"] == APPLIED else ""),
            "applied_at": row["applied_at"],
            "outcome": row["outcome"],
            "outcome_at": row["outcome_at"],
            # What the panel said at the time, so a reply can be weighed against the call
            # that preceded it rather than against one recomputed after the fact.
            "verdict": row["verdict"],
            "direction": row["direction"],
            "covered": row["covered"],
            "stated": row["stated"],
            "suggested_choice": row["suggested_choice"],
            "followed_suggestion": (None if not row["suggested_choice"]
                                    else row["suggested_choice"] == ("later"
                                         if row["decision"] == LATER else "broad")),
            "reason": row["reason"],
        })
    return rows


def status(connection: sqlite3.Connection, *, today: date | None = None) -> dict[str, Any]:
    rows = tracker_rows(connection, today=today)
    ages = [row["days_open"] for row in rows if row["days_open"] is not None]
    replies = [row["outcome"] for row in rows if row["outcome"]]
    applied = [row for row in rows if row["current_status"] == "Applied"]
    return {
        "schema_version": SCHEMA_VERSION,
        "saved": len(rows),
        "applied": len(applied),
        # Counted over applications, not over everything kept, and only over the ones whose
        # outcome is known. An unanswered application and one never followed up look alike
        # from here, so they are not merged into a rate.
        "with_recorded_outcome": len(replies),
        "outcomes": {name: replies.count(name) for name in sorted(set(replies))},
        # The question the funnel exists to answer: did the call precede the reply. Reported
        # per verdict rather than as one rate, because a rate over mixed verdicts says
        # nothing about whether the verdict was worth anything.
        "by_verdict": {
            name: {
                "saved": sum(1 for row in rows if row["verdict"] == name),
                "applied": sum(1 for row in rows
                               if row["verdict"] == name and row["current_status"] == "Applied"),
                "with_outcome": sum(1 for row in rows if row["verdict"] == name and row["outcome"]),
            }
            for name in sorted({row["verdict"] for row in rows if row["verdict"]})
        },
        "without_recorded_verdict": sum(1 for row in rows if not row["verdict"]),
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
    save_parser.add_argument("--decision", default=LATER, choices=sorted(DECISIONS))
    outcome_parser = commands.add_parser("outcome")
    outcome_parser.add_argument("--job-url", required=True)
    outcome_parser.add_argument("--outcome", required=True, choices=sorted(OUTCOMES))
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
                          actor=args.actor, reason=args.reason, decision=args.decision)
        elif args.command == "outcome":
            result = record_outcome(connection, args.job_url, args.outcome)
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
