#!/usr/bin/env python3
"""Which of a posting's requirements are mandatory, which are wishes, and which are neither.

A queue that counts every stated requirement the same way cannot tell "we need R" from "R a
plus", so a posting asking for twelve nice-to-haves and two musts reads as harder than one
asking for two musts. This sorts that out, and it does it in three tiers rather than two,
because two is a lie: most requirement lines say nothing at all about their own weight.

**Measured before it was written**, over the 112 postings in the 2026-09-07 queue — 2,241
requirement lines:

| | lines |
| --- | ---: |
| under a required-ish heading | 1,307 |
| under a preferred heading | 934 |
| **under a Required heading, but the line itself says preferred** | **77** |
| under a Required heading, and the line also says required | 49 |
| saying both things in one line | 8 |
| under a Preferred heading but saying required | 1 (a boilerplate disclaimer) |

Three rules follow from those numbers, in order of consequence:

1. **The line beats the heading.** 77 lines say "preferred" under a Required heading and
   effectively one says the reverse, so a line that states its own weight is believed.
   "MBA preferred but not required" is not a must-have however the section is titled.
2. **Both cues in one line is ambiguous, not a tie to break.** "Minimum of one year of
   supervisory experience required; 3+ years preferred" states two different requirements in
   one sentence. Splitting it would be guessing which half the reader meant.
3. **A heading only decides when it is explicit.** "Requirements", "Minimum Qualifications"
   and "Must Have" say what they mean. "Qualifications" (19 postings), "About You" (10) and
   "Knowledge, Skills, and Abilities" (10) do not, and calling those must-have is an
   *upgrade* nobody wrote.

`unknown` is never quietly resolved in either direction. It is not a weaker `preferred` — it
is the tier for a requirement whose weight the posting did not state, and a candidate is not
helped by pretending the posting was clearer than it was. It is reported as its own number.

No model. Every classification is a regex over the employer's own words, and each one is
returned with the cue that fired and where in the description it was found, so a
misclassification can be looked at rather than argued about.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import posting_sections  # noqa: E402
from evidence_matcher import EVIDENCE_ORDER as _RANK  # noqa: E402
from evidence_matcher import match_requirement  # noqa: E402

MUST_HAVE = "must_have"
PREFERRED = "preferred"
UNKNOWN = "unknown"
TIERS = (MUST_HAVE, PREFERRED, UNKNOWN)

# How much of a posting's must-have list actually reached a deterministic evidence outcome.
# Separate from coverage on purpose: "nothing was parsed" and "nothing is missing" are
# different facts, and treating the first as the second let ignorance outrank evaluation.
UNASSESSED = "unassessed"
PARTIALLY_ASSESSED = "partially_assessed"
FULLY_ASSESSED = "fully_assessed"

DIRECT = "direct"
# What may count as covering a must-have. Named rather than implied, because the whole point
# of a must-have column is that adjacent evidence does not fill it.
COVERING = {DIRECT}

# Inline cues. Deliberately narrow: a phrase gets in only if it states weight outright.
# `(?<!not )` and its friends: found by sampling, on "Interest in learning more about life
# science (prior knowledge is not required)", which the cue turned into a must-have by
# matching the very word that was being negated.
MUST_CUE = re.compile(
    r"\b(?<!not )(?<!not a )(?<!not be )(?<!isn't )(?<!is not )(?<!are not )"
    r"(?:required|requirement|must\s+(?:have|be|possess|hold|demonstrate)|mandatory"
    r"|minimum\s+(?:of\s+)?(?:\d|one|two|three|four|five)"
    r"|at\s+least\s+(?:\d|one|two|three|four|five))\b", re.I)
# `bonus` alone was too loose: it fired on "eligible for our Annual Performance Bonus Plan",
# a compensation sentence with no opinion about the requirement. Narrowed to the phrasings
# that weigh a requirement. `ideally` is kept — it weighs the clause it modifies — but it is
# the loosest cue here and the sample below is where it earns its place.
PREFERRED_CUE = re.compile(
    r"\b(?:preferred|preferable|preferably|nice[\s-]to[\s-]have|a\s+plus"
    r"|bonus\s+(?:points|if)|desirable|desired|ideally|advantageous"
    r"|would\s+be\s+great)\b", re.I)

# "preferred but not required" is one statement, not two. Without this the pair reads as a
# contradiction and lands in `unknown`, when the employer could hardly have been clearer.
# Written over the same four words the corpus actually uses for the first half.
_SOFT = r"(?:preferred|preferable|desirable|desired|a\s+plus)"
PREFERRED_NOT_REQUIRED = re.compile(
    rf"\b{_SOFT}\b[^.;]{{0,30}}\bnot\s+required\b"
    rf"|\bnot\s+required\b[^.;]{{0,30}}\b{_SOFT}\b", re.I)

# Headings that state weight outright, and headings that only sound like they do. The second
# list is why this module exists: every one of these currently becomes a required section.
MUST_HEADINGS = (
    "required", "requirements", "required qualifications", "minimum qualifications",
    "basic qualifications", "must have", "must haves", "required skills",
    "you'll need to have", "you will need to have", "minimum requirements",
    "required experience", "essential qualifications", "essential requirements",
)
PREFERRED_HEADINGS = (
    "preferred", "preferred qualifications", "nice to have", "nice-to-have", "nice to haves",
    "bonus points", "desired qualifications", "preferred skills", "a plus", "preferred experience",
    "pluses", "extra credit",
    # Found by sampling the real queue: each of these was being read as a requirement line
    # rather than as the heading it is, so the lines beneath it inherited whatever section
    # they were in — usually the ambiguous "qualifications".
    "bonus if you have", "bonus points for", "desirable, but not required",
    "desirable but not required", "preferred, but not required", "preferred but not required",
    "nice to have (but not required)", "what would be nice to have",
)
# Headings that end a requirement list without being one. `posting_sections` closes on the
# about-us family; these are the ones that were letting benefits and interview logistics
# inherit the weight of the requirements above them. Measured across the 112 queue postings:
# "interviewing with" 49, "perks & benefits" 48, "what we offer" 13,
# "compensation & total rewards" 8 — 120 occurrences, and every line beneath each of them was
# being tiered as a requirement.
SECTION_ENDING_HEADINGS = (
    "perks & benefits", "perks and benefits", "benefits", "what we offer",
    "what we offer you", "interviewing with", "the process", "hiring process",
    "our hiring process", "the interview process", "our interview process",
    "interview process", "what to expect", "next steps", "how we hire",
    "compensation & total rewards", "compensation and total rewards", "total rewards",
    "base pay", "salary range", "pay range",
)
# Real headings over real postings that name a requirement list without weighting it.
AMBIGUOUS_HEADINGS = (
    "qualifications", "what you'll bring", "what you will bring", "what you bring",
    "what we're looking for", "what we are looking for", "who you are", "about you",
    "knowledge, skills, and abilities", "knowledge, skills and abilities", "skills",
    "experience", "your experience", "your background", "what you'll need",
    "what you need", "the ideal candidate", "candidate profile",
)

# Openings for a block of non-mandatory requirements that are not shaped like the headings
# above. Narrow on purpose: each was read off a real posting.
# `#LI-Remote`, `#LI-Associate` — LinkedIn tracking tags, left in the body by the poster.
TRACKING_TAG = re.compile(r"^#[A-Za-z]{2}-", re.I)

# A bare pay range: "$195,000—$225,000 USD". `posting_sections.FALLBACK_EXCLUSION` misses it
# because it matches the words compensation, salary and pay range, and this line has none of
# them. Found by the corpus-wide check rather than by the hand sample — in Komodo a salary
# sub-heading happened to end the section first, so the sample never showed it.
# Work-authorization and sponsorship statements. They are requirements, and they are not
# *this* kind: `field_policy` puts the sponsorship domain in `ALWAYS_MANUAL_DOMAINS` and the
# routing gate already decides eligibility on them. Counting them here as skill requirements
# put "we cannot sponsor" among the must-haves of the two highest-ranked postings, where it
# read as something the candidate's evidence could cover.
ELIGIBILITY_STATEMENT = re.compile(
    r"\bsponsor(?:ship|ing)?\b|\blegally\s+authoriz|\bwork\s+authorization\b"
    r"|\bright\s+to\s+work\b|\bvisa\s+status\b", re.I)

BARE_PAY_RANGE = re.compile(
    r"^\$?\d[\d,]*(?:\.\d+)?\s*(?:[—–-]|to)\s*\$?\d[\d,]*(?:\.\d+)?"
    r"\s*(?:USD|CAD|EUR|GBP|per\s+\w+|/\s*\w+)?\.?$", re.I)

TRANSITION_TO_PREFERRED = re.compile(
    r"\b(?:additional\s+skills|additional\s+experience|also\s+(?:nice|great|love)"
    r"|even\s+better|bonus\s+skills|we(?:'d| would)\s+also\s+love"
    r"|you\s+might\s+also|extra\s+credit)\b", re.I)

REASONS = {
    "cue_scope_is_local": UNKNOWN,
    "clause_states_required": MUST_HAVE,
    "clause_states_preferred": PREFERRED,
    "line_states_required": MUST_HAVE,
    "line_states_preferred": PREFERRED,
    "line_states_preferred_not_required": PREFERRED,
    "line_states_both": UNKNOWN,
    "heading_states_required": MUST_HAVE,
    "heading_states_preferred": PREFERRED,
    "heading_does_not_state_weight": UNKNOWN,
    "no_heading": UNKNOWN,
}


# Employers type the apostrophe their editor gives them. `posting_sections`' heading tables
# are written with the straight one, so "Where You’ll Work" and "What You’ll Do" matched
# nothing: 21 of the 112 queue postings write the curly form, and their *responsibilities*
# were being read as requirements because the heading that should have ended the section
# was invisible.
CURLY = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"'})


def straighten(line: str) -> str:
    return str(line or "").translate(CURLY)


def _normalize(line: str) -> str:
    return straighten(line).strip().strip(":").casefold()


def heading_weight(line: str) -> tuple[str, str] | None:
    """`(tier, heading)` when this line is a requirement heading, else None.

    Matched against the full phrase first and by prefix afterwards, the way
    `posting_sections._heading_key` does, so "What You Bring to Komodo Health (Required)"
    is read as the required heading it is rather than as the ambiguous one it starts with.
    """
    text = _normalize(line)
    if not text or len(text) > 100:
        return None
    for tier, table in ((MUST_HAVE, MUST_HEADINGS), (PREFERRED, PREFERRED_HEADINGS),
                        (UNKNOWN, AMBIGUOUS_HEADINGS)):
        if text in table:
            return tier, text
    if not text.endswith((".", "!", "?")) and len(text) <= 80:
        # Longest first: "required qualifications" must win over "required", and an
        # explicit tail such as "(required)" must win over the ambiguous stem it follows.
        for tier, table in ((MUST_HAVE, MUST_HEADINGS), (PREFERRED, PREFERRED_HEADINGS),
                            (UNKNOWN, AMBIGUOUS_HEADINGS)):
            for heading in sorted(table, key=len, reverse=True):
                if len(heading) >= 9 and text.startswith(heading) \
                        and posting_sections._heading_shaped(line, heading, text):
                    if tier is UNKNOWN and (MUST_CUE.search(text) or PREFERRED_CUE.search(text)):
                        # The tail says what the stem did not.
                        return (MUST_HAVE if MUST_CUE.search(text) else PREFERRED), text
                    return tier, text
    return None


def split_clauses(line: str) -> list[str] | None:
    """A line stating two weights, split into the two requirements it states — or None.

    Only on a semicolon, and only when every clause carries exactly one kind of cue.
    "Bachelor's degree required; advanced degree (MBA, MPH, MS) a plus" is two requirements
    written on one line, and calling the whole thing ambiguous throws away a must-have the
    employer stated plainly. A comma would not do: the corpus is full of commas inside lists
    of tools, and splitting there would invent requirements rather than separate them.

    Anything this cannot resolve cleanly stays one line and stays `unknown`.
    """
    clauses = [clause.strip(" ;.") for clause in line.split(";")]
    clauses = [clause for clause in clauses if clause]
    if len(clauses) < 2:
        return None
    for clause in clauses:
        must, preferred = MUST_CUE.search(clause), PREFERRED_CUE.search(clause)
        if bool(must) == bool(preferred):
            # No cue, or both again. Either way the split did not resolve anything.
            return None
    return clauses


# A colon or a trailing ellipsis is how a posting introduces the block beneath it. Nothing
# else is used as a heading signal: requirement bullets routinely have no terminal
# punctuation, so a "short line in title case" rule would delete real requirements.
HEADING_SHAPED = (":", "\u2026", "...")


def looks_like_a_heading(line: str) -> bool:
    text = straighten(line).strip()
    return bool(text) and len(text) <= 90 and text.endswith(HEADING_SHAPED)


def _section_ending(line: str) -> bool:
    text = _normalize(line)
    if not text or len(text) > 60:
        return False
    for heading in SECTION_ENDING_HEADINGS:
        if text == heading or (text.startswith(heading)
                               and posting_sections._heading_shaped(line, heading, text)):
            return True
    return False


def _enclosing_bracket(line: str, at: int) -> int | None:
    """Where the bracket containing this offset opened, or None if it is not in one."""
    stack: list[int] = []
    for index, character in enumerate(line):
        if index == at:
            return stack[0] if stack else None
        if character in "([":
            stack.append(index)
        elif character in ")]" and stack:
            stack.pop()
    return None


def cue_scope_is_local(line: str, cue: re.Match) -> bool:
    """A weight word inside a bracketed aside qualifies the aside, not the requirement.

    "3+ years in Life Sciences Consulting (Business or Management Consulting preferred)"
    states a mandatory three years and a preference about which kind of consulting. Reading
    the parenthesis as governing the whole line downgraded the years requirement, which is
    the opposite mistake to the one the heading rule was written to stop. The requirement
    outside the bracket is what makes the scope local: a line that is *only* a parenthetical
    has nothing else for the cue to be narrower than.
    """
    opened = _enclosing_bracket(line, cue.start())
    if opened is None:
        return False
    if opened == 0:
        # "(Preferred) Familiarity with dbt" — a bracket that opens the line is labelling the
        # line, not qualifying something before it. There is nothing before it to qualify.
        return False
    outside = re.sub(r"[(\[][^)\]]*[)\]]", " ", line).strip(" ,;.")
    return len(outside) >= 12


def classify_line(line: str, heading: tuple[str, str] | None) -> dict[str, Any]:
    """One requirement line's tier, the reason, and the words that decided it."""
    must = MUST_CUE.search(line)
    preferred = PREFERRED_CUE.search(line)
    if preferred and not must and cue_scope_is_local(line, preferred):
        # Deliberately `unknown` and not the heading's tier: the line has a weight word in it
        # whose reach cannot be determined, so neither reading is safe to assert.
        return _decision("cue_scope_is_local", preferred, heading)
    if PREFERRED_NOT_REQUIRED.search(line):
        match = PREFERRED_NOT_REQUIRED.search(line)
        return _decision("line_states_preferred_not_required", match, heading)
    if must and preferred:
        # Two weights in one sentence. Reported with both cues so a reader can see why.
        return _decision("line_states_both", must, heading, second=preferred)
    if must:
        return _decision("line_states_required", must, heading)
    if preferred:
        return _decision("line_states_preferred", preferred, heading)
    if heading is None:
        return _decision("no_heading", None, heading)
    tier, text = heading
    if tier is MUST_HAVE:
        return _decision("heading_states_required", None, heading)
    if tier is PREFERRED:
        return _decision("heading_states_preferred", None, heading)
    return _decision("heading_does_not_state_weight", None, heading)


def _decision(reason: str, match: re.Match | None, heading: tuple[str, str] | None,
              second: re.Match | None = None) -> dict[str, Any]:
    cues = [m.group(0) for m in (match, second) if m is not None]
    return {"tier": REASONS[reason], "reason": reason, "cues": cues,
            "cue_spans": [list(m.span()) for m in (match, second) if m is not None],
            "heading": heading[1] if heading else None}


def tier_lines(description: str) -> list[dict[str, Any]]:
    """Every requirement line in a posting, tiered, with where it was found.

    Line offsets are into the description as given, so a classification can be checked
    against the employer's text rather than against a copy of it.
    """
    text = str(description or "")
    results: list[dict[str, Any]] = []
    heading: tuple[str, str] | None = None
    offset = 0
    for raw in text.splitlines(keepends=True):
        start = offset
        offset += len(raw)
        line = raw.strip()
        if not line:
            continue
        if re.search(r"\b(?:we are an )?equal opportunity employer\b|\bVEVRAA\b", line, re.I):
            heading = None
            continue
        if posting_sections._heading_key(straighten(line)) == "__close__":
            heading = None
            continue
        if _section_ending(line):
            heading = None
            continue
        found = heading_weight(line)
        if found:
            heading = found
            continue
        if looks_like_a_heading(line):
            # An unrecognised sub-heading inside a requirement list. If it states a weight
            # it starts a new block at that weight; otherwise the safe reading is that the
            # requirement list ended, because the alternative is every line beneath a
            # heading nobody parsed inheriting `must_have`. Komodo's posting is the case
            # this is for: a salary sub-heading, a location sub-heading and an AI-policy
            # section were all contributing must-haves.
            text = straighten(line)
            if MUST_CUE.search(text):
                heading = (MUST_HAVE, _normalize(line))
            elif PREFERRED_CUE.search(text) or TRANSITION_TO_PREFERRED.search(text):
                heading = (PREFERRED, _normalize(line))
            else:
                heading = None
            continue
        if TRANSITION_TO_PREFERRED.search(straighten(line)) and len(line) <= 90:
            # "Additional skills and experience we'll prioritize…" — a preferred block
            # opening without a colon. Kept as a transition rather than an ending because
            # what follows are real requirements, just not mandatory ones.
            heading = (PREFERRED, _normalize(line))
            continue
        if posting_sections._heading_key(straighten(line)):
            # A heading this module does not weigh — responsibilities, compensation. It ends
            # the requirement section rather than letting its lines inherit the last one.
            heading = None
            continue
        if heading is None:
            continue
        item = posting_sections.BULLET.sub("", line).strip(" ;.")
        if not item or len(item) > posting_sections.MAX_ITEM_CHARS:
            continue
        if posting_sections.FALLBACK_EXCLUSION.search(item) or TRACKING_TAG.match(item) \
                or BARE_PAY_RANGE.match(item) or ELIGIBILITY_STATEMENT.search(item):
            # Compensation and benefits prose that sits inside a requirement block, and the
            # applicant-tracking tags employers leave in the body. `posting_sections` already
            # refuses these on its fallback path; reusing its list rather than writing a
            # second one that would drift from it.
            continue
        # Where `item` actually begins, not where the line does: the bullet marker and the
        # trailing punctuation were stripped, and an offset that ignored that would point a
        # reader at "• " rather than at the requirement.
        must, preferred = MUST_CUE.search(item), PREFERRED_CUE.search(item)
        found_at = raw.find(item)
        offset_in_line = start + (found_at if found_at >= 0 else 0)
        clauses = (split_clauses(item)
                   if must and preferred and not PREFERRED_NOT_REQUIRED.search(item) else None)
        if clauses:
            for clause in clauses:
                cue = MUST_CUE.search(clause) or PREFERRED_CUE.search(clause)
                reason = ("clause_states_required" if MUST_CUE.search(clause)
                          else "clause_states_preferred")
                results.append({
                    "line": clause, "offset": offset_in_line + item.find(clause),
                    "length": len(clause), "tier": REASONS[reason], "reason": reason,
                    "cues": [cue.group(0)], "cue_spans": [list(cue.span())],
                    "heading": heading[1] if heading else None, "from_line": item})
            continue
        decision = classify_line(item, heading)
        results.append({"line": item, "offset": offset_in_line,
                        "length": len(raw.rstrip("\n")), **decision})
    return results


def summarize(description: str, facts: list[dict[str, Any]] | None = None,
              *, ontology: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per-tier coverage and gaps, counted per requirement line.

    The unit is the line the employer wrote, not the terms it distils to. "Experience with
    SAS" yields both the tool `SAS` and the capability `cap.statistical-programming`; counting
    those separately made one requirement simultaneously covered and a gap, and inflated the
    gap column of every posting that named a tool. A line is covered when the evidence covers
    something it asked for.

    A must-have line counts as covered only on `direct` evidence. Transferable and
    mention-only evidence is reported in its own column and never counted as covering a
    mandatory requirement, because the whole reason for a must-have column is that adjacent
    experience does not fill it.
    """
    facts = facts or []
    lines = tier_lines(description)
    report: dict[str, Any] = {"tiers": {}, "lines": lines}
    for tier in TIERS:
        entries = [entry for entry in lines if entry["tier"] == tier]
        # Counts are of lines and the term lists are the detail behind them, so `direct`
        # and `gaps` are the same unit and add up with `stated_lines`. Counting covered
        # terms against uncovered terms compared two different things.
        counts = {"direct": 0, "adjacent": 0, "gaps": 0}
        direct: list[str] = []
        adjacent: list[str] = []
        gaps: list[str] = []
        unrecognised: list[str] = []
        for entry in entries:
            distilled = posting_sections.distill_terms([entry["line"]], ontology=ontology)
            terms = list(dict.fromkeys([*distilled["terms"], *distilled["capabilities"]]))
            if not terms:
                # A requirement nobody parsed is not a requirement nobody has. Kept in full
                # so the reader can see exactly what the distiller could not read, rather
                # than a count that looks like a small number.
                unrecognised.append(entry["line"])
                continue
            strengths = [match_requirement(term, facts)["strength"] for term in terms]
            best = max(strengths, key=lambda name: _RANK.get(name, 0))
            covered_by = [term for term, strength in zip(terms, strengths)
                          if strength == best and strength != "none"]
            if best in COVERING:
                counts["direct"] += 1
                direct.extend(covered_by)
            elif best != "none":
                counts["adjacent"] += 1
                adjacent.extend(covered_by)
            else:
                counts["gaps"] += 1
                gaps.extend(terms)
        # Line counts describe the posting; **unique** term counts are what ordering may
        # use. Komodo's Infrastructure Engineer stated GitHub in three separate AI and CI
        # paragraphs, so one piece of evidence counted three times and carried the posting
        # to the top of the queue past its own uncovered AWS, Docker, Snowflake, Spark and
        # dbt. A requirement named twice is not two requirements met.
        parsed = len(entries) - len(unrecognised)
        if parsed == 0:
            state = UNASSESSED
        elif unrecognised:
            state = PARTIALLY_ASSESSED
        else:
            state = FULLY_ASSESSED
        report["tiers"][tier] = {
            "assessment": state,
            "stated_lines": len(entries),
            "unrecognised_lines": len(unrecognised),
            "unrecognised_requirements": unrecognised,
            "direct": counts["direct"],
            "adjacent": counts["adjacent"],
            "gaps": counts["gaps"],
            "unique_direct": len(set(direct)),
            "unique_adjacent": len(set(adjacent)),
            "unique_gaps": len(set(gaps)),
            "direct_terms": sorted(set(direct)),
            "adjacent_terms": sorted(set(adjacent)),
            "gap_terms": sorted(set(gaps)),
            "parsed_lines": parsed,
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Tier a posting's stated requirements.")
    parser.add_argument("--card", required=True, type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--lines", action="store_true", help="print every tiered line")
    args = parser.parse_args()
    card = json.loads(args.card.read_text(encoding="utf-8"))
    facts = []
    if args.candidate:
        facts = json.loads(args.candidate.read_text(encoding="utf-8")).get("facts") or []
    report = summarize(card.get("description", ""), facts)
    if not args.lines:
        report.pop("lines")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
