#!/usr/bin/env python3
"""Which protected authority, if any, may answer a form field.

`docs/lever-first-form-readiness.md` audited the 27 controls of the reviewed Lever semantic
fixture and found that only 2 resolve to career evidence. The rest are contact facts, legal
status, a negotiating stance, per-employer disclosures, an acquisition-source preference, and
four voluntary protected-characteristic questions. Answering them all through the same
"fact or answer" path is what this module refuses.

Every classification here is deterministic and reads only the field identifier and question
text. That text comes from the page and is untrusted, so it is used in exactly one direction:
**a pattern hit may add caution, never remove it.** A page that declares a race question as
`source_kind: "answer"` still lands in `always_manual`; a page that declares an ordinary
question as EEO merely costs a pause. `archive_core.SENSITIVE_FIELD_PATTERN` already uses this
asymmetry and this module follows it.

No model is involved. A question that does not match is not guessed at — it falls through to
the existing answer path, which pauses on `new_question`.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
import sys  # noqa: E402
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from urllib.parse import urlparse  # noqa: E402

from _common import context_matches, parse_time  # noqa: E402


DISPOSITIONS = {"fact", "answer", "material", "always_manual", "unsupported"}

# Domains whose fields are never answered from a Jobloom source, whatever the page declares.
ALWAYS_MANUAL_DOMAINS = {"voluntary_eeo", "compensation", "employer_conflict", "sponsorship"}
MANUAL_REASONS = {
    "voluntary_eeo": "voluntary_disclosure_manual",
    "compensation": "employer_defined_compensation_manual",
    "employer_conflict": "employer_entity_not_approved",
    "sponsorship": "sponsorship_meaning_ambiguous",
}

LOCALE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
LOOPBACK_HOST = re.compile(r"^127\.0\.0\.1$")

# Non-disclosure options are chosen from this table, never typed by a user. A free-text
# allowlist is how a real demographic value would reach the database: a user who pasted
# "Asian" into it would have stored exactly the value this whole boundary exists to keep out.
# Registration therefore references a token and a version; the strings live only in code.
NONDISCLOSURE_VOCABULARY_VERSION = "2026-09-01"
NONDISCLOSURE_VOCABULARY: dict[str, dict[str, tuple[str, ...]]] = {
    "en-US": {
        "prefer_not_to_answer": (
            "Prefer not to answer",
            "I prefer not to answer",
            "I do not wish to answer",
            "I don't wish to answer",
            "I don't want to answer",
            "I do not want to answer",
        ),
        "decline_to_answer": (
            "Decline to answer",
            "I decline to answer",
        ),
        "decline_to_self_identify": (
            "Decline to self-identify",
            "I decline to self-identify",
            "I do not wish to self-identify",
            "I don't wish to self-identify",
            "I choose not to self-identify",
        ),
    },
}

# What the archive may say about a protected-characteristic control. None of these is an
# answer; they exist so the deliberate blind spot is stated rather than discovered.
HANDLING_MARKERS = {"policy_declined", "user_handled"}

# Retired, not merely unused. Rows written by an earlier version are read as `unknown`,
# because this version cannot say what evidence they rested on.
RETIRED_MARKERS = {"not_present"}


def require_locale(value: Any, label: str = "locale") -> str:
    """One locale contract, shared by policy registration and page observation."""
    if not isinstance(value, str) or not 0 < len(value) <= 32 or not LOCALE_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a bounded IETF language tag such as en-US")
    return value


def vocabulary_options(locale: str, tokens: list[str], version: str) -> list[str]:
    """Expand reviewed tokens into the exact strings a page may offer."""
    if version != NONDISCLOSURE_VOCABULARY_VERSION:
        raise ValueError(f"unknown non-disclosure vocabulary version: {version}")
    families = NONDISCLOSURE_VOCABULARY.get(locale)
    if not families:
        raise ValueError(f"no reviewed non-disclosure vocabulary for locale: {locale}")
    unknown = sorted(set(tokens) - set(families))
    if unknown:
        raise ValueError(f"unreviewed non-disclosure option tokens: {', '.join(unknown)}")
    return sorted({option for token in tokens for option in families[token]})


def disposition(field_id: str, question: str, control: str,
                source_kind: str | None) -> tuple[str, str | None, str | None]:
    """Which authority, if any, may answer this field: one of `DISPOSITIONS`.

    Returned as `(disposition, domain, family)` so the caller can both route the field and
    name why. Domain rules win over whatever the page declared, in that order.
    """
    domain = classify(field_id, question)
    if domain:
        kind, family = domain
        if kind in ALWAYS_MANUAL_DOMAINS:
            return "always_manual", kind, family
        return "answer", kind, family
    if control == "file":
        return "material", None, None
    if source_kind == "fact":
        return "fact", None, None
    if source_kind in {None, "answer"}:
        return "answer", None, None
    return "unsupported", None, None

# Voluntary protected-characteristic disclosures. Values never enter Jobloom.
VOLUNTARY_EEO_FAMILIES = {
    "eeo_race": re.compile(r"race|ethnic", re.IGNORECASE),
    "eeo_veteran": re.compile(r"veteran", re.IGNORECASE),
    "eeo_disability": re.compile(r"disabilit|disabled", re.IGNORECASE),
    "eeo_gender": re.compile(r"\bgender\b|\bsex\b|self.?identif.*\bgender\b", re.IGNORECASE),
}

# Employer-defined compensation brackets. The bands exist only in page text.
COMPENSATION_BRACKET = re.compile(
    r"compensation|salary|\bpay\b|\bcomp\b|remuneration", re.IGNORECASE)

# Kept as two families on purpose: a relative at the company and a commercial relationship
# with it are different disclosures and must never share an answer.
EMPLOYER_CONFLICT_FAMILIES = {
    "conflict_related_person": re.compile(
        r"relative|related person|family member|spouse|immediate family", re.IGNORECASE),
    "conflict_commercial_relationship": re.compile(
        r"customer|partner|reseller|supplier|vendor|distributor", re.IGNORECASE),
}

SPONSORSHIP = re.compile(r"sponsor", re.IGNORECASE)
SPONSORSHIP_NOW = re.compile(r"\bnow\b|currently|presently|at this time|to begin", re.IGNORECASE)
SPONSORSHIP_FUTURE = re.compile(
    r"\bfuture\b|\bever\b|at any (?:point|time)|later|down the (?:road|line)", re.IGNORECASE)

DISCOVERY_SOURCE = re.compile(
    r"how did you (?:hear|find|learn)|where did you (?:hear|find)|referral source|"
    r"how.{0,20}(?:hear|find).{0,20}about", re.IGNORECASE)

# The four exact immigration meanings. A broad question may not stand in for one of them.
IMMIGRATION_MEANINGS = {
    "work_authorized_now", "sponsorship_now", "sponsorship_future", "employer_action_required",
}

# Discovery source is a user preference about acquisition, not a fact and not a career claim.
DISCOVERY_ANSWER_TYPES = {"application_specific", "conditional_preference"}

NONDISCLOSURE_FAMILIES = set(VOLUNTARY_EEO_FAMILIES)


def classify(field_id: str, question: str) -> tuple[str, str] | None:
    """Return `(domain, family)` for a field that needs a domain rule, else `None`.

    Order is by consequence, not by likelihood: a question that reads as both a
    protected characteristic and something else is treated as the protected one.
    """
    text = f"{field_id} {question}"
    for family, pattern in VOLUNTARY_EEO_FAMILIES.items():
        if pattern.search(text):
            return "voluntary_eeo", family
    for family, pattern in EMPLOYER_CONFLICT_FAMILIES.items():
        if pattern.search(text):
            return "employer_conflict", family
    if SPONSORSHIP.search(text):
        return "sponsorship", "sponsorship"
    if COMPENSATION_BRACKET.search(text):
        return "compensation", "compensation"
    if DISCOVERY_SOURCE.search(text):
        return "discovery_source", "discovery_source"
    return None


def sponsorship_is_ambiguous(question: str) -> bool:
    """Always true in v1. Page wording may not settle which canonical meaning is being asked.

    This function used to resolve the question when its wording named exactly one point in
    time — "do you require sponsorship now?" reading as `sponsorship_now`. That let page text
    *lower* caution, which is the one direction untrusted input may never move: an employer
    who writes "now" while meaning "now or in the future" would have had a narrower answer
    submitted than the question asked for, and the four meanings are separate precisely
    because that substitution is the harm.

    Resolving one of the four requires a user-reviewed canonical answer scoped to this
    application, which `answer_library` already enforces through `IMMIGRATION_CANONICAL_IDS`.
    Until a mapping is reviewed for this application, the field is the user's.
    """
    return True


def initialize(connection: sqlite3.Connection) -> None:
    """The non-disclosure policy store.

    Deliberately not an AnswerEntry and deliberately not in `answers`: it holds no answer to
    any question about the person. It records that the user chose not to disclose, and which
    exact option strings they reviewed as meaning that. Its freshness rules are built from the
    same primitives as answers (`parse_time`, `context_matches`) but map to their own reason
    codes, because a policy has no `confirmation_status`, no `answer_type`, and no
    `legal_commitment` case.
    """
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS nondisclosure_policies (
            policy_id TEXT PRIMARY KEY,
            question_family TEXT NOT NULL,
            locale TEXT NOT NULL,
            option_tokens_json TEXT NOT NULL,
            vocabulary_version TEXT NOT NULL,
            confirmed_by TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            effective_from TEXT,
            expires_at TEXT,
            revoked_at TEXT,
            revocation_reason TEXT,
            scope_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS nondisclosure_family
            ON nondisclosure_policies (question_family);
        CREATE TABLE IF NOT EXISTS replay_surfaces (
            surface_id TEXT PRIMARY KEY,
            origin TEXT NOT NULL,
            nonce TEXT NOT NULL,
            fixture_sha256 TEXT NOT NULL,
            page_digests_json TEXT NOT NULL DEFAULT '{}',
            renderer_version TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT
        );
        CREATE INDEX IF NOT EXISTS replay_surface_origin ON replay_surfaces (origin);
        CREATE TABLE IF NOT EXISTS nondisclosure_handling (
            application_id TEXT NOT NULL,
            field_id TEXT NOT NULL,
            marker TEXT NOT NULL,
            evidence_kind TEXT NOT NULL,
            evidence_ref TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (application_id, field_id)
        );
    """)
    connection.commit()


def register_policy(
    connection: sqlite3.Connection,
    policy_id: str,
    question_family: str,
    locale: str,
    option_tokens: list[str],
    confirmed_by: str,
    confirmed_at: datetime,
    scope: dict[str, Any] | None = None,
    effective_from: datetime | None = None,
    expires_at: datetime | None = None,
    vocabulary_version: str = NONDISCLOSURE_VOCABULARY_VERSION,
) -> dict[str, Any]:
    if question_family not in NONDISCLOSURE_FAMILIES:
        raise ValueError(f"unknown non-disclosure family: {question_family}")
    if confirmed_by != "user":
        raise ValueError("a non-disclosure policy requires an explicit user confirmation")
    require_locale(locale)
    if not option_tokens:
        raise ValueError("a non-disclosure policy requires reviewed option tokens")
    # Validates the tokens against the reviewed vocabulary and throws away the expansion:
    # the row stores the token and the version, so no page-facing string — and therefore no
    # demographic value — can be written here at all.
    vocabulary_options(locale, option_tokens, vocabulary_version)
    # A policy that can never apply should not reach the database, even though resolution
    # would refuse it later.
    start = effective_from or confirmed_at
    if expires_at and expires_at <= start:
        raise ValueError("a non-disclosure policy must expire after it takes effect")
    if effective_from and expires_at and expires_at <= confirmed_at:
        raise ValueError("a non-disclosure policy must expire after it was confirmed")
    connection.execute(
        "INSERT INTO nondisclosure_policies (policy_id, question_family, locale, "
        "option_tokens_json, vocabulary_version, confirmed_by, confirmed_at, effective_from, "
        "expires_at, revoked_at, revocation_reason, scope_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
        (policy_id, question_family, locale,
         json.dumps(sorted(set(option_tokens))), vocabulary_version,
         confirmed_by, confirmed_at.isoformat(),
         effective_from.isoformat() if effective_from else None,
         expires_at.isoformat() if expires_at else None,
         json.dumps(scope or {}, sort_keys=True), confirmed_at.isoformat()),
    )
    connection.commit()
    return {"policy_id": policy_id, "question_family": question_family, "locale": locale}


def revoke_policy(connection: sqlite3.Connection, policy_id: str, reason: str,
                  at: datetime) -> dict[str, Any]:
    connection.execute(
        "UPDATE nondisclosure_policies SET revoked_at=?, revocation_reason=? "
        "WHERE policy_id=? AND revoked_at IS NULL",
        (at.isoformat(), reason, policy_id),
    )
    connection.commit()
    return {"policy_id": policy_id, "revoked": True}


def policy_issue(row: sqlite3.Row, context: dict[str, Any], at: datetime) -> str | None:
    """Why this policy does not apply, in the policy's own vocabulary."""
    if row["revoked_at"]:
        return "nondisclosure_policy_revoked"
    effective = parse_time(row["effective_from"])
    if effective and at < effective:
        return "nondisclosure_policy_not_yet_effective"
    expires = parse_time(row["expires_at"])
    if expires and at >= expires:
        return "nondisclosure_policy_expired"
    if not context_matches(json.loads(row["scope_json"]), context):
        return "nondisclosure_policy_scope_mismatch"
    return None


def normalize_options(options: Any) -> list[dict[str, str]] | None:
    """Accept `[{"label": ..., "value": ...}]` and reject anything looser.

    A real control's visible label and its submitted value are different strings —
    `<option value="3">Decline to self-identify</option>` — so matching the label proves
    nothing about what the form would send. Both halves are required.
    """
    if options is None:
        return None
    if not isinstance(options, list):
        raise ValueError("observed field options must be a list")
    normalized = []
    for option in options:
        if not isinstance(option, dict) or set(option) != {"label", "value"}:
            raise ValueError("each option must carry exactly a label and a value")
        if not all(isinstance(option[key], str) and 0 < len(option[key]) <= 256
                   for key in ("label", "value")):
            raise ValueError("option labels and values must be bounded strings")
        normalized.append({"label": option["label"], "value": option["value"]})
    return normalized


def resolve_nondisclosure(
    connection: sqlite3.Connection,
    question_family: str,
    locale: str,
    options: list[dict[str, str]] | None,
    context: dict[str, Any],
    at: datetime,
    form_url: str = "",
) -> dict[str, Any]:
    """Pick the one reviewed non-disclosure option a page offers, or say why not.

    Selecting "decline to answer" discloses nothing, which is why it may be automated at all
    while a demographic value may not. That only holds while the match is exact: a fuzzy match
    could select a real category, so anything other than exactly one hit pauses.

    The reviewed vocabulary is matched against the option's **label**, because that is what a
    person reviewed. What gets submitted is the option's **value**, which is the page's own
    opaque string and is carried through unread. Matching a label is therefore evidence that
    the control offers a non-disclosure choice, not evidence about what the form sends — so a
    label that maps to more than one value, or to none, pauses.
    """
    rows = connection.execute(
        "SELECT * FROM nondisclosure_policies WHERE question_family=? AND locale=?",
        (question_family, locale),
    ).fetchall()
    if not rows:
        return {"applied": False, "reason": "nondisclosure_policy_absent"}
    issues: list[str] = []
    applicable = []
    for row in rows:
        issue = policy_issue(row, context, at)
        if issue:
            issues.append(issue)
            continue
        applicable.append(row)
    if not applicable:
        return {"applied": False, "reason": sorted(set(issues))[0]}
    if len(applicable) > 1:
        return {"applied": False, "reason": "nondisclosure_policy_conflict"}
    policy = applicable[0]
    if not options:
        return {"applied": False, "reason": "nondisclosure_option_unavailable"}
    allowed = set(vocabulary_options(
        policy["locale"], json.loads(policy["option_tokens_json"]), policy["vocabulary_version"]))
    matches = [option for option in options if option["label"] in allowed]
    if len(matches) != 1:
        return {"applied": False, "reason": "nondisclosure_option_ambiguous"}
    if len({option["value"] for option in matches}) != 1:
        return {"applied": False, "reason": "nondisclosure_option_ambiguous"}
    if not option_mapping_trusted(connection, form_url, matches[0], at):
        return {"applied": False, "reason": "option_mapping_unverified"}
    return {"applied": True, "policy_id": policy["policy_id"],
            "option_label": matches[0]["label"], "submitted_value": matches[0]["value"]}


def conflict_derivation(
    connection: sqlite3.Connection,
    family: str,
    context: dict[str, Any],
    at: datetime,
) -> dict[str, Any]:
    """Whether a per-employer conflict answer may be derived. In v1 it never may.

    The contract a future derivation must satisfy, so that building it later cannot quietly
    weaken it:

    1. a user-approved employer entity, resolved from the backend application, not from page
       text and not from `jobs.normalized_employer` — that column is a deduplication and
       search key, so it may propose a candidate and never establish identity;
    2. the relationship type kept separate per family;
    3. a conflict registry the user has explicitly certified complete, still in date;
    4. a record carrying the application ID, registry version, certification, and a
       derivation hash.

    None of those objects exists yet. The important half is what absence means: a registry
    that does not mention this employer is not evidence that no conflict exists, so a miss is
    `unknown`, never `No`. Until the subsystem is approved and built, every conflict field is
    the user's to answer on the page.
    """
    if family not in EMPLOYER_CONFLICT_FAMILIES:
        raise ValueError(f"unknown conflict family: {family}")
    return {"derivable": False, "reason": "employer_entity_not_approved"}


# `not_present` is a statement about the whole form, so it is keyed by an identifier no
# observed field can carry: `_require_id` forbids `*`.
INVENTORY_SCOPE = "*inventory*"

# What each marker is allowed to be believed on. A marker never rests on an absence.
MARKER_EVIDENCE = {
    "policy_declined": "verified_policy_step",
    "user_handled": "user_confirmation",
}


def record_handling(connection: sqlite3.Connection, application_id: str, field_id: str,
                    marker: str, evidence_ref: str, at: datetime) -> dict[str, Any]:
    """Record how a protected-characteristic control was handled — never what was chosen.

    This table has no value column on purpose. `record_field` refuses a
    `nondisclosure_policy` source for the same reason: an ApplicationField stores what was
    entered, and for these controls Jobloom must not be able to say.

    Every marker carries the evidence it rests on. Nothing here may be inferred from
    something not happening: a control that stopped appearing may have been completed by the
    user, or the observer may have missed it, or the page may have re-rendered, and those are
    not the same event.
    """
    if marker not in HANDLING_MARKERS:
        raise ValueError(f"unknown handling marker: {marker}")
    if not evidence_ref or not isinstance(evidence_ref, str):
        raise ValueError(f"{marker} requires the evidence it rests on")
    connection.execute(
        "INSERT INTO nondisclosure_handling (application_id, field_id, marker, evidence_kind, "
        "evidence_ref, recorded_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(application_id, field_id) DO UPDATE SET marker=excluded.marker, "
        "evidence_kind=excluded.evidence_kind, evidence_ref=excluded.evidence_ref, "
        "recorded_at=excluded.recorded_at",
        (application_id, field_id, marker, MARKER_EVIDENCE[marker], evidence_ref, at.isoformat()),
    )
    return {"application_id": application_id, "field_id": field_id, "marker": marker}


def confirm_user_handled(connection: sqlite3.Connection, application_id: str, field_id: str,
                         actor: str, confirmation_ref: str, at: datetime) -> dict[str, Any]:
    """The only way `user_handled` may be written: the user says so.

    A control that vanished from the next observation is not the user having answered it.
    """
    if actor != "user":
        raise ValueError("only the user can confirm they handled a voluntary disclosure")
    result = record_handling(connection, application_id, field_id, "user_handled",
                             confirmation_ref, at)
    connection.commit()
    return result


def handling_markers(connection: sqlite3.Connection, application_id: str) -> dict[str, str]:
    """The markers recorded so far. An empty result is an empty result."""
    return {row["field_id"]: row["marker"] for row in connection.execute(
        "SELECT field_id, marker FROM nondisclosure_handling WHERE application_id=? "
        "ORDER BY field_id", (application_id,),
    )}


def handling_summary(connection: sqlite3.Connection, application_id: str) -> dict[str, Any]:
    """What the archive may say about voluntary disclosures, including that it does not know.

    No marker is not "there was no such control". It is also what a half-observed form, a
    missed control, an abandoned session, and a failed write all look like. Only a finalized
    inventory that covered every page can turn that into `not_present`.
    """
    # A row an earlier version wrote is not re-interpreted into a claim this version has
    # decided it cannot support. It is dropped from the reading, and if nothing else is
    # recorded the answer is `unknown`.
    markers = {field_id: marker for field_id, marker in
               handling_markers(connection, application_id).items()
               if field_id != INVENTORY_SCOPE and marker not in RETIRED_MARKERS}
    if not markers:
        return {"status": "unknown", "markers": {}}
    return {"status": "recorded", "markers": markers}


# --- option mapping trust -------------------------------------------------

# A page supplies both halves of an option, so a page can lie about either. The value below
# is what makes a label match mean anything: Jobloom's own replay renderer derives the value
# from the label by this rule, so an observed pair can be recomputed rather than believed.
def replay_option_value(label: str, nonce: str) -> str:
    """The value Jobloom's renderer emits for a label under one issued surface nonce.

    The nonce is what makes this worth checking. The derivation rule is public, so without a
    secret any process could compute a matching pair; with one, only the renderer Jobloom
    started for this run can.
    """
    if not nonce:
        raise ValueError("a replay option value requires an issued surface nonce")
    digest = hashlib.sha256(f"{nonce}\0{label}".encode("utf-8")).hexdigest()
    return f"opt-{digest[:16]}"


def register_replay_surface(connection: sqlite3.Connection, surface_id: str, origin: str,
                            nonce: str, fixture_sha256: str, renderer_version: str,
                            issued_at: datetime, expires_at: datetime,
                            page_digests: dict[str, str] | None = None) -> dict[str, Any]:
    """Record that Jobloom started this replay server, and on which exact origin.

    Issued by the process that starts the renderer, so the backend holds the record. Nothing
    a page says can create one.
    """
    parsed = urlparse(origin)
    if parsed.scheme != "http" or not LOOPBACK_HOST.fullmatch(parsed.hostname or ""):
        raise ValueError("a replay surface must be a loopback origin")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("a replay surface origin carries no path")
    if not nonce or len(nonce) < 32:
        raise ValueError("a replay surface requires a session nonce")
    connection.execute(
        "INSERT INTO replay_surfaces (surface_id, origin, nonce, fixture_sha256, "
        "page_digests_json, renderer_version, issued_at, expires_at, revoked_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (surface_id, origin, nonce, fixture_sha256,
         json.dumps(page_digests or {}, sort_keys=True), renderer_version,
         issued_at.isoformat(), expires_at.isoformat()),
    )
    connection.commit()
    return {"surface_id": surface_id, "origin": origin}


def revoke_replay_surface(connection: sqlite3.Connection, surface_id: str,
                          at: datetime) -> None:
    connection.execute(
        "UPDATE replay_surfaces SET revoked_at=? WHERE surface_id=? AND revoked_at IS NULL",
        (at.isoformat(), surface_id))
    connection.commit()


def active_replay_surface(connection: sqlite3.Connection, form_url: str,
                          at: datetime) -> sqlite3.Row | None:
    """The surface record for this exact origin, if Jobloom issued one and it is live."""
    parsed = urlparse(form_url or "")
    if parsed.scheme != "http" or not parsed.hostname or not parsed.port:
        return None
    origin = f"http://{parsed.hostname}:{parsed.port}"
    rows = connection.execute(
        "SELECT * FROM replay_surfaces WHERE origin=? AND revoked_at IS NULL", (origin,),
    ).fetchall()
    live = [row for row in rows
            if not (parse_time(row["expires_at"]) and at >= parse_time(row["expires_at"]))]
    if len(live) != 1:
        # Zero is nothing to trust. More than one means two processes claim the same origin
        # and this cannot tell which is serving the page in front of it, so picking the most
        # recent would be a guess presented as a fact.
        return None
    return live[0]


def surface_attestation(connection: sqlite3.Connection, page_url: str,
                        at: datetime) -> dict[str, Any] | None:
    """What `fill_core` may tell a worker about the surface it is being sent to.

    The worker never reads this database. It is handed the origin, renderer version and the
    digest of the exact page it is expected to load, and checks the page it actually gets
    against them. The nonce is deliberately absent: the worker has no use for it, and a
    package is a file on disk.
    """
    surface = active_replay_surface(connection, page_url, at)
    if not surface:
        return None
    parsed = urlparse(page_url)
    digests = json.loads(surface["page_digests_json"] or "{}")
    if parsed.path not in digests:
        return None
    return {"origin": surface["origin"], "renderer_version": surface["renderer_version"],
            "page_path": parsed.path, "page_sha256": digests[parsed.path],
            "expires_at": surface["expires_at"]}


def option_mapping_trusted(connection: sqlite3.Connection, form_url: str,
                           option: dict[str, str], at: datetime) -> bool:
    """Whether this label may be believed to select this value.

    `<option value="Asian">Prefer not to answer</option>` is the attack: the reviewed label
    is offered while a real category is submitted. Not being able to read a value is not the
    same as the value being safe.

    An earlier version accepted any `http://127.0.0.1` origin whose value matched the public
    derivation, which is not a provenance test at all — a loopback address is a network
    location, and any local process can listen on one and compute a public hash. Trust now
    requires a surface record the backend issued when it started the renderer, bound to that
    exact origin and carrying a nonce that only Jobloom and its own renderer hold. A live ATS
    would need an approved adapter mapping with a fixed hash and version; none exists, so on
    every other surface the control is the user's.
    """
    surface = active_replay_surface(connection, form_url, at)
    if not surface:
        return False
    try:
        expected = replay_option_value(option.get("label", ""), surface["nonce"])
    except ValueError:
        return False
    return option.get("value") == expected


def finalize_handling(connection: sqlite3.Connection, application_id: str,
                      observed_eeo_field_ids: list[str], inventory_ref: str,
                      at: datetime) -> dict[str, Any]:
    """Settle what can be settled about voluntary disclosures. In v1 that is never absence.

    A completed page chain is self-reported: index, predecessor hash and final-page flag all
    come from the observer, and an observer that never saw a page cannot report its absence.
    Renaming the evidence made the weak claim honest without making it strong, so v1 does not
    write `not_present` at all. "No voluntary-disclosure control appeared in the self-reported
    chain" is reportable; "this form has none" is not.

    `not_present` stays in `HANDLING_MARKERS` because a future surface that can enumerate a
    whole form — a supervised live acceptance with a full-page inventory, say — could earn it.
    Nothing today may write it.
    """
    recorded = handling_markers(connection, application_id)
    missing = sorted(set(observed_eeo_field_ids) - set(recorded))
    if missing:
        raise ValueError("voluntary disclosure controls were observed without a handling marker")
    return handling_summary(connection, application_id)
