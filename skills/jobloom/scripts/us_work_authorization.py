#!/usr/bin/env python3
"""One question, confirmed once for the country and re-confirmed for each application.

`us-work-authorization-v1`, and deliberately its own version rather than an edit to
`TASK14_INTAKE_SHAPE`. That constant records a reviewed round bound to one application; a
version that changed underneath would make the same name mean two different grants of
authority depending on when it was read. Task 14's constants, worksheet and proposals are
untouched, and this covers exactly one question — the one confirmed on a real Lever page on
2026-09-11.

**Two confirmations, and neither implies the other.**

The first saves the answer: scoped to the country, `event_driven`, because a work
authorization is a fact about a person and not about an application. The second allows that
answer to be used in one application, expires within fourteen days, and binds to the exact
answer and the exact value the user was shown. `answer_library.immigration_authorization`
reads the second; without it the first is on file and unusable, which is the whole point of
the rule.

**The value never leaves the window.** It arrives over the loopback service from the page the
user typed it into, is written, and is returned only to that same window so the authorization
screen can show what is being authorised. It does not pass through this module's CLI, through
a log line, or through anything printed.

**No trigger is registered.** `invalidation_triggers` is empty, for the reason Task 14 gives:
`invalidate_by_trigger` has no production caller, so a trigger named here would be a
declaration nobody ever raises. The wired channel is the binding itself — an authorization
names an `answer_id` and a value digest, so a changed or superseded answer stops matching and
the field goes back to asking. That is checked by a test rather than asserted here.

**Nothing about sponsorship is stored or derived.** `sponsorship_now` and `sponsorship_future`
are `always_manual` in `field_policy` and stay there; being authorised to work is not an
answer about needing sponsorship, and the two are not computed from each other.
"""

from __future__ import annotations

import json
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
import field_policy  # noqa: E402

VERSION = "us-work-authorization-v1"
CANONICAL_ID = "work_authorized_now"
COUNTRY = "US"
# The exact wording confirmed on the Lever page, already registered as a verified question
# form. Matching is exact, so this is the string and not a description of it.
QUESTION = "Are you authorized to work in the US?"
CANONICAL_MEANING = "Authorized to work in this country now"
# Every value taken from a register that already exists. `event_driven` is one of
# `answer_library.VALIDITY_CLASSES`; `time_sensitive_fact` is one of `ANSWER_TYPES`;
# `user_confirmed` is one of `SOURCE_TYPES`. No new strings.
ANSWER_SHAPE: dict[str, Any] = {
    "canonical_id": CANONICAL_ID,
    "canonical_meaning": CANONICAL_MEANING,
    "question": QUESTION,
    "answer_type": "time_sensitive_fact",
    "source_type": "user_confirmed",
    "validity_class": "event_driven",
    "scope": {"country": COUNTRY},
    "auto_fill_allowed": True,
    # Never. This surface fills; it does not submit.
    "auto_submit_allowed": False,
    "invalidation_triggers": [],
    "dependent_fact_ids": [],
}
AUTHORIZATION_TTL = timedelta(days=14)
MAX_ANSWER = 200


def _now(at: datetime | None) -> datetime:
    return at or datetime.now(timezone.utc)


def check_answer(value: Any) -> str:
    """The value as the user typed it, or a refusal. Never a shortened version of it.

    The refusal names no value: a message quoting it would put a work-authorization answer
    into an exception, a log line and an HTTP body, which is three places it must not reach.
    """
    if not isinstance(value, str):
        raise ValueError("an answer must be text")
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("an answer cannot be blank")
    if len(trimmed) > MAX_ANSWER:
        raise ValueError("answer is longer than this can store")
    return trimmed


def state(connection: sqlite3.Connection, application_id: str,
          *, at: datetime | None = None) -> dict[str, Any]:
    """Where this question stands for this application. Read-only.

    Returns the stored answer, because the authorization screen has to show the user what
    they would be authorising. "Allow reuse" with nothing on screen is not a re-confirmation
    of a work-authorization answer, which is what the rule asks for.
    """
    at = _now(at)
    disposition, domain, _family = field_policy.disposition(
        field_id=CANONICAL_ID, question=QUESTION, control="radio", source_kind=None)
    row = connection.execute(
        "SELECT answer_id, answer_json, confirmed_at FROM answers WHERE canonical_id=? "
        "AND confirmation_status='confirmed' AND status='active' "
        "ORDER BY confirmed_at DESC LIMIT 1", (CANONICAL_ID,)).fetchone()
    report: dict[str, Any] = {
        "version": VERSION,
        "question": QUESTION,
        "canonical_id": CANONICAL_ID,
        "country": COUNTRY,
        "application_id": application_id,
        "disposition": disposition,
        "domain": domain,
        "answer_exists": row is not None,
        "answer_id": row["answer_id"] if row else None,
        # Shown to the user, in their own window, so the second confirmation is about
        # something they can see.
        "answer": json.loads(row["answer_json"]) if row else None,
        "answer_value_sha256": (answer_library.answer_value_sha256(connection, row["answer_id"])
                                if row else None),
        "confirmed_at": row["confirmed_at"] if row else None,
    }
    if row is None:
        report["authorized"] = False
        report["detail"] = "no_confirmed_answer"
        return report
    allowed, detail = answer_library.immigration_authorization(
        connection, application_id, CANONICAL_ID, row, at)
    report["authorized"] = allowed
    report["detail"] = detail
    return report


def save_answer(connection: sqlite3.Connection, value: Any,
                *, at: datetime | None = None) -> dict[str, Any]:
    """The first confirmation: this is my work authorization, for this country.

    Supersedes an earlier answer rather than editing one. Editing would leave every
    authorization granted against the old value still naming an `answer_id` whose content had
    changed underneath it; superseding means those authorizations stop matching, which is the
    behaviour the rule wants and the reason no trigger has to fire.
    """
    at = _now(at)
    answer = check_answer(value)
    previous = connection.execute(
        "SELECT answer_id FROM answers WHERE canonical_id=? AND status='active'",
        (CANONICAL_ID,)).fetchall()
    answer_id = f"answer-{VERSION}-{secrets.token_hex(6)}"
    entry = dict(ANSWER_SHAPE)
    entry.update({"answer_id": answer_id, "answer": answer,
                  "confirmation_status": "confirmed", "confirmed_at": at.isoformat()})
    if previous:
        entry["supersedes_id"] = previous[0]["answer_id"]
    answer_library.add_answer(connection, entry)
    for row in previous:
        connection.execute("UPDATE answers SET status='superseded' WHERE answer_id=?",
                           (row["answer_id"],))
    connection.commit()
    # The value is not in the return. The caller reads it back through `state`, which serves
    # the window that is showing it.
    return {"version": VERSION, "answer_id": answer_id, "superseded": len(previous),
            "scope": dict(ANSWER_SHAPE["scope"]),
            "validity_class": ANSWER_SHAPE["validity_class"]}


def authorize(connection: sqlite3.Connection, application_id: str, answer_id: str,
              answer_value_sha256: str, *, at: datetime | None = None) -> dict[str, Any]:
    """The second confirmation: yes, use *this* answer in *this* application.

    The digest is supplied by the caller and checked against the stored answer, so an
    authorization recorded against a screen showing a stale value is refused rather than
    granted. Fourteen days at most, revocable, and it authorises nothing else.
    """
    at = _now(at)
    answer_library.add_answer_authorization(connection, {
        "authorization_id": f"auth-{VERSION}-{secrets.token_hex(6)}",
        "confirmed_at": at.isoformat(),
        "expires_at": (at + AUTHORIZATION_TTL).isoformat(),
        "scope": {"application_id": application_id, "country": COUNTRY},
        "canonical_id": CANONICAL_ID,
        "answer_id": answer_id,
        "answer_value_sha256": answer_value_sha256,
        "actor": "user",
    })
    return {"version": VERSION, "application_id": application_id, "answer_id": answer_id,
            "expires_at": (at + AUTHORIZATION_TTL).isoformat()}
