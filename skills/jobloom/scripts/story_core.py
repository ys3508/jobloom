#!/usr/bin/env python3
"""Stories: an approved, provenance-bound projection of the evidence bank.

A Story is what a person says out loud about something they did — in an interview, or in the
free-text box on an application form. Neither surface has an evidence system behind it today,
and the reference implementations this is adapted from keep their stories as free markdown,
where a number in the `Result` line is attached to nothing. That is the one thing this module
exists to prevent.

So a Story is **not** a source of truth. It is a versioned, user-approved projection over
CandidateFacts that were already confirmed, and every factual assertion inside it is bound to
the EvidenceUnits that support it. The narrative framing is real work — it is what makes a
story tellable — but it is never promoted to evidence by having been written down.

Three consequences worth stating, because they are what the tests are about.

**Nothing unbound is selectable.** A version's narrative must be completely accounted for:
every span of it is either a claim bound to evidence, or a span the user explicitly marked as
framing. Whatever is left over is reported as `unbound_spans`, and a version with any of them
cannot reach an answer, a coverage count, or an interview pack. It can still exist as a draft,
because a half-written story is a normal thing to have.

**A claim can weaken its evidence, never strengthen it.** The same rule
`resume_core.validate_claims_manifest` applies to resume claims: a claim's `evidence_class`
may not exceed the strongest source it cites. Transferable evidence stays transferable no
matter how the sentence is phrased.

**Approval is of an exact content hash.** Editing one character produces a new immutable
version, and the previous approval does not follow it. That is also why capability mappings,
domain tags and the earned secret live on the version rather than beside it: changing any of
them changes what the user approved.

A Story is bound to the CandidateSnapshot it was approved against. When the active snapshot
changes, approval does not silently carry: the bindings are re-resolved against the new
snapshot and the user is asked again, the same explicit successor pattern `resume_migration`
uses for a resume. `prepare_successor` reports what actually moved so the second approval is
answering a real question rather than clicking through.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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
import candidate_core  # noqa: E402
import capability_ontology  # noqa: E402
import evidence_units  # noqa: E402
from _common import require_table  # noqa: E402
from evidence_matcher import EVIDENCE_ORDER  # noqa: E402

DRAFT = "draft"
APPROVED = "approved"
REVOKED = "revoked"

STAR_PARTS = ("situation", "task", "action", "result")
CONFIDENTIALITY = ("reusable", "employer_confidential", "application_confidential")
USABLE_FACT_STATUS = {"confirmed", "locked"}

# A claim may carry any evidence class the ontology knows, except `none` — a claim supported
# by nothing is not a weaker claim, it is an unsupported one, and it has its own handling.
CLAIM_CLASSES = tuple(name for name in EVIDENCE_ORDER if name != "none")
UNSUPPORTED = "unsupported"

_ONTOLOGY: dict[str, Any] | None = None


def ontology() -> dict[str, Any]:
    """The shared, versioned capability ontology, loaded once.

    Capability and domain tags are what a later step will retrieve a story by, so they are
    canonical ids from this file rather than free text. A tag nobody reviewed would let a
    story be found under a name the rest of the system does not use — the same drift the
    evidence resolver was introduced to end.
    """
    global _ONTOLOGY
    if _ONTOLOGY is None:
        _ONTOLOGY = capability_ontology.load_ontology()
    return _ONTOLOGY


def _capability_layer(capability_id: str) -> str | None:
    for entry in ontology()["capabilities"]:
        if entry["capability_id"] == capability_id:
            return entry["layer"]
    return None


# Characters that carry no assertion on their own, so a residue made only of these is not
# unbound material. Punctuation in both scripts, because the fact library holds both.
FILLER = re.compile(r"^[\s\.,;:!\?\-—–…\"'“”‘’()\[\]/、。，；：！？「」『』（）]*$")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(content: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS stories (
            story_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            current_version_id TEXT,
            confidentiality TEXT NOT NULL,
            confidential_employer TEXT,
            confidential_employer_normalized TEXT,
            confidential_application_id TEXT,
            applicability_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT,
            status_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS story_versions (
            version_id TEXT PRIMARY KEY,
            story_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            candidate_snapshot_sha256 TEXT NOT NULL,
            authored_by TEXT NOT NULL,
            title TEXT NOT NULL,
            star_json TEXT NOT NULL,
            primary_capability TEXT NOT NULL,
            capability_bindings_json TEXT NOT NULL,
            domains_json TEXT NOT NULL,
            earned_secret TEXT,
            reflection TEXT,
            framing_spans_json TEXT NOT NULL,
            unbound_spans_json TEXT NOT NULL,
            supersedes_version_id TEXT,
            created_at TEXT NOT NULL,
            approved_by TEXT,
            approved_at TEXT,
            FOREIGN KEY (story_id) REFERENCES stories(story_id)
        );
        CREATE INDEX IF NOT EXISTS story_versions_story_idx
            ON story_versions(story_id, created_at);

        CREATE TABLE IF NOT EXISTS story_claims (
            version_id TEXT NOT NULL,
            claim_id TEXT NOT NULL,
            claim_text TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL,
            evidence_class TEXT NOT NULL,
            PRIMARY KEY (version_id, claim_id),
            FOREIGN KEY (version_id) REFERENCES story_versions(version_id)
        );

        CREATE TABLE IF NOT EXISTS story_competency_mappings (
            version_id TEXT NOT NULL,
            competency TEXT NOT NULL,
            relation TEXT NOT NULL,
            claim_ids_json TEXT NOT NULL,
            limitation TEXT,
            reviewed_by TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            PRIMARY KEY (version_id, competency),
            FOREIGN KEY (version_id) REFERENCES story_versions(version_id)
        );

        CREATE TABLE IF NOT EXISTS story_usage_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_version_id TEXT NOT NULL,
            application_id TEXT,
            interview_id TEXT,
            round_id TEXT,
            question_type TEXT NOT NULL,
            used_at TEXT NOT NULL,
            outcome_ref TEXT
        );
        CREATE INDEX IF NOT EXISTS story_usage_version_idx
            ON story_usage_events(story_version_id, used_at);

        CREATE TABLE IF NOT EXISTS story_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            story_id TEXT,
            version_id TEXT,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );
    """)
    _migrate(connection)
    connection.commit()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _migrate(connection: sqlite3.Connection) -> None:
    """Bring a database written by an earlier schema forward, without inventing review.

    `CREATE TABLE IF NOT EXISTS` leaves an existing table exactly as it was, so a database
    that already held stories would keep the old columns and every new read would fail on a
    column that is not there.

    Two properties this has to have, and the first version had neither.

    **It runs inside a transaction.** Adding a column and backfilling it are one change. A
    crash between them used to leave every row at the `'{}'` default while the column's
    existence made the next run skip the backfill entirely — a database that looked migrated
    and had lost its capabilities. Each step is wrapped, so it either lands or does not.

    **It repairs rather than detects.** Whether work is needed is decided from the data, not
    from whether a column exists, so a run interrupted anywhere is fixed by running again.
    `primary_capability` is never dropped, which is what makes that always possible: even
    with `secondary_capabilities_json` already gone, a safe binding can be rebuilt from it.

    What it will not do is invent review. An older version named a capability and no claims,
    so nothing on disk says which claims evidenced it, and backfilling "all of them" would
    recreate precisely the promotion this schema exists to prevent — silently, on data nobody
    would look at again. The binding is carried with an empty claim list, `selectable`
    answers `capability_binding_unreviewed`, and the story is preserved and retrieved by
    nothing. The way back is to draft a successor with real bindings and approve it;
    `prepare_successor` cannot do it, because it would have to supply the very claim ids
    nobody recorded, and it says so by name.
    """
    _migrate_capability_bindings(connection)
    _migrate_confidential_employer(connection)
    _migrate_mapping_claims(connection)


def _unreviewed_binding(primary_capability: str, secondary: list[str]) -> str:
    return canonical_json({
        "primary": {"capability_id": primary_capability, "claim_ids": []},
        "secondary": [{"capability_id": item, "claim_ids": []} for item in secondary],
        "migrated_without_claims": True})


def read_bindings(raw: str | None) -> dict[str, Any]:
    """Whatever is in the column, as a shape the rest of the module can rely on.

    Total by construction: every caller of this reads a stored blob, and a stored blob can be
    anything if a write was interrupted or a row was edited by hand. An earlier version
    tolerated only *unparseable* JSON, so a column holding well-formed JSON of the wrong type
    — `[]`, `null`, `5` — raised `AttributeError` inside `initialize` and the database could
    not be opened at all. That is not an evidence escalation, but it is a worse failure than
    the one it was guarding: nothing can be done to a database that will not start.

    An empty result means "no usable binding", which is what the migration repairs and what
    `selectable` reports as `capability_binding_unreviewed`. Nothing here invents a claim id:
    a malformed `claim_ids` becomes empty, never a guess at what it meant.

    This answers the *shape* question only. Whether the ids it holds mean anything —
    a capability the ontology has, claims this version contains — is `binding_problems`.
    """
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    primary = value.get("primary")
    if not isinstance(primary, dict) or not isinstance(primary.get("capability_id"), str) \
            or not primary["capability_id"].strip():
        return {}
    bindings: dict[str, Any] = {
        "primary": {"capability_id": primary["capability_id"],
                    "claim_ids": _claim_ids(primary.get("claim_ids"))},
        "secondary": [
            {"capability_id": item["capability_id"],
             "claim_ids": _claim_ids(item.get("claim_ids"))}
            for item in (value.get("secondary") or [])
            if isinstance(item, dict) and isinstance(item.get("capability_id"), str)
            and item["capability_id"].strip()]
        if isinstance(value.get("secondary"), list) else [],
    }
    if value.get("migrated_without_claims"):
        bindings["migrated_without_claims"] = True
    return bindings


def _loads(raw: str | None) -> Any:
    try:
        return json.loads(raw or "null")
    except ValueError:
        return None


def _claim_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _legacy_secondary(raw: str | None) -> list[str]:
    """The replaced column, read the same way: anything unusable is nothing, never an error."""
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def binding_problems(connection: sqlite3.Connection, version_id: str,
                     bindings: dict[str, Any]) -> list[str]:
    """Reasons a stored binding cannot be trusted, checked against what it names.

    `read_bindings` answers whether the blob has the right *shape*. That is not the same
    question as whether it means anything: a hand-edited or half-written row can name a
    capability the ontology does not have, or claims this version does not contain, and be
    perfectly well-formed while doing it. Validation at write time cannot stand in for this,
    because a database that was edited after the write never went through write time.

    So the check is against the world the binding refers to, and every anomaly fails closed.
    A retired capability makes stories bound to it unselectable until they are re-bound,
    which is the intended direction: the ontology is the reviewed vocabulary, and a story
    retrievable under a name the system no longer uses is a story nobody reviewed under it.
    """
    if not bindings:
        return ["capability_binding_unreviewed"]
    reasons: list[str] = []
    primary = bindings["primary"]
    if not primary["claim_ids"]:
        reasons.append("capability_binding_unreviewed")
    known = {row["claim_id"] for row in connection.execute(
        "SELECT claim_id FROM story_claims WHERE version_id=?", (version_id,))}
    for binding in [primary, *bindings["secondary"]]:
        if _capability_layer(binding["capability_id"]) != "SKILL":
            reasons.append("capability_not_in_ontology")
        if any(claim_id not in known for claim_id in binding["claim_ids"]):
            reasons.append("capability_binding_claim_missing")
    return sorted(set(reasons))


def _needs_binding(raw: str | None) -> bool:
    """A row is unmigrated if what is on disk is not what the code reads.

    Not only when there is no usable binding: also when the blob merely *reads* as one after
    normalization. A `claim_ids` holding a string, or a `secondary` of the wrong type, is
    silently emptied on every read, and leaving the original in place would mean a person
    inspecting the row sees a binding the code does not act on — the same gap between what a
    thing reports and what it does that this repository has had to record before.

    Comparing the canonical forms covers every field at once rather than the one that was
    noticed, so a shape nobody thought of is repaired too.
    """
    bindings = read_bindings(raw)
    if not bindings:
        return True
    stored = _loads(raw)
    return canonical_json(stored) != canonical_json(bindings)


def _migrate_capability_bindings(connection: sqlite3.Connection) -> None:
    if "capability_bindings_json" not in _columns(connection, "story_versions"):
        with connection:
            connection.execute(
                "ALTER TABLE story_versions ADD COLUMN capability_bindings_json TEXT "
                "NOT NULL DEFAULT '{}'")
    # Read the data, not the schema. A previous run that added the column and stopped leaves
    # rows at the default, and those are exactly the rows still to do.
    has_secondary = "secondary_capabilities_json" in _columns(connection, "story_versions")
    columns = "version_id, primary_capability, capability_bindings_json" + (
        ", secondary_capabilities_json" if has_secondary else "")
    pending = [row for row in connection.execute(
        f"SELECT {columns} FROM story_versions").fetchall()
        if _needs_binding(row["capability_bindings_json"])]
    if pending:
        with connection:
            for row in pending:
                normalized = read_bindings(row["capability_bindings_json"])
                if normalized:
                    # Readable after normalization: keep what it says and write that down,
                    # rather than flattening a usable binding to the unreviewed form.
                    repaired = canonical_json(normalized)
                else:
                    secondary = _legacy_secondary(row["secondary_capabilities_json"]) \
                        if has_secondary else []
                    repaired = _unreviewed_binding(row["primary_capability"], secondary)
                connection.execute(
                    "UPDATE story_versions SET capability_bindings_json=? WHERE version_id=?",
                    (repaired, row["version_id"]))
    if has_secondary:
        # Only once nothing is pending, because the drop destroys what the backfill reads.
        # Dropped rather than left NOT NULL beside its replacement, where every future insert
        # would have to keep feeding a column nothing reads.
        with connection:
            connection.execute(
                "ALTER TABLE story_versions DROP COLUMN secondary_capabilities_json")


def _migrate_confidential_employer(connection: sqlite3.Connection) -> None:
    if "confidential_employer_normalized" not in _columns(connection, "stories"):
        with connection:
            connection.execute(
                "ALTER TABLE stories ADD COLUMN confidential_employer_normalized TEXT")
    pending = connection.execute(
        "SELECT story_id, confidential_employer FROM stories "
        "WHERE confidential_employer IS NOT NULL "
        "AND (confidential_employer_normalized IS NULL "
        "     OR confidential_employer_normalized='')").fetchall()
    if pending:
        with connection:
            for row in pending:
                connection.execute(
                    "UPDATE stories SET confidential_employer_normalized=? WHERE story_id=?",
                    (application_core.normalize_text(row["confidential_employer"]),
                     row["story_id"]))


def _migrate_mapping_claims(connection: sqlite3.Connection) -> None:
    mappings = _columns(connection, "story_competency_mappings")
    if mappings and "claim_ids_json" not in mappings:
        # Same withholding, for the same reason: a mapping that named no claims cannot be
        # given some. It stops producing a fit until it is recorded again.
        with connection:
            connection.execute(
                "ALTER TABLE story_competency_mappings ADD COLUMN claim_ids_json TEXT "
                "NOT NULL DEFAULT '[]'")


def _event(connection: sqlite3.Connection, story_id: str | None, version_id: str | None,
           actor: str, event_type: str, reason_code: str, metadata: dict[str, Any],
           at: datetime | None = None) -> None:
    connection.execute(
        "INSERT INTO story_events (created_at, story_id, version_id, actor, event_type, "
        "reason_code, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ((at or now_utc()).isoformat(), story_id, version_id, actor, event_type, reason_code,
         canonical_json(metadata)))


# ---- resolving what the evidence actually says ------------------------------------


def evidence_index(connection: sqlite3.Connection, snapshot_sha256: str) -> dict[str, dict[str, Any]]:
    """Every EvidenceUnit a snapshot's usable facts produce, keyed by unit id.

    Read from `candidate_facts` rather than from the snapshot file, because this is the same
    table the rest of the system judges a fact by, and a claim must be answerable to that.
    Facts that are neither confirmed nor locked are left out: a proposed fact is not evidence
    yet, and a story that cited one would be selectable on the strength of a suggestion.
    """
    require_table(connection, "candidate_facts")
    index: dict[str, dict[str, Any]] = {}
    for row in connection.execute(
            "SELECT fact_id, status, locked, evidence_strength FROM candidate_facts "
            "WHERE content_sha256=?", (snapshot_sha256,)):
        if row["status"] not in USABLE_FACT_STATUS:
            continue
        index[evidence_units.unit_id(snapshot_sha256, row["fact_id"])] = {
            "fact_id": row["fact_id"], "status": row["status"],
            "locked": bool(row["locked"]), "source_strength": row["evidence_strength"]}
    return index


def _active_snapshot(connection: sqlite3.Connection) -> str:
    require_table(connection, "candidate_snapshots")
    row = connection.execute(
        "SELECT content_sha256 FROM candidate_snapshots WHERE status='active' "
        "AND registered_by='user'").fetchone()
    if not row:
        raise ValueError("no active user-registered candidate snapshot")
    return row["content_sha256"]


# ---- the narrative must be completely accounted for --------------------------------


def _narrative(content: dict[str, Any]) -> str:
    parts = [content["star"][part] for part in STAR_PARTS]
    for optional in ("earned_secret", "reflection"):
        if content.get(optional):
            parts.append(content[optional])
    return "\n".join(parts)


def account_for(narrative: str, spans: list[str]) -> list[str]:
    """What is left of the narrative once every claim and framing span is taken out.

    The alternative was to ask a model which sentences make factual assertions, which puts a
    model in front of the gate that decides whether a story may be used — the wrong side of
    the ladder, and unfalsifiable besides. This asks the author instead: account for all of
    it, either by binding it to evidence or by marking it framing. Whatever neither covers is
    returned here, and its presence is what makes a version unselectable.

    Spans are matched verbatim and never overlap; a span appearing twice covers only its
    first free occurrence, so repeating a sentence does not silently cover the repeat.
    """
    covered = [False] * len(narrative)
    for span in spans:
        text = span.strip()
        if not text:
            continue
        start = 0
        while True:
            found = narrative.find(text, start)
            if found < 0:
                break
            if not any(covered[found:found + len(text)]):
                for index in range(found, found + len(text)):
                    covered[index] = True
                break
            start = found + 1
    residue, current = [], []
    for index, character in enumerate(narrative):
        if covered[index]:
            if current:
                residue.append("".join(current))
                current = []
        else:
            current.append(character)
    if current:
        residue.append("".join(current))
    return [chunk.strip() for chunk in residue if not FILLER.match(chunk)]


# ---- drafting a version ------------------------------------------------------------


def _binding(value: Any, label: str) -> dict[str, Any]:
    """One capability and the claims that evidence *it*, not the story around it.

    A capability used to be a bare id, and the class a competency retrieved at was the
    strongest class anywhere in the version. So a capability whose only support was
    transferable read as direct whenever some unrelated claim in the same story was direct —
    G2 defeated at retrieval, by arithmetic over the wrong set. A binding names its own
    claims, and the class is computed from those.
    """
    if isinstance(value, str):
        raise ValueError(f"{label} must name the claims that evidence it")
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a capability binding")
    capability_id = value.get("capability_id")
    if _capability_layer(capability_id) != "SKILL":
        raise ValueError(f"{capability_id} is not a reviewed SKILL capability")
    claim_ids = value.get("claim_ids")
    if not isinstance(claim_ids, list) or not claim_ids \
            or any(not isinstance(item, str) for item in claim_ids):
        raise ValueError(f"{capability_id} must be bound to at least one claim")
    return {"capability_id": capability_id, "claim_ids": sorted(set(claim_ids))}


def _validate_shape(content: dict[str, Any]) -> None:
    if not isinstance(content.get("title"), str) or not content["title"].strip():
        raise ValueError("a story version requires title")
    star = content.get("star")
    if not isinstance(star, dict) or any(
            not isinstance(star.get(part), str) or not star[part].strip() for part in STAR_PARTS):
        raise ValueError("a story version requires a complete STAR narrative")
    for field in ("domains", "framing_spans"):
        value = content.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{field} must be a list of strings")
    for domain_id in content.get("domains", []):
        if _capability_layer(domain_id) != "DOMAIN":
            raise ValueError(f"{domain_id} is not a reviewed DOMAIN tag")
    claims = content.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("a story version requires at least one claim")


def _validate_bindings(content: dict[str, Any], claims: list[dict[str, Any]]) -> tuple:
    known = {claim["claim_id"] for claim in claims}
    primary = _binding(content.get("primary_capability"), "primary_capability")
    secondary = [_binding(item, "secondary_capability")
                 for item in content.get("secondary_capabilities", [])]
    for binding in [primary, *secondary]:
        missing = [claim_id for claim_id in binding["claim_ids"] if claim_id not in known]
        if missing:
            raise ValueError(
                f"{binding['capability_id']} is bound to a claim this version does not "
                f"have: {missing[0]}")
    return primary, secondary


def _validate_claims(content: dict[str, Any], index: dict[str, dict[str, Any]],
                     narrative: str) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for claim in content["claims"]:
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id or claim_id in seen:
            raise ValueError("every claim requires a unique claim_id")
        seen.add(claim_id)
        text = claim.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"claim {claim_id} requires text")
        if text not in narrative:
            # A claim is a span of what the story actually says. One that quotes nothing in
            # the narrative binds evidence to a sentence nobody will ever tell.
            raise ValueError(f"claim {claim_id} does not appear in the narrative")
        refs = claim.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs):
            raise ValueError(f"claim {claim_id} requires at least one evidence_ref")
        evidence_class = claim.get("evidence_class")
        if evidence_class == UNSUPPORTED:
            # Allowed to exist so a draft can record "this is the part with nothing behind
            # it". `selectable` refuses it, which is the whole point of writing it down.
            resolved.append({"claim_id": claim_id, "text": text, "evidence_refs": list(refs),
                             "evidence_class": UNSUPPORTED})
            continue
        if evidence_class not in CLAIM_CLASSES:
            raise ValueError(f"claim {claim_id} has an invalid evidence_class")
        missing = [ref for ref in refs if ref not in index]
        if missing:
            raise ValueError(
                f"claim {claim_id} cites evidence the snapshot does not have: {missing[0]}")
        strongest = max(EVIDENCE_ORDER[index[ref]["source_strength"]] for ref in refs)
        if EVIDENCE_ORDER[evidence_class] > strongest:
            # The rule `resume_core.validate_claims_manifest` applies to a resume claim,
            # applied here for the same reason: a sentence cannot be better evidenced than
            # the evidence it cites, however it is worded.
            raise ValueError(f"claim {claim_id} inflates its supporting evidence")
        resolved.append({"claim_id": claim_id, "text": text, "evidence_refs": list(refs),
                         "evidence_class": evidence_class})
    return resolved


def draft_version(connection: sqlite3.Connection, content: dict[str, Any], *,
                  story_id: str | None = None, authored_by: str = "user",
                  confidentiality: str = "reusable",
                  confidential_employer: str | None = None,
                  confidential_application_id: str | None = None,
                  applicability: dict[str, Any] | None = None,
                  supersedes_version_id: str | None = None,
                  snapshot_sha256: str | None = None,
                  at: datetime | None = None) -> dict[str, Any]:
    """Write an immutable draft version. Approves nothing.

    The returned `content_sha256` is what the user must later approve by name, so the thing
    they read and the thing that becomes usable are provably the same thing.
    """
    initialize(connection)
    if authored_by not in {"user", "model_assisted"}:
        raise ValueError("authored_by must be user or model_assisted")
    if confidentiality not in CONFIDENTIALITY:
        raise ValueError("invalid confidentiality")
    if confidentiality == "employer_confidential" and not confidential_employer:
        raise ValueError("employer_confidential requires the employer it is confidential to")
    if confidentiality == "application_confidential" and not confidential_application_id:
        raise ValueError("application_confidential requires its application_id")
    snapshot = snapshot_sha256 or _active_snapshot(connection)
    _validate_shape(content)
    narrative = _narrative(content)
    index = evidence_index(connection, snapshot)
    claims = _validate_claims(content, index, narrative)
    primary, secondary = _validate_bindings(content, claims)
    framing = list(content.get("framing_spans", []))
    unbound = account_for(narrative, [claim["text"] for claim in claims] + framing)

    timestamp = (at or now_utc()).isoformat()
    if story_id:
        story = connection.execute("SELECT * FROM stories WHERE story_id=?", (story_id,)).fetchone()
        if not story:
            raise ValueError("story not found")
        if story["status"] == REVOKED:
            raise ValueError("a revoked story takes no new versions")
    else:
        story_id = f"S-{uuid.uuid4().hex[:10]}"
        connection.execute(
            "INSERT INTO stories (story_id, status, current_version_id, confidentiality, "
            "confidential_employer, confidential_employer_normalized, "
            "confidential_application_id, applicability_json, created_at) "
            "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)",
            (story_id, DRAFT, confidentiality, confidential_employer,
             application_core.normalize_text(confidential_employer)
             if confidential_employer else None,
             confidential_application_id, canonical_json(applicability or {}), timestamp))

    stored = {"title": content["title"], "star": {part: content["star"][part] for part in STAR_PARTS},
              "primary_capability": primary,
              "secondary_capabilities": secondary,
              "domains": list(content.get("domains", [])),
              "earned_secret": content.get("earned_secret") or None,
              "reflection": content.get("reflection") or None,
              "framing_spans": framing, "claims": claims,
              "candidate_snapshot_sha256": snapshot}
    digest = content_hash(stored)
    version_id = f"SV-{uuid.uuid4().hex[:12]}"
    connection.execute(
        "INSERT INTO story_versions (version_id, story_id, content_sha256, "
        "candidate_snapshot_sha256, authored_by, title, star_json, primary_capability, "
        "capability_bindings_json, domains_json, earned_secret, reflection, "
        "framing_spans_json, unbound_spans_json, supersedes_version_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (version_id, story_id, digest, snapshot, authored_by, stored["title"],
         canonical_json(stored["star"]), primary["capability_id"],
         canonical_json({"primary": primary, "secondary": secondary}),
         canonical_json(stored["domains"]),
         stored["earned_secret"], stored["reflection"], canonical_json(framing),
         canonical_json(unbound), supersedes_version_id, timestamp))
    for claim in claims:
        connection.execute(
            "INSERT INTO story_claims (version_id, claim_id, claim_text, evidence_refs_json, "
            "evidence_class) VALUES (?, ?, ?, ?, ?)",
            (version_id, claim["claim_id"], claim["text"],
             canonical_json(claim["evidence_refs"]), claim["evidence_class"]))
    _event(connection, story_id, version_id, authored_by, "story_version_drafted",
           "draft_written", {"claims": len(claims), "unbound_spans": len(unbound),
                             "snapshot_sha256": snapshot}, at)
    connection.commit()
    return {"story_id": story_id, "version_id": version_id, "content_sha256": digest,
            "status": DRAFT, "approved": False, "claims": claims,
            "unbound_spans": unbound, "candidate_snapshot_sha256": snapshot,
            "next_step": "the user reads the exact version and approves its content hash"}


# ---- approving one exact version ---------------------------------------------------


def approve_version(connection: sqlite3.Connection, version_id: str, content_sha256: str,
                    actor: str = "user", at: datetime | None = None) -> dict[str, Any]:
    """Approve the exact content the user read.

    The hash is a required argument rather than something looked up, so an approval cannot
    land on a version that changed between being shown and being confirmed.
    """
    require_table(connection, "story_versions")
    version = connection.execute(
        "SELECT * FROM story_versions WHERE version_id=?", (version_id,)).fetchone()
    if not version:
        raise ValueError("story version not found")
    if version["approved_at"]:
        raise ValueError("that version is already approved")
    if version["content_sha256"] != content_sha256:
        raise ValueError("the approved content hash does not match this version")
    story = connection.execute(
        "SELECT * FROM stories WHERE story_id=?", (version["story_id"],)).fetchone()
    if story["status"] == REVOKED:
        raise ValueError("a revoked story takes no approvals")
    if json.loads(version["unbound_spans_json"]):
        raise ValueError("this version has narrative that is bound to nothing")
    unsupported = connection.execute(
        "SELECT claim_id FROM story_claims WHERE version_id=? AND evidence_class=?",
        (version_id, UNSUPPORTED)).fetchone()
    if unsupported:
        raise ValueError(f"claim {unsupported['claim_id']} is supported by nothing")
    if version["candidate_snapshot_sha256"] != _active_snapshot(connection):
        raise ValueError("this version was written against a profile that is no longer active")

    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE story_versions SET approved_by=?, approved_at=? WHERE version_id=? "
        "AND approved_at IS NULL", (actor, timestamp, version_id))
    connection.execute(
        "UPDATE stories SET status=?, current_version_id=?, status_reason=? WHERE story_id=?",
        (APPROVED, version_id, "user_approved", version["story_id"]))
    _event(connection, version["story_id"], version_id, actor, "story_version_approved",
           "user_approved", {"content_sha256": content_sha256}, at)
    connection.commit()
    return {"story_id": version["story_id"], "version_id": version_id, "status": APPROVED,
            "approved_by": actor, "approved_at": timestamp}


def revoke(connection: sqlite3.Connection, story_id: str, reason: str,
           actor: str = "user", at: datetime | None = None) -> dict[str, Any]:
    require_table(connection, "stories")
    story = connection.execute("SELECT * FROM stories WHERE story_id=?", (story_id,)).fetchone()
    if not story:
        raise ValueError("story not found")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "UPDATE stories SET status=?, revoked_at=?, status_reason=? WHERE story_id=?",
        (REVOKED, timestamp, reason, story_id))
    _event(connection, story_id, story["current_version_id"], actor, "story_revoked", reason,
           {}, at)
    connection.commit()
    return {"story_id": story_id, "status": REVOKED, "revoked_at": timestamp}


# ---- whether a version may actually be used ----------------------------------------


def selectable(connection: sqlite3.Connection, version_id: str) -> dict[str, Any]:
    """The deterministic gate. No model, no scoring, no ranking — may this be used at all.

    Every reason is a stable code rather than prose, because callers act on them and a
    reader needs to know which one fired without reading English.
    """
    require_table(connection, "story_versions")
    version = connection.execute(
        "SELECT * FROM story_versions WHERE version_id=?", (version_id,)).fetchone()
    if not version:
        return {"selectable": False, "reasons": ["version_unknown"]}
    story = connection.execute(
        "SELECT * FROM stories WHERE story_id=?", (version["story_id"],)).fetchone()
    reasons: list[str] = []
    if not version["approved_at"]:
        reasons.append("version_not_approved")
    if story["status"] == REVOKED:
        reasons.append("story_revoked")
    if story["current_version_id"] != version_id:
        reasons.append("superseded_version")
    if json.loads(version["unbound_spans_json"]):
        reasons.append("narrative_not_fully_bound")
    bindings = read_bindings(version["capability_bindings_json"])
    # Shape, then meaning. A binding may be perfectly well-formed and still name a
    # capability the ontology does not have or claims this version does not contain.
    reasons.extend(binding_problems(connection, version_id, bindings))

    snapshot = version["candidate_snapshot_sha256"]
    try:
        active = _active_snapshot(connection)
    except ValueError:
        active = None
    if snapshot != active:
        reasons.append("candidate_snapshot_changed")
    index = evidence_index(connection, snapshot)
    classes: dict[str, str] = {}
    for claim in connection.execute(
            "SELECT * FROM story_claims WHERE version_id=?", (version_id,)):
        if claim["evidence_class"] == UNSUPPORTED:
            reasons.append("claim_unsupported")
            continue
        refs = json.loads(claim["evidence_refs_json"])
        if any(ref not in index for ref in refs):
            reasons.append("evidence_no_longer_valid")
            continue
        strongest = max(EVIDENCE_ORDER[index[ref]["source_strength"]] for ref in refs)
        if EVIDENCE_ORDER[claim["evidence_class"]] > strongest:
            reasons.append("evidence_weakened")
        classes[claim["claim_id"]] = claim["evidence_class"]
    return {"selectable": not reasons, "reasons": sorted(set(reasons)),
            "version_id": version_id, "story_id": version["story_id"],
            "evidence_classes": classes,
            "confidentiality": story["confidentiality"],
            "confidential_employer": story["confidential_employer"],
            "confidential_employer_normalized": story["confidential_employer_normalized"],
            "confidential_application_id": story["confidential_application_id"]}


# ---- carrying a story across a change of snapshot ----------------------------------


def stranded(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Approved stories the active profile left behind, and what changed under each."""
    require_table(connection, "stories")
    try:
        active = _active_snapshot(connection)
    except ValueError:
        return []
    rows = connection.execute(
        "SELECT s.story_id, s.current_version_id, v.candidate_snapshot_sha256, v.title "
        "FROM stories s JOIN story_versions v ON v.version_id = s.current_version_id "
        "WHERE s.status=? AND v.candidate_snapshot_sha256 != ? ORDER BY s.story_id",
        (APPROVED, active)).fetchall()
    return [{"story_id": row["story_id"], "version_id": row["current_version_id"],
             "title": row["title"], "approved_against": row["candidate_snapshot_sha256"],
             "changes": restate(connection, row["current_version_id"])} for row in rows]


def restate(connection: sqlite3.Connection, version_id: str,
            snapshot_sha256: str | None = None) -> dict[str, Any]:
    """Re-resolve one version's bindings against a snapshot, and report what moved.

    Read-only. The point is that the second approval is answering a real question — *these
    claims were checked against who you were; are they still true of who you are now?* — and
    a carry that could not say what changed would be asking the user to click, not to decide.
    """
    require_table(connection, "story_versions")
    version = connection.execute(
        "SELECT * FROM story_versions WHERE version_id=?", (version_id,)).fetchone()
    if not version:
        raise ValueError("story version not found")
    target = snapshot_sha256 or _active_snapshot(connection)
    was = evidence_index(connection, version["candidate_snapshot_sha256"])
    now = evidence_index(connection, target)
    # A unit id is derived from the snapshot, so the same fact has a different id under the
    # new one. The fact is the thing that persists; the reference is re-derived from it.
    fact_of = {unit_id: entry["fact_id"] for unit_id, entry in was.items()}
    by_fact = {entry["fact_id"]: unit_id for unit_id, entry in now.items()}

    claims, changed, lost = [], [], []
    for claim in connection.execute(
            "SELECT * FROM story_claims WHERE version_id=? ORDER BY claim_id", (version_id,)):
        refs = json.loads(claim["evidence_refs_json"])
        facts = [fact_of.get(ref) for ref in refs]
        carried = [by_fact.get(fact) for fact in facts]
        missing = [fact for fact, unit in zip(facts, carried) if fact is None or unit is None]
        entry = {"claim_id": claim["claim_id"], "text": claim["claim_text"],
                 "evidence_class": claim["evidence_class"],
                 "fact_ids": [fact for fact in facts if fact],
                 "evidence_refs": [unit for unit in carried if unit]}
        if missing:
            entry["status"] = "evidence_missing"
            lost.append(claim["claim_id"])
        else:
            strongest = max(EVIDENCE_ORDER[now[unit]["source_strength"]] for unit in carried)
            if EVIDENCE_ORDER[claim["evidence_class"]] > strongest:
                entry["status"] = "evidence_weakened"
                entry["available_class"] = next(
                    name for name, rank in sorted(EVIDENCE_ORDER.items(), key=lambda kv: -kv[1])
                    if rank == strongest)
                changed.append(claim["claim_id"])
            else:
                entry["status"] = "unchanged"
        claims.append(entry)
    return {"version_id": version_id, "target_snapshot_sha256": target,
            "claims": claims, "changed_claims": changed, "lost_claims": lost,
            "carryable": not lost,
            "identical": not changed and not lost}


def prepare_successor(connection: sqlite3.Connection, version_id: str,
                      at: datetime | None = None) -> dict[str, Any]:
    """Re-bind an approved version to the active snapshot as a new draft.

    Preparing is not approving. The successor is written with references re-derived from the
    same facts, and it arrives unapproved however little changed — an unchanged projection is
    still a projection of a different profile, and the user is the one who says so.
    """
    require_table(connection, "story_versions")
    version = connection.execute(
        "SELECT * FROM story_versions WHERE version_id=?", (version_id,)).fetchone()
    if not version:
        raise ValueError("story version not found")
    if not version["approved_at"]:
        raise ValueError("only an approved version has anything to carry forward")
    target = _active_snapshot(connection)
    if version["candidate_snapshot_sha256"] == target:
        raise ValueError("this story is already bound to the active profile")
    bindings = read_bindings(version["capability_bindings_json"])
    if not (bindings.get("primary") or {}).get("claim_ids"):
        # Carrying re-derives evidence references from the same facts; it cannot supply claim
        # ids nobody recorded. A version migrated from the schema where a capability was a
        # bare label has to be re-bound by a person, as a new draft, and approved.
        raise ValueError(
            "this version's capability names no claims, so there is nothing to carry: draft "
            "a successor with real capability bindings and approve it")
    moved = restate(connection, version_id, target)
    if moved["lost_claims"]:
        raise ValueError(
            "the active profile no longer supports every claim; the claim must be re-evidenced "
            f"or removed: {moved['lost_claims'][0]}")
    star = json.loads(version["star_json"])
    content = {
        "title": version["title"], "star": star,
        "primary_capability": bindings["primary"],
        "secondary_capabilities": bindings.get("secondary", []),
        "domains": json.loads(version["domains_json"]),
        "earned_secret": version["earned_secret"], "reflection": version["reflection"],
        "framing_spans": json.loads(version["framing_spans_json"]),
        "claims": [{"claim_id": claim["claim_id"], "text": claim["text"],
                    "evidence_refs": claim["evidence_refs"],
                    # Weakened evidence is written down as weakened. It is never carried at
                    # the class it used to hold, which is what G2 refuses in both directions.
                    "evidence_class": claim.get("available_class", claim["evidence_class"])}
                   for claim in moved["claims"]],
    }
    drafted = draft_version(connection, content, story_id=version["story_id"],
                            authored_by="user", supersedes_version_id=version_id,
                            snapshot_sha256=target, at=at)
    drafted["restated"] = moved
    drafted["next_step"] = ("the user reads what changed and approves the successor's "
                            "content hash")
    return drafted


# ---- usage --------------------------------------------------------------------------


# ---- mapping a story to the competency a question tests -----------------------------
#
# Two layers, and the separation is the point. The first answers whether a version may be
# used at all and what evidence class supports it; it is arithmetic over approved rows. The
# second may reorder what the first returned. Nothing in the second can add a story, remove
# one, or change a class — a ranking signal that could do any of those would be an evidence
# decision wearing a ranking signal's clothes.

ANSWERS = "answers"
ANSWERS_WITH_LIMITATION = "answers_with_limitation"
RELATIONS = (ANSWERS, ANSWERS_WITH_LIMITATION)

STRONG = "strong"
WORKABLE = "workable"
TRANSFERABLE = "transferable"
GAP = "gap"
FIT_ORDER = {STRONG: 3, WORKABLE: 2, TRANSFERABLE: 1}

# What counts as covering a competency at all. `mention_only` is deliberately absent: the
# spec names transferable as the weakest coverage there is, and a fact the profile merely
# mentions is not something to answer an interview question out of.
COVERING = {"direct", "strongly_related"}


def record_mapping(connection: sqlite3.Connection, version_id: str, competency: str,
                   claim_ids: list[str], relation: str = ANSWERS,
                   limitation: str | None = None, actor: str = "user",
                   at: datetime | None = None) -> dict[str, Any]:
    """Record that a reviewed version answers a competency its capabilities do not name.

    Stored rather than inferred on each use, so what a story is retrieved under is something
    a person decided once and can be shown, not something recomputed differently next time.

    It names its own claims for the same reason a capability binding does: a mapping that
    said only "this story answers that" would be retrieved at whatever the story's strongest
    claim happened to be, and reaching a competency the capabilities do not name is exactly
    where that is least likely to be the evidence anyone meant.
    """
    require_table(connection, "story_competency_mappings")
    if relation not in RELATIONS:
        raise ValueError("invalid mapping relation")
    if relation == ANSWERS_WITH_LIMITATION and not (limitation or "").strip():
        raise ValueError("a limitation must say what the limitation is")
    if _capability_layer(competency) != "SKILL":
        raise ValueError(f"{competency} is not a reviewed SKILL capability")
    version = connection.execute(
        "SELECT story_id FROM story_versions WHERE version_id=?", (version_id,)).fetchone()
    if not version:
        raise ValueError("story version not found")
    if not isinstance(claim_ids, list) or not claim_ids:
        raise ValueError("a mapping must name the claims that evidence it")
    known = {row["claim_id"] for row in connection.execute(
        "SELECT claim_id FROM story_claims WHERE version_id=?", (version_id,))}
    missing = [claim_id for claim_id in claim_ids if claim_id not in known]
    if missing:
        raise ValueError(f"the mapping names a claim this version does not have: {missing[0]}")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "INSERT OR REPLACE INTO story_competency_mappings (version_id, competency, relation, "
        "claim_ids_json, limitation, reviewed_by, reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (version_id, competency, relation, canonical_json(sorted(set(claim_ids))), limitation,
         actor, timestamp))
    _event(connection, version["story_id"], version_id, actor, "story_mapping_recorded",
           relation, {"competency": competency, "claims": len(set(claim_ids))}, at)
    connection.commit()
    return {"version_id": version_id, "competency": competency, "relation": relation,
            "claim_ids": sorted(set(claim_ids)), "limitation": limitation,
            "reviewed_by": actor, "reviewed_at": timestamp}


def bound_class(classes: dict[str, str], claim_ids: list[str]) -> str | None:
    """The class a *binding* can be cited at: the strongest of the claims it names.

    Scoped to the binding, which is the whole correction. The strongest class anywhere in the
    version says nothing about the capability being asked for, and using it let a capability
    supported only by transferable evidence be retrieved as strong because some other claim
    in the same story happened to be direct.
    """
    bound = [classes[claim_id] for claim_id in claim_ids if claim_id in classes]
    if not bound:
        return None
    return max(bound, key=lambda name: EVIDENCE_ORDER[name])


def _band(evidence_class: str | None, covering_fit: str) -> dict[str, Any] | None:
    if evidence_class in COVERING:
        return {"fit": covering_fit, "evidence_class": evidence_class}
    if evidence_class == TRANSFERABLE:
        return {"fit": TRANSFERABLE, "evidence_class": evidence_class}
    return None


def _confidentiality_allows(check: dict[str, Any], employer: str | None,
                            application_id: str | None) -> bool:
    """A hard AND restriction, not a retrieval hint. Absent context does not open it.

    `employer` here is the normalized identity the *service* resolved from the application
    row, never a name a caller offered. A caller able to say which employer it was would be
    able to unlock every employer-confidential story by naming the right one.
    """
    kind = check["confidentiality"]
    if kind == "employer_confidential":
        return bool(employer) and employer == check["confidential_employer_normalized"]
    if kind == "application_confidential":
        return bool(application_id) and application_id == check["confidential_application_id"]
    return True


def _fit(connection: sqlite3.Connection, version: sqlite3.Row, competency: str,
         classes: dict[str, str]) -> dict[str, Any] | None:
    """Which band this version answers one competency in, on the evidence bound to it."""
    bindings = read_bindings(version["capability_bindings_json"])
    primary = bindings.get("primary") or {}
    if not primary.get("capability_id"):
        # `selectable` refuses such a version first, so this is belt to that brace: a row
        # whose binding was never written is retrieved by nothing rather than raising.
        return None
    if binding_problems(connection, version["version_id"], bindings):
        # Checked here as well as at the gate, so retrieval does not rely on a caller having
        # asked the gate first. A binding that cannot be trusted answers nothing.
        return None
    if primary["capability_id"] == competency:
        band = _band(bound_class(classes, primary["claim_ids"]), STRONG)
        return {**band, "why": "primary_capability",
                "claim_ids": primary["claim_ids"]} if band else None
    for secondary in bindings["secondary"]:
        if secondary["capability_id"] == competency:
            band = _band(bound_class(classes, secondary["claim_ids"]), WORKABLE)
            return {**band, "why": "secondary_capability",
                    "claim_ids": secondary["claim_ids"]} if band else None
    mapping = connection.execute(
        "SELECT * FROM story_competency_mappings WHERE version_id=? AND competency=?",
        (version["version_id"], competency)).fetchone()
    if mapping:
        claim_ids = _claim_ids(_loads(mapping["claim_ids_json"]))
        if any(claim_id not in classes for claim_id in claim_ids):
            # The same suspicion the capability bindings get: a stored mapping naming a claim
            # this version does not have was not reviewed against this version.
            return None
        band = _band(bound_class(classes, claim_ids), WORKABLE)
        if band:
            return {**band, "why": "reviewed_mapping", "claim_ids": claim_ids,
                    "limitation": mapping["limitation"]}
    return None


def _validate_advisory(advisory: Any) -> dict[str, dict[str, Any]]:
    """Advisory ranking is allowed to be a model's opinion. It has to say that it is."""
    if not advisory:
        return {}
    ranked: dict[str, dict[str, Any]] = {}
    for entry in advisory:
        version_id = entry.get("version_id")
        provenance = entry.get("provenance") or {}
        if not isinstance(version_id, str) or not version_id:
            raise ValueError("an advisory signal must name the version it ranks")
        if provenance.get("source") != "model" or not provenance.get("model") \
                or not provenance.get("at"):
            raise ValueError("an advisory signal must carry model provenance and a timestamp")
        if not isinstance(entry.get("score"), (int, float)):
            raise ValueError("an advisory signal must carry a score")
        ranked[version_id] = {"score": float(entry["score"]),
                              "confidence": entry.get("confidence"),
                              "provenance": provenance}
    return ranked


def application_identity(connection: sqlite3.Connection,
                         application_id: str) -> dict[str, Any] | None:
    """Who an application is actually to, read from the rows that already know.

    There is deliberately no way to pass an employer in. Confidentiality is decided against
    this, and a caller that could name the employer could unlock an employer-confidential
    story by naming the right one — which is not a restriction, it is a password everybody
    can read off the story they want.
    """
    require_table(connection, "applications")
    row = connection.execute(
        "SELECT a.application_id, j.employer, j.normalized_employer FROM applications a "
        "LEFT JOIN jobs j ON j.job_id = a.job_id WHERE a.application_id=?",
        (application_id,)).fetchone()
    if not row:
        return None
    return {"application_id": row["application_id"], "employer": row["employer"],
            "normalized_employer": row["normalized_employer"]}


def map_stories(connection: sqlite3.Connection, competencies: list[str], *,
                application_id: str | None = None,
                advisory: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Which approved stories can answer each competency, and on what evidence.

    Read-only, and it produces nothing submittable. A competency no story covers comes back
    as a gap rather than as the nearest thing, because the nearest thing is how a bridging
    claim gets invented.

    The only identity input is `application_id`; the employer is resolved from it here.
    """
    require_table(connection, "story_versions")
    identity = application_identity(connection, application_id) if application_id else None
    employer = identity["normalized_employer"] if identity else None
    ranked = _validate_advisory(advisory)
    usage_counts = {row["story_id"]: row["uses"] for row in connection.execute(
        "SELECT v.story_id AS story_id, COUNT(*) AS uses FROM story_usage_events e "
        "JOIN story_versions v ON v.version_id = e.story_version_id GROUP BY v.story_id")}
    current = connection.execute(
        "SELECT v.* FROM stories s JOIN story_versions v ON v.version_id = s.current_version_id "
        "WHERE s.status=? ORDER BY v.version_id", (APPROVED,)).fetchall()

    eligible: list[tuple[sqlite3.Row, dict[str, Any]]] = []
    for version in current:
        check = selectable(connection, version["version_id"])
        if not check["selectable"]:
            continue
        if not _confidentiality_allows(check, employer, application_id):
            continue
        eligible.append((version, check))

    result: dict[str, Any] = {}
    for competency in competencies:
        rows = []
        for version, check in eligible:
            fit = _fit(connection, version, competency, check["evidence_classes"])
            if not fit:
                continue
            entry = {"story_id": version["story_id"], "version_id": version["version_id"],
                     "title": version["title"],
                     "use_count": usage_counts.get(version["story_id"], 0), **fit}
            if version["version_id"] in ranked:
                entry["advisory"] = ranked[version["version_id"]]
            rows.append(entry)
        rows.sort(key=lambda row: (
            -FIT_ORDER[row["fit"]],
            -EVIDENCE_ORDER[row["evidence_class"]],
            # Only after the deterministic keys tie: a model's opinion, then least-used, then
            # the id, so the order is stable with or without an opinion being offered.
            -(row.get("advisory") or {}).get("score", 0.0),
            row["use_count"], row["version_id"]))
        result[competency] = {"fit": rows[0]["fit"] if rows else GAP, "stories": rows}
    return result


def record_use(connection: sqlite3.Connection, version_id: str, question_type: str, *,
               application_id: str | None = None, interview_id: str | None = None,
               round_id: str | None = None, outcome_ref: str | None = None,
               at: datetime | None = None) -> dict[str, Any]:
    """Append one usage event. `use_count` and `last_used` are read from these, never set."""
    require_table(connection, "story_usage_events")
    if not connection.execute(
            "SELECT 1 FROM story_versions WHERE version_id=?", (version_id,)).fetchone():
        raise ValueError("story version not found")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "INSERT INTO story_usage_events (story_version_id, application_id, interview_id, "
        "round_id, question_type, used_at, outcome_ref) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (version_id, application_id, interview_id, round_id, question_type, timestamp,
         outcome_ref))
    connection.commit()
    return {"version_id": version_id, "used_at": timestamp}


def usage(connection: sqlite3.Connection, story_id: str) -> dict[str, Any]:
    require_table(connection, "story_usage_events")
    rows = connection.execute(
        "SELECT e.used_at FROM story_usage_events e JOIN story_versions v "
        "ON v.version_id = e.story_version_id WHERE v.story_id=? ORDER BY e.used_at",
        (story_id,)).fetchall()
    return {"story_id": story_id, "use_count": len(rows),
            "last_used": rows[-1]["used_at"] if rows else None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Stories bound to evidence.")
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("stranded")
    draft = commands.add_parser("draft")
    draft.add_argument("--content", required=True, type=Path)
    draft.add_argument("--story")
    approve = commands.add_parser("approve")
    approve.add_argument("--version", required=True)
    approve.add_argument("--content-sha256", required=True)
    check = commands.add_parser("selectable")
    check.add_argument("--version", required=True)
    carry = commands.add_parser("prepare-successor")
    carry.add_argument("--version", required=True)
    args = parser.parse_args()

    connection = candidate_core.connect(args.db)
    initialize(connection)
    if args.command == "init":
        result: Any = {"status": "initialized", "db": str(args.db)}
    elif args.command == "stranded":
        result = stranded(connection)
    elif args.command == "draft":
        result = draft_version(connection,
                               json.loads(args.content.read_text(encoding="utf-8")),
                               story_id=args.story)
    elif args.command == "approve":
        result = approve_version(connection, args.version, args.content_sha256)
    elif args.command == "selectable":
        result = selectable(connection, args.version)
    else:
        result = prepare_successor(connection, args.version)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
