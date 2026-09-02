#!/usr/bin/env python3
"""Local deterministic Jobloom answer library and freshness gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from _common import answer_issue, context_matches, parse_time  # noqa: E402


IMMIGRATION_CANONICAL_IDS = {
    "work_authorized_now",
    "sponsorship_now",
    "sponsorship_future",
    "employer_action_required",
}
ANSWER_TYPES = {
    "stable_fact", "time_sensitive_fact", "conditional_preference", "company_specific",
    "role_specific", "application_specific", "voluntary_disclosure", "legal_commitment",
    "open_text_template", "derived_answer",
}
VALIDITY_CLASSES = {"stable", "periodic", "event_driven", "per_application"}
SOURCE_TYPES = {
    "user_confirmed", "verified_candidate_fact", "approved_resume", "user_rule", "deterministic_derivation",
}
SCOPE_FIELDS = {"country", "jurisdiction", "company", "role_family", "employment_type", "application_id", "queue_id"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_question(question: str) -> str:
    value = unicodedata.normalize("NFKC", question).casefold().strip()
    value = re.sub(r"\s+", " ", value)
    return value.rstrip(" ?.!")


def connect(path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    initialize(connection)
    if str(path) != ":memory:":
        os.chmod(path, 0o600)
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS answers (
            answer_id TEXT PRIMARY KEY,
            canonical_id TEXT NOT NULL,
            canonical_meaning TEXT NOT NULL,
            answer_json TEXT NOT NULL,
            answer_type TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_ref TEXT,
            confirmation_status TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            effective_from TEXT,
            expires_at TEXT,
            review_after TEXT,
            validity_class TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            preconditions_json TEXT NOT NULL,
            exclusions_json TEXT NOT NULL,
            auto_fill_allowed INTEGER NOT NULL,
            auto_submit_allowed INTEGER NOT NULL,
            sensitivity TEXT NOT NULL,
            invalidation_triggers_json TEXT NOT NULL,
            dependent_fact_ids_json TEXT NOT NULL,
            supersedes_id TEXT,
            status TEXT NOT NULL,
            ambiguity_notes TEXT,
            FOREIGN KEY (supersedes_id) REFERENCES answers(answer_id)
        );
        CREATE INDEX IF NOT EXISTS answers_canonical_idx ON answers(canonical_id, status);

        CREATE TABLE IF NOT EXISTS question_forms (
            normalized_question TEXT NOT NULL,
            canonical_id TEXT NOT NULL,
            match_level TEXT NOT NULL,
            verified_by_user INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (normalized_question, canonical_id)
        );

        CREATE TABLE IF NOT EXISTS authorizations (
            authorization_id TEXT PRIMARY KEY,
            confirmed_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            revoked_at TEXT,
            status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            entity_id TEXT,
            metadata_json TEXT NOT NULL
        );
    """)
    connection.commit()


def audit(connection: sqlite3.Connection, event_type: str, entity_id: str | None, metadata: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO audit_events (created_at, event_type, entity_id, metadata_json) VALUES (?, ?, ?, ?)",
        (now_utc().isoformat(), event_type, entity_id, json.dumps(metadata, sort_keys=True)),
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def add_answer(connection: sqlite3.Connection, entry: dict[str, Any]) -> None:
    required = {
        "answer_id", "canonical_id", "canonical_meaning", "answer", "answer_type", "source_type",
        "confirmation_status", "confirmed_at", "validity_class", "auto_fill_allowed", "auto_submit_allowed",
    }
    missing = sorted(required - set(entry))
    if missing:
        raise ValueError(f"answer entry missing required fields: {', '.join(missing)}")
    if entry["answer_type"] not in ANSWER_TYPES:
        raise ValueError(f"invalid answer_type: {entry['answer_type']}")
    if entry["source_type"] not in SOURCE_TYPES:
        raise ValueError("model inference is not a valid answer source")
    if entry["validity_class"] not in VALIDITY_CLASSES:
        raise ValueError(f"invalid validity_class: {entry['validity_class']}")
    if entry["confirmation_status"] != "confirmed":
        raise ValueError("only user-confirmed answers may enter the active library")
    if not isinstance(entry["auto_fill_allowed"], bool) or not isinstance(entry["auto_submit_allowed"], bool):
        raise ValueError("auto-fill and auto-submit permissions must be explicit booleans")
    if entry["auto_submit_allowed"] and not entry["auto_fill_allowed"]:
        raise ValueError("automatic submission cannot be enabled when automatic filling is disabled")
    scope = entry.get("scope", {})
    unknown_scope = sorted(set(scope) - SCOPE_FIELDS)
    if unknown_scope:
        raise ValueError(f"unsupported scope fields: {', '.join(unknown_scope)}")
    if entry["validity_class"] == "per_application":
        application_id = scope.get("application_id")
        if not isinstance(application_id, str) or not application_id.strip():
            raise ValueError("per_application answers require scope.application_id")
    if entry["answer_type"] in {"legal_commitment", "voluntary_disclosure"} and entry["auto_submit_allowed"]:
        raise ValueError(f"{entry['answer_type']} cannot enable automatic submission in the MVP")
    confirmed = parse_time(entry["confirmed_at"])
    effective = parse_time(entry.get("effective_from"))
    expires = parse_time(entry.get("expires_at"))
    review_after = parse_time(entry.get("review_after"))
    if not confirmed:
        raise ValueError("confirmed_at is required")
    if expires and expires <= (effective or confirmed):
        raise ValueError("answer expiration must be after its effective or confirmation time")
    if review_after and review_after <= confirmed:
        raise ValueError("answer review_after must be after confirmation")
    if entry["validity_class"] == "periodic" and not (review_after or expires):
        raise ValueError("periodic answers require review_after or expires_at")

    connection.execute("""
        INSERT INTO answers (
            answer_id, canonical_id, canonical_meaning, answer_json, answer_type, source_type, source_ref,
            confirmation_status, confirmed_at, effective_from, expires_at, review_after, validity_class,
            scope_json, preconditions_json, exclusions_json, auto_fill_allowed, auto_submit_allowed,
            sensitivity, invalidation_triggers_json, dependent_fact_ids_json, supersedes_id, status, ambiguity_notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entry["answer_id"], entry["canonical_id"], entry["canonical_meaning"], _json(entry["answer"]),
        entry["answer_type"], entry["source_type"], entry.get("source_ref"), entry["confirmation_status"],
        entry["confirmed_at"], entry.get("effective_from"), entry.get("expires_at"), entry.get("review_after"),
        entry["validity_class"], _json(scope), _json(entry.get("preconditions", {})),
        _json(entry.get("exclusions", {})), int(entry["auto_fill_allowed"]), int(entry["auto_submit_allowed"]),
        entry.get("sensitivity", "normal"), _json(entry.get("invalidation_triggers", [])),
        _json(entry.get("dependent_fact_ids", [])), entry.get("supersedes_id"), entry.get("status", "active"),
        entry.get("ambiguity_notes"),
    ))
    if entry.get("supersedes_id"):
        connection.execute("UPDATE answers SET status='superseded' WHERE answer_id=?", (entry["supersedes_id"],))
    audit(connection, "answer_added", entry["answer_id"], {"canonical_id": entry["canonical_id"]})
    connection.commit()


def add_question_form(
    connection: sqlite3.Connection,
    canonical_id: str,
    question: str,
    match_level: str = "exact",
    verified_by_user: bool = True,
) -> None:
    if match_level not in {"exact", "semantic_equivalent"}:
        raise ValueError("match_level must be exact or semantic_equivalent")
    if match_level == "semantic_equivalent" and not verified_by_user:
        raise ValueError("semantic equivalents must be user-verified before reuse")
    normalized = normalize_question(question)
    connection.execute(
        "INSERT INTO question_forms VALUES (?, ?, ?, ?, ?)",
        (normalized, canonical_id, match_level, int(verified_by_user), now_utc().isoformat()),
    )
    question_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    audit(connection, "question_form_added", canonical_id, {"match_level": match_level, "question_hash": question_hash})
    connection.commit()


# A reviewed question-form manifest is a vocabulary, never an answer: a question and the
# meaning it was reviewed to have. That is why one can live in the repository while every
# answer stays in the private root, and why importing one is safe to automate while
# confirming an answer never is.
QUESTION_FORM_MANIFEST_FIELDS = {"schema_version", "reviewed_at", "reviewed_by", "note",
                                 "forms"}
QUESTION_FORM_FIELDS = {"canonical_id", "canonical_meaning", "question", "match_level",
                        "verified_by_user", "recorded_in"}
# The reviewed disposition approval, read as strictly as the manifest it vouches for. An
# oracle skimmed with `.get` is an oracle that can be replaced by an empty object.
PROVENANCE_FIELDS = {"reviewed_at", "reviewed_by", "note", "entries"}
PROVENANCE_ENTRY_FIELDS = {"kind", "recorded_label", "role", "source_fixture",
                           "source_fixture_sha256", "expected_disposition",
                           "expected_target"}
# Every entry carries a domain and a family, null together or set together. `review_reason`
# belongs to exactly the entries that pause. Nothing is loosely optional: a key set that
# tolerates absences tolerates an approval that dropped the field carrying the answer.
PROVENANCE_ENTRY_FIELDS_ALWAYS_MANUAL = PROVENANCE_ENTRY_FIELDS | {
    "expected_domain", "expected_family", "review_reason"}
PROVENANCE_ENTRY_FIELDS_ORDINARY = PROVENANCE_ENTRY_FIELDS | {
    "expected_domain", "expected_family"}
SHA256_SHAPE = re.compile(r"^[0-9a-f]{64}$")
# Mirrors `field_policy.DISPOSITIONS` and `semantic_replay.ROLE_CONTROLS`, declared here so
# the answer library does not import either module to read one set. Tests hold them equal.
REVIEWED_DISPOSITIONS = {"fact", "answer", "material", "always_manual", "unsupported"}
RECORDED_ROLES = {"textbox", "combobox", "radiogroup", "file"}
# Which reviewed dispositions may become a question form at all. A form says "this wording
# means this answerable thing", so a control reviewed as `always_manual` must never have one:
# the whole point of that disposition is that the question reaches the user. `material` is a
# file upload rather than a question with an answer, and `unsupported` is a refusal.
#
# This is a floor and not the permission. `answer` and `fact` between them cover every
# contact fact, every profile URL and every employment fact in the corpus, so allowing a
# disposition would have allowed `profile.github` and `employment.current_company` along with
# the three contact meanings anybody reviewed. The permission is the explicit set of targets
# the caller passes, which is why it has no default.
FORMABLE_DISPOSITIONS = {"answer", "fact"}

# The meanings Task 14 reviewed, pinned in code because a permission an input file can widen
# is not a permission. The intake plan was that file for one commit: copying it, adding
# `profile.website`, and adding the matching reviewed form to the manifest imported eleven
# forms past the corpus check, the provenance check and the disposition floor — every one of
# which passed, because every one of them was true. Widening this set is an edit to this
# file, under review, and never an argument.
TASK14_QUESTION_FORM_TARGETS = frozenset({
    "work_authorized_now", "citizenship_status", "permanent_residence_status",
    "current_country_of_residence", "prior_employment_at_this_company",
    "prior_employment_at_an_affiliate", "discovery_source",
    "contact.email", "contact.phone", "profile.linkedin",
})
# Of those, the three whose values exist only inside one composite contact fact. They are the
# only `fact` meanings this task may speak for; everything else the corpus reviews as a fact
# stays out.
TASK14_CONTACT_TARGETS = frozenset({"contact.email", "contact.phone", "profile.linkedin"})
# The four legal-status meanings, and the two prior-employment ones, that must not travel
# between applications.
TASK14_PER_APPLICATION_TARGETS = frozenset({
    "work_authorized_now", "citizenship_status", "permanent_residence_status",
    "current_country_of_residence", "prior_employment_at_this_company",
    "prior_employment_at_an_affiliate", "discovery_source",
})
DISCOVERY_ANSWER_TYPES = {"application_specific", "conditional_preference"}
INTAKE_PLAN_FIELDS = {"schema_version", "reviewed_at", "reviewed_by", "note",
                      "application_id", "entries"}
INTAKE_ENTRY_FIELDS = {"canonical_id", "canonical_meaning", "answer", "answer_type",
                       "source_type", "validity_class", "scope", "auto_fill_allowed",
                       "auto_submit_allowed", "engine_enforced_recheck", "confirmation"}


def intake_plan_targets(plan: Any) -> frozenset[str]:
    """Read a reviewed intake plan, and return the targets only if it is the reviewed one.

    The plan is checked, never trusted: it says which meanings are to be confirmed and under
    what terms, and this reads it to confirm it still describes the reviewed arrangement. It
    is not where the permission comes from. `TASK14_QUESTION_FORM_TARGETS` is, and a plan
    that names anything else is refused rather than obeyed.
    """
    if not isinstance(plan, dict) or set(plan) != INTAKE_PLAN_FIELDS:
        raise ValueError("intake plan has an unexpected shape")
    for field in ("schema_version", "reviewed_at", "reviewed_by", "note", "application_id"):
        if not isinstance(plan[field], str) or not plan[field].strip():
            raise ValueError(f"intake plan metadata must be a non-empty string: {field}")
    entries = plan["entries"]
    if not isinstance(entries, list):
        raise ValueError("intake plan entries have an unexpected shape")
    application = plan["application_id"]
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != INTAKE_ENTRY_FIELDS:
            raise ValueError("intake plan entry has an unexpected shape")
        target = entry["canonical_id"]
        if not isinstance(target, str) or target in seen:
            raise ValueError("intake plan repeats or mis-types a meaning")
        seen.add(target)
        if entry["answer"] is not None:
            # The one field this file may never hold. A plan carrying an answer is a private
            # value in the repository.
            raise ValueError("an intake plan may not carry an answer")
        if entry["source_type"] != "user_confirmed":
            raise ValueError("an intake entry must be the user's own confirmation")
        if entry["auto_fill_allowed"] is not True or entry["auto_submit_allowed"] is not False:
            raise ValueError("an intake entry may fill automatically and never submit")
        scope = entry["scope"]
        if not isinstance(scope, dict):
            raise ValueError("intake plan scope has an unexpected shape")
        if target in TASK14_PER_APPLICATION_TARGETS:
            if entry["validity_class"] != "per_application":
                raise ValueError("this meaning must be confirmed per application")
            if scope.get("country") != "US" or scope.get("application_id") != application:
                raise ValueError("this meaning must be bound to the reviewed application")
        if target == "discovery_source" and entry["answer_type"] not in DISCOVERY_ANSWER_TYPES:
            raise ValueError("how an opening was heard about is stated, not derived")
        if target in TASK14_CONTACT_TARGETS and entry["validity_class"] != "stable":
            raise ValueError("a contact meaning does not change with the application")
    if seen != set(TASK14_QUESTION_FORM_TARGETS):
        # Both directions: a plan that added a meaning, and a plan that dropped one so the
        # remaining nine would import quietly.
        raise ValueError("intake plan does not name the reviewed meanings")
    return TASK14_QUESTION_FORM_TARGETS


def _manifest_forms(manifest: Any) -> list[dict[str, Any]]:
    """Read a manifest strictly, or refuse it whole."""
    if not isinstance(manifest, dict) or set(manifest) != QUESTION_FORM_MANIFEST_FIELDS:
        raise ValueError("question-form manifest has an unexpected shape")
    forms = manifest["forms"]
    if not isinstance(forms, list) or not forms:
        raise ValueError("question-form manifest carries no forms")
    seen: dict[str, str] = {}
    for form in forms:
        if not isinstance(form, dict) or set(form) != QUESTION_FORM_FIELDS:
            raise ValueError("question form has an unexpected shape")
        for field in ("canonical_id", "canonical_meaning", "question"):
            if not isinstance(form[field], str) or not form[field].strip():
                raise ValueError(f"question form field must be a non-empty string: {field}")
        if form["match_level"] != "exact":
            # A semantic equivalent is a claim about meaning that a person makes one at a
            # time. Importing a file full of them would make that claim in bulk.
            raise ValueError("only exact question forms may be imported from a manifest")
        if form["verified_by_user"] is not True:
            raise ValueError("an imported question form must be user-verified")
        recorded = form["recorded_in"]
        if (not isinstance(recorded, list) or not recorded
                or not all(isinstance(name, str) and name.strip() for name in recorded)):
            raise ValueError("a question form must name where its wording was recorded")
        normalized = normalize_question(form["question"])
        if not normalized:
            raise ValueError("a question form normalizes to nothing")
        previous = seen.get(normalized)
        if previous is not None and previous != form["canonical_id"]:
            # Exactly what `match_answer` reports as `question_mapping_conflict`, caught
            # before it is written rather than after it starts pausing pages.
            raise ValueError(f"one question mapped to two meanings: {normalized}")
        if previous == form["canonical_id"]:
            raise ValueError(f"question form repeated in the manifest: {normalized}")
        seen[normalized] = form["canonical_id"]
    return forms


def _corpus_controls(corpus_root: Path) -> dict[tuple[str, str], tuple[str, str, str]]:
    """Every control the pinned corpus actually records, read from the corpus.

    A digest proves a file exists and has not changed. It proves nothing about whether the
    approval describes what is inside it: with every hash correct, an approval could rename a
    control's wording and its role, a manifest could use the renamed wording, and the two
    files would agree with each other about a form no employer ships. The only way to close
    that is to read the controls.

    Returns `{(fixture, kind): (label, role, digest)}`.
    """
    if not isinstance(corpus_root, Path) or not corpus_root.is_dir():
        raise ValueError("the recorded corpus this approval names is not readable")
    controls: dict[tuple[str, str], tuple[str, str, str]] = {}
    for directory in sorted(path for path in corpus_root.iterdir() if path.is_dir()):
        source = directory / "fixture.json"
        if not source.is_file():
            continue
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            fixture = json.loads(raw)
            steps = fixture["steps"]
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError("a recorded fixture cannot be read") from error
        for step in steps:
            for control in step.get("controls") or []:
                if not isinstance(control, dict) or not {"kind", "label", "role"} <= set(control):
                    # A fixture missing the fields the approval is checked against cannot be
                    # checked against, and silently skipping it would let a corrupted corpus
                    # approve whatever the approval says.
                    raise ValueError("a recorded fixture is missing control fields")
                key = (directory.name, control["kind"])
                if key in controls:
                    raise ValueError("a recorded fixture repeats a control")
                controls[key] = (control["label"], control["role"], digest)
    if not controls:
        raise ValueError("the recorded corpus holds no controls")
    return controls


def _provenance_index(provenance: Any, corpus_root: Path,
                      allowed_targets: set[str]) -> dict[str, set[tuple[str, str]]]:
    """Which `(fixture, meaning)` pairs actually recorded each wording.

    The index keeps the meaning, not just the fixture. Checking a question against the set
    of fixtures that recorded it proves the wording is real and proves nothing about what it
    means, so `Email address` could be imported as `work_authorized_now` and every check
    would pass — the label exists, the fixtures match, and the mapping is nonsense.
    """
    if not isinstance(provenance, dict) or set(provenance) != PROVENANCE_FIELDS:
        raise ValueError("provenance has an unexpected shape")
    for field in ("reviewed_at", "reviewed_by", "note"):
        if not isinstance(provenance[field], str) or not provenance[field].strip():
            raise ValueError(f"provenance metadata must be a non-empty string: {field}")
    entries = provenance["entries"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("provenance carries no reviewed entries")
    if not isinstance(allowed_targets, (set, frozenset)) or not allowed_targets:
        # Checked before it is coerced. `set("profile.website")` is a set of thirteen
        # letters, which is a strange and quiet way to permit nothing. The pinned-set check
        # lives at the public entry point, so this stays usable for asking what the index
        # would say about a permission that does not exist.
        raise ValueError("permitted targets must be a set of canonical meanings")
    corpus = _corpus_controls(corpus_root)

    index: dict[str, set[tuple[str, str]]] = {}
    reviewed: set[tuple[str, str]] = set()
    meanings: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("provenance entry has an unexpected shape")
        manual = entry.get("expected_disposition") == "always_manual"
        expected_keys = (PROVENANCE_ENTRY_FIELDS_ALWAYS_MANUAL if manual
                         else PROVENANCE_ENTRY_FIELDS_ORDINARY)
        if set(entry) != expected_keys:
            # `review_reason` belongs to exactly the entries that pause, and every entry
            # carries a domain and a family. An approval that dropped one is an approval
            # missing the field that carried the answer.
            raise ValueError("provenance entry has an unexpected shape")
        for field in ("kind", "recorded_label", "role", "source_fixture", "expected_target",
                      "expected_disposition"):
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise ValueError(f"provenance entry field must be a non-empty string: {field}")
        if manual and (not isinstance(entry["review_reason"], str)
                       or not entry["review_reason"].strip()):
            raise ValueError("a pausing entry must say why")
        domain, family = entry["expected_domain"], entry["expected_family"]
        if (domain is None) != (family is None):
            # A family without a domain names a rule that did not fire.
            raise ValueError("provenance domain and family must stand or fall together")
        for value in (domain, family):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("provenance domain and family must be names or null")
        if not SHA256_SHAPE.fullmatch(entry["source_fixture_sha256"] or ""):
            raise ValueError("provenance entry does not name a sha256 digest")
        if entry["expected_disposition"] not in REVIEWED_DISPOSITIONS:
            raise ValueError("provenance entry names an unknown disposition")
        if entry["role"] not in RECORDED_ROLES:
            raise ValueError("provenance entry names an unrecorded control role")

        control = (entry["source_fixture"], entry["kind"])
        if control in reviewed:
            raise ValueError(f"provenance reviews one control twice: {control[1]}")
        reviewed.add(control)
        recorded = corpus.get(control)
        if recorded is None:
            raise ValueError("provenance reviews a control the corpus does not record")
        # Read from the corpus, not taken from the approval. A digest says the file has not
        # changed; this says the approval describes what is in it.
        if (entry["recorded_label"], entry["role"], entry["source_fixture_sha256"]) != recorded:
            raise ValueError("provenance does not match the recorded control")

        label, target = entry["recorded_label"], entry["expected_target"]
        if meanings.setdefault(label, target) != target:
            # The same thing `match_answer` reports as `question_mapping_conflict`, in the
            # file that is supposed to be settling it.
            raise ValueError("provenance reviews one wording as two meanings")
        if (entry["expected_disposition"] in FORMABLE_DISPOSITIONS
                and target in allowed_targets):
            index.setdefault(label, set()).add((entry["source_fixture"], target))

    missing = sorted(set(corpus) - reviewed)
    if missing:
        # Both directions. Walking the approval can never notice a control nobody reviewed,
        # which is the same hole the disposition approval itself once had.
        raise ValueError("the corpus records a control the approval never reviewed")
    return index


def import_question_forms(connection: sqlite3.Connection, manifest: Any, provenance: Any,
                          corpus_root: Path, allowed_targets: set[str]) -> dict[str, Any]:
    """Apply a reviewed manifest to this library, wholly or not at all.

    Deliberately not called by `initialize`. A file appearing in a checkout is not a person
    deciding to trust it, and a library that silently gained a vocabulary on first connect
    would be one nobody chose. This runs when someone runs it.

    Idempotent: a row already present exactly as the manifest describes is counted and left
    alone, so re-importing after adding one form does not fail on the other nine. A row
    present *differently* — a different meaning for the same wording, or a match level that
    was widened — is a conflict and stops the whole import, because a half-applied vocabulary
    is worse than none.

    `allowed_targets` is the explicit set of canonical meanings this import may create forms
    for, and it has no default. A disposition is a floor, not a permission: `answer` and
    `fact` between them cover every contact fact, every profile URL and every employment fact
    the corpus records, so gating on disposition alone let `profile.github`,
    `profile.website`, `profile.portfolio`, `contact.full_name` and
    `employment.current_company` in beside the three contact meanings that were actually
    reviewed for this.

    `provenance` is the reviewed disposition approval and is required. It was briefly
    optional, which made the whole check decorative: any file in a checkout that called
    itself `exact` and `verified_by_user` could write arbitrary mappings in bulk, and a
    manifest saying so is not a person confirming it. Each question must appear in the
    approval verbatim, under the fixtures the manifest names, **and reviewed as the meaning
    the manifest gives it** — the last of those was missing too, so `Email address` mapped to
    `work_authorized_now` passed every check: the label existed, the fixtures matched, and
    the mapping was nonsense.
    """
    forms = _manifest_forms(manifest)
    if not isinstance(allowed_targets, (set, frozenset)):
        # Checked before it is coerced, and before anything is read.
        raise ValueError("permitted targets must be a set of canonical meanings")
    if set(allowed_targets) != set(TASK14_QUESTION_FORM_TARGETS):
        # The permission is pinned in this file. An input that names a different set is
        # refused rather than obeyed, whichever direction it moved.
        raise ValueError("permitted targets are not the reviewed set")
    recorded = _provenance_index(provenance, corpus_root, set(allowed_targets))
    unpermitted = sorted({form["canonical_id"] for form in forms} - set(allowed_targets))
    if unpermitted:
        # The disposition floor is not the permission. `answer` and `fact` between them cover
        # every contact fact, every profile URL and every employment fact in the corpus, so
        # allowing a disposition would have allowed `profile.github` and
        # `employment.current_company` beside the three contact meanings anybody reviewed.
        raise ValueError("question form names a meaning nobody permitted for import")
    for form in forms:
        question = form["question"]
        if question not in recorded:
            # Either nobody recorded this wording, or every control that did was reviewed as
            # something a question form may not speak for — `always_manual` most of all,
            # whose entire purpose is that the question reaches the user.
            raise ValueError("question form is not recorded employer wording")
        claimed = {(fixture, form["canonical_id"]) for fixture in form["recorded_in"]}
        if claimed != recorded[question]:
            # One comparison covers both halves: a fixture that did not record this wording,
            # and a meaning nobody reviewed this wording as.
            raise ValueError("question form does not match its reviewed provenance")

    planned, already = [], []
    for form in forms:
        normalized = normalize_question(form["question"])
        rows = connection.execute(
            "SELECT canonical_id, match_level, verified_by_user FROM question_forms "
            "WHERE normalized_question=?", (normalized,)).fetchall()
        matching = [row for row in rows if row["canonical_id"] == form["canonical_id"]]
        if any(row["canonical_id"] != form["canonical_id"] for row in rows):
            raise ValueError(f"this library already maps that question elsewhere: {normalized}")
        if matching:
            row = matching[0]
            if row["match_level"] != form["match_level"] or not row["verified_by_user"]:
                raise ValueError(f"this library holds a different form for: {normalized}")
            already.append(form["canonical_id"])
            continue
        planned.append((normalized, form))

    timestamp = now_utc().isoformat()
    try:
        for normalized, form in planned:
            connection.execute(
                "INSERT INTO question_forms VALUES (?, ?, ?, ?, ?)",
                (normalized, form["canonical_id"], form["match_level"],
                 int(form["verified_by_user"]), timestamp))
            audit(connection, "question_form_imported", form["canonical_id"], {
                "match_level": form["match_level"],
                # The question is hashed, exactly as `add_question_form` hashes it: an audit
                # log is not a place to accumulate a second copy of the vocabulary.
                "question_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            })
    except Exception:
        connection.rollback()
        raise
    connection.commit()
    return {
        "imported": len(planned),
        "already_present": len(already),
        "canonical_ids": sorted({form["canonical_id"] for form in forms}),
    }


def authorization_current(
    connection: sqlite3.Connection,
    authorization_id: str | None,
    context: dict[str, Any],
    at: datetime,
) -> tuple[bool, str | None]:
    if not authorization_id:
        return False, "standing_authorization_missing"
    row = connection.execute("SELECT * FROM authorizations WHERE authorization_id=?", (authorization_id,)).fetchone()
    if not row:
        return False, "standing_authorization_unknown"
    if row["status"] != "active" or row["revoked_at"]:
        return False, "standing_authorization_revoked"
    if at >= parse_time(row["expires_at"]):
        return False, "standing_authorization_expired"
    if not context_matches(json.loads(row["scope_json"]), context):
        return False, "standing_authorization_scope_mismatch"
    return True, None


def match_answer(
    connection: sqlite3.Connection,
    question: str,
    context: dict[str, Any],
    authorization_id: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    at = at or now_utc()
    forms = connection.execute(
        "SELECT * FROM question_forms WHERE normalized_question=?",
        (normalize_question(question),),
    ).fetchall()
    canonical_ids = {row["canonical_id"] for row in forms if row["verified_by_user"]}
    if not forms:
        return {"decision": "ask", "reason": "new_question", "auto_fill_ready": False}
    if len(canonical_ids) != 1:
        return {"decision": "conflict", "reason": "question_mapping_conflict", "auto_fill_ready": False}
    canonical_id = next(iter(canonical_ids))
    rows = connection.execute(
        "SELECT * FROM answers WHERE canonical_id=? AND confirmation_status='confirmed'",
        (canonical_id,),
    ).fetchall()
    stale_reasons: list[str] = []
    candidates: list[tuple[int, sqlite3.Row]] = []
    for row in rows:
        scope = json.loads(row["scope_json"])
        validity = answer_issue(row, context, at)
        if validity:
            stale_reasons.append(validity)
            continue
        candidates.append((len(scope), row))
    if not candidates:
        reason = sorted(set(stale_reasons))[0] if stale_reasons else "no_applicable_answer"
        return {"decision": "ask", "reason": reason, "canonical_id": canonical_id, "auto_fill_ready": False}
    best_specificity = max(score for score, _ in candidates)
    best = [row for score, row in candidates if score == best_specificity]
    values = {row["answer_json"] for row in best}
    if len(values) > 1:
        return {"decision": "conflict", "reason": "conflicting_active_answers", "canonical_id": canonical_id, "auto_fill_ready": False}
    selected = max(best, key=lambda row: parse_time(row["confirmed_at"]) or datetime.min.replace(tzinfo=timezone.utc))
    if canonical_id in IMMIGRATION_CANONICAL_IDS:
        application_id = context.get("application_id")
        selected_scope = json.loads(selected["scope_json"])
        if not application_id or selected_scope.get("application_id") != application_id:
            return {"decision": "ask", "reason": "immigration_recheck_required",
                    "canonical_id": canonical_id, "answer_id": selected["answer_id"],
                    "auto_fill_ready": False}
    if selected["answer_type"] == "legal_commitment":
        return {"decision": "ask", "reason": "legal_commitment_requires_review", "canonical_id": canonical_id, "answer_id": selected["answer_id"], "auto_fill_ready": False}
    if not selected["auto_fill_allowed"]:
        return {"decision": "ask", "reason": "automatic_fill_not_allowed", "canonical_id": canonical_id, "answer_id": selected["answer_id"], "auto_fill_ready": False}
    authorized, authorization_reason = authorization_current(connection, authorization_id, context, at)
    result = {
        "decision": "use",
        "reason": "verified_answer_match",
        "canonical_id": canonical_id,
        "answer_id": selected["answer_id"],
        "answer": json.loads(selected["answer_json"]),
        "match_level": forms[0]["match_level"],
        "channel_b_fresh": True,
        "channel_a_current": authorized,
        "auto_fill_ready": authorized,
        "auto_submit_ready": bool(authorized and selected["auto_submit_allowed"]),
        "per_application_recheck_required": canonical_id in IMMIGRATION_CANONICAL_IDS,
    }
    if not authorized:
        result["authorization_reason"] = authorization_reason
    audit(connection, "answer_matched", selected["answer_id"], {
        "canonical_id": canonical_id, "decision": result["decision"], "auto_fill_ready": result["auto_fill_ready"],
    })
    connection.commit()
    return result


def add_authorization(connection: sqlite3.Connection, entry: dict[str, Any]) -> None:
    required = {"authorization_id", "confirmed_at", "expires_at", "scope"}
    missing = sorted(required - set(entry))
    if missing:
        raise ValueError(f"authorization missing required fields: {', '.join(missing)}")
    confirmed = parse_time(entry["confirmed_at"])
    expires = parse_time(entry["expires_at"])
    if not confirmed or not expires or expires <= confirmed:
        raise ValueError("authorization expiration must be after confirmation")
    if expires - confirmed > timedelta(days=14):
        raise ValueError("standing authorization may not exceed fourteen days")
    unknown_scope = sorted(set(entry["scope"]) - SCOPE_FIELDS)
    if unknown_scope:
        raise ValueError(f"unsupported authorization scope fields: {', '.join(unknown_scope)}")
    if not entry["scope"]:
        raise ValueError("standing authorization requires a non-empty scope")
    connection.execute(
        "INSERT INTO authorizations VALUES (?, ?, ?, ?, ?, ?)",
        (entry["authorization_id"], entry["confirmed_at"], entry["expires_at"], _json(entry["scope"]), None, "active"),
    )
    audit(connection, "authorization_added", entry["authorization_id"], {"expires_at": entry["expires_at"]})
    connection.commit()


def revoke_authorization(connection: sqlite3.Connection, authorization_id: str, at: datetime | None = None) -> None:
    timestamp = (at or now_utc()).isoformat()
    cursor = connection.execute(
        "UPDATE authorizations SET revoked_at=?, status='revoked' WHERE authorization_id=? AND status='active'",
        (timestamp, authorization_id),
    )
    if cursor.rowcount != 1:
        raise ValueError("active authorization not found")
    audit(connection, "authorization_revoked", authorization_id, {})
    connection.commit()


def invalidate_by_trigger(connection: sqlite3.Connection, trigger: str) -> list[str]:
    rows = connection.execute("SELECT answer_id, invalidation_triggers_json FROM answers WHERE status='active'").fetchall()
    affected = [row["answer_id"] for row in rows if trigger in json.loads(row["invalidation_triggers_json"])]
    for answer_id in affected:
        connection.execute("UPDATE answers SET status='stale' WHERE answer_id=?", (answer_id,))
        audit(connection, "answer_invalidated", answer_id, {"trigger": trigger})
    connection.commit()
    return affected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    add = subparsers.add_parser("add-answer")
    add.add_argument("--entry", required=True, type=Path)
    form = subparsers.add_parser("add-form")
    form.add_argument("--canonical-id", required=True)
    form.add_argument("--question", required=True)
    form.add_argument("--match-level", choices=["exact", "semantic_equivalent"], default="exact")
    form.add_argument("--verified-by-user", action="store_true")
    authorization = subparsers.add_parser("add-authorization")
    authorization.add_argument("--entry", required=True, type=Path)
    revoke = subparsers.add_parser("revoke-authorization")
    revoke.add_argument("--authorization-id", required=True)
    forms_import = subparsers.add_parser("import-question-forms")
    forms_import.add_argument("--manifest", required=True, type=Path)
    forms_import.add_argument("--provenance", required=True, type=Path,
                              help="the reviewed disposition approval every question must "
                                   "be traceable to, wording and meaning both; the recorded "
                                   "corpus is read from `upstream/` beside it")
    forms_import.add_argument("--intake-plan", required=True, type=Path,
                              help="the value-free plan, checked against the reviewed set of "
                                   "meanings rather than trusted to define it")
    invalidate = subparsers.add_parser("invalidate")
    invalidate.add_argument("--trigger", required=True)
    match = subparsers.add_parser("match")
    match.add_argument("--question", required=True)
    match.add_argument("--context", required=True, type=Path)
    match.add_argument("--authorization-id")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    if args.command == "init":
        result = {"status": "initialized", "db": str(args.db)}
    elif args.command == "add-answer":
        entry = json.loads(args.entry.read_text(encoding="utf-8"))
        add_answer(connection, entry)
        result = {"status": "added", "answer_id": entry["answer_id"]}
    elif args.command == "add-form":
        verified = args.verified_by_user or args.match_level == "exact"
        add_question_form(connection, args.canonical_id, args.question, args.match_level, verified)
        result = {"status": "added", "canonical_id": args.canonical_id}
    elif args.command == "add-authorization":
        entry = json.loads(args.entry.read_text(encoding="utf-8"))
        add_authorization(connection, entry)
        result = {"status": "added", "authorization_id": entry["authorization_id"]}
    elif args.command == "revoke-authorization":
        revoke_authorization(connection, args.authorization_id)
        result = {"status": "revoked", "authorization_id": args.authorization_id}
    elif args.command == "import-question-forms":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        provenance = json.loads(args.provenance.read_text(encoding="utf-8"))
        plan = json.loads(args.intake_plan.read_text(encoding="utf-8"))
        # The plan is validated against the pinned set, not read as the permission. A copy of
        # it with one meaning added is refused rather than obeyed.
        permitted = intake_plan_targets(plan)
        # The corpus sits beside the approval, never as its own argument: a caller who could
        # point it somewhere else could point it at recordings they wrote themselves.
        result = import_question_forms(connection, manifest, provenance,
                                       args.provenance.parent / "upstream", permitted)
    elif args.command == "invalidate":
        result = {"status": "invalidated", "answer_ids": invalidate_by_trigger(connection, args.trigger)}
    elif args.command == "match":
        context = json.loads(args.context.read_text(encoding="utf-8"))
        result = match_answer(connection, args.question, context, args.authorization_id)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
