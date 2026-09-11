#!/usr/bin/env python3
"""What a real application form asks, sorted into who may answer it.

Everything needed to answer a form already exists as a command: `field_policy` says which
authority may speak for a field, `answer_library` says whether a confirmed answer covers the
question, `evaluate_job` says whether the opening is one to spend an evening on at all. What
has never existed is the step where a person, holding an employer's actual form, finds out
*where this application is stuck* before spending the evening on it.

This is that step, and it decides nothing new. Every verdict below is returned by the module
that already owned it; this file chooses the order they are asked in and gives each question
one lane:

  profile_ready               a locked CandidateFact in the snapshot this application's
                              materials are bound to already means this question
  answer_ready                a confirmed AnswerEntry covers it and a live standing
                              authorization is in scope
  answer_needs_authorization  the answer exists; nothing currently authorises filling it
  you_answer                  nothing covers it, or something covers it and is not usable —
                              the reason says which
  manual_only                 a domain rule fired: legal, compensation, EEO, conflict, referral
  narrative_gap               unmapped, reads like a story question, and the StoryBank has
                              nothing approved

**The order is by consequence, not by likelihood**, the same order `story_answers` uses. A
domain rule is asked first and wins outright, because the asymmetry `field_policy` is built on
only holds in one direction: a pattern hit may add caution, never remove it. A question that
matches both `eeo_race` and every narrative cue in this file is `manual_only`, and no amount of
story-shaped wording moves it.

**An answer existing is not permission to fill it.** The two were one lane in the first version
and that lane was wrong: it showed a green tick for a question whose standing authorization had
expired, which is exactly the moment a person needs to be told. `answer_needs_authorization`
is a blocking lane.

**Nothing here writes.** `answer_library.inspect_answer` is the read-only sibling of
`match_answer`: same decision, no audit event, no commit. The audited path stays where a value
actually reaches a form.

**The split is proposed, never trusted.** Cutting pasted text into questions is a guess about
someone else's markup, and `350dd4f` is the record of what happens when a splitter is allowed
to conclude: `(Python or R) and SQL` became two requirements and one class assignment met one
of them. So `split_questions` returns its cuts with the exact source span each came from, the
page shows them, and nothing is classified until the person says the cuts are right.

**A narrative hint is not a claim about the candidate.** `narrative_gap` changes which help is
offered — an ordering of the user's own facts to write from — and nothing else. `related_material`
ranks by shared content words, returns the words that put each fact there, and says how many
facts it looked at, so a thin list can be told apart from an empty bank. No sentence is
composed here, and a fact is never presented as an answer.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import answer_library  # noqa: E402
import candidate_profile  # noqa: E402
import evaluate_job  # noqa: E402
import field_policy  # noqa: E402

# Six, not four. The first version collapsed "the profile holds this", "an answer exists but
# nothing authorises filling it" and "an answer exists and something does" into one green lane
# called ready, which told a person a form was handled when what it meant was that a value
# existed somewhere. The three differ in what the person has to do next, which is the only
# thing a preflight is for.
LANES = ("profile_ready", "answer_ready", "answer_needs_authorization",
         "you_answer", "manual_only", "narrative_gap")
# What still needs the person before this application can be finished. An answer waiting on a
# standing authorization counts: nothing fills without one.
BLOCKING_LANES = ("answer_needs_authorization", "you_answer", "manual_only", "narrative_gap")

# Cues that a question wants a story rather than a value. Deliberately narrow: this only ever
# changes which help is offered, so a miss costs a person one glance at their own facts and a
# false positive costs nothing at all. It is checked last, after the domain rule.
NARRATIVE_CUES = (
    re.compile(r"\btell us about\b|\bdescribe a\b|\bgive an example\b|\bwalk us through\b", re.I),
    re.compile(r"\ba time (?:when|you)\b|\bhow (?:did|would) you\b|\bwhy (?:do you|are you)\b", re.I),
    re.compile(r"\bwhat (?:interests|excites|draws) you\b|\byour (?:experience|approach) (?:with|to)\b", re.I),
)
# A question long enough to be prose and carrying no cue is still probably a story box. Set
# from the reviewed Lever fixture, where no value question ran past 120 characters.
NARRATIVE_LENGTH = 120

# Lines that are headings, instructions or legal boilerplate rather than questions. A line
# dropped here is still returned, marked `skipped`, because a splitter that silently discards
# is the same defect as one that silently concludes.
NOT_A_QUESTION = re.compile(
    r"^\s*\*?\s*(?:required|optional|please (?:complete|review)|section \w+|page \d+"
    r"|application form|apply (?:now|for)|step \d+|\*)\s*$", re.I)


def split_questions(text: str) -> list[dict[str, Any]]:
    """Propose a cut of pasted form text into questions, with the span each came from.

    Returns every segment, including the ones it would not treat as a question, so the person
    confirming the split can see what was dropped rather than discovering it missing later.
    """
    if not isinstance(text, str):
        raise ValueError("pasted text must be a string")
    if len(text) > 100_000:
        raise ValueError("pasted text is too large to split")
    segments: list[dict[str, Any]] = []
    offset = 0
    for raw in text.split("\n"):
        start, offset = offset, offset + len(raw) + 1
        line = raw.strip()
        if not line:
            continue
        begin = start + (len(raw) - len(raw.lstrip()))
        segment = {
            "text": line,
            "span": [begin, begin + len(line)],
            "skipped": bool(NOT_A_QUESTION.match(line)) or len(line) < 3,
        }
        segments.append(segment)
    return segments


def is_narrative(question: str) -> bool:
    """A hint that a question wants a story. Never suppresses a caution; see the module note."""
    if any(cue.search(question) for cue in NARRATIVE_CUES):
        return True
    return len(question) > NARRATIVE_LENGTH


# Words that say nothing about which of a person's facts a question is reaching for. Short and
# closed on purpose: this list only decides what is *offered as material*, so a word missing
# from it costs a slightly worse ordering and never a hidden fact.
QUESTION_STOPWORDS = frozenset("""
a an and are as at be been but by can could describe did do does for from give had has have
how in into is it its me my of on or our please provide say tell that the their them then
there these they this those time to told up us use used was we were what when where which
who why will with would you your yourself example instance about
""".split())
# How many facts a narrative question is offered. A page of twenty is a page nobody reads.
MATERIAL_LIMIT = 6


def related_material(question: str, facts: list[dict[str, Any]],
                     limit: int = MATERIAL_LIMIT) -> list[dict[str, Any]]:
    """Facts worth looking at while writing an answer, ranked by shared content words.

    Explicitly **not** `evidence_matcher.related_facts`, which the first version called here
    and which returned nothing every time. That function asks whether a fact covers a
    *requirement* and demands every token of it be present — the right rule for "SQL" and an
    impossible one for "Please describe a time when you built a clinical trial database", where
    the fact would have to contain the words "please" and "describe". Using it here did not
    produce a wrong answer; it produced a permanently empty column that read as "you have no
    relevant experience", which is worse.

    What this returns is an ordering of the user's own facts, and it concludes nothing: not
    that a fact answers the question, not that it is sufficient, not that it is the best one.
    Overlap is reported alongside each fact so the ordering can be disagreed with, and the
    count of what was left out is returned so a short list never reads as an empty bank.
    """
    wanted = {token for token in re.findall(r"[\w']+", question.casefold())
              if len(token) > 2 and token not in QUESTION_STOPWORDS}
    if not wanted:
        return []
    scored = []
    for fact in facts:
        if fact.get("status") not in {"confirmed", "locked"}:
            continue
        surface = " ".join(str(part) for part in
                           [fact.get("value", ""), *(fact.get("keywords") or [])])
        available = set(re.findall(r"[\w']+", surface.casefold()))
        overlap = sorted(wanted & available)
        if overlap:
            scored.append({"id": fact["id"], "overlap": overlap})
    scored.sort(key=lambda item: (-len(item["overlap"]), item["id"]))
    return scored[:limit]


def snapshot_facts(connection: sqlite3.Connection, snapshot_sha256: str) -> list[dict[str, Any]]:
    """The snapshot's own facts, in the shape `evidence_matcher` reads.

    From the rows, not from the candidate document: a document on disk is whatever was last
    written beside the database, and the facts a form may draw on are the ones the registered
    snapshot holds. `resolve_canonical_fact` reads the same rows, so the two paths cannot come
    to disagree about what the profile says.
    """
    rows = connection.execute(
        "SELECT fact_id, value_json, evidence_strength, status, expires_at "
        "FROM candidate_facts WHERE content_sha256=?", (snapshot_sha256,)).fetchall()
    facts = []
    for row in rows:
        try:
            value = json.loads(row["value_json"])
        except (TypeError, ValueError):
            continue
        facts.append({"id": row["fact_id"], "value": value,
                      "evidence_strength": row["evidence_strength"],
                      "status": row["status"], "expires_at": row["expires_at"]})
    return facts


def application_snapshot(connection: sqlite3.Connection,
                         application_id: str) -> tuple[str | None, str | None]:
    """The CandidateSnapshot this application's live material lock is bound to, or why none.

    The join is the one `archive_core` and `application_core` already use to resolve a fact
    source: a lock that has not been invalidated, through the resume version it locked, to the
    snapshot that resume was approved against — and that snapshot must still be the active,
    user-registered one. Registering a profile invalidates the locks bound to the old snapshot,
    so a lock that survives and an active snapshot are the same fact stated twice; asking for
    both is what makes a stale one visible instead of silently resolving against whatever is
    active now.
    """
    row = connection.execute("""
        SELECT cs.content_sha256
        FROM material_locks ml
        JOIN resume_versions rv ON rv.version_id = ml.resume_version_id
        JOIN candidate_snapshots cs ON cs.content_sha256 = rv.candidate_profile_sha256
        WHERE ml.application_id=? AND ml.invalidated_at IS NULL
          AND cs.status='active' AND cs.registered_by='user'
    """, (application_id,)).fetchone()
    if not row:
        return None, "application_materials_not_locked_to_active_profile"
    return row["content_sha256"], None


def _lane(question: str, lane: str, reason: str | None, **extra: Any) -> dict[str, Any]:
    """One shape for every lane, so a caller cannot read a key that only some lanes carry."""
    result = {
        "question": question,
        "lane": lane,
        "reason": reason,
        "source": None,
        "canonical_id": None,
        "domain": None,
        "family": None,
        "narrative_hint": False,
        "answer_exists": False,
        "related_overlap": {},
        "facts_considered": 0,
        # Reported, never granted. This surface fills nothing and submits nothing, and the
        # second of these is stated on every lane rather than omitted on most of them.
        "auto_fill_ready": False,
        "auto_submit_ready": False,
        "related_fact_ids": [],
    }
    result.update(extra)
    result["lane"] = lane
    result["reason"] = reason
    return result


def classify_question(connection: sqlite3.Connection, question: str, *,
                      snapshot_sha256: str | None, context: dict[str, Any],
                      facts: list[dict[str, Any]] | None = None,
                      at: Any = None) -> dict[str, Any]:
    """One question, one lane, and the reason the lane was chosen.

    The order is by consequence and each step may only narrow what follows it:

    1. `field_policy` decides which authority may speak at all, and its refusals are final. A
       question in a legal, immigration, compensation, EEO, conflict or referral domain is
       `manual_only` however it is worded and whatever a later step would have said, because
       the asymmetry that module is built on holds in one direction only.
    2. The reviewed meaning of the exact question, from `answer_library.canonical_meaning`. No
       similarity, no invention: an unreviewed question is `new_question` and a contested one
       pauses.
    3. A meaning that is profile data resolves through `candidate_profile.resolve_canonical_fact`
       against the snapshot this application's materials are locked to. Its reasons —
       missing, ambiguous, not locked, expired, forbidden — are passed through as they are.
    4. A meaning that is not profile data goes to `answer_library.inspect_answer`, which reads
       and never writes, and which separates *an answer exists* from *something currently
       authorises filling it*.
    5. Only an unmapped question may reach the StoryBank hint, and only related facts come back
       with it — material to write from, never a drafted answer.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("a question must be a non-empty string")
    question = question.strip()
    facts = facts or []

    # (1) The field identifier is the page's, and this path has no page. Passing the question
    # as both is safe in exactly one direction: `field_policy` reads them to add caution.
    disposition, domain, family = field_policy.disposition(
        field_id=question, question=question, control="text", source_kind=None)
    if disposition == "always_manual":
        return _lane(question, "manual_only", f"domain:{domain}", domain=domain, family=family)
    if disposition == "unsupported":
        return _lane(question, "you_answer", "unsupported_source", domain=domain, family=family)

    # (2)
    canonical_id, mapping_reason = answer_library.canonical_meaning(connection, question)
    if canonical_id is None and mapping_reason == "question_mapping_conflict":
        return _lane(question, "you_answer", mapping_reason, domain=domain, family=family)

    if canonical_id is not None:
        # (2a) The same domain rules, now reading the reviewed meaning instead of the page's
        # wording. A form whose label trips nothing — "Which of these describes you" — can
        # still carry a meaning of `eeo.race`, and the meaning is the more trustworthy of the
        # two inputs because a person reviewed it. This is the asymmetry again and not a second
        # taxonomy: `field_policy` owns the rule, and it is being shown better evidence.
        #
        # `candidate_profile.FORBIDDEN_MEANINGS` is deliberately *not* the test here. It means
        # "never a profile field", and half of it — `discovery_source`, `citizenship_status`,
        # the prior-employment pair — is exactly what the AnswerLibrary is for.
        meaning_disposition, meaning_domain, meaning_family = field_policy.disposition(
            field_id=canonical_id, question=canonical_id, control="text", source_kind=None)
        if meaning_disposition == "always_manual":
            return _lane(question, "manual_only", f"meaning:{meaning_domain}",
                         canonical_id=canonical_id, domain=meaning_domain,
                         family=meaning_family)
        if canonical_id in candidate_profile.PROFILE_V1:
            if not snapshot_sha256:
                return _lane(question, "you_answer",
                             "application_materials_not_locked_to_active_profile",
                             canonical_id=canonical_id, domain=domain, family=family)
            fact, reason = candidate_profile.resolve_canonical_fact(
                connection, canonical_id, snapshot_sha256, at)
            if fact is None:
                return _lane(question, "you_answer", reason, canonical_id=canonical_id,
                             source="profile", domain=domain, family=family)
            # A locked fact in the snapshot this application is bound to. The value is not
            # returned: the preflight reports that the profile answers this, and the fill path
            # is what resolves a value into a form.
            return _lane(question, "profile_ready", "profile_fact_locked",
                         canonical_id=canonical_id, source="profile",
                         domain=domain, family=family, answer_exists=True,
                         auto_fill_ready=True)

        # (4)
        inspection = answer_library.inspect_answer(connection, question, context, at)
        if inspection.get("answer_exists"):
            lane = "answer_ready" if inspection["auto_fill_ready"] else "answer_needs_authorization"
            return _lane(question, lane, inspection.get("reason"), canonical_id=canonical_id,
                         source="answer_library", domain=domain, family=family,
                         answer_exists=True, auto_fill_ready=inspection["auto_fill_ready"],
                         authorization_reason=inspection.get("authorization_reason"))
        return _lane(question, "you_answer", inspection.get("reason"),
                     canonical_id=canonical_id, source="answer_library",
                     domain=domain, family=family)

    # (5) Unmapped. Only here may a question be read as narrative.
    narrative = is_narrative(question)
    if not narrative:
        return _lane(question, "you_answer", mapping_reason, domain=domain, family=family)
    # Offered to draw on, not as an answer: an ordering of the user's own facts, with the
    # words that put each one there, and the number that did not make the list.
    material = related_material(question, facts)
    return _lane(question, "narrative_gap", mapping_reason, domain=domain, family=family,
                 narrative_hint=True,
                 related_fact_ids=[item["id"] for item in material],
                 related_overlap={item["id"]: item["overlap"] for item in material},
                 facts_considered=sum(1 for fact in facts
                                      if fact.get("status") in {"confirmed", "locked"}))


def classify(connection: sqlite3.Connection, questions: list[str], *,
             snapshot_sha256: str | None, context: dict[str, Any],
             facts: list[dict[str, Any]] | None = None,
             at: Any = None) -> dict[str, Any]:
    """Every question sorted, plus the counts a person reads before deciding to continue."""
    if not isinstance(questions, list) or not questions:
        raise ValueError("classification needs at least one question")
    if len(questions) > 250:
        raise ValueError("a form page carries at most 250 questions")
    results = [classify_question(connection, question, snapshot_sha256=snapshot_sha256,
                                 context=context, facts=facts, at=at)
               for question in questions]
    counts = {lane: sum(1 for item in results if item["lane"] == lane) for lane in LANES}
    return {"questions": results, "counts": counts,
            "blocking": sum(counts[lane] for lane in BLOCKING_LANES)}


def queue(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """The openings an application already exists for, newest first.

    Only applications, not every routed opening: the queue a person opens this window to work
    is the one they have already decided to apply to. Deciding *which* openings deserve an
    application is `review_queue`'s job and is not re-litigated here.
    """
    rows = connection.execute("""
        SELECT a.application_id, a.state, a.category, a.resume_version_id,
               j.job_id, j.employer, j.title, j.location, j.canonical_url
        FROM applications a JOIN jobs j ON j.job_id = a.job_id
        ORDER BY a.rowid DESC
    """).fetchall()
    return [dict(row) for row in rows]


def _job_card(connection: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT job_card_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        raise ValueError("no such job")
    return json.loads(row["job_card_json"] or "{}")


def readiness(connection: sqlite3.Connection, application_id: str,
              candidate: dict[str, Any]) -> dict[str, Any]:
    """Job, bound resume, and what `evaluate_job` already knows would stop this application.

    The knockout column is not computed here. `evaluate_job.evaluate` has owned it since the
    MVP — sponsorship, country, work arrangement, employment type, salary floor, excluded
    employer, duplicate application — and it separates what it can decide (`hard_filter_
    failures`) from what only the user can (`uncertainties`), failing closed into the second
    whenever the JobCard says `unknown`. This carries both to the screen unchanged.
    """
    row = connection.execute("""
        SELECT a.application_id, a.job_id, a.state, a.category, a.resume_version_id,
               a.submission_policy, j.employer, j.title, j.location, j.canonical_url
        FROM applications a JOIN jobs j ON j.job_id = a.job_id
        WHERE a.application_id=?
    """, (application_id,)).fetchone()
    if not row:
        raise ValueError("no such application")
    application = dict(row)
    card = _job_card(connection, application["job_id"])
    try:
        evaluation = evaluate_job.evaluate(candidate, card)
    except ValueError as error:
        # A JobCard that predates a field `evaluate_job` requires is a real state and not a
        # crash: the window says the knockout check could not run and why, and the rest of
        # the page still works.
        evaluation = {"unavailable": str(error)[:200]}
    return {
        "application": application,
        "sponsorship_statements": card.get("sponsorship_statements") or [],
        "evaluation": evaluation,
    }
