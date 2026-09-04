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

The write path below follows from that second decision. Locking happens at snapshot approval,
so intake cannot end at a confirmation: it proposes a round of fields, takes the two answers on
a private worksheet, drafts one new CandidateSnapshot, shows what activating that snapshot
would invalidate, and switches only when the user names the draft by its exact hash. One
snapshot per round rather than one per field, because a snapshot change invalidates every
material lock bound to the old one — a per-field switch would pay that price nine times.

See `references/candidate-profile.md`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import candidate_core  # noqa: E402
import resume_core  # noqa: E402
from _common import (classify_composite_contact, parse_time,  # noqa: E402
                     require_table, update_private_document,
                     worksheet_shape_digest, write_private_document)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


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
    # All four immigration meanings, not only the one the reviewed corpus has a form for.
    # Three of them are absent from `PROFILE_V1` too, so they were already refused - but as
    # "no such profile field", which reads like an oversight in the shape rather than the rule
    # it actually is. They are never interchangeable and none of them is profile data.
    "work_authorized_now", "sponsorship_now", "sponsorship_future",
    "employer_action_required", "citizenship_status", "permanent_residence_status",
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


# ---- intake: how a confirmed value becomes a fact the planner may fill ---------

# Which meanings a round of proposing may cover. Derived from the two measured properties on
# the field rather than listed by hand: the corpus asks for it, and every recorded control
# that carried it was marked required. A hand-written set here would be a taste; this one
# moves only when the measurement moves, and the test pins today's membership so it cannot
# move quietly. Optional and user-demand fields are deliberately absent from every round in
# this version: a profile round costs a new CandidateSnapshot, and a field that no recorded
# form requires does not earn one.
# What the window asks, and in what order. A screen per group of fields a person thinks about
# at once, because asking twenty things one at a time - each with its own two confirmations -
# is a form nobody finishes. The shape follows the employer forms these fields come from: a
# legal name together, an address together, a way to be reached together.
#
# The address is here because the user asked for it, not because the corpus did; `demand`
# records which, and `contact.address.line1` and its neighbours are all `user`. That is the
# label doing its job rather than being overruled - a field set may grow on a person's own
# requirement, it may not grow on looking reasonable.
PROFILE_SCREENS = (
    ("name", ("contact.first_name", "contact.last_name", "contact.full_name",
              "contact.preferred_name")),
    ("address", ("contact.address.line1", "contact.address.line2", "contact.location_city",
                 "contact.location_region", "contact.postal_code", "contact.country",
                 "contact.location")),
    ("reach", ("contact.email", "contact.phone_country", "contact.phone",
               "contact.phone_extension")),
    ("links", ("profile.linkedin", "profile.github", "profile.portfolio", "profile.website",
               "employment.current_company")),
)

PROFILE_ROUNDS = {
    "onboarding-v1": frozenset(
        canonical_id for _, fields in PROFILE_SCREENS for canonical_id in fields),
}

# The measured floor the round has to clear: every meaning the corpus asks for and marks
# required wherever it appears. Derived, so it tracks the measurement; asserted against the
# round in the tests, so a screen cannot quietly drop one.
CORPUS_REQUIRED = frozenset(
    field.canonical_id for field in PROFILE_V1.values()
    if field.demand == DEMAND_CORPUS and field.required_where_present)

# The `fact_type` a profile fact carries into the snapshot. Pinned per group, not per field:
# the type is how the rest of the system talks about a fact's kind, and a type per field would
# invent twenty-one kinds nobody else knows.
GROUP_FACT_TYPE = {
    "name": "identity",
    "contact": "contact",
    "mailing_address": "location",
    "current_location": "location",
    "links": "profile",
    "employment": "employment",
}

# The fact types a composite value may be read out of. The coverage measurement found the
# profile-relevant facts in exactly these three; scanning every fact in the snapshot would let
# an address inside an unrelated resume-derived fact arrive as a proposal, and a proposal is
# the thing a person is most likely to wave through.
COMPOSITE_SOURCE_TYPES = ("identity", "contact", "location")

# A profile worksheet carries no `application_id`. That is the difference from the answer
# worksheet in one field: an answer is scoped to where it may be used, and a profile fact is
# scoped to the snapshot that holds it.
PROFILE_WORKSHEET_EDITABLE_FIELDS = ("value", "confirmed_by_user", "autofill_allowed_by_user")
PROFILE_WORKSHEET_FIELDS = {"proposal_id", "proposal_nonce", "shape_sha256", "snapshot_sha256",
                            "round", "note", "entries"}
PROFILE_WORKSHEET_ENTRY_FIELDS = {"canonical_id", "group", "demand", "required_where_present",
                                  "what_it_is", "how_to_answer", "value", "value_source",
                                  "source_fact_ids", "confirmed_by_user",
                                  "autofill_allowed_by_user"}

WORKSHEET_NOTE = (
    "Private. Two separate questions per field. `confirmed_by_user` says the value is right. "
    "`autofill_allowed_by_user` says Jobloom may type it into an employer's form. Both true "
    "produces a fact the planner may fill from; confirmed alone records it without granting "
    "that; neither leaves the field out entirely, which is a decision and not an unfinished "
    "worksheet. Nothing here is derived: a full name is not first plus last, a country code is "
    "not read out of a phone number, and a value nobody proposed is one you state. Confirming "
    "this worksheet prepares a new CandidateSnapshot; it does not activate one."
)

HOW_TO_ANSWER = "Write it exactly as it should be typed into an employer's form."

VALUE_FROM_FACT = "read out of a contact fact you already confirmed - check it"
VALUE_FROM_USER = "you state it; nothing here proposes it"
VALUE_AMBIGUOUS = "more than one fact could mean this, so nothing is proposed; state it"


def connect(path: Path | str) -> sqlite3.Connection:
    """The same terms every other component opens the database on, including the mode.

    Spelled out rather than borrowed from `candidate_core.connect` because that one runs its
    own `initialize` and not this module's; what is shared is the settings, and those are the
    part a copy could get wrong quietly.
    """
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    initialize(connection)
    if str(path) != ":memory:":
        os.chmod(path, 0o600)
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    """Create the intake tables, and the candidate tables they depend on.

    `candidate_core.initialize` is called rather than assumed: it carries the migration that
    adds `candidate_facts.canonical_id`, which is the column `resolve_canonical_fact` reads. A
    database that has the profile tables and not that column would resolve nothing and say
    only that the fact is missing.
    """
    candidate_core.initialize(connection)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS profile_proposals (
            proposal_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            snapshot_sha256 TEXT NOT NULL,
            shape_sha256 TEXT NOT NULL,
            round TEXT NOT NULL,
            targets_json TEXT NOT NULL,
            nonce TEXT NOT NULL,
            status TEXT NOT NULL,
            drafted_at TEXT,
            registered_at TEXT
        );
        CREATE TABLE IF NOT EXISTS profile_drafts (
            draft_sha256 TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            base_snapshot_sha256 TEXT NOT NULL,
            draft_path TEXT NOT NULL,
            file_sha256 TEXT NOT NULL,
            added_json TEXT NOT NULL,
            impact_json TEXT NOT NULL,
            status TEXT NOT NULL,
            registered_at TEXT,
            FOREIGN KEY (proposal_id) REFERENCES profile_proposals(proposal_id)
        );
    """)
    connection.commit()


def require_proposable(canonical_id: str) -> ProfileField:
    """The one gate every layer repeats, because each layer is reachable on its own.

    Proposing, confirming and registering each check this. Two of the three would be enough
    for an honest caller and none of them would be enough for a tampered worksheet, which is
    the case the repetition is for: a meaning inserted into a worksheet after it was written
    reaches confirmation without ever having been proposed.
    """
    if canonical_id in FORBIDDEN_MEANINGS:
        raise ValueError("that meaning is not profile data")
    if canonical_id in PROFILE_V1_DEFERRED:
        raise ValueError("that meaning is recorded as unclear and has no reviewed shape")
    field = PROFILE_V1.get(canonical_id)
    if field is None:
        raise ValueError("no such profile field")
    return field


def _active_snapshot_row(connection: sqlite3.Connection) -> sqlite3.Row:
    require_table(connection, "candidate_snapshots")
    row = connection.execute(
        "SELECT * FROM candidate_snapshots WHERE status='active'").fetchone()
    if not row:
        raise ValueError("no active candidate snapshot")
    return row


def _read_composites(connection: sqlite3.Connection, snapshot_sha256: str
                     ) -> tuple[dict[str, tuple[str, list[str]]], frozenset[str]]:
    """Meanings a composite fact already holds, and the ones too many facts claim.

    Two facts offering one meaning proposes nothing. Picking either would put a value in front
    of the user with the authority of a proposal and no arbitration behind it, and a person
    checking a plausible value is the weakest gate in the whole flow. Both answers come from
    one walk, because two walks could disagree about which facts were even looked at.
    """
    found: dict[str, list[tuple[str, str]]] = {}
    for row in connection.execute(
            "SELECT fact_id, fact_type, canonical_id, value_json FROM candidate_facts "
            "WHERE content_sha256=?", (snapshot_sha256,)):
        if row["fact_type"] not in COMPOSITE_SOURCE_TYPES:
            continue
        if row["canonical_id"]:
            # A fact that already states one meaning exactly is not a composite to read parts
            # out of. Scanning it would make every field this intake wrote look like a second
            # claim on its own meaning, and a later round would propose nothing at all.
            continue
        value = json.loads(row["value_json"])
        if not isinstance(value, str):
            continue
        for meaning, piece in classify_composite_contact(value).items():
            found.setdefault(meaning, []).append((piece, row["fact_id"]))
    single = {meaning: (sources[0][0], [sources[0][1]])
              for meaning, sources in found.items() if len(sources) == 1}
    return single, frozenset(m for m, sources in found.items() if len(sources) > 1)


def propose_profile(connection: sqlite3.Connection, round_name: str,
                    at: datetime | None = None, sink=None) -> dict[str, Any]:
    """Prepare a private worksheet of the profile fields the user is being asked to confirm.

    Nothing is written to the profile and nothing is returned that carries a value. A value
    already held inside a composite fact is offered so the user checks rather than retypes it;
    everything else is blank, because the alternative to a value nobody holds is a guess.
    """
    if round_name not in PROFILE_ROUNDS:
        raise ValueError("no such reviewed round")
    targets = sorted(PROFILE_ROUNDS[round_name])
    if not targets:
        raise ValueError("the reviewed round names no fields")
    require_table(connection, "profile_proposals")
    snapshot = _active_snapshot_row(connection)
    proposable, ambiguous = _read_composites(connection, snapshot["content_sha256"])

    entries = []
    for target in targets:
        field = require_proposable(target)
        value, sources = proposable.get(target, (None, []))
        if value is not None:
            source_prose = VALUE_FROM_FACT
        elif target in ambiguous:
            source_prose = VALUE_AMBIGUOUS
        else:
            source_prose = VALUE_FROM_USER
        entries.append({
            "canonical_id": target,
            "group": field.group,
            "demand": field.demand,
            "required_where_present": field.required_where_present,
            "what_it_is": field.note,
            "how_to_answer": HOW_TO_ANSWER,
            "value": value,
            "value_source": source_prose,
            "source_fact_ids": sources,
            "confirmed_by_user": False,
            "autofill_allowed_by_user": False,
        })

    proposal_id = f"profile-proposal-{secrets.token_hex(8)}"
    nonce = secrets.token_hex(16)
    timestamp = (at or now_utc()).isoformat()
    worksheet = {
        "proposal_id": proposal_id, "proposal_nonce": nonce, "shape_sha256": "",
        "snapshot_sha256": snapshot["content_sha256"], "round": round_name,
        "note": WORKSHEET_NOTE, "entries": entries,
    }
    worksheet["shape_sha256"] = worksheet_shape_digest(
        worksheet, PROFILE_WORKSHEET_EDITABLE_FIELDS)

    undo = None
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "INSERT INTO profile_proposals (proposal_id, created_at, snapshot_sha256, "
            "shape_sha256, round, targets_json, nonce, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'open')",
            (proposal_id, timestamp, snapshot["content_sha256"], worksheet["shape_sha256"],
             round_name, json.dumps(targets), nonce))
        if sink is not None:
            # Inside the transaction, as in the answer loop: a worksheet the caller could not
            # write must not leave an open proposal behind, and a proposal that could not be
            # recorded must not leave a worksheet that looks usable.
            undo = sink(worksheet)
        connection.commit()
    except Exception:
        connection.rollback()
        if undo is not None:
            undo()
        raise
    return {"worksheet": worksheet,
            "summary": {"proposal_id": proposal_id, "round": round_name,
                        "canonical_ids": targets,
                        "proposed": sum(1 for entry in entries if entry["value"] is not None)}}


def _new_fact_id(canonical_id: str) -> str:
    """A fact id derived from the meaning, so the same meaning never lands twice under two ids.

    Derived rather than random because the collision this prevents is the one that matters: a
    second intake writing `contact.email` under a fresh id would give the meaning two facts,
    and `resolve_canonical_fact` would then refuse both as ambiguous rather than fill either.
    """
    fact_id = "fact-profile-" + canonical_id.replace(".", "-")
    resume_core._require_safe_id(fact_id, "fact_id")
    return fact_id


def _draft_candidate(connection: sqlite3.Connection, snapshot: sqlite3.Row,
                     confirmed: list[tuple[str, dict[str, Any], str, bool]],
                     proposal_id: str, timestamp: str) -> tuple[dict[str, Any], list[str]]:
    """The next CandidateSnapshot: every fact the active one holds, plus the confirmed ones.

    Nothing is removed and nothing is edited. The composite fact stays exactly as it is,
    because answers already name it in their `dependent_fact_ids` and dropping it would break
    an invalidation chain to tidy up a value; the facts split out of it record it as their
    source instead. The copy is checked fact by fact against the hashes the database holds, so
    "this draft only adds" is verified rather than asserted.
    """
    candidate_core.verify_snapshot_file(snapshot)
    candidate, content_hash = resume_core.load_valid_candidate(Path(snapshot["snapshot_path"]))
    if content_hash != snapshot["content_sha256"]:
        raise ValueError("the active snapshot file does not match its recorded content hash")
    stored = {row["fact_id"]: row["fact_sha256"] for row in connection.execute(
        "SELECT fact_id, fact_sha256 FROM candidate_facts WHERE content_sha256=?",
        (snapshot["content_sha256"],))}
    facts = [dict(fact) for fact in candidate["facts"]]
    for fact in facts:
        if stored.get(fact["id"]) != resume_core.canonical_hash(fact):
            raise ValueError("the active snapshot's facts do not match the recorded hashes")

    held = {fact["id"] for fact in facts}
    added = []
    for canonical_id, entry, status, locked in confirmed:
        field = require_proposable(canonical_id)
        fact_id = _new_fact_id(canonical_id)
        if fact_id in held:
            # A meaning already carried by a fact of its own. Re-confirming it is a change to
            # an existing fact, which is a supersession this version does not implement, not
            # an addition it can make quietly.
            raise ValueError("that meaning already has a profile fact in the active snapshot")
        held.add(fact_id)
        facts.append({
            "id": fact_id,
            "type": GROUP_FACT_TYPE[field.group],
            "canonical_id": canonical_id,
            "value": entry["value"],
            "status": status,
            "locked": locked,
            # The user stating their own contact detail is the strongest evidence there is for
            # it. It says nothing about any skill: no claim may cite a profile fact.
            "evidence_strength": "direct",
            "confirmed_at": timestamp,
            "source": {"kind": "profile_intake", "proposal_id": proposal_id,
                       "derived_from": list(entry["source_fact_ids"])},
        })
        added.append(canonical_id)

    draft = dict(candidate)
    draft["facts"] = facts
    draft.pop("content_sha256", None)
    draft["content_sha256"] = resume_core.canonical_hash(draft)
    return draft, added


def _impact(connection: sqlite3.Connection, snapshot: sqlite3.Row,
            draft: dict[str, Any]) -> dict[str, Any]:
    """What registering this draft would invalidate, counted before anything is registered.

    Read-only, and value-free: ids of locks, versions and applications are not candidate data.
    The stale-answer set is computed with the same function registration uses, so the preview
    cannot promise one thing and the switch do another.
    """
    require_table(connection, "material_locks")
    locks = connection.execute("""
        SELECT ml.lock_id, ml.application_id, ml.resume_version_id
        FROM material_locks ml
        JOIN resume_versions rv ON rv.version_id=ml.resume_version_id
        -- `!=` and not `IS NOT`, because this must predict what `register_snapshot`
        -- does and that is the predicate it uses. A version bound to no snapshot at
        -- all keeps its lock there, so the preview must not promise it loses one.
        WHERE ml.invalidated_at IS NULL AND rv.candidate_profile_sha256 != ?
    """, (draft["content_sha256"],)).fetchall()
    applications = sorted({row["application_id"] for row in locks})
    changed = candidate_core._changed_fact_ids(connection, snapshot["content_sha256"], draft)
    require_table(connection, "answers")
    stale = sorted(
        row["canonical_id"] for row in connection.execute(
            "SELECT canonical_id, dependent_fact_ids_json FROM answers WHERE status='active'")
        if changed.intersection(json.loads(row["dependent_fact_ids_json"])))
    reviews = 0
    if applications:
        placeholders = ",".join("?" * len(applications))
        require_table(connection, "pre_submit_reviews")
        reviews = connection.execute(
            "SELECT COUNT(*) FROM pre_submit_reviews WHERE status IN ('generated','approved') "
            f"AND application_id IN ({placeholders})", applications).fetchone()[0]
    return {
        "material_locks_invalidated": len(locks),
        "resume_versions_needing_rebinding": sorted({row["resume_version_id"] for row in locks}),
        "applications_affected": applications,
        "pre_submit_reviews_invalidated": reviews,
        "answers_going_stale": stale,
    }


def confirm_profile(connection: sqlite3.Connection, worksheet: Any, private_root: Path,
                    at: datetime | None = None) -> dict[str, Any]:
    """Turn a filled-in worksheet into a draft snapshot and a preview of what it would cost.

    Registers nothing and activates nothing. A proposal is single-use here as it is for
    answers: a second draft from one proposal would leave two drafts and no way to say which
    an approval referred to. Changing an answer means proposing again, which also re-reads the
    profile the worksheet is being checked against.
    """
    require_table(connection, "profile_proposals")
    connection.execute("BEGIN IMMEDIATE")
    try:
        return _confirm_within_transaction(connection, worksheet, private_root, at)
    except Exception:
        connection.rollback()
        raise


def check_worksheet(connection: sqlite3.Connection,
                    worksheet: Any) -> tuple[sqlite3.Row, sqlite3.Row]:
    """Everything that binds a worksheet to its proposal, and nothing that acts on it.

    Separate from confirmation because the interactive filler runs the same checks before it
    asks the first question. A person who answers nine questions into a worksheet whose
    proposal has already been drafted has spent nine answers on a file that cannot be
    confirmed, and finding that out at the end is finding it out too late.
    """
    if not isinstance(worksheet, dict) or set(worksheet) != PROFILE_WORKSHEET_FIELDS:
        raise ValueError("worksheet has an unexpected shape")
    require_table(connection, "profile_proposals")
    proposal = connection.execute(
        "SELECT * FROM profile_proposals WHERE proposal_id=?",
        (worksheet["proposal_id"],)).fetchone()
    if not proposal:
        raise ValueError("no such proposal")
    if not secrets.compare_digest(str(worksheet["proposal_nonce"]), str(proposal["nonce"])):
        raise ValueError("worksheet does not belong to that proposal")
    if proposal["status"] != "open":
        raise ValueError("this proposal has already been drafted")

    entries = worksheet["entries"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("worksheet carries no entries")
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != PROFILE_WORKSHEET_ENTRY_FIELDS:
            raise ValueError("worksheet entry has an unexpected shape")
    if worksheet_shape_digest(worksheet, PROFILE_WORKSHEET_EDITABLE_FIELDS) != proposal["shape_sha256"]:
        raise ValueError("worksheet no longer matches the proposal it came from")
    targets = json.loads(proposal["targets_json"])
    if [entry["canonical_id"] for entry in entries] != sorted(targets):
        raise ValueError("worksheet does not cover the proposed fields")
    snapshot = _active_snapshot_row(connection)
    if snapshot["content_sha256"] != proposal["snapshot_sha256"]:
        # The profile moved under the worksheet, so a proposed value may have been read out of
        # a fact that no longer says that. Propose again rather than confirm a stale reading.
        raise ValueError("the candidate profile changed since this was proposed")
    return proposal, snapshot


def _confirm_within_transaction(connection: sqlite3.Connection, worksheet: dict[str, Any],
                                private_root: Path, at: datetime | None) -> dict[str, Any]:
    proposal, snapshot = check_worksheet(connection, worksheet)
    entries = worksheet["entries"]

    confirmed, recorded_only, left_out = [], [], []
    for entry in entries:
        canonical_id = entry["canonical_id"]
        require_proposable(canonical_id)
        status, locked = gate_status(entry["confirmed_by_user"],
                                     entry["autofill_allowed_by_user"])
        if status == "unconfirmed":
            left_out.append(canonical_id)
            continue
        value = entry["value"]
        if not isinstance(value, str) or not value.strip():
            # Confirming an empty field would file a blank as the user's word for it.
            raise ValueError("a confirmed field must carry a value")
        confirmed.append((canonical_id, entry, status, locked))
        if not locked:
            recorded_only.append(canonical_id)
    if not confirmed:
        raise ValueError("nothing was confirmed")

    timestamp = (at or now_utc()).isoformat()
    draft, added = _draft_candidate(connection, snapshot, confirmed,
                                    proposal["proposal_id"], timestamp)
    if draft["content_sha256"] == snapshot["content_sha256"]:
        raise ValueError("the draft is identical to the active snapshot")
    impact = _impact(connection, snapshot, draft)

    path = Path(private_root) / "profile-drafts" / f"{draft['content_sha256'][:16]}.json"
    undo = write_private_document(path, Path(private_root), draft)
    try:
        connection.execute(
            "UPDATE profile_proposals SET status='drafted', drafted_at=? "
            "WHERE proposal_id=? AND status='open'", (timestamp, proposal["proposal_id"]))
        connection.execute(
            "INSERT INTO profile_drafts (draft_sha256, proposal_id, created_at, "
            "base_snapshot_sha256, draft_path, file_sha256, added_json, impact_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')",
            (draft["content_sha256"], proposal["proposal_id"], timestamp,
             snapshot["content_sha256"], str(path), resume_core.file_sha256(path),
             json.dumps(sorted(added)), json.dumps(impact)))
        connection.commit()
    except Exception:
        connection.rollback()
        undo()
        raise
    return {
        "draft_sha256": draft["content_sha256"],
        "draft_path": str(path),
        "base_snapshot_sha256": snapshot["content_sha256"],
        "facts_locked": sorted(set(added) - set(recorded_only)),
        "facts_recorded_only": sorted(recorded_only),
        "fields_left_out": sorted(left_out),
        "impact_if_registered": impact,
        "registered": False,
        "next_step": ("review the impact, then approve this exact draft by its sha256; "
                      "nothing is active until you do"),
    }


def register_profile(connection: sqlite3.Connection, draft_sha256: str, store: Path,
                     actor: str, at: datetime | None = None) -> dict[str, Any]:
    """Activate one approved draft, wholly or not at all.

    The user names the draft by its exact hash, as they do for a pre-submit review, because
    approving "the draft" is not approving a particular set of facts. Everything this writes
    happens in the transaction `register_snapshot` commits: the proposal is claimed and the
    draft marked before that call, so a registration that fails takes the bookkeeping with it
    and never leaves a consumed proposal behind a snapshot that does not exist.
    """
    if actor != "user":
        raise ValueError("activating a candidate profile requires the user actor")
    require_table(connection, "profile_drafts")
    connection.execute("BEGIN IMMEDIATE")
    try:
        draft_row = connection.execute(
            "SELECT * FROM profile_drafts WHERE draft_sha256=?", (draft_sha256,)).fetchone()
        if not draft_row:
            raise ValueError("no such draft")
        if draft_row["status"] != "open":
            raise ValueError("this draft has already been registered")
        proposal = connection.execute(
            "SELECT * FROM profile_proposals WHERE proposal_id=?",
            (draft_row["proposal_id"],)).fetchone()
        if not proposal or proposal["status"] != "drafted":
            raise ValueError("this draft's proposal is not awaiting approval")
        snapshot = _active_snapshot_row(connection)
        if snapshot["content_sha256"] != draft_row["base_snapshot_sha256"]:
            # Somebody registered a snapshot between the preview and the approval, so the
            # impact the user read was computed against a profile that is no longer there.
            raise ValueError("the candidate profile changed since this draft was prepared")
        path = Path(draft_row["draft_path"])
        if not path.is_file() or resume_core.file_sha256(path) != draft_row["file_sha256"]:
            raise ValueError("the draft file has changed since it was prepared")
        draft, content_hash = resume_core.load_valid_candidate(path)
        if content_hash != draft_sha256:
            raise ValueError("the draft file no longer states the approved content hash")
        for fact in draft["facts"]:
            # The third place the forbidden check runs, and the only one inside the switch.
            # The two before it are checks on a file; this one is a check on what is about to
            # become the active profile, which is the thing the rule is actually about.
            if fact.get("canonical_id"):
                require_proposable(fact["canonical_id"])

        moment = at or now_utc()
        timestamp = moment.isoformat()
        claimed = connection.execute(
            "UPDATE profile_proposals SET status='registered', registered_at=? "
            "WHERE proposal_id=? AND status='drafted'", (timestamp, proposal["proposal_id"]))
        if claimed.rowcount != 1:
            raise ValueError("this draft's proposal is not awaiting approval")
        claimed = connection.execute(
            "UPDATE profile_drafts SET status='registered', registered_at=? "
            "WHERE draft_sha256=? AND status='open'", (timestamp, draft_sha256))
        if claimed.rowcount != 1:
            raise ValueError("this draft has already been registered")
        # Commits the whole transaction, this bookkeeping included, or rolls all of it back.
        registered = candidate_core.register_snapshot(connection, Path(store), path, actor,
                                                     moment)
    except Exception:
        connection.rollback()
        raise
    return {
        "content_sha256": registered["content_sha256"],
        "superseded": draft_row["base_snapshot_sha256"],
        "facts_added": json.loads(draft_row["added_json"]),
        "predicted_impact": json.loads(draft_row["impact_json"]),
        "observed_impact": _observed_impact(connection, registered["content_sha256"],
                                            timestamp),
    }


def _observed_impact(connection: sqlite3.Connection, content_sha256: str,
                     timestamp: str) -> dict[str, Any]:
    """What this switch actually invalidated, read back rather than restated from the preview.

    Scoped to this registration's own timestamp, because an unscoped count would grow with
    every profile change ever made and would agree with a one-change preview only on the first
    one. The stale-answer count cannot be scoped that way - the invalidation walk records no
    time on the row - so it is reported as the total it is and never as this switch's delta.
    """
    require_table(connection, "material_locks")
    locks = connection.execute(
        "SELECT resume_version_id, application_id FROM material_locks "
        "WHERE invalidation_reason='candidate_snapshot_changed' AND invalidated_at=?",
        (timestamp,)).fetchall()
    require_table(connection, "answers")
    stale = connection.execute("SELECT COUNT(*) FROM answers WHERE status='stale'").fetchone()[0]
    require_table(connection, "pre_submit_reviews")
    reviews = connection.execute(
        "SELECT COUNT(*) FROM pre_submit_reviews WHERE status='invalidated' "
        "AND invalidation_reason='candidate_snapshot_changed' AND invalidated_at=?",
        (timestamp,)).fetchone()[0]
    resolvable = sorted(
        canonical_id for canonical_id in PROFILE_V1
        if resolve_canonical_fact(connection, canonical_id, content_sha256)[0] is not None)
    return {
        "material_locks_invalidated": len(locks),
        "resume_versions_needing_rebinding": sorted({row["resume_version_id"] for row in locks}),
        "applications_affected": sorted({row["application_id"] for row in locks}),
        "answers_stale_total": stale,
        "pre_submit_reviews_invalidated": reviews,
        "meanings_now_resolvable": resolvable,
    }


def status(connection: sqlite3.Connection) -> dict[str, Any]:
    """Which meanings the active snapshot can already fill from, and which it cannot.

    Names meanings and reason codes. A value never reaches this report.
    """
    require_table(connection, "candidate_snapshots")
    active = connection.execute(
        "SELECT content_sha256 FROM candidate_snapshots WHERE status='active'").fetchone()
    if not active:
        return {"active_snapshot": None, "resolvable": [], "unresolved": {}}
    resolvable, unresolved = [], {}
    for canonical_id in sorted(PROFILE_V1):
        fact, reason = resolve_canonical_fact(connection, canonical_id,
                                              active["content_sha256"], now_utc())
        if fact is not None:
            resolvable.append(canonical_id)
        else:
            unresolved[canonical_id] = reason
    return {"active_snapshot": active["content_sha256"],
            "round": sorted(set().union(*PROFILE_ROUNDS.values())),
            "resolvable": resolvable, "unresolved": unresolved}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--private-root", type=Path,
                        help="where worksheets and drafts are written; defaults beside the db")
    parser.add_argument("--store", type=Path, help="candidate snapshot store")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    propose = commands.add_parser("propose-profile")
    propose.add_argument("--round", required=True, dest="round_name")
    propose.add_argument("--out", required=True, type=Path)
    fill = commands.add_parser("fill-profile")
    fill.add_argument("--worksheet", required=True, type=Path)
    confirm = commands.add_parser("confirm-profile")
    confirm.add_argument("--worksheet", required=True, type=Path)
    register = commands.add_parser("register-profile")
    register.add_argument("--draft-sha256", required=True)
    register.add_argument("--actor", required=True)
    commands.add_parser("status")
    args = parser.parse_args()

    private_root = args.private_root or args.db.parent
    store = args.store or args.db.parent / "candidates"
    connection = connect(args.db)
    if args.command == "init":
        initialize(connection)
        result = {"status": "initialized", "private_root": str(private_root)}
    elif args.command == "propose-profile":
        result = propose_profile(
            connection, args.round_name,
            sink=lambda sheet: write_private_document(args.out, private_root, sheet),
        )["summary"]
        result["worksheet"] = str(args.out)
    elif args.command == "fill-profile":
        if not sys.stdin.isatty():
            # An intake reading from a pipe is how a scripted value gets filed as the user's
            # own word. The whole point of this command is that a person answered.
            raise SystemExit("fill-profile asks questions, so it needs a terminal")
        result = fill_profile(args.worksheet, private_root, connection)
    elif args.command == "confirm-profile":
        worksheet = json.loads(args.worksheet.read_text(encoding="utf-8"))
        result = confirm_profile(connection, worksheet, private_root)
    elif args.command == "register-profile":
        result = register_profile(connection, args.draft_sha256, store, args.actor)
    else:
        result = status(connection)
    if args.command != "fill-profile":
        print(json.dumps(result, indent=2, ensure_ascii=False))



# ---- the interactive filler ----------------------------------------------------
#
# A worksheet a person edits in a text editor is a developer's affordance, not the step. This
# is the step: Jobloom asks, the user answers, and the answer goes into the worksheet the user
# was already given. It writes only the three fields the digest leaves editable, so a filled
# worksheet is still the worksheet its proposal bound.
#
# What it does not do is normalise anything silently. `full_name` is not first plus last and a
# country code is not read off a phone number; that rule does not become negotiable because
# the value arrived through a prompt rather than through an editor. What it may do is refuse a
# value that cannot be what it claims to be, and offer a rewrite the user accepts or declines
# — the same relationship the proposed values have to the composite fact they came from.


def _check_email(value: str) -> str | None:
    if " " in value or value.count("@") != 1:
        return "an email address is one local part, one @, and one domain"
    local, _, domain = value.partition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return "an email address is one local part, one @, and one domain"
    return None


def _check_phone(value: str) -> str | None:
    if sum(character.isdigit() for character in value) < 7:
        return "a phone number needs at least seven digits"
    return None


def _check_phone_country(value: str) -> str | None:
    if not re.fullmatch(r"\+?\d{1,4}", value):
        return "a dialling country code is one to four digits, optionally after a +"
    return None


def _check_url(value: str) -> str | None:
    if not value.startswith(("http://", "https://")):
        return "a link starts with http:// or https://"
    host = value.split("://", 1)[1].split("/", 1)[0]
    if "." not in host or " " in value:
        return "a link needs a host"
    return None


def _check_text(value: str) -> str | None:
    return None


def _offer_country_code(value: str) -> str | None:
    """`1` almost certainly means `+1`. Offered, because almost is not a licence to rewrite."""
    return f"+{value}" if value.isdigit() else None


def _offer_scheme(value: str) -> str | None:
    """A profile pasted without its scheme. Offered rather than assumed: some forms want the
    bare handle, and the user is the one who knows which their profile is."""
    return f"https://{value}" if value and "://" not in value else None


# Per meaning, because a rule that applied to a group would have to be right for the whole
# group. Anything not named here is text: refused only when it is empty.
COUNTRY_NAMES = tuple(json.loads(
    (Path(__file__).resolve().parent.parent / "assets" / "countries.json")
    .read_text(encoding="utf-8"))["names"])
_COUNTRY_INDEX = {name.casefold(): name for name in COUNTRY_NAMES}


def _check_country(value: str) -> str | None:
    if value.casefold() not in _COUNTRY_INDEX:
        return "pick a country from the list"
    return None


def _offer_country(value: str) -> str | None:
    """The same country under a different capitalisation is the same country, and is offered
    in the list's own spelling rather than accepted as typed - so two profiles that mean the
    United States do not hold two different strings."""
    return _COUNTRY_INDEX.get(value.casefold())


FIELD_RULES = {
    "contact.country": (_check_country, _offer_country),
    "contact.email": (_check_email, None),
    "contact.phone": (_check_phone, None),
    "contact.phone_country": (_check_phone_country, _offer_country_code),
    "profile.linkedin": (_check_url, _offer_scheme),
    "profile.github": (_check_url, _offer_scheme),
    "profile.portfolio": (_check_url, _offer_scheme),
    "profile.website": (_check_url, _offer_scheme),
}

def check_value(canonical_id: str, value: str) -> dict[str, Any]:
    """One field's value, checked once, for whoever is asking.

    The prompt loop and the desktop wizard both call this rather than each carrying its own
    idea of what an email address is. It reports three things and changes none of them: the
    value with its surrounding whitespace gone, a complaint if it cannot be what it claims to
    be, and a rewrite worth offering. The rewrite is never applied here — a caller shows it,
    the user accepts it, and the accepted value comes back through this function again.
    """
    require_proposable(canonical_id)
    if not isinstance(value, str):
        raise ValueError("a value is text")
    check, offer = FIELD_RULES.get(canonical_id, (_check_text, None))
    # Stripped, not reformatted: removing the whitespace around a value is the absence of a
    # change. Everything past that is offered rather than done.
    stripped = value.strip()
    if not stripped:
        return {"value": "", "empty": True, "complaint": None, "suggestion": None}
    suggestion = offer(stripped) if offer is not None else None
    return {"value": stripped, "empty": False, "complaint": check(stripped),
            "suggestion": suggestion if suggestion and suggestion != stripped else None}


def apply_answers(worksheet_path: Path, private_root: Path, connection: sqlite3.Connection,
                  answers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Write the user's answers into the worksheet their proposal bound, or write nothing.

    The one writer, so a terminal prompt and a desktop form cannot disagree about what a
    filled worksheet looks like. Only the three fields the digest leaves editable are touched,
    every confirmed value goes back through `check_value`, and a worksheet that fails anywhere
    is left exactly as it was rather than half updated.
    """
    worksheet = json.loads(Path(worksheet_path).read_text(encoding="utf-8"))
    check_worksheet(connection, worksheet)
    known = {entry["canonical_id"] for entry in worksheet["entries"]}
    unknown = sorted(set(answers) - known)
    if unknown:
        raise ValueError("answers name fields this worksheet does not carry")

    prepared, locked, recorded, left_out = [], [], [], []
    for entry in worksheet["entries"]:
        canonical_id = entry["canonical_id"]
        given = answers.get(canonical_id) or {}
        confirmed = given.get("confirmed", False)
        autofill = given.get("autofill", False)
        # Raises on the one illegal combination: authorised for filling, never confirmed.
        gate_status(confirmed, autofill)
        if not confirmed:
            prepared.append((entry, entry["value"], False, False))
            left_out.append(canonical_id)
            continue
        checked = check_value(canonical_id, given.get("value") or "")
        if checked["empty"]:
            raise ValueError("a confirmed field must carry a value")
        if checked["complaint"]:
            raise ValueError(f"{canonical_id}: {checked['complaint']}")
        prepared.append((entry, checked["value"], True, autofill))
        (locked if autofill else recorded).append(canonical_id)
    if not locked and not recorded:
        raise ValueError("nothing was confirmed")

    for entry, value, confirmed, autofill in prepared:
        entry["value"] = value
        entry["confirmed_by_user"] = confirmed
        entry["autofill_allowed_by_user"] = autofill
    update_private_document(Path(worksheet_path), Path(private_root), worksheet)
    return {"written": True, "worksheet": str(worksheet_path), "locked": sorted(locked),
            "recorded_only": sorted(recorded), "left_out": sorted(left_out)}


def _yes(answer: str) -> bool:
    return answer.strip().lower() in {"y", "yes"}


def _ask_value(entry: dict[str, Any], ask, say) -> str | None:
    """One field's value at the prompt, with any rewrite offered rather than applied."""
    canonical_id = entry["canonical_id"]
    if entry["value"]:
        say(f"    proposed: {entry['value']}")
        say(f"    ({entry['value_source']})")
        if _yes(ask("    keep this value? [y/n] ")):
            return entry["value"]
    while True:
        checked = check_value(canonical_id, ask("    value (blank line leaves this field out): "))
        if checked["empty"]:
            return None
        if checked["suggestion"]:
            say(f"    did you mean: {checked['suggestion']}")
            if _yes(ask("    use that instead? [y/n] ")):
                checked = check_value(canonical_id, checked["suggestion"])
        if checked["complaint"] is None:
            return checked["value"]
        say(f"    that cannot be right: {checked['complaint']}")


def fill_profile(worksheet_path: Path, private_root: Path, connection: sqlite3.Connection,
                 ask=input, say=print) -> dict[str, Any]:
    """Ask the user each field of a worksheet, and write down what they say.

    The binding checks run before the first question rather than after the last: nine answers
    typed into a worksheet whose proposal is already spent are nine answers thrown away.
    Nothing is written until every field has been asked and the user says to write it, so
    stopping halfway leaves the worksheet exactly as it was.
    """
    worksheet = json.loads(Path(worksheet_path).read_text(encoding="utf-8"))
    check_worksheet(connection, worksheet)
    entries = worksheet["entries"]

    say(f"\n{len(entries)} fields in round {worksheet['round']}.")
    say("Two questions each: whether the value is right, and whether Jobloom may type it into")
    say("an employer's form. Answering only the first records it without granting the second.")
    say("Nothing is derived from anything else, so a field nobody proposed is one you state.\n")

    answered = []
    for index, entry in enumerate(entries, start=1):
        canonical_id = entry["canonical_id"]
        say(f"[{index}/{len(entries)}] {canonical_id}")
        say(f"    {entry['what_it_is']}")
        value = _ask_value(entry, ask, say)
        if value is None:
            say("    left out of this round.\n")
            answered.append((entry, None, False, False))
            continue
        confirmed = _yes(ask("    confirm this value is correct? [y/n] "))
        if not confirmed:
            say("    left out of this round.\n")
            answered.append((entry, None, False, False))
            continue
        autofill = _yes(ask("    may Jobloom type it into an employer's form? [y/n] "))
        say("")
        answered.append((entry, value, True, autofill))

    locked = [entry["canonical_id"] for entry, _, confirmed, auto in answered if confirmed and auto]
    recorded = [entry["canonical_id"] for entry, _, confirmed, auto in answered
                if confirmed and not auto]
    left_out = [entry["canonical_id"] for entry, _, confirmed, _ in answered if not confirmed]
    say("Summary, by meaning. No value is repeated here.")
    for canonical_id in locked:
        say(f"    {canonical_id:<24} confirmed, fillable")
    for canonical_id in recorded:
        say(f"    {canonical_id:<24} confirmed, recorded only")
    for canonical_id in left_out:
        say(f"    {canonical_id:<24} left out")
    if not locked and not recorded:
        say("\nNothing was confirmed, so there is nothing to write.")
        return {"written": False, "locked": [], "recorded_only": [], "left_out": left_out}
    if not _yes(ask("\nWrite these answers into the worksheet? [y/n] ")):
        say("Nothing written. The worksheet is as it was.")
        return {"written": False, "locked": [], "recorded_only": [], "left_out": left_out}

    written = apply_answers(
        worksheet_path, private_root, connection,
        {entry["canonical_id"]: {"value": value, "confirmed": confirmed, "autofill": autofill}
         for entry, value, confirmed, autofill in answered})
    say(f"Written to {worksheet_path}.")
    return written


if __name__ == "__main__":
    main()
