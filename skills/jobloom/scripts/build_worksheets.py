#!/usr/bin/env python3
"""Two sheets for a day of applying by hand: what to work through, and what was done.

They are separate files because they are read at different moments and change on different
clocks. The queue is a snapshot of a pull and is replaced whole by the next one; the record
grows a row at a time and must never be overwritten. Writing them into one workbook would
put a file that must not be regenerated next to one that must.

Neither sheet decides anything. The queue's order comes from `review_queue.py`, which ranks
on evidence coverage and demotes `ranking_score` to a late tiebreak because that score runs
against the evidence (`references/known-liabilities.md`). Nothing here re-ranks, re-scores,
or filters: a row this file dropped would be a decision made by a spreadsheet writer.

`evidence` columns are counts of requirements, not a fit percentage. `direct` is how many of
the posting's stated requirements the confirmed facts cover directly, `stated` is how many
it asked for. A posting with 0 direct and 12 stated is not a bad match — it may be one
nothing parsed — so both numbers are shown and neither is divided by the other.
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
import saved_jobs  # noqa: E402
import worksheet_writer as sheets  # noqa: E402

QUEUE_COLUMNS = [
    "#", "direction", "weight %", "direct", "covered", "stated", "employer", "title",
    "location", "arrangement", "salary", "posted", "days open", "evidence", "also matches",
    "why review", "duplicates", "link",
]
QUEUE_WIDTHS = [4, 30, 8, 7, 8, 7, 24, 46, 24, 12, 22, 12, 10, 30, 30, 34, 30, 56]

RECORD_COLUMNS = [
    "decided", "employer", "role", "location", "status", "evidence", "applied at",
    "confirmed submitted", "outcome", "outcome at", "verdict", "direction", "direct",
    "stated", "suggested", "followed", "posted", "days open", "deadline", "link",
]
RECORD_WIDTHS = [20, 24, 46, 24, 12, 22, 20, 20, 18, 20, 12, 30, 7, 7, 12, 10, 12, 10, 12, 56]


def _salary(value: Any) -> str:
    """What the employer stated, or blank. Nothing is imputed from a title or a market."""
    if not isinstance(value, dict):
        return ""
    low, high = value.get("minimum"), value.get("maximum")
    currency = value.get("currency") or ""
    if low and high:
        return f"{currency} {low:,}–{high:,}".strip()
    if low or high:
        return f"{currency} {low or high:,}".strip()
    return ""


def _reasons(row: dict[str, Any]) -> str:
    """Why the router sent it to review rather than matching it outright.

    Kept verbatim as reason codes. Rewriting them into prose here would put a second
    vocabulary beside `direction_core`'s, and the two would drift.
    """
    return ", ".join(row.get("review_reasons") or [])


def queue_sheet(queue: dict[str, Any], *, today: date | None = None) -> sheets.Sheet:
    rows = []
    for row in queue["rows"]:
        evidence = row.get("evidence") or {}
        group = row.get("group") or {}
        members = group.get("members") or []
        rows.append([
            row.get("rank"),
            row.get("direction_id", ""),
            row.get("weight_percent"),
            evidence.get("direct"),
            evidence.get("recognized_requirements"),
            evidence.get("stated_requirements"),
            row.get("employer", ""),
            row.get("title", ""),
            row.get("location", ""),
            row.get("work_arrangement", ""),
            _salary(row.get("salary")),
            row.get("posted_at", ""),
            saved_jobs.days_open(row.get("posted_at"), today),
            ", ".join(evidence.get("direct_requirements") or []),
            ", ".join(row.get("also_matches") or []),
            _reasons(row),
            # A group holds independent openings that share an employer and title. It is
            # never a merge: two postings differing in one word score 0.997 on their text,
            # so each keeps its own row and its own link.
            f"{len(members) + 1} independent openings" if members else "",
            row.get("apply_url") or row.get("canonical_url", ""),
        ])
    return sheets.Sheet("To apply", QUEUE_COLUMNS, rows,
                        link_column=len(QUEUE_COLUMNS), widths=QUEUE_WIDTHS)


def record_sheet(connection: sqlite3.Connection, *, today: date | None = None) -> sheets.Sheet:
    rows = []
    for row in saved_jobs.tracker_rows(connection, today=today):
        rows.append([
            row["saved_time"], row["employer"], row["role"], row["location"],
            row["current_status"],
            # Three rungs, never collapsed: "stated at decision" was written before the form
            # was open. Only the top two count as applications.
            row["applied_evidence"],
            row["applied_at"], row["submitted_confirmed_at"], row["outcome"], row["outcome_at"],
            row["verdict"], row["direction"], row["covered"], row["stated"],
            row["suggested_choice"], row["followed_suggestion"],
            row["posted_at"], row["days_open"], row["deadline"], row["job_url"],
        ])
    return sheets.Sheet("Applied", RECORD_COLUMNS, rows,
                        link_column=len(RECORD_COLUMNS), widths=RECORD_WIDTHS)


def build(queue_path: Path | None, db_path: Path | None, out_dir: Path,
          *, today: date | None = None) -> dict[str, Any]:
    written = []
    if queue_path:
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        sheet = queue_sheet(queue, today=today)
        sheets.write_xlsx(out_dir / "to-apply.xlsx", [sheet])
        sheets.write_csv(out_dir / "to-apply.csv", sheet)
        written.append({"sheet": "to-apply", "rows": len(sheet.rows), "source": str(queue_path)})
    if db_path:
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            saved_jobs.initialize(connection)
            sheet = record_sheet(connection, today=today)
            summary = saved_jobs.status(connection, today=today)
        finally:
            connection.close()
        sheets.write_xlsx(out_dir / "applied.xlsx", [sheet])
        sheets.write_csv(out_dir / "applied.csv", sheet)
        written.append({"sheet": "applied", "rows": len(sheet.rows), "source": str(db_path),
                        # Stated beside the count so the sheet is never read as a
                        # submission total: the first number counts decisions.
                        "decided_to_apply": summary["applied"],
                        "confirmed_submitted": summary["confirmed_submitted"],
                        "stated_not_confirmed": summary["stated_not_confirmed"]})
    return {"directory": str(out_dir), "written": written,
            "built_at": (datetime.now(timezone.utc)).isoformat()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", type=Path, help="review_queue.py --output JSON")
    parser.add_argument("--db", type=Path, help="the private database holding saved_jobs")
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    if not args.queue and not args.db:
        parser.error("nothing to build: pass --queue, --db, or both")
    print(json.dumps(build(args.queue, args.db, args.out_dir), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
