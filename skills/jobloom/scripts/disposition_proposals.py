#!/usr/bin/env python3
"""Proposing what the confirmed facts say about a requirement nothing could parse.

Fifty-seven blank cells is not a review, it is a memory test, and the answers are already in
the fact library. So this reads each unread requirement against the active CandidateSnapshot
and proposes a disposition — with the fact ids it rests on, so the proposal can be checked
rather than believed.

Nothing here decides anything. Every proposal carries `requires_user_confirmation: true` and
`final_disposition: null`, and no queue row or application state is touched.

The rule that shapes the whole module: **absence of evidence is not evidence of absence.**
A requirement nothing matched gets no proposed disposition at all — it gets
`candidate_evidence_status: no_supporting_evidence_found`, which is a different statement and
leaves the judgement where it belongs. `does_not_meet` is proposed only where the confirmed
facts *positively establish* the mismatch, and there are exactly three shapes where they can:

- a **degree** the profile does not hold, after reading every education fact;
- a **duration** longer than the confirmed career timeline;
- a **named technology** the controlled resolver recognised and found nothing for.

Everything else that finds no evidence stays unproposed. `unclear_ask_employer` is never
proposed: whether an employer wrote something ambiguous is a judgement about their prose, not
a fact about the candidate, and inventing a detector for it would be inventing a distinction.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import posting_sections  # noqa: E402
import requirement_tiers  # noqa: E402
from evidence_matcher import EVIDENCE_ORDER  # noqa: E402
from evidence_matcher import match_requirement, match_requirement_prose  # noqa: E402

MEETS = "meets"
PARTIALLY_MEETS = "partially_meets"
DOES_NOT_MEET = "does_not_meet"
NOT_A_REQUIREMENT = "not_a_requirement"

NO_EVIDENCE = "no_supporting_evidence_found"
EVIDENCE_FOUND = "supporting_evidence_found"
NOT_APPLICABLE = "not_a_candidate_comparison"

USABLE = {"confirmed", "locked"}

# Degree levels in the order that lets "does the profile hold at least this?" be a comparison
# rather than a lookup. A certificate is deliberately not on the ladder: it is a credential,
# not a degree, and letting it stand in for one is the substitution this repository refuses.
DEGREE_LEVELS = ("associate", "bachelor", "master", "doctorate")
DEGREE_PATTERNS = (
    (r"\bph\.?d\.?\b|\bdoctora(?:l|te)\b|\bdsc\b|\bdrph\b", "doctorate"),
    (r"\bm\.?s\.?c?\.?\b|\bmaster'?s?\b|\bm\.?p\.?h\.?\b|\bm\.?b\.?a\.?\b|\bm\.?eng\b",
     "master"),
    (r"\bb\.?s\.?c?\.?\b|\bbachelor'?s?\b|\bb\.?a\.?\b", "bachelor"),
    (r"\bassociate'?s degree\b", "associate"),
)
# Fields named often enough in this corpus to be worth comparing. A field not here is not
# compared at all rather than guessed at.
FIELD_TERMS = (
    "statistics", "biostatistics", "epidemiology", "public health", "data science",
    "computer science", "mathematics", "bioinformatics", "engineering", "economics",
    "life sciences", "biology", "chemistry", "physics", "operations research",
    "medical technology", "medical laboratory", "environmental health", "informatics",
)

# A field list that is open at the end establishes nothing by not containing your field.
# "a quantitative field (e.g., Statistics, Mathematics…)", "STEM … or equivalent", "or
# related field" all admit degrees they do not name, so a mismatch against them is not the
# positive establishment `does_not_meet` requires. Only a closed list — "MS or PhD in
# Statistics or Biostatistics" — can settle it.
OPEN_ENDED_FIELD = re.compile(
    r"\be\.?g\.?\b|\bsuch\s+as\b|\bor\s+(?:related|equivalent|similar|other)\b"
    r"|\bequivalent\s+(?:experience|degree|qualification)\b|\bstem\b"
    r"|\bquantitative\s+(?:field|discipline|degree)\b|\bor\s+a\s+related\b"
    r"|\betc\.?\b", re.I)

DURATION = re.compile(
    r"\b(?:minimum\s+(?:of\s+)?|at\s+least\s+)?(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?years?\b",
    re.I)
DATE_RANGE = re.compile(
    r"(\d{1,2})[/\-](\d{4})\s*[–\-—]\s*(?:(\d{1,2})[/\-](\d{4})|present)", re.I)

# Lines that are not a candidate comparison at all. Reuses the posting-side exclusions rather
# than writing a second list that would drift from them.
MARKETING = re.compile(
    r"^at\s+[A-Z][\w&.\- ]{2,30},\s|\bwe(?:'re| are)\s+(?:looking|a\s+|an\s+)"
    r"|\byou'?ll\s+join\b|\bour\s+(?:mission|values|culture|team\s+is)\b"
    r"|\bequal\s+opportunity\b|\bprivacy\b|\bapplicant\s+data\b"
    r"|\bnot\s+a\s+perfect\s+match\b", re.I)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---- what the profile actually holds ------------------------------------------------


# A certificate, minor or concentration named alongside a degree is not the degree's field.
# "Master of Public Health – Environmental Health Science (Certificate in Biostatistics)" is
# a public-health master's, and reading Biostatistics out of it as the field is precisely the
# substitution that turns a certificate into a degree nobody awarded.
ASIDE = re.compile(r"\([^)]*\)|\b(?:certificate|minor|concentration|track|focus)\s+in\b.*",
                   re.I)


def degree_fields(value: str) -> list[str]:
    """The fields a degree is *in*, matched on whole words and excluding asides.

    Whole words because `statistics` is a substring of `biostatistics`: without the boundary
    a Biostatistics certificate satisfied a Statistics requirement by accident.
    """
    text = ASIDE.sub(" ", value).casefold()
    return [term for term in FIELD_TERMS
            if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text)]


def named_levels(text: str) -> list[str]:
    """Every degree level a requirement names, ordered weakest first.

    "MS or PhD in Statistics" is satisfied by the master's; taking the first pattern that
    matched reported "no doctorate" about a requirement a master's would have met.
    """
    found = {name for pattern, name in DEGREE_PATTERNS if re.search(pattern, text, re.I)}
    return sorted(found, key=DEGREE_LEVELS.index)


def held_degrees(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every degree the confirmed education facts record, with the fact each came from."""
    held = []
    for fact in facts:
        if fact.get("type") != "education" or fact.get("status") not in USABLE:
            continue
        value = str(fact.get("value") or "")
        levels = named_levels(value)
        if not levels:
            continue
        held.append({"fact_id": fact["id"], "level": levels[-1],
                     "fields": degree_fields(value), "value": value})
    return held


def career_span_years(facts: list[dict[str, Any]], *, today: date | None = None
                      ) -> dict[str, Any]:
    """The confirmed career timeline, read from the dated experience headers.

    The earliest start to the latest end, which is the *most* the profile can support. A
    duration requirement longer than this is established as unmet by the facts themselves;
    one shorter than it is not thereby met, because the requirement usually names a domain
    the span says nothing about.
    """
    today = today or date.today()
    starts, ends, fact_ids = [], [], []
    for fact in facts:
        if fact.get("type") != "experience_header" or fact.get("status") not in USABLE:
            continue
        for match in DATE_RANGE.finditer(str(fact.get("value") or "")):
            start_month, start_year, end_month, end_year = match.groups()
            starts.append(date(int(start_year), max(1, min(12, int(start_month))), 1))
            ends.append(date(int(end_year), max(1, min(12, int(end_month))), 1)
                        if end_year else today)
            fact_ids.append(fact["id"])
    if not starts:
        return {"years": None, "fact_ids": []}
    earliest, latest = min(starts), max(ends)
    months = (latest.year - earliest.year) * 12 + latest.month - earliest.month
    return {"years": round(months / 12, 1), "earliest": earliest.isoformat(),
            "latest": latest.isoformat(), "fact_ids": sorted(set(fact_ids))}


# ---- proposing one requirement ------------------------------------------------------


def _proposal(disposition: str | None, *, confidence: str, reason: str,
              fact_ids: list[str] | None = None, evidence_class: str | None = None,
              status: str = NO_EVIDENCE) -> dict[str, Any]:
    return {"proposed_disposition": disposition, "confidence": confidence,
            "supporting_fact_ids": sorted(fact_ids or []),
            "evidence_class": evidence_class, "short_reason": reason,
            "candidate_evidence_status": status,
            "requires_user_confirmation": True, "final_disposition": None}


def propose(requirement: str, facts: list[dict[str, Any]], *,
            degrees: list[dict[str, Any]] | None = None,
            span: dict[str, Any] | None = None) -> dict[str, Any]:
    """One requirement, read against the confirmed facts. Decides nothing."""
    degrees = held_degrees(facts) if degrees is None else degrees
    span = career_span_years(facts) if span is None else span
    text = requirement.strip()

    if MARKETING.search(text) or posting_sections.FALLBACK_EXCLUSION.search(text) \
            or requirement_tiers.ELIGIBILITY_STATEMENT.search(text):
        return _proposal(NOT_A_REQUIREMENT, confidence="high", status=NOT_APPLICABLE,
                         reason="company, eligibility or compensation prose rather than "
                                "something the candidate's evidence answers")

    degree = _degree_proposal(text, degrees)
    if degree:
        return degree
    duration = _duration_proposal(text, span)
    if duration:
        return duration
    technology = _technology_proposal(text, facts)
    if technology:
        return technology

    found = match_requirement_prose(text, facts)
    if not found.get("recognized"):
        # No safe rule for this sentence. Distinct from "the candidate lacks it", and the
        # only honest thing to report.
        return _proposal(None, confidence="none", status=NO_EVIDENCE,
                         reason="no controlled rule reads this sentence; the facts were not "
                                "compared against it")
    strength = found.get("strength", "none")
    if strength == "none":
        # The resolver knows the concept and matched nothing. That is still absence, and
        # absence of a *concept* is not a mismatch: "strong communication skills" going
        # unmatched says the profile does not phrase it that way, not that it is missing.
        # Only a named technology can be established as absent, and that is handled above.
        return _proposal(None, confidence="none", status=NO_EVIDENCE,
                         reason="the resolver recognised the concept and matched no fact; "
                                "that is absence of a match, not a mismatch")
    if strength in requirement_tiers.COVERING:
        return _proposal(MEETS, confidence="medium", fact_ids=found["fact_ids"],
                         evidence_class=strength, status=EVIDENCE_FOUND,
                         reason="confirmed evidence covers this directly")
    return _proposal(PARTIALLY_MEETS, confidence="medium", fact_ids=found["fact_ids"],
                     evidence_class=strength, status=EVIDENCE_FOUND,
                     reason=f"evidence is {strength}, not direct; the requirement asks for "
                            "more than the facts establish")


def named_technologies(text: str) -> list[str]:
    """The controlled tool names a requirement mentions.

    A tool is a thing the profile either records or does not. `does_not_meet` is available
    for these and for nothing else the resolver merely recognises, because "no fact matched
    the concept of communication" is a statement about vocabulary, not about the candidate.
    """
    return [tool for tool in posting_sections.TOOL_TERMS
            if re.search(rf"(?<![A-Za-z0-9+#]){re.escape(tool)}(?![A-Za-z0-9+#])", text, re.I)]


def _technology_proposal(text: str, facts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Named tools, checked one at a time against the whole confirmed profile.

    A tool is the one thing a profile either records or does not, which is why this is the
    only place absence is allowed to become `does_not_meet`. Checked before the prose
    resolver, because a sentence naming Docker and Snowflake is answerable whether or not
    the resolver has a concept for the sentence as a whole.
    """
    named = named_technologies(text)
    if not named:
        return None
    covered, missing, fact_ids, classes = [], [], [], []
    for tool in named:
        found = match_requirement(tool, facts)
        if found["strength"] == "none":
            missing.append(tool)
        else:
            covered.append(tool)
            fact_ids.extend(found["fact_ids"])
            classes.append(found["strength"])
    if not covered:
        return _proposal(DOES_NOT_MEET, confidence="medium", status=NO_EVIDENCE,
                         reason=f"names {', '.join(missing)}; the whole confirmed profile "
                                "holds no fact for any of them")
    weakest = min(classes, key=lambda name: EVIDENCE_ORDER[name]) \
        if classes else "none"
    if missing or weakest not in requirement_tiers.COVERING:
        detail = (f"covered: {', '.join(covered)}" +
                  (f"; not covered: {', '.join(missing)}" if missing else "") +
                  (f"; evidence is {weakest}" if weakest not in requirement_tiers.COVERING
                   else ""))
        return _proposal(PARTIALLY_MEETS, confidence="medium", fact_ids=fact_ids,
                         evidence_class=weakest, status=EVIDENCE_FOUND, reason=detail)
    return _proposal(MEETS, confidence="medium", fact_ids=fact_ids, evidence_class=weakest,
                     status=EVIDENCE_FOUND,
                     reason=f"confirmed facts cover every named tool: {', '.join(covered)}")


def _degree_proposal(text: str, degrees: list[dict[str, Any]]) -> dict[str, Any] | None:
    levels = named_levels(text)
    if not levels:
        return None
    # The lowest level the requirement accepts: "MS or PhD" is met by the master's.
    wanted = levels[0]
    wanted_fields = [term for term in FIELD_TERMS
                     if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text.casefold())]
    reviewed = [entry["fact_id"] for entry in degrees]
    at_level = [entry for entry in degrees
                if DEGREE_LEVELS.index(entry["level"]) >= DEGREE_LEVELS.index(wanted)]
    if not at_level:
        return _proposal(DOES_NOT_MEET, confidence="high", fact_ids=reviewed,
                         status=EVIDENCE_FOUND,
                         reason=f"the requirement accepts a {' or '.join(levels)}; every "
                                "education fact was read and none reaches that level")
    if not wanted_fields:
        if re.search(r"\b(?:field|discipline|major|subject)\b", text, re.I):
            # It names a field; the controlled vocabulary just does not hold it. Claiming
            # "the requirement names no field" would turn a vocabulary gap into a pass.
            return _proposal(None, confidence="none",
                             fact_ids=[entry["fact_id"] for entry in at_level],
                             status=NO_EVIDENCE,
                             reason=f"the profile holds a {wanted} degree, but this names a "
                                    "field the controlled vocabulary does not hold, so "
                                    "whether the field matches was not determined")
        return _proposal(MEETS, confidence="medium",
                         fact_ids=[entry["fact_id"] for entry in at_level],
                         evidence_class="direct", status=EVIDENCE_FOUND,
                         reason=f"the profile holds a {wanted} degree and the requirement "
                                "names no field")
    matching = [entry for entry in at_level
                if set(entry["fields"]) & set(wanted_fields)]
    if matching:
        return _proposal(MEETS, confidence="medium",
                         fact_ids=[entry["fact_id"] for entry in matching],
                         evidence_class="direct", status=EVIDENCE_FOUND,
                         reason=f"the profile holds a {wanted} degree in "
                                f"{', '.join(sorted(set(wanted_fields) & set(matching[0]['fields'])))}")
    held = ", ".join(sorted({entry["level"] for entry in at_level}))
    if OPEN_ENDED_FIELD.search(text):
        # The employer left the list open. Not matching the named examples is not a mismatch.
        return _proposal(None, confidence="none", fact_ids=reviewed, status=NO_EVIDENCE,
                         reason=f"the profile holds a {held} degree, and this names its "
                                "fields as examples or accepts an equivalent — so whether "
                                "the profile's fields qualify is not settled by the facts")
    return _proposal(DOES_NOT_MEET, confidence="high", fact_ids=reviewed,
                     status=EVIDENCE_FOUND,
                     reason=f"the requirement asks for a {wanted} in "
                            f"{' or '.join(wanted_fields)} and names no equivalent; the "
                            f"profile holds a {held} in other fields, and a related "
                            "certificate is not the degree asked for")


def _duration_proposal(text: str, span: dict[str, Any]) -> dict[str, Any] | None:
    if span.get("years") is None:
        return None
    match = DURATION.search(text)
    if not match:
        return None
    wanted = int(match.group(1))
    if wanted > span["years"]:
        return _proposal(DOES_NOT_MEET, confidence="high", fact_ids=span["fact_ids"],
                         status=EVIDENCE_FOUND,
                         reason=f"asks for {wanted} years; the dated experience headers span "
                                f"{span['years']} years ({span['earliest']} to "
                                f"{span['latest']}), so no reading of them reaches it")
    # The span is long enough, which says nothing about the domain the requirement names.
    return _proposal(None, confidence="none", fact_ids=span["fact_ids"],
                     status=NO_EVIDENCE,
                     reason=f"the {span['years']}-year career span is long enough for "
                            f"{wanted} years, but the facts do not establish that many years "
                            "in what this requirement asks about")


def annotate(sheet: dict[str, Any], facts: list[dict[str, Any]],
             at: datetime | None = None) -> dict[str, Any]:
    """Add a proposal to every unread requirement in a worksheet. Changes nothing else."""
    degrees, span = held_degrees(facts), career_span_years(facts)
    counts: dict[str, int] = {}
    for entry in sheet.get("survivors", []):
        for item in entry.get("unread_requirements", []):
            proposal = propose(item["requirement"], facts, degrees=degrees, span=span)
            item.update(proposal)
            key = proposal["proposed_disposition"] or "unproposed"
            counts[key] = counts.get(key, 0) + 1
    sheet["proposals"] = {
        "built_at": (at or now_utc()).isoformat(),
        "candidate_snapshot_sha256": sheet.get("candidate_snapshot_sha256"),
        "counts": counts,
        "decides_nothing": "every proposal requires user confirmation; no queue row or "
                           "application state is touched",
    }
    return sheet


def decisive_first(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    """Postings ordered by how close their proposals come to settling them.

    A posting with a positively established mismatch is worth reading before one with a
    handful of unproposed lines, because one answer closes it.
    """
    def weight(entry: dict[str, Any]) -> tuple:
        items = entry.get("unread_requirements", [])
        blocking = sum(1 for item in items
                       if item.get("proposed_disposition") == DOES_NOT_MEET)
        unproposed = sum(1 for item in items if item.get("proposed_disposition") is None)
        return (-blocking, unproposed, entry["lane_rank"])
    return sorted(sheet.get("survivors", []), key=weight)


def render(sheet: dict[str, Any]) -> str:
    counts = sheet["proposals"]["counts"]
    total = sum(counts.values())
    blocked_now = counts.get(DOES_NOT_MEET, 0)
    lines = [
        "# Disposition proposals", "",
        f"Built {sheet['proposals']['built_at'][:19]}Z against the active CandidateSnapshot.",
        "",
        f"**{total} requirements read, {blocked_now} with a mismatch the facts positively "
        f"establish, {counts.get('unproposed', 0)} the facts cannot speak to.** Nothing here "
        "is decided: every row needs your confirmation, and no queue row or application state "
        "has been touched.",
        "",
        "**Absence of evidence is not evidence of absence.** A requirement with no proposal "
        "is one the confirmed facts say nothing about — not one the candidate fails. "
        "`does_not_meet` is proposed only where the facts establish the mismatch: a degree "
        "not held after reading every education fact, a duration longer than the confirmed "
        "career timeline, or a requirement the controlled resolver recognised and found "
        "nothing for.", "",
        "`unclear_ask_employer` is never proposed — whether an employer wrote something "
        "ambiguous is a judgement about their prose, not a fact about the candidate.", "",
        "Postings are ordered by how close the proposals come to settling them.", "",
    ]
    for entry in decisive_first(sheet):
        items = entry.get("unread_requirements", [])
        blocking = [item for item in items
                    if item.get("proposed_disposition") == DOES_NOT_MEET]
        lines += ["", f"## {entry['employer']} — {entry['title']}", "",
                  f"{entry['location']} · {entry['lane']} #{entry['lane_rank']} · "
                  f"{entry['parsed_lines']} of {entry['stated_lines']} must-have lines "
                  f"evaluated by the queue"]
        if blocking:
            lines += ["", f"**{len(blocking)} requirement(s) the facts say are not met.** "
                          "One confirmation closes this posting."]
        lines += ["", "| requirement | proposal | confidence | evidence | why |",
                  "|---|---|---|---|---|"]
        for item in items:
            requirement = item["requirement"].replace("|", "\\|")[:150]
            proposal = item.get("proposed_disposition") or "—"
            evidence = ", ".join(item.get("supporting_fact_ids") or []) or "—"
            reason = (item.get("short_reason") or "").replace("|", "\\|")
            lines.append(f"| {requirement} | **{proposal}** | {item.get('confidence')} "
                         f"| {evidence} | {reason} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--worksheet", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()

    sheet = json.loads(args.worksheet.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    sheet["candidate_snapshot_sha256"] = candidate.get("content_sha256")
    annotate(sheet, candidate.get("facts") or [])
    if args.output:
        args.output.write_text(json.dumps(sheet, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(render(sheet), encoding="utf-8")
    print(json.dumps(sheet["proposals"]["counts"], indent=2))


if __name__ == "__main__":
    main()
