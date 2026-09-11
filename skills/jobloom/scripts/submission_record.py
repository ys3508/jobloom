#!/usr/bin/env python3
"""Recording that a person finished an employer's form themselves.

Every other write path in this project describes something Jobloom did. This one describes
something the user did somewhere Jobloom cannot see, which is the whole of applying today: the
fill worker is local-fixture only, so the form is filled by hand in the employer's own tab and
the only record of it is what the person says afterwards.

`saved_jobs` already models exactly this, in three rungs that are never collapsed:

1. `decision='applied'` — pressed before the form is opened, so it counts *intentions* and
   includes every application abandoned at an account wall.
2. `submitted_confirmed_at` — the user saying afterwards that they finished it. Still their
   word, but now given about something that happened rather than something intended.
3. `application_core`'s `submitted` — positive submission evidence plus a material lock, and
   unreachable while no browser worker exists.

This module climbs rungs 1 and 2 and **cannot reach rung 3**, by construction: it never calls
`application_core.transition` and never writes `submission_evidence`. A reference the user
types — a confirmation number, an employer email — is stored beside their confirmation on the
saved job, because a value typed at a keyboard is their word about an employer artefact and
not an observation of one. Letting it into `submission_evidence` would let the second rung's
evidence open the third rung's gate, which is the one thing the three rungs exist to prevent.

**An archive is not made here.** `archive_core.create_archive` requires a rung-3 submission —
an archivable state, a `submitted_at`, and a `use_type='submitted'` resume usage — and a
hand-made application has none of them. That is a property of the evidence, not a gap in this
file, so nothing here fakes one.

What it does produce is the tracker's own source: `archive_core.write_tracker_source` for the
rung-3 side and `saved_jobs.tracker_rows` for this one, already joined on the posting's URL so
a job present in both is reported once from each side rather than counted twice. Turning that
into `applications.xlsx` stays a command — it needs Node and an external package, and spawning
a process is not a privilege a loopback window should quietly acquire.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import application_core  # noqa: E402
import saved_jobs  # noqa: E402

# The rungs, named so a caller cannot accidentally describe one as another.
RUNG_INTENDED = 1
RUNG_CONFIRMED = 2
RUNG_EVIDENCED = 3
REFERENCE_KINDS = tuple(sorted(application_core.SUCCESS_EVIDENCE_TYPES))


def _application(connection: sqlite3.Connection, application_id: str) -> dict[str, Any]:
    row = connection.execute("""
        SELECT a.application_id, a.job_id, a.state, a.resume_version_id,
               j.employer, j.title, j.location, j.canonical_url, j.job_card_json
        FROM applications a JOIN jobs j ON j.job_id = a.job_id
        WHERE a.application_id=?
    """, (application_id,)).fetchone()
    if not row:
        raise ValueError("no such application")
    return dict(row)


def _saved_row(connection: sqlite3.Connection, canonical_url: str) -> sqlite3.Row | None:
    url = application_core.canonicalize_url(canonical_url or "")
    return connection.execute(
        "SELECT * FROM saved_jobs WHERE job_url=?", (url,)).fetchone()


def state(connection: sqlite3.Connection, application_id: str) -> dict[str, Any]:
    """Which rung this application is on, and what the next press would record.

    Read-only. The window shows this before either button so a person can see that pressing
    records their own word — the distinction the three rungs are built on is worth putting on
    the screen rather than only in a docstring.
    """
    saved_jobs.initialize(connection)
    application = _application(connection, application_id)
    saved = _saved_row(connection, application["canonical_url"])
    rung = RUNG_INTENDED - 1
    if saved is not None and saved["decision"] == saved_jobs.APPLIED:
        rung = RUNG_INTENDED
        if saved["submitted_confirmed_at"]:
            rung = RUNG_CONFIRMED
    return {
        "application_id": application["application_id"],
        "employer": application["employer"],
        "title": application["title"],
        "location": application["location"],
        "canonical_url": application["canonical_url"],
        "resume_version_id": application["resume_version_id"],
        "state": application["state"],
        "rung": rung,
        "intended_at": saved["applied_at"] if saved is not None else None,
        "confirmed_at": saved["submitted_confirmed_at"] if saved is not None else None,
        "reference_kind": saved["submitted_reference_kind"] if saved is not None else None,
        "reference": saved["submitted_reference"] if saved is not None else None,
        "reference_kinds": list(REFERENCE_KINDS),
        # Stated rather than left to be inferred from the absence of an archive.
        "evidenced": False,
        "evidence_unreachable_reason": "no_browser_worker",
    }


def intend(connection: sqlite3.Connection, application_id: str, *, actor: str = "user",
           at: datetime | None = None) -> dict[str, Any]:
    """Rung 1: the user is about to go and apply. Written before the employer's form opens.

    Deliberately separate from the confirmation. Recording both on one press would put the
    intention and the completion at the same instant and lose the gap between them, and that
    gap is the abandonment rate — the number that makes a reply rate over intentions wrong.
    """
    saved_jobs.initialize(connection)
    application = _application(connection, application_id)
    card = json.loads(application["job_card_json"] or "{}")
    # The card is the posting as it was pulled, so the saved row describes the same opening the
    # application does. Only the URL is load-bearing: it is what joins the two sides.
    card.setdefault("canonical_url", application["canonical_url"])
    card.setdefault("title", application["title"])
    card.setdefault("employer", application["employer"])
    result = saved_jobs.save(connection, card, actor=actor,
                             decision=saved_jobs.APPLIED, at=at)
    return {**result, "rung": RUNG_INTENDED, "application_id": application_id}


def confirm(connection: sqlite3.Connection, application_id: str, *,
            reference_kind: str | None = None, reference: str | None = None,
            at: datetime | None = None) -> dict[str, Any]:
    """Rung 2: the user says they finished the form. Their word, recorded after the act.

    Refuses when rung 1 was never pressed, because a confirmation with no intention behind it
    has no `applied_at` to sit after and the gap between the two would be unmeasurable.
    """
    saved_jobs.initialize(connection)
    application = _application(connection, application_id)
    result = saved_jobs.confirm_submitted(
        connection, application["canonical_url"],
        reference_kind=reference_kind, reference=reference, at=at)
    return {**result, "application_id": application_id,
            "rung": RUNG_CONFIRMED, "evidenced": False}


def pending(connection: sqlite3.Connection) -> dict[str, str]:
    """Which openings the user has already said they finished, keyed by application.

    The queue this window shows is applications, and an application whose form is done is not
    one to open again. The exclusion reads the saved row rather than the application's state:
    the state machine has no rung-2 value and `ready_to_fill` is still true of it — nothing
    was filled by Jobloom, and nothing will be.
    """
    found = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='saved_jobs'").fetchone()
    if not found:
        return {}
    rows = connection.execute("""
        SELECT a.application_id, s.submitted_confirmed_at
        FROM applications a
        JOIN jobs j ON j.job_id = a.job_id
        JOIN saved_jobs s ON s.job_url = j.canonical_url
        WHERE s.submitted_confirmed_at IS NOT NULL
    """).fetchall()
    return {row["application_id"]: row["submitted_confirmed_at"] for row in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("state", "intend"):
        sub = commands.add_parser(name)
        sub.add_argument("--application-id", required=True)
    confirm_parser = commands.add_parser("confirm")
    confirm_parser.add_argument("--application-id", required=True)
    confirm_parser.add_argument("--reference-kind", choices=REFERENCE_KINDS)
    confirm_parser.add_argument("--reference")
    args = parser.parse_args()
    connection = sqlite3.connect(str(args.db))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    if args.command == "state":
        result = state(connection, args.application_id)
    elif args.command == "intend":
        result = intend(connection, args.application_id)
    else:
        result = confirm(connection, args.application_id,
                         reference_kind=args.reference_kind, reference=args.reference)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
