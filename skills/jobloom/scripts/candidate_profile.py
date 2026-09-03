#!/usr/bin/env python3
"""The reviewed shape of a Candidate Profile, and how a form field reaches a fact.

Three stores, three questions. The Candidate Profile holds *who I am*: a name, a way to be
reached, where I live. The AnswerLibrary holds *how I answer this employer's question*. An
Application holds *what was used this time*. They were not separate, and the AnswerLibrary was
quietly acting as an address book — which works until the same email has to mean one thing on
a form and another in a profile.

Two decisions here are worth stating rather than reading out of the code.

**A field reaches a fact by meaning, not by id.** `fill_core` reads `source_id` off the
observation and looks it up, so something upstream had to know that `contact.full_name` is
`fact-0001`. The only such mapping in the repository is a three-entry constant in a test
fixture, and a real employer page has never heard of `fact-0001`. So an observer reports a
canonical meaning and this module resolves it, against the active snapshot, to exactly one
locked fact — or refuses. Two facts claiming one meaning is a pause, not a coin toss.

**Accurate and usable are two different confirmations.** A profile of `confirmed` facts fills
nothing: `fill_core` requires `status = 'locked'` and `locked`. So intake records two answers
per field — "this value is right" and "Jobloom may type it into forms" — and only both
together produce a locked fact. That is intake writing down an authorization the user gave,
not intake granting one to itself.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from _common import parse_time, require_table  # noqa: E402


# Where a field came from, which is not the same as whether it is wanted. `corpus` means the
# reviewed recordings ask for it; `user` means it exists because the user needs it and no
# recording proves demand — an honest label, so a field set cannot grow on the strength of
# looking reasonable.
DEMAND_CORPUS = "corpus"
DEMAND_USER = "user"

GROUPS = ("name", "contact", "mailing_address", "current_location", "links", "employment")


class ProfileField:
    """One canonical meaning a profile may hold."""

    __slots__ = ("canonical_id", "group", "demand", "required_where_present", "note")

    def __init__(self, canonical_id: str, group: str, demand: str,
                 required_where_present: bool, note: str) -> None:
        self.canonical_id = canonical_id
        self.group = group
        self.demand = demand
        # Measured, not guessed: whether every recorded control carrying this meaning was
        # marked required in the fixture that recorded it. `false` for a field the corpus
        # never demands, and for one the corpus never mentions.
        self.required_where_present = required_where_present
        self.note = note


def _field(canonical_id, group, demand, required, note):
    return ProfileField(canonical_id, group, demand, required, note)


PROFILE_V1: dict[str, ProfileField] = {field.canonical_id: field for field in (
    # ---- name: five separate confirmations, and no derivation ----------------------
    # `full_name` is not first + last. Name order is not universally western, middle names,
    # suffixes and compound surnames all break the concatenation, and ATS forms ask for both
    # the whole name and its parts. A derivation would turn "technically joinable" into "the
    # display name the user accepts", which is a different claim.
    _field("contact.first_name", "name", DEMAND_CORPUS, True, "given name as the user writes it"),
    _field("contact.middle_name", "name", DEMAND_USER, False, "optional; no recording asks for it"),
    _field("contact.last_name", "name", DEMAND_CORPUS, True, "family name as the user writes it"),
    _field("contact.preferred_name", "name", DEMAND_CORPUS, False,
           "what to be called; never overwrites the legal or full name"),
    _field("contact.full_name", "name", DEMAND_CORPUS, True,
           "confirmed on its own, never derived from the parts"),
    # ---- contact ------------------------------------------------------------------
    _field("contact.email", "contact", DEMAND_CORPUS, True, "required by every fixture that asks"),
    _field("contact.phone", "contact", DEMAND_CORPUS, True, "kept as the user writes it"),
    _field("contact.phone_country", "contact", DEMAND_CORPUS, True, "dialling country, asked separately"),
    _field("contact.phone_extension", "contact", DEMAND_USER, False, "optional; no recording asks for it"),
    # ---- mailing address: schema now, values only when a form asks -----------------
    # No fixture asks for a street or a postal code, so there is no recorded evidence that a
    # profile needs one. The schema exists so the first form that does ask has somewhere to
    # put the answer; until then a missing address is a pause, never a guess from a city.
    _field("contact.address.line1", "mailing_address", DEMAND_USER, False, "street; asked when a form asks"),
    _field("contact.address.line2", "mailing_address", DEMAND_USER, False, "optional second line"),
    _field("contact.postal_code", "mailing_address", DEMAND_USER, False, "never inferred from a city"),
    # ---- current location: what the corpus actually asks about ---------------------
    _field("contact.location_city", "current_location", DEMAND_CORPUS, True, "city as the user states it"),
    _field("contact.location_region", "current_location", DEMAND_USER, False, "state or province"),
    _field("contact.country", "current_location", DEMAND_USER, False, "country of residence"),
    _field("contact.location", "current_location", DEMAND_CORPUS, True,
           "the single-field form some vendors ask for; confirmed separately from its parts"),
    # ---- links ---------------------------------------------------------------------
    _field("profile.linkedin", "links", DEMAND_CORPUS, True, "required by every fixture that asks"),
    _field("profile.github", "links", DEMAND_CORPUS, False, "optional everywhere it appears"),
    _field("profile.portfolio", "links", DEMAND_CORPUS, False, "optional everywhere it appears"),
    _field("profile.website", "links", DEMAND_CORPUS, False, "optional everywhere it appears"),
    # ---- employment: a profile fact that changes -----------------------------------
    _field("employment.current_company", "employment", DEMAND_CORPUS, False,
           "profile data, but not stable: it changes when the user does"),
)}

# Recorded as unknown rather than admitted or dismissed. One fixture asks for it, labelled
# `Current location profile`, optional, as free text — which could be a URL, a saved search or
# something else entirely. Guessing which would put a guess in the profile.
PROFILE_V1_DEFERRED = {
    "profile.location_url": "labelled `Current location profile`; meaning unclear, so unmapped",
}

# Never a profile field, whatever a form calls it. Each belongs somewhere else: a secret store,
# a pause, or the AnswerLibrary. A profile that learned to supply one would be answering a
# question whose whole disposition is that it reaches the user.
FORBIDDEN_MEANINGS = {
    "password", "cookie", "token", "credential", "ssn", "passport", "drivers_licence",
    "national_id", "bank_account", "payment_card", "captcha",
    "eeo.race", "eeo.gender", "eeo.disability", "eeo.veteran",
    "compensation.target_salary", "compensation.total_range",
    "authorization.sponsorship_status", "authorization.sponsorship_select",
    "work_authorized_now", "citizenship_status", "permanent_residence_status",
    "current_country_of_residence", "prior_employment_at_this_company",
    "prior_employment_at_an_affiliate", "referral.contact", "discovery_source",
}

# The two answers intake records per field, and the one combination that produces a fact the
# planner may use.
CONFIRMATION_GATES = ("confirmed_by_user", "autofill_allowed_by_user")


def gate_status(confirmed: Any, autofill_allowed: Any) -> tuple[str, bool]:
    """Which fact state two user answers produce.

    Accurate and usable are separate questions, and a profile that conflated them would
    either fill nothing — every fact `confirmed` and the planner refusing all of them — or
    fill things the user only meant to record.
    """
    if not isinstance(confirmed, bool) or not isinstance(autofill_allowed, bool):
        raise ValueError("both confirmations must be stated as booleans")
    if autofill_allowed and not confirmed:
        raise ValueError("a value cannot be authorised for filling before it is confirmed")
    if not confirmed:
        return "unconfirmed", False
    return ("locked", True) if autofill_allowed else ("confirmed", False)


# Why a canonical meaning did not resolve to something the planner may fill. Stable codes: a
# pause has to say which of these it is, and none of them may name a value.
PROFILE_FIELD_UNKNOWN = "profile_field_unknown"
PROFILE_FACT_MISSING = "profile_fact_missing"
PROFILE_FACT_AMBIGUOUS = "profile_fact_ambiguous"
PROFILE_FACT_NOT_LOCKED = "profile_fact_not_locked"
PROFILE_FACT_EXPIRED = "profile_fact_expired"
PROFILE_FACT_FORBIDDEN = "profile_meaning_not_profile_data"


def resolve_canonical_fact(connection: sqlite3.Connection, canonical_id: str,
                           snapshot_sha256: str, at=None) -> tuple[sqlite3.Row | None, str | None]:
    """The one locked fact in this snapshot that means `canonical_id`, or why there is none.

    Fails closed in every direction. An unknown meaning, a meaning that is not profile data, no
    fact, more than one fact, a fact that is only confirmed, an expired fact — each is a stable
    reason and never a choice. Two facts claiming one meaning is the case worth naming: picking
    either would fill an employer's form from a value nobody arbitrated.
    """
    if canonical_id in FORBIDDEN_MEANINGS:
        return None, PROFILE_FACT_FORBIDDEN
    if canonical_id not in PROFILE_V1:
        return None, PROFILE_FIELD_UNKNOWN
    require_table(connection, "candidate_facts")
    rows = connection.execute(
        "SELECT * FROM candidate_facts WHERE content_sha256=? AND canonical_id=?",
        (snapshot_sha256, canonical_id)).fetchall()
    if not rows:
        return None, PROFILE_FACT_MISSING
    locked = [row for row in rows if row["status"] == "locked" and row["locked"]]
    if len(rows) > 1 and len(locked) != 1:
        return None, PROFILE_FACT_AMBIGUOUS
    if not locked:
        return None, PROFILE_FACT_NOT_LOCKED
    if len(locked) > 1:
        return None, PROFILE_FACT_AMBIGUOUS
    fact = locked[0]
    expires = parse_time(fact["expires_at"])
    if expires and at is not None and at >= expires:
        return None, PROFILE_FACT_EXPIRED
    return fact, None
