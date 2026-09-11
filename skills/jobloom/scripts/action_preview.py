#!/usr/bin/env python3
"""What a Fill-Only run *would* do, written down and handed to a person. Nothing is executed.

Between an observation and a worker there has never been a step where somebody could look at
the plan. `fill_core` builds a package inside a session, behind a lease, and the first time
anybody sees what it decided is after a browser has already typed. This is that step, and it
is the whole module: resolve every field to its source, hash the value it would carry, bind
the result to the page it was planned against, and print it.

**It executes nothing.** There is no Playwright import here and no operation vocabulary: no
fill, select, check, uncheck or upload appears in this file, so the package is a description
and not a thing that can run. Starting a worker is a separate decision, made after a person
has read this.

**No value leaves.** A value is read to hash it and is never returned, printed, logged or
written: an action carries a selector, a control kind, the *reference* to its source — a fact
id, an answer id, a resume version — and the digest the worker would have to reproduce. That
digest is what makes the exchange verifiable without either side handling the value twice.

**Nothing is consumed.** The package carries a nonce, an issue time, an expiry and a
consumption state, because a package that lacked them would be a different shape from the one
a worker is ever given — but `consumed` stays false and no grant is reserved. A preview that
spent the grant would make reading the plan the act of authorising it.

**The refusals are the point.** Sponsorship is `always_manual` and never reaches an action; a
hidden EEO control never reaches one either; a file comes only from the application's own live
material lock and never from an answer; and a profile meaning resolves only against the
snapshot that lock is bound to. Each field that is not planned is listed with why, because a
plan that silently covered nine of twenty-three fields would read as a plan for the form.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import answer_library  # noqa: E402
import apply_assist  # noqa: E402
import candidate_profile  # noqa: E402
import resume_core  # noqa: E402
from _common import require_table  # noqa: E402

ADAPTER_VERSION = "lever-fill-only-preview-0.1.0"
# How long a real package would be good for. Carried so the preview has the shape a worker is
# given; nothing here issues one.
PACKAGE_TTL = timedelta(minutes=15)

# Why a field is not planned, in two parts: what has to happen, and what kind of thing it is.
# Enumerated so a screen can group them and so no free-text explanation is invented per field.
UNHANDLED_REASONS = (
    ("user_input_required", "application_specific"),
    ("user_input_required", "optional_identity"),
    ("user_input_required", "consent"),
    ("narrative_gap", "storybank"),
    ("manual_only", "field_policy_domain"),
    ("material_unavailable", "no_live_material_lock"),
    ("profile_gap", "no_locked_fact"),
    ("answer_gap", "no_confirmed_answer"),
    ("answer_gap", "no_standing_authorization"),
    ("not_observable", "hidden"),
    ("not_observable", "hidden_and_unreadable"),
    ("unsupported", "auxiliary_control"),
    ("unreviewed", "no_reviewed_reason"),
)

# The user's own decision about the four questions this page asks that Jobloom will not
# answer, recorded the way an exact form is: reviewed, per question, with the platform it was
# seen on. Not inferred — "a textarea is narrative" is a rule nobody wrote, and a checkbox
# group being an identity question is not a property of checkboxes. A question with no entry
# here is reported `unreviewed`, which is a thing for a person to resolve rather than a thing
# to guess at.
REVIEWED_UNHANDLED = {
    ("lever", "Which location are you applying for?"):
        ("user_input_required", "application_specific"),
    ("lever", "Pronouns"):
        ("user_input_required", "optional_identity"),
    ("lever", "How many years of experience do you have working in the Pharma/Life Sciences "
              "industry?"):
        ("narrative_gap", "storybank"),
    ("lever", "Please share your experience in pharmaceutical commercial functions, "
              "specifying the areas or functions within the industry where you have the most "
              "expertise."):
        ("narrative_gap", "storybank"),
    ("lever", "I would like to opt-in to receive text messages from Beghou Consulting. "
              "Please answer with Yes or No. You can reply STOP to opt-out at any time."):
        ("user_input_required", "consent"),
}
REVIEWED_UNHANDLED_SOURCE = "user decision, 2026-09-11"


class Refused(Exception):
    """The plan could not be made. A code, never a value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _digest(value: Any) -> str:
    return resume_core.canonical_hash(value)


def reviewed_reason(question: str) -> tuple[str, str] | None:
    """A reviewed reason for this exact question, or nothing.

    Exact, like every other lookup here. The first version of this table was written from the
    summary line rather than from the observation and three of its five entries silently
    missed — and failed *open*, to `unreviewed`, which is what a lookup that cannot resemble
    does when it is wrong. A fuzzy one would have matched them and been wrong quietly.
    """
    return REVIEWED_UNHANDLED.get(("lever", question))


def live_material_lock(connection: sqlite3.Connection,
                       application_id: str) -> dict[str, Any] | None:
    """The one lock this application may draw a file from, or nothing.

    A file is the one thing an answer may never supply. It comes from the lock, which names an
    approved resume version and the digest of the bytes that were locked, and if the lock has
    been invalidated — by a rebind, or by the profile it was approved against being superseded
    — there is no file to plan and the field says so.
    """
    require_table(connection, "material_locks")
    row = connection.execute("""
        SELECT ml.lock_id, ml.resume_version_id, ml.resume_file_sha256,
               rv.file_format, rv.status, rv.candidate_profile_sha256
        FROM material_locks ml
        JOIN resume_versions rv ON rv.version_id = ml.resume_version_id
        WHERE ml.application_id=? AND ml.invalidated_at IS NULL AND rv.status='approved'
    """, (application_id,)).fetchone()
    return dict(row) if row else None


def _resolve_profile(connection: sqlite3.Connection, canonical_id: str,
                     snapshot_sha256: str, at: datetime) -> tuple[dict[str, Any] | None, str]:
    """One locked fact in the snapshot this application's materials are bound to, or why not.

    Against that snapshot and no other. Falling back to whatever profile is active would fill
    an employer's form from facts the resume beside it was never approved against.
    """
    fact, reason = candidate_profile.resolve_canonical_fact(
        connection, canonical_id, snapshot_sha256, at)
    if fact is None:
        return None, reason
    return {
        "source_kind": "fact",
        "source_id": fact["fact_id"],
        "source_status": fact["status"],
        # The value is read here, hashed, and dropped. What the worker gets is the digest it
        # must reproduce from the page after typing.
        "expected_sha256": _digest(json.loads(fact["value_json"])),
    }, ""


def _resolve_answer(connection: sqlite3.Connection, question: str,
                    context: dict[str, Any], at: datetime) -> tuple[dict[str, Any] | None, str]:
    """A confirmed AnswerEntry that something currently authorises filling, or why not.

    `inspect_answer` is the read-only sibling of `match_answer`: same decision, no audit event,
    no commit. A preview that went through the audited path would record that an answer was
    matched for a form nothing filled.
    """
    inspection = answer_library.inspect_answer(connection, question, context, at)
    if not inspection.get("answer_exists"):
        return None, "no_confirmed_answer"
    if not inspection.get("auto_fill_ready"):
        return None, "no_standing_authorization"
    row = connection.execute(
        "SELECT answer_id, answer_json FROM answers WHERE canonical_id=? "
        "AND confirmation_status='confirmed'", (inspection["canonical_id"],)).fetchone()
    if row is None:
        return None, "no_confirmed_answer"
    return {
        "source_kind": "answer",
        "source_id": row["answer_id"],
        "source_status": "confirmed",
        "expected_sha256": _digest(json.loads(row["answer_json"])),
    }, ""


def plan(connection: sqlite3.Connection, observation: dict[str, Any],
         application_id: str, *, at: datetime | None = None) -> dict[str, Any]:
    """The package a worker would be given, and every field it does not cover.

    Refuses rather than plans when the application's materials are not locked to a live,
    active profile: without that binding there is no snapshot a profile meaning may resolve
    against and no file a material field may draw on, and planning around it would mean
    choosing a profile on the user's behalf.
    """
    at = at or datetime.now(timezone.utc)
    if observation.get("page_sha256") is None or not observation.get("fields"):
        raise Refused("observation_unusable")
    if observation.get("values_read"):
        raise Refused("observation_carries_values")

    snapshot_sha256, lock_reason = apply_assist.application_snapshot(connection, application_id)
    if snapshot_sha256 is None:
        raise Refused(lock_reason or "application_materials_not_locked_to_active_profile")
    lock = live_material_lock(connection, application_id)
    context = {"application_id": application_id}

    actions: list[dict[str, Any]] = []
    unhandled: list[dict[str, Any]] = []

    def not_planned(field: dict[str, Any], reason: str, detail: str) -> None:
        assert (reason, detail) in UNHANDLED_REASONS, (reason, detail)
        unhandled.append({
            "field_id": field["field_id"],
            "match_question": field["match_question"],
            "control": field["control"],
            "required": field["required"],
            "reason": reason, "detail": detail,
            "field_sha256": field["field_sha256"],
        })

    for field in observation["fields"]:
        automation = field["automation"]
        if automation == "manual_only":
            not_planned(field, "manual_only", "field_policy_domain")
        elif automation == "not_visible":
            not_planned(field, "not_observable", "hidden")
        elif automation == "hidden_unknown":
            not_planned(field, "not_observable", "hidden_and_unreadable")
        elif automation == "unsupported_auxiliary_control":
            not_planned(field, "unsupported", "auxiliary_control")
        elif automation == "no_canonical_meaning":
            reviewed = reviewed_reason(field["match_question"])
            if reviewed is None:
                not_planned(field, "unreviewed", "no_reviewed_reason")
            else:
                not_planned(field, *reviewed)
        elif automation == "material":
            if lock is None:
                not_planned(field, "material_unavailable", "no_live_material_lock")
            else:
                actions.append({
                    "field_id": field["field_id"], "selector": field["selector"],
                    "control": field["control"], "operation": "upload",
                    "source_kind": "material", "source_id": lock["resume_version_id"],
                    "source_status": lock["status"],
                    # The digest of the locked bytes. A worker proves it uploaded the file the
                    # lock names; nothing here reads the file.
                    "expected_sha256": lock["resume_file_sha256"],
                    "field_sha256": field["field_sha256"],
                })
        elif automation == "fillable":
            canonical_id = field["canonical_id"]
            if canonical_id in candidate_profile.PROFILE_V1:
                resolved, why = _resolve_profile(connection, canonical_id,
                                                 snapshot_sha256, at)
                if resolved is None:
                    not_planned(field, "profile_gap", "no_locked_fact")
                    continue
            else:
                resolved, why = _resolve_answer(connection, field["match_question"],
                                                context, at)
                if resolved is None:
                    not_planned(field, "answer_gap", why)
                    continue
            operation = {"select": "select", "checkbox": "check",
                         "radio": "select"}.get(field["control"], "fill")
            actions.append({
                "field_id": field["field_id"], "selector": field["selector"],
                "control": field["control"], "operation": operation,
                "canonical_id": canonical_id, **resolved,
                "field_sha256": field["field_sha256"],
            })
        else:
            not_planned(field, "unreviewed", "no_reviewed_reason")

    binding = {
        "application_id": application_id,
        "adapter_version": ADAPTER_VERSION,
        "observer_version": observation.get("observer_version"),
        "page_sha256": observation["page_sha256"],
        # The set of fields this plan was made against, order-independent: a page that gains
        # or loses a question is a different page to plan for, and one whose questions were
        # merely reordered is not.
        "field_sha256_set": _digest(sorted(f["field_sha256"] for f in observation["fields"])),
        "candidate_snapshot_sha256": snapshot_sha256,
        "material_lock_id": lock["lock_id"] if lock else None,
    }
    return {
        "schema_version": "0.1.0",
        "preview": True,
        # The shape a real package carries, so what a person reads here is what a worker would
        # be handed. None of it is issued: no grant is reserved and nothing is consumed.
        "nonce": secrets.token_hex(16),
        "issued_at": at.isoformat(),
        "expires_at": (at + PACKAGE_TTL).isoformat(),
        "consumed": False,
        "single_use": True,
        "binding": binding,
        "package_sha256": _digest({"binding": binding, "actions": actions}),
        "actions": actions,
        "unhandled": unhandled,
        "action_count": len(actions),
        "unhandled_count": len(unhandled),
        "values_included": False,
        "executed": False,
    }


def write_preview(preview: dict[str, Any], output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(preview, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return {"status": "written", "actions": preview["action_count"],
            "unhandled": preview["unhandled_count"],
            "package_sha256": preview["package_sha256"]}


def summarise(preview: dict[str, Any]) -> str:
    lines = [f"{preview['action_count']} actions · {preview['unhandled_count']} not planned"
             f" · package {preview['package_sha256'][:12]} · consumed={preview['consumed']}"]
    lines.append(f"  bound to {preview['binding']['application_id']}"
                 f" · {preview['binding']['adapter_version']}"
                 f" · page {preview['binding']['page_sha256'][:12]}"
                 f" · fields {preview['binding']['field_sha256_set'][:12]}")
    lines.append("  ACTIONS")
    for action in preview["actions"]:
        lines.append(f"    {action['operation']:7} {action['control']:9}"
                     f" {action['source_kind']:8} {action['source_id'][:34]:36}"
                     f" sha {action['expected_sha256'][:10]} {action['selector'][:26]}")
    lines.append("  NOT PLANNED")
    for item in sorted(preview["unhandled"], key=lambda x: (x["reason"], x["detail"])):
        mark = "required" if item["required"] else "optional"
        lines.append(f"    {item['reason']:22} {item['detail']:26} {mark:8}"
                     f" {item['match_question'][:44]}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--observation", required=True, type=Path)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    # Read-only. A preview that could write is a preview somebody will one day run twice.
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        observation = json.loads(args.observation.read_text(encoding="utf-8"))
        preview = plan(connection, observation, args.application_id)
    except Refused as refused:
        print(json.dumps({"status": "refused", "reason": refused.code}, indent=2))
        raise SystemExit(2)
    finally:
        connection.close()
    print(json.dumps(write_preview(preview, args.output), indent=2))
    print(summarise(preview))


if __name__ == "__main__":
    main()
