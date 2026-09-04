#!/usr/bin/env python3
"""Carrying an approved resume across a change of CandidateSnapshot.

Registering a new profile invalidates every material lock bound to the old snapshot, and it
should: the lock says "these exact bytes were approved against this exact profile", and one
half of that just moved. What the repository had no path for was the other side of it. An
approved ResumeVersion records the snapshot it was approved against, and
`_require_resume_authorized_for_application` requires that snapshot to be the active one - so
after a profile switch the resume is refused at binding, at locking and at selection. Nothing
could restore it: `approve_version` accepts only a draft, and the recorded hash on an approved
version is not editable, correctly.

So the fix is not a "rebind" button. It is a **successor**: the same file, registered again as
its own immutable draft, put in front of the user with its claims manifest revalidated against
the new profile, approved by them, and only then bound and locked. What the user is being asked
is a real question - *these claims were checked against who you were; are they still true of who
you are now?* - and a button that skipped it would be answering it for them.

Three decisions worth stating.

**The successor records no `parent_version_id`.** That column means "derived from" in the
generation chain, and `_validate_parent` refuses it for a `user_provided` resume so that a
supplied PDF cannot claim an evidence lineage it never earned. A snapshot migration is a
different relation entirely - not derived from, but *the same document under a new profile* -
so it gets its own record here rather than being squeezed into a field that means something
else.

**Only `user_provided` goes down this path.** A `generated` resume is bound to an approved
adaptation plan and a `direction_baseline` one to a baseline plan, and both plans carry their
own snapshot hashes; migrating either means deciding what happens to a stale plan, which is a
separate design with its own review. All three of the resumes this exists for are
`user_provided`, so the other two modes are refused by name rather than half-handled.

**A failure leaves the application in `materials_in_progress`, not in `ready_to_fill`.** The
state machine already allows `ready_to_fill -> materials_in_progress` for exactly this - the
materials went away before any worker touched them - and `transition` commits as it goes, by
design. So this does not wrap the sequence in one transaction; it orders it so that every
partial outcome is a true statement about the application. Stuck at `materials_in_progress`
means the materials really are not ready, and running the migration again continues from there.
The final move back to `ready_to_fill` re-runs `require_active_material_lock` on its own, so no
step here can talk the application into a readiness it does not have.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import application_core  # noqa: E402
import resume_core  # noqa: E402
from _common import require_table  # noqa: E402

# The one source mode this path handles, and the reason the others are named rather than
# silently unmatched.
MIGRATABLE_SOURCE_MODE = "user_provided"
PREPARED, APPROVED, BOUND = "prepared", "approved", "bound"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def initialize(connection: sqlite3.Connection) -> None:
    resume_core.initialize(connection)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS resume_migrations (
            migration_id TEXT PRIMARY KEY,
            predecessor_version_id TEXT NOT NULL,
            successor_version_id TEXT NOT NULL UNIQUE,
            predecessor_snapshot_sha256 TEXT NOT NULL,
            successor_snapshot_sha256 TEXT,
            file_sha256 TEXT NOT NULL,
            claims_manifest_path TEXT NOT NULL,
            claims_manifest_sha256 TEXT NOT NULL,
            application_id TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            approved_at TEXT,
            bound_at TEXT,
            FOREIGN KEY (predecessor_version_id) REFERENCES resume_versions(version_id),
            FOREIGN KEY (successor_version_id) REFERENCES resume_versions(version_id)
        );
        CREATE INDEX IF NOT EXISTS resume_migration_predecessor_idx
            ON resume_migrations(predecessor_version_id, status);
    """)
    connection.commit()


def _active_snapshot(connection: sqlite3.Connection) -> str:
    require_table(connection, "candidate_snapshots")
    row = connection.execute(
        "SELECT content_sha256 FROM candidate_snapshots WHERE status='active' "
        "AND registered_by='user'").fetchone()
    if not row:
        raise ValueError("no active user-registered candidate snapshot")
    return row[0]


def _blocked_reason(version: sqlite3.Row, carried: sqlite3.Row | None) -> str | None:
    """Why this one cannot be carried, or nothing if it can.

    Every case says something. A row that is neither carryable nor explained shows a person
    an empty cell where an answer belongs, which is how `kind` being unaccounted for stayed
    invisible: a `user_provided` master source was not migratable and gave no reason.
    """
    if carried:
        return "already carried forward"
    if version["source_mode"] != MIGRATABLE_SOURCE_MODE:
        return "source mode carries a plan and needs its own migration"
    if version["kind"] != "direction":
        return "only a direction resume is used for an application"
    return None


def stranded(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Approved resumes the active snapshot has left behind, and whether this path can carry them.

    Names every one and migrates none. Which resume is worth carrying is the user's call and
    depends on what they are about to apply to; a function that picked would be answering a
    question about their week from a database.
    """
    active = _active_snapshot(connection)
    require_table(connection, "material_locks")
    rows = []
    for version in connection.execute(
            # `master_source` is deliberately absent: it is refused for an application by
            # kind whatever snapshot it was approved against, so it is not something an
            # application is waiting on and listing it here would read as a job to do.
            "SELECT * FROM resume_versions WHERE status='approved' AND kind!='master_source' "
            "AND candidate_profile_sha256 IS NOT ? ORDER BY version_id", (active,)):
        live_lock = connection.execute(
            "SELECT application_id FROM material_locks WHERE resume_version_id=? "
            "AND invalidated_at IS NULL", (version["version_id"],)).fetchone()
        held = connection.execute(
            "SELECT application_id FROM material_locks WHERE resume_version_id=? "
            "AND invalidation_reason='candidate_snapshot_changed' ORDER BY locked_at DESC",
            (version["version_id"],)).fetchone()
        migration = connection.execute(
            "SELECT successor_version_id, status FROM resume_migrations "
            "WHERE predecessor_version_id=? AND status IN (?, ?)",
            (version["version_id"], PREPARED, APPROVED)).fetchone()
        # A predecessor whose successor is bound is still approved against a superseded
        # snapshot, so it is still stranded and is still listed - it would still be refused if
        # anything tried to use it. What has changed is that there is nothing left to do about
        # it, and a list that dropped the row would lose the reason its successor exists.
        carried = connection.execute(
            "SELECT successor_version_id FROM resume_migrations "
            "WHERE predecessor_version_id=? AND status=?",
            (version["version_id"], BOUND)).fetchone()
        migratable = (version["kind"] == "direction"
                      and version["source_mode"] == MIGRATABLE_SOURCE_MODE
                      and carried is None)
        rows.append({
            "version_id": version["version_id"],
            "kind": version["kind"],
            "direction": version["direction"],
            "source_mode": version["source_mode"],
            "migratable": migratable,
            "carried_by": carried["successor_version_id"] if carried else None,
            "blocked_reason": _blocked_reason(version, carried),
            # Which application went quiet when the profile moved. This is the urgency, and it
            # is why the list is ordered by the user's attention rather than by id.
            "application_id": (live_lock or held or {"application_id": None})["application_id"],
            "lock_lost": held is not None and live_lock is None,
            "in_progress": dict(migration) if migration else None,
        })
    return rows


def prepare_successor(connection: sqlite3.Connection, store: Path, predecessor_id: str,
                      successor_id: str | None = None,
                      at: datetime | None = None) -> dict[str, Any]:
    """Register the same file again as a draft, to be reviewed against the new profile.

    Allowed before the new snapshot exists: preparing is not approving, and a draft approves
    nothing. The bytes are taken from the predecessor's own stored snapshot and verified
    against the hash recorded for it, so no caller supplies a file and none can substitute one.
    """
    initialize(connection)
    predecessor = connection.execute(
        "SELECT * FROM resume_versions WHERE version_id=?", (predecessor_id,)).fetchone()
    if not predecessor:
        raise ValueError("resume version not found")
    if predecessor["status"] != "approved":
        raise ValueError("only an approved resume has anything to carry forward")
    if predecessor["kind"] != "direction" or predecessor["source_mode"] != MIGRATABLE_SOURCE_MODE:
        raise ValueError("this path carries user-provided direction resumes only")
    if predecessor["candidate_profile_sha256"] == _active_snapshot(connection):
        raise ValueError("this resume is already approved against the active profile")
    open_migration = connection.execute(
        "SELECT successor_version_id FROM resume_migrations WHERE predecessor_version_id=? "
        "AND status IN (?, ?)", (predecessor_id, PREPARED, APPROVED)).fetchone()
    if open_migration:
        raise ValueError("a successor for this resume is already in progress")

    # Hash-checked against what the registry recorded, which is what makes "the same bytes" a
    # verified claim rather than the caller's word. `verify_version_file` also re-checks the
    # approved manifest, so a tampered manifest stops here rather than at approval.
    resume_core.verify_version_file(predecessor)
    manifest_path = Path(predecessor["claims_manifest_path"] or "")
    if not manifest_path.is_file():
        raise ValueError("the approved claims manifest is missing")

    actual_id = successor_id or f"{predecessor_id}--snapshot-{uuid.uuid4().hex[:8]}"
    resume_core.register_version(
        connection, Path(store), Path(predecessor["snapshot_path"]), actual_id,
        predecessor["kind"], predecessor["direction"], parent_version_id=None,
        actor="user", at=at, source_mode=MIGRATABLE_SOURCE_MODE)
    successor = connection.execute(
        "SELECT * FROM resume_versions WHERE version_id=?", (actual_id,)).fetchone()
    if successor["file_sha256"] != predecessor["file_sha256"]:
        # The copy is the only place the bytes could change, and a successor that is not the
        # same document is not a successor. Nothing has been approved, so refusing here leaves
        # a draft the user can discard rather than a resume that quietly differs.
        raise ValueError("the successor's bytes do not match the resume it carries forward")

    timestamp = (at or now_utc()).isoformat()
    migration_id = f"migration-{uuid.uuid4().hex[:12]}"
    connection.execute(
        "INSERT INTO resume_migrations (migration_id, predecessor_version_id, "
        "successor_version_id, predecessor_snapshot_sha256, file_sha256, claims_manifest_path, "
        "claims_manifest_sha256, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (migration_id, predecessor_id, actual_id, predecessor["candidate_profile_sha256"],
         predecessor["file_sha256"], str(manifest_path),
         predecessor["claims_manifest_sha256"], PREPARED, timestamp))
    connection.commit()
    return {"migration_id": migration_id, "predecessor_version_id": predecessor_id,
            "successor_version_id": actual_id, "status": PREPARED,
            "same_bytes": True, "claims_manifest_path": str(manifest_path),
            "approved": False,
            "next_step": "the user reviews the file and its claims, then approves the successor"}


def approve_successor(connection: sqlite3.Connection, successor_id: str, candidate_path: Path,
                      actor: str, at: datetime | None = None) -> dict[str, Any]:
    """Approve the successor against the new profile, with its claims checked there.

    The manifest is the predecessor's own, verified to be the file that was recorded, and it is
    revalidated in full by `approve_version`: every claim's facts must still exist in the new
    snapshot at the strength it claims. A snapshot that only added facts passes; one that
    changed or dropped a cited fact does not, and that refusal is the whole point of asking.
    """
    require_table(connection, "resume_migrations")
    migration = connection.execute(
        "SELECT * FROM resume_migrations WHERE successor_version_id=?", (successor_id,)).fetchone()
    if not migration:
        raise ValueError("no migration is carrying that resume")
    if migration["status"] != PREPARED:
        raise ValueError("that successor is not awaiting approval")
    active = _active_snapshot(connection)
    if active == migration["predecessor_snapshot_sha256"]:
        # Approving here would produce a second approved resume for one direction under the
        # profile the first is already approved against. Nothing is wrong with the profile;
        # it simply has not moved yet, and there is nothing to carry across.
        raise ValueError("the candidate profile has not changed; there is nothing to migrate")
    manifest_path = Path(migration["claims_manifest_path"])
    if not manifest_path.is_file() or resume_core.file_sha256(manifest_path) != migration["claims_manifest_sha256"]:
        raise ValueError("the claims manifest has changed since the successor was prepared")

    result = resume_core.approve_version(connection, successor_id, Path(candidate_path),
                                         manifest_path, actor, at)
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE resume_migrations SET status=?, approved_at=?, successor_snapshot_sha256=? "
        "WHERE migration_id=? AND status=?",
        (APPROVED, timestamp, active, migration["migration_id"], PREPARED))
    connection.commit()
    return {"migration_id": migration["migration_id"], "successor_version_id": successor_id,
            "status": APPROVED, "approved_against": active, "version": result,
            "next_step": "bind the successor to the application and lock the materials"}


def bind_for_application(connection: sqlite3.Connection, application_id: str, successor_id: str,
                         actor: str = "user", at: datetime | None = None) -> dict[str, Any]:
    """Put the application back on its feet with the approved successor.

    Ordered so that every stopping point is true. Preparation is entered first, which is what
    the application's state already is in substance; binding and locking re-run every material
    gate on their own; and the move back to `ready_to_fill` is refused unless a live lock
    exists. A failure part-way leaves preparation in progress, which is exactly what is then
    the case.
    """
    require_table(connection, "resume_migrations")
    migration = connection.execute(
        "SELECT * FROM resume_migrations WHERE successor_version_id=?", (successor_id,)).fetchone()
    if not migration:
        raise ValueError("no migration is carrying that resume")
    if migration["status"] != APPROVED:
        raise ValueError("that successor has not been approved")
    application = connection.execute(
        "SELECT state, cover_letter_version_id FROM applications WHERE application_id=?",
        (application_id,)).fetchone()
    if not application:
        raise ValueError("application not found")
    if application["state"] not in {"ready_to_fill", "materials_in_progress"}:
        raise ValueError("this application is not waiting on its materials")
    _require_cover_letter_carried(connection, application["cover_letter_version_id"])

    if application["state"] == "ready_to_fill":
        application_core.transition(connection, application_id, "materials_in_progress",
                                    actor, "candidate_snapshot_changed", at=at)
    resume_core.bind_version(connection, application_id, successor_id, at=at)
    lock = resume_core.lock_materials(connection, application_id, at=at)
    application_core.transition(connection, application_id, "ready_to_fill", actor,
                                "materials_migrated", at=at)
    connection.execute(
        "UPDATE resume_migrations SET status=?, bound_at=?, application_id=? "
        "WHERE migration_id=? AND status=?",
        (BOUND, (at or now_utc()).isoformat(), application_id, migration["migration_id"],
         APPROVED))
    connection.commit()
    return {"migration_id": migration["migration_id"], "application_id": application_id,
            "successor_version_id": successor_id, "lock_id": lock["lock_id"],
            "status": BOUND, "state": "ready_to_fill"}


def _require_cover_letter_carried(connection: sqlite3.Connection,
                                  cover_letter_version_id: str | None) -> None:
    """A cover letter the application already binds is not left behind quietly.

    `lock_materials` would refuse it anyway, and that is the point: it would refuse *after* the
    application had been moved into preparation and the resume rebound, leaving the migration
    stopped on an error about a document nobody was thinking about. Checked first, by name, so
    the answer is "your cover letter needs its own migration" before anything moves.

    Carrying one is not implemented here on purpose. A cover letter records the snapshot it was
    approved against exactly as a resume does, so carrying it across means the same successor
    dance for a second document, and doing that as a side effect of the resume's migration
    would approve a document the user was never shown.
    """
    if not cover_letter_version_id:
        return
    require_table(connection, "cover_letter_versions")
    cover = connection.execute(
        "SELECT candidate_profile_sha256, status FROM cover_letter_versions WHERE version_id=?",
        (cover_letter_version_id,)).fetchone()
    if not cover:
        raise ValueError("the bound cover letter is missing")
    if cover["status"] != "approved":
        raise ValueError("the bound cover letter is not approved")
    if cover["candidate_profile_sha256"] != _active_snapshot(connection):
        raise ValueError("the bound cover letter needs its own migration")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--store", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("stranded")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--predecessor", required=True)
    prepare.add_argument("--successor")
    approve = commands.add_parser("approve")
    approve.add_argument("--successor", required=True)
    approve.add_argument("--candidate", required=True, type=Path)
    approve.add_argument("--actor", required=True)
    bind = commands.add_parser("bind")
    bind.add_argument("--application", required=True)
    bind.add_argument("--successor", required=True)
    bind.add_argument("--actor", required=True)
    args = parser.parse_args()

    store = args.store or args.db.parent / "resumes"
    connection = resume_core.connect(args.db)
    if args.command == "init":
        initialize(connection)
        result = {"status": "initialized"}
    elif args.command == "stranded":
        result = {"stranded": stranded(connection)}
    elif args.command == "prepare":
        result = prepare_successor(connection, store, args.predecessor, args.successor)
    elif args.command == "approve":
        result = approve_successor(connection, args.successor, args.candidate, args.actor)
    else:
        result = bind_for_application(connection, args.application, args.successor, args.actor)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
