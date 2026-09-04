"""Shared fail-closed helpers for Jobloom's deterministic safety gates."""

from __future__ import annotations

import hashlib
import json
import os
import re
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


def worksheet_shape_digest(worksheet: dict[str, Any], editable: tuple[str, ...]) -> str:
    """A hash of everything in a worksheet the user is not being asked to change.

    Written as "the whole thing minus the editable fields" rather than as a list of fields to
    cover, because the list was the bug: it named the arrangement and left the wording beside
    it free, so a worksheet could be edited to explain itself differently - or, worse, to
    carry a meaning that went straight into the database under another name. Adding a field to
    a worksheet now covers it by default instead of forgetting it by default.

    `proposal_id` is covered. Leaving it out made two proposals of the same round and snapshot
    share a digest, so a worksheet filled in against one could have its id swapped for the
    other's and be accepted - the single-use rule and the no-swapping rule failing together.
    Only the digest field itself is excluded, because it cannot cover itself.
    """
    covered = {key: value for key, value in worksheet.items()
               if key not in {"entries", "shape_sha256"}}
    covered["entries"] = [
        {field: value for field, value in entry.items() if field not in editable}
        for entry in worksheet["entries"]]
    return hashlib.sha256(
        json.dumps(covered, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()


def classify_composite_contact(source: str) -> dict[str, str]:
    """Split a composite contact fact into the meanings it holds.

    Here rather than beside either caller because both the AnswerLibrary and the Candidate
    Profile read the same composite fact for the same three meanings, and two copies of
    this would be two answers to "which part is the phone number" — the failure this
    repository has already paid for once, in a requirement matcher that existed twice.

    Classified once, in order of how distinctive each shape is, rather than scanned once per
    meaning: an earlier version asked "does this piece contain linkedin.com?" and so missed a
    profile URL on any other host, which is a property of the host and not of the fact. What
    identifies the pieces is that one is labelled, one holds an address, and one is mostly
    digits.
    """
    found: dict[str, str] = {}
    for piece in [part.strip() for part in re.split(r"[\u01c1|]", source) if part.strip()]:
        labelled = re.match(r"^\s*linkedin\s*:\s*(.+)$", piece, flags=re.IGNORECASE)
        if labelled or "linkedin." in piece.lower():
            found.setdefault("profile.linkedin",
                             labelled.group(1).strip() if labelled else piece)
        elif "@" in piece and " " not in piece:
            found.setdefault("contact.email", piece)
        elif sum(character.isdigit() for character in piece) >= 7:
            found.setdefault("contact.phone", piece)
    return found


def write_private_document(path: Path, private_root: Path,
                           document: dict[str, Any]):
    """Create a worksheet where private things belong, exclusively, already unreadable.

    A worksheet carries proposed answers and a draft profile carries confirmed ones, so where
    either lands and how it is created are part of the boundary rather than a convenience. `write_text` then `chmod` creates the file with the
    default mode first, which is a window in which anyone on the machine can read it; it also
    follows a symlink and overwrites whatever was there. `O_CREAT | O_EXCL` with the mode given
    at creation has neither problem, and refuses rather than replacing.

    The path must be inside the private root — the directory the library itself lives in — so
    a caller cannot ask for a copy of their own contact details in the repository.
    """
    root = private_root.resolve()
    parent = path.parent
    # Resolved whether or not it exists yet: `resolve` handles a missing tail and still
    # follows the symlinks above it, which `absolute` does not — on a machine where /tmp is
    # itself a link, comparing one against the other says a path is outside a root it is in.
    resolved_parent = parent.resolve()
    if resolved_parent != root and root not in resolved_parent.parents:
        raise ValueError("a worksheet belongs in the private root and nowhere else")
    # Every component between the root and the file, walked on the path **as given** rather
    # than as resolved. Walking the resolved path can never see a link, because resolving is
    # exactly what removes them — and a link pointing somewhere else inside the private root
    # resolves to a contained path, so the check above passes and nothing else notices that a
    # link was followed at all.
    walker = Path(os.path.abspath(parent))
    between = []
    while True:
        if walker.exists() and walker.resolve() == root:
            break
        if walker == walker.parent:
            break
        between.append(walker)
        walker = walker.parent
    for candidate in between:
        if candidate.is_symlink():
            raise ValueError("a worksheet path may not pass through a symlink")
    if path.is_symlink():
        # Named apart from the case below: following it would write a private answer wherever
        # it points, which is a different mistake from overwriting something.
        raise ValueError("a worksheet path may not pass through a symlink")
    if path.exists():
        # Refused rather than replaced: the file it would overwrite may be a worksheet
        # somebody is part-way through.
        raise ValueError("that worksheet already exists")
    resolved_parent.mkdir(parents=True, exist_ok=True)
    resolved_parent.chmod(0o700)
    body = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    # `O_NOFOLLOW` closes the gap between the check above and this call: the check is a
    # courtesy that gives a clear message, and the flag is what actually holds if the path is
    # replaced with a link in between. `O_EXCL` refuses an existing file, and the mode is
    # given at creation rather than narrowed afterwards, which would leave the file readable
    # for as long as it took to chmod it.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    handle = os.open(path, flags, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
    except Exception:
        path.unlink(missing_ok=True)
        raise

    def undo() -> None:
        """Remove exactly the file this call created, and nothing else."""
        path.unlink(missing_ok=True)

    return undo
