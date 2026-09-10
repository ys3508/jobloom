#!/usr/bin/env python3
"""Proposing an application answer from a story, through the contracts that already exist.

This is the step where a story could stop being an interview aid and start putting words in
an employer's form, so almost all of it is about what must happen *before* a story is allowed
anywhere near the question.

There is no second answer store here and no second sensitive-question taxonomy. The
disposition comes from `field_policy`, the stop boundary from `pre_submit_core`, the question
meaning and the exact-reuse decision from `answer_library`, and an approved draft lands in the
AnswerLibrary through `answer_library.add_answer` like any other answer. What this module adds
is the ordering between them, and one new thing: when a known, narrative-safe question has no
reusable answer, it offers the user a choice of *their own approved stories* rather than
composing a sentence.

The order of the gates is by consequence, not by likelihood:

1. anything on the existing mandatory-pause list stops the page;
2. `field_policy` decides which authority may answer at all — `always_manual`, `unsupported`,
   `material` and `fact` all produce no draft;
3. the AnswerLibrary is asked for an exact, fresh, scope-valid, authorized answer, with no
   model involved; an unknown or conflicting question form pauses;
4. a question whose domain rule fired is *supported but sensitive*: exact reuse or nothing.
   A story may not compose one;
5. only then, for an ordinary narrative question, are stories retrieved — and the user picks.

**A draft is not an answer.** It is written down with everything needed to judge it — the
application, the employer, the canonical meaning, the story version, the exact evidence
references, the snapshot, and a content hash — and it becomes reusable only when the user
approves that exact hash with a scope and an expiry. Approving it authorizes reuse and
nothing else: not a submit, not a Next, not another form whose meaning merely looks similar.

**Which competency a question tests is not inferred here.** A model deciding that would sit
in front of the evidence gate, one step removed. The caller supplies it, and a question with
no reviewed competency pauses with `competency_not_mapped` rather than being guessed at.
"""

from __future__ import annotations

import argparse
import hashlib
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

import answer_library  # noqa: E402
import candidate_core  # noqa: E402
import field_policy  # noqa: E402
import pre_submit_core  # noqa: E402
import story_core  # noqa: E402
from _common import require_table  # noqa: E402
from evidence_matcher import EVIDENCE_ORDER  # noqa: E402

NONE_OF_THESE = "none_of_these"

# Part of the answer, not a note beside it. Fixed wording rather than composed: the point is
# that whoever reuses the text reads the qualification, and reviewing it once is only
# meaningful if it cannot come out differently the next time.
ADJACENT_QUALIFIER = ("(Adjacent experience: this draws on transferable rather than direct "
                      "evidence of {competency}.)")
MAX_OPTIONS = 3

DRAFT = "draft"
APPROVED = "approved"
DISCARDED = "discarded"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def initialize(connection: sqlite3.Connection) -> None:
    story_core.initialize(connection)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS question_form_competencies (
            canonical_id TEXT NOT NULL,
            competency TEXT NOT NULL,
            reviewed_by TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            PRIMARY KEY (canonical_id, competency)
        );

        CREATE TABLE IF NOT EXISTS story_answer_drafts (
            draft_id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            employer TEXT,
            canonical_id TEXT NOT NULL,
            normalized_question TEXT NOT NULL,
            competency TEXT NOT NULL,
            question_form_sha256 TEXT NOT NULL,
            story_version_id TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL,
            dependent_fact_ids_json TEXT NOT NULL,
            candidate_snapshot_sha256 TEXT NOT NULL,
            evidence_class TEXT NOT NULL,
            answer_text TEXT NOT NULL,
            bridge TEXT,
            content_sha256 TEXT NOT NULL,
            auto_fill_ready INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            approved_at TEXT,
            approved_answer_id TEXT,
            FOREIGN KEY (story_version_id) REFERENCES story_versions(version_id)
        );
        CREATE INDEX IF NOT EXISTS story_answer_drafts_application_idx
            ON story_answer_drafts(application_id, status);
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(story_answer_drafts)")}
    if "question_form_sha256" not in columns:
        # An existing drafts table predates the lock. The column is added empty rather than
        # backfilled with today's digest, because today's digest is not evidence about what
        # the form said when the draft was written — and pretending otherwise is the whole
        # thing the lock is for. `approve_draft` refuses such a draft by name.
        connection.execute(
            "ALTER TABLE story_answer_drafts ADD COLUMN question_form_sha256 TEXT "
            "NOT NULL DEFAULT ''")
    connection.commit()


def _outcome(decision: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"decision": decision, "reason": reason, "auto_fill_ready": False, **extra}


def record_question_competency(connection: sqlite3.Connection, canonical_id: str,
                               competency: str, actor: str = "user",
                               at: datetime | None = None) -> dict[str, Any]:
    """Review that a canonical question meaning tests a capability.

    This is the artifact that used to be a call argument. A caller naming the competency was
    naming which of the user's stories it wanted, one step in front of the evidence gate; and
    a model naming it would be doing the same thing less visibly. A meaning with no reviewed
    competency still pauses — that is the point of writing it down rather than inferring it.
    """
    require_table(connection, "question_form_competencies")
    if story_core._capability_layer(competency) != "SKILL":
        raise ValueError(f"{competency} is not a reviewed SKILL capability")
    if not isinstance(canonical_id, str) or not canonical_id.strip():
        raise ValueError("a mapping must name the canonical question meaning")
    timestamp = (at or now_utc()).isoformat()
    connection.execute(
        "INSERT OR REPLACE INTO question_form_competencies (canonical_id, competency, "
        "reviewed_by, reviewed_at) VALUES (?, ?, ?, ?)",
        (canonical_id, competency, actor, timestamp))
    connection.commit()
    return {"canonical_id": canonical_id, "competency": competency, "reviewed_by": actor,
            "reviewed_at": timestamp}


def reviewed_competencies(connection: sqlite3.Connection, canonical_id: str) -> list[str]:
    require_table(connection, "question_form_competencies")
    return [row["competency"] for row in connection.execute(
        "SELECT competency FROM question_form_competencies WHERE canonical_id=? "
        "ORDER BY competency", (canonical_id,))]


def question_form_digest(connection: sqlite3.Connection, question: str) -> str:
    """A hash of every registered form for this question, as it stands right now.

    The draft records which mapping authorized its meaning, not merely what the meaning was.
    A form later remapped, unverified, or joined by a second canonical id is a different
    answer to "what is this question", and a draft approved under the old one would carry a
    provenance that no longer holds.
    """
    rows = connection.execute(
        "SELECT normalized_question, canonical_id, match_level, verified_by_user, created_at "
        "FROM question_forms WHERE normalized_question=? ORDER BY canonical_id",
        (answer_library.normalize_question(question),)).fetchall()
    return hashlib.sha256(canonical_json([dict(row) for row in rows]).encode("utf-8")).hexdigest()


def _rendered(connection: sqlite3.Connection, version_id: str) -> str:
    """The story as approved, in STAR order. Assembled, never composed.

    Every span of an approved version is either a bound claim or a reviewed framing span, so
    reading it back verbatim cannot introduce an assertion nobody approved. Anything that
    rewrote it here would be generating text outside the binding gate.
    """
    version = connection.execute(
        "SELECT star_json, earned_secret FROM story_versions WHERE version_id=?",
        (version_id,)).fetchone()
    star = json.loads(version["star_json"])
    parts = [star[part] for part in story_core.STAR_PARTS]
    if version["earned_secret"]:
        parts.append(version["earned_secret"])
    return " ".join(part.strip() for part in parts)


def propose(connection: sqlite3.Connection, *, application_id: str, field_id: str,
            question: str, control: str, source_kind: str | None = None,
            authorization_id: str | None = None, context: dict[str, Any] | None = None,
            legal_items: list[str] | None = None, chosen_version_id: str | None = None,
            at: datetime | None = None) -> dict[str, Any]:
    """Decide what may answer one observed field, and draft only where that is a story.

    The application names itself and nothing else. Employer identity and the competency the
    question tests are both resolved here, from rows somebody registered, because either one
    accepted as an argument is a way to widen what a caller may reach.
    """
    initialize(connection)
    at = at or now_utc()
    identity = story_core.application_identity(connection, application_id)
    if not identity:
        return _outcome("pause", "application_unknown")
    employer = identity["employer"]
    context = dict(context or {})
    context["application_id"] = application_id
    if employer:
        context["company"] = employer

    stopped = sorted(set(legal_items or []) & pre_submit_core.MANDATORY_PAUSES)
    if stopped:
        # Composing the existing boundary rather than restating it: this module must never be
        # able to weaken a stop into something a story is allowed to answer.
        return _outcome("pause", "stop_boundary", stop_items=stopped)
    if control in pre_submit_core.MANDATORY_PAUSES:
        return _outcome("pause", "stop_boundary", stop_items=[control])

    disposition, domain, family = field_policy.disposition(
        field_id, question, control, source_kind)
    if disposition == "always_manual":
        return _outcome("manual", "always_manual", domain=domain, family=family)
    if disposition == "unsupported":
        return _outcome("pause", "unsupported_field")
    if disposition == "material":
        return _outcome("manual", "material_field")
    if disposition == "fact":
        return _outcome("manual", "fact_field", domain=domain)

    matched = answer_library.match_answer(connection, question, context, authorization_id, at)
    if matched["decision"] == "use":
        # The cheapest rung, and the one that must stay free of a model: an exact question
        # form, a fresh scope-valid answer, and an independently current authorization.
        return {"decision": "reuse", "reason": matched["reason"],
                "canonical_id": matched["canonical_id"], "answer_id": matched["answer_id"],
                "answer": matched["answer"], "auto_fill_ready": matched["auto_fill_ready"],
                "channel_a_current": matched["channel_a_current"],
                "channel_b_fresh": matched["channel_b_fresh"]}
    if matched["decision"] == "conflict":
        return _outcome("pause", matched["reason"], canonical_id=matched.get("canonical_id"))
    if matched.get("reason") == "new_question":
        return _outcome("pause", "unknown_question_form")
    canonical_id = matched.get("canonical_id")
    if matched.get("answer_id"):
        # An approved answer for this meaning exists and merely cannot be filled
        # automatically — it needs a human, a per-application recheck, or a legal review. The
        # user reviews the answer they already approved. Composing a second one from a story
        # here would quietly fork one canonical meaning into two texts.
        return _outcome("review_existing_answer", matched["reason"],
                        canonical_id=canonical_id, answer_id=matched["answer_id"])
    if domain:
        # Supported, and sensitive. The question has a reviewed meaning, but nothing composed
        # may answer it: either an exact approved answer was current above, or the user does.
        return _outcome("pause", "sensitive_requires_exact_answer",
                        canonical_id=canonical_id, domain=domain, family=family,
                        answer_reason=matched.get("reason"))
    competencies = reviewed_competencies(connection, canonical_id)
    if not competencies:
        return _outcome("pause", "competency_not_mapped", canonical_id=canonical_id)

    mapped = story_core.map_stories(connection, competencies, application_id=application_id)
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for competency in competencies:
        for row in mapped[competency]["stories"]:
            if row["version_id"] in seen:
                continue
            seen.add(row["version_id"])
            merged.append({**row, "competency": competency})
    merged.sort(key=lambda row: (-story_core.FIT_ORDER[row["fit"]],
                                 -EVIDENCE_ORDER[row["evidence_class"]],
                                 row["use_count"], row["version_id"]))
    options = merged[:MAX_OPTIONS]
    if not options:
        return _outcome("gap", "no_story_covers_this", canonical_id=canonical_id,
                        competencies=competencies)
    if chosen_version_id is None:
        return {"decision": "choose", "reason": "user_chooses_the_story",
                "canonical_id": canonical_id, "competencies": competencies,
                "auto_fill_ready": False, "allow_none": NONE_OF_THESE,
                "options": [{"version_id": row["version_id"], "story_id": row["story_id"],
                             "title": row["title"], "fit": row["fit"],
                             "competency": row["competency"],
                             "evidence_class": row["evidence_class"],
                             "why": row["why"], "claim_ids": row["claim_ids"],
                             "limitation": row.get("limitation")}
                            for row in options]}
    if chosen_version_id == NONE_OF_THESE:
        return _outcome("gap", "user_chose_none", canonical_id=canonical_id,
                        competencies=competencies)
    chosen = next((row for row in options if row["version_id"] == chosen_version_id), None)
    if not chosen:
        # Being offered is what makes a version choosable. A version named from outside the
        # offer has not been through the eligibility and confidentiality gates for this field.
        return _outcome("pause", "choice_not_offered", canonical_id=canonical_id)

    return _draft(connection, application_id=application_id, employer=employer,
                  canonical_id=canonical_id, question=question,
                  competency=chosen["competency"], chosen=chosen, at=at)


def _draft(connection: sqlite3.Connection, *, application_id: str, employer: str | None,
           canonical_id: str, question: str, competency: str, chosen: dict[str, Any],
           at: datetime) -> dict[str, Any]:
    version_id = chosen["version_id"]
    version = connection.execute(
        "SELECT candidate_snapshot_sha256 FROM story_versions WHERE version_id=?",
        (version_id,)).fetchone()
    snapshot = version["candidate_snapshot_sha256"]
    # Two different questions, and conflating them was a defect.
    #
    # *What evidence supports this capability* is binding-scoped: it decides which band the
    # story was retrieved in, and reading it over the whole version is what let a transferable
    # capability be promoted by an unrelated direct claim.
    #
    # *What does this text assert, and on what* is text-scoped. The answer is the whole
    # rendered version, so provenance and invalidation must cover **every** claim in it.
    # Narrowing these to the binding left the text asserting things whose evidence was not
    # recorded and whose loss would not invalidate the answer.
    bound = sorted(set(chosen["claim_ids"]))
    refs, rendered_classes = [], []
    for claim in connection.execute(
            "SELECT claim_id, evidence_refs_json, evidence_class FROM story_claims "
            "WHERE version_id=? ORDER BY claim_id", (version_id,)):
        refs.extend(json.loads(claim["evidence_refs_json"]))
        rendered_classes.append(claim["evidence_class"])
    refs = sorted(set(refs))
    index = story_core.evidence_index(connection, snapshot)
    fact_ids = sorted({index[ref]["fact_id"] for ref in refs if ref in index})
    binding_class = chosen["evidence_class"]
    rendered_floor = min(rendered_classes, key=lambda name: EVIDENCE_ORDER[name])
    text = _rendered(connection, version_id)

    # The qualifier goes into the answer itself, not beside it. A note stored next to the
    # text is not read by whoever reuses the text: an answer approved on transferable
    # evidence would come back on the next form reading exactly like a direct one, which is
    # `transferable never upgrades` defeated by the reuse path rather than by the rules.
    qualified = (binding_class == story_core.TRANSFERABLE
                 or rendered_floor not in story_core.COVERING)
    bridge = None
    if qualified:
        bridge = ("This answer rests on adjacent experience rather than direct evidence of "
                  f"{competency}. Say so rather than letting it read as direct.")
        text = f"{text} {ADJACENT_QUALIFIER.format(competency=competency)}"
    auto_fill_ready = False

    form_digest = question_form_digest(connection, question)
    payload = {"application_id": application_id, "employer": employer,
               "canonical_id": canonical_id, "competency": competency,
               "question_form_sha256": form_digest,
               "story_version_id": version_id, "evidence_refs": refs,
               "candidate_snapshot_sha256": snapshot, "evidence_class": binding_class,
               "rendered_evidence_floor": rendered_floor, "binding_claim_ids": bound,
               "answer_text": text, "bridge": bridge}
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    draft_id = f"AD-{uuid.uuid4().hex[:12]}"
    connection.execute(
        "INSERT INTO story_answer_drafts (draft_id, application_id, employer, canonical_id, "
        "normalized_question, competency, question_form_sha256, story_version_id, "
        "evidence_refs_json, dependent_fact_ids_json, candidate_snapshot_sha256, "
        "evidence_class, answer_text, bridge, content_sha256, auto_fill_ready, status, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (draft_id, application_id, employer, canonical_id,
         answer_library.normalize_question(question), competency, form_digest, version_id,
         canonical_json(refs), canonical_json(fact_ids), snapshot, binding_class, text,
         bridge, digest, int(auto_fill_ready), DRAFT, at.isoformat()))
    story_core._event(connection, chosen["story_id"], version_id, "system",
                      "answer_drafted", "story_chosen_by_user",
                      {"draft_id": draft_id, "canonical_id": canonical_id,
                       "evidence_class": binding_class, "application_id": application_id},
                      at)
    connection.commit()
    return {"decision": "drafted", "reason": "drafted_from_chosen_story",
            "draft_id": draft_id, "canonical_id": canonical_id,
            "story_version_id": version_id, "evidence_class": binding_class,
            "rendered_evidence_floor": rendered_floor, "qualified": qualified,
            "evidence_refs": refs, "dependent_fact_ids": fact_ids,
            "question_form_sha256": form_digest, "binding_claim_ids": bound,
            "candidate_snapshot_sha256": snapshot, "answer_text": text, "bridge": bridge,
            "content_sha256": digest, "auto_fill_ready": auto_fill_ready,
            "next_step": "the user approves this exact content with a scope and an expiry"}


def approve_draft(connection: sqlite3.Connection, draft_id: str, content_sha256: str, *,
                  scope: dict[str, Any], validity_class: str,
                  answer_type: str = "open_text_template",
                  expires_at: str | None = None, review_after: str | None = None,
                  auto_fill_allowed: bool = False, actor: str = "user",
                  at: datetime | None = None) -> dict[str, Any]:
    """Turn one exact draft into an AnswerLibrary entry. Authorizes reuse and nothing else.

    The entry carries the facts behind the story's evidence as `dependent_fact_ids`, so the
    invalidation the library already runs — a changed fact, a flipped visa status — reaches an
    answer that came from a story exactly as it reaches any other.
    """
    require_table(connection, "story_answer_drafts")
    row = connection.execute(
        "SELECT * FROM story_answer_drafts WHERE draft_id=?", (draft_id,)).fetchone()
    if not row:
        raise ValueError("answer draft not found")
    if row["status"] != DRAFT:
        raise ValueError("that draft is no longer awaiting approval")
    if row["content_sha256"] != content_sha256:
        raise ValueError("the approved content hash does not match this draft")
    check = story_core.selectable(connection, row["story_version_id"])
    if not check["selectable"]:
        raise ValueError(
            f"the story this was drafted from is no longer usable: {check['reasons'][0]}")
    if not row["question_form_sha256"]:
        raise ValueError("this draft predates the question-form lock and cannot say which "
                         "meaning it was written under")
    current_form = connection.execute(
        "SELECT normalized_question, canonical_id, match_level, verified_by_user, created_at "
        "FROM question_forms WHERE normalized_question=? ORDER BY canonical_id",
        (row["normalized_question"],)).fetchall()
    if hashlib.sha256(canonical_json([dict(form) for form in current_form]).encode(
            "utf-8")).hexdigest() != row["question_form_sha256"]:
        # The mapping that said what this question means is not the one the draft was written
        # under. Approving would attach the answer to a meaning nobody reviewed it against.
        raise ValueError("the question form changed since this draft was written")
    if row["bridge"] and auto_fill_allowed:
        raise ValueError("an answer resting on transferable evidence is not auto-fill on "
                         "first approval")

    at = at or now_utc()
    answer_id = f"ans-story-{uuid.uuid4().hex[:12]}"
    answer_library.add_answer(connection, {
        "answer_id": answer_id, "canonical_id": row["canonical_id"],
        "canonical_meaning": row["canonical_id"],
        "answer": row["answer_text"], "answer_type": answer_type,
        # Not `derived_answer`: a person read this exact text and said yes to it. The library
        # refuses model inference as a source outright, and nothing here needs an exception.
        "source_type": "user_confirmed", "source_ref": row["story_version_id"],
        "confirmation_status": "confirmed", "confirmed_at": at.isoformat(),
        "validity_class": validity_class, "expires_at": expires_at,
        "review_after": review_after, "scope": scope,
        "auto_fill_allowed": bool(auto_fill_allowed), "auto_submit_allowed": False,
        "dependent_fact_ids": json.loads(row["dependent_fact_ids_json"]),
        "invalidation_triggers": ["candidate_snapshot_changed"],
    })
    connection.execute(
        "UPDATE story_answer_drafts SET status=?, approved_at=?, approved_answer_id=? "
        "WHERE draft_id=? AND status=?",
        (APPROVED, at.isoformat(), answer_id, draft_id, DRAFT))
    story_core._event(connection, check["story_id"], row["story_version_id"], actor,
                      "answer_approved", "user_approved_exact_draft",
                      {"draft_id": draft_id, "answer_id": answer_id,
                       "canonical_id": row["canonical_id"]}, at)
    connection.commit()
    return {"draft_id": draft_id, "answer_id": answer_id, "status": APPROVED,
            "auto_fill_allowed": bool(auto_fill_allowed), "auto_submit_allowed": False,
            "authorizes": "reuse_of_this_answer_only",
            "does_not_authorize": ["submit", "next_or_continue", "another_question_meaning"]}


def discard_draft(connection: sqlite3.Connection, draft_id: str, reason: str,
                  at: datetime | None = None) -> dict[str, Any]:
    require_table(connection, "story_answer_drafts")
    connection.execute(
        "UPDATE story_answer_drafts SET status=? WHERE draft_id=? AND status=?",
        (DISCARDED, draft_id, DRAFT))
    connection.commit()
    return {"draft_id": draft_id, "status": DISCARDED, "reason": reason}


def main() -> None:
    parser = argparse.ArgumentParser(description="Propose an application answer from a story.")
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    ask = commands.add_parser("propose")
    ask.add_argument("--application", required=True)
    ask.add_argument("--field-id", required=True)
    ask.add_argument("--question", required=True)
    ask.add_argument("--control", required=True)
    ask.add_argument("--employer")
    ask.add_argument("--competency")
    ask.add_argument("--authorization")
    ask.add_argument("--choose")
    args = parser.parse_args()

    connection = candidate_core.connect(args.db)
    initialize(connection)
    if args.command == "init":
        result: Any = {"status": "initialized", "db": str(args.db)}
    else:
        result = propose(connection, application_id=args.application, field_id=args.field_id,
                         question=args.question, control=args.control,
                         employer=args.employer, competency=args.competency,
                         authorization_id=args.authorization,
                         chosen_version_id=args.choose)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
