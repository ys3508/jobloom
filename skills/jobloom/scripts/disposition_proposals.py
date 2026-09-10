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
              status: str = NO_EVIDENCE, obligations: list[dict[str, Any]] | None = None,
              unverified: list[str] | None = None) -> dict[str, Any]:
    return {"proposed_disposition": disposition, "confidence": confidence,
            "supporting_fact_ids": sorted(fact_ids or []),
            "evidence_class": evidence_class, "short_reason": reason,
            "candidate_evidence_status": status,
            "obligations": obligations or [],
            "unverified_obligations": unverified or [],
            "requires_user_confirmation": True, "final_disposition": None}


# ---- a requirement is a set of obligations, not a bag of words -----------------------

# Qualifiers that are obligations in their own right. Each narrows what would otherwise be
# met by ordinary evidence, and each was found endorsing a whole sentence off a fact that
# spoke only to the noun it modifies: a classroom presentation answering "executive-level
# communication", a database answering "lead teams and manage multiple projects".
QUALIFIERS = (
    ("executive", r"\bexecutive[-\s]level\b|\bexecutive\b|\bc[-\s]suite\b"),
    ("senior", r"\bsenior\s+(?:stakeholder|leader|executive|management)"),
    ("non-technical", r"\bnon[-\s]technical\b|\bnontechnical\b"),
    ("lead teams", r"\blead(?:ing)?\s+(?:cross[-\s]functional\s+)?teams?\b"
                   r"|\bpeople\s+manage(?:ment|r)\b|\bmanage\s+(?:a\s+)?team\b"),
    ("multiple projects", r"\bmultiple\s+(?:projects|clients|workstreams)\b"
                          r"|\bsimultaneous(?:ly)?\b|\bconcurrent(?:ly)?\b"),
    ("fast-paced", r"\bfast[-\s]paced\b|\bdynamic\s+environment\b|\bambiguity\b"),
    ("coder", r"\bcoder\b|\bcompetitive\s+programming\b|\backm?[-\s]icpc\b"
              r"|\bicpc\b|\bio[ip]\b|\bipsc\b"),
    ("modular", r"\bmodular\b|\bextensible\b|\bscalable\s+systems?\b"),
    ("regulated", r"\bregulated\s+environment\b|\bgxp\b|\bcfr\s+part\s+11\b"),
    ("client-facing", r"\bclient[-\s]facing\b|\bcustomer[-\s]facing\b"),
    ("cross-functional", r"\bcross[-\s]functional\b"),
    ("record-level", r"\brecord[-\s]level\b|\bpatient[-\s]level\b|\bclaims?\s+data\b"),
)

# Where a sentence divides into obligations that must each hold. `or` is deliberately absent:
# it joins alternatives, and splitting on it would turn a choice into a list of demands.
OBLIGATION_SPLIT = re.compile(r";|,\s+and\s+|\s+and\s+|,(?!\s*(?:e\.g\.|i\.e\.))", re.I)


def obligations_of(requirement: str) -> list[str]:
    """The parts of a requirement that must each hold, split on conjunctions only.

    Never on `or`. "MD, PharmD, NP, PA, RN, MPH, or 5+ years" is one obligation with seven
    ways to satisfy it, and splitting it into seven demands would be the opposite error to
    the one this module is fixing.
    """
    if re.search(r"\bor\b", requirement, re.I):
        return [requirement.strip()]
    parts = [part.strip(" ;.,") for part in OBLIGATION_SPLIT.split(requirement)]
    return [part for part in parts if len(part) > 2] or [requirement.strip()]


def unverified_qualifiers(requirement: str, facts: list[dict[str, Any]]) -> list[str]:
    """Qualifiers the sentence carries that no confirmed fact speaks to.

    Checked against the facts' own text: a qualifier is verified only when some fact uses it,
    not when the thing it qualifies is evidenced.
    """
    corpus = " ".join(str(fact.get("value") or "") for fact in facts
                      if fact.get("status") in USABLE).casefold()
    unverified = []
    for name, pattern in QUALIFIERS:
        if re.search(pattern, requirement, re.I) and not re.search(pattern, corpus, re.I):
            unverified.append(name)
    return unverified


def propose(requirement: str, facts: list[dict[str, Any]], *,
            degrees: list[dict[str, Any]] | None = None,
            span: dict[str, Any] | None = None) -> dict[str, Any]:
    """One requirement, read against the confirmed facts. Decides nothing.

    `meets` is available only when every obligation in the sentence resolved and every
    qualifier it carries is spoken to by a fact. Anything else caps at `partially_meets` or
    stays unproposed: an unresolved part of a requirement does not disappear because another
    part matched.
    """
    degrees = held_degrees(facts) if degrees is None else degrees
    span = career_span_years(facts) if span is None else span
    text = requirement.strip()

    if MARKETING.search(text) or posting_sections.FALLBACK_EXCLUSION.search(text) \
            or requirement_tiers.ELIGIBILITY_STATEMENT.search(text):
        return _proposal(NOT_A_REQUIREMENT, confidence="high", status=NOT_APPLICABLE,
                         reason="company, eligibility or compensation prose rather than "
                                "something the candidate's evidence answers")

    credential = _credential_proposal(text, facts)
    if credential:
        return credential
    duration = _duration_proposal(text, span)
    if duration:
        return duration

    parts = obligations_of(text)
    resolved: list[dict[str, Any]] = []
    for part in parts:
        resolved.append(_resolve(part, facts))
    unverified = unverified_qualifiers(text, facts)
    unmet = [entry for entry in resolved if entry["strength"] == "none"]
    fact_ids = [fid for entry in resolved for fid in entry["fact_ids"]]
    classes = [entry["strength"] for entry in resolved if entry["strength"] != "none"]
    # The weakest necessary component constrains a compound requirement. Taking the strongest
    # is what let one directly evidenced clause carry four unevidenced ones.
    weakest = min(classes, key=lambda name: EVIDENCE_ORDER[name]) if classes else None

    if not classes:
        return _proposal(None, confidence="none", status=NO_EVIDENCE, obligations=resolved,
                         unverified=unverified,
                         reason="no obligation in this requirement resolved to a confirmed "
                                "fact")
    if unmet or unverified:
        detail = []
        if unmet:
            detail.append("unresolved: " + "; ".join(entry["obligation"][:60]
                                                     for entry in unmet))
        if unverified:
            detail.append("no fact speaks to: " + ", ".join(unverified))
        return _proposal(PARTIALLY_MEETS, confidence="medium", fact_ids=fact_ids,
                         evidence_class=weakest, status=EVIDENCE_FOUND,
                         obligations=resolved, unverified=unverified,
                         reason="; ".join(detail))
    if weakest not in requirement_tiers.COVERING:
        return _proposal(PARTIALLY_MEETS, confidence="medium", fact_ids=fact_ids,
                         evidence_class=weakest, status=EVIDENCE_FOUND,
                         obligations=resolved, unverified=unverified,
                         reason=f"every obligation resolved, but the weakest evidence is "
                                f"{weakest} rather than direct")
    return _proposal(MEETS, confidence="medium", fact_ids=fact_ids, evidence_class=weakest,
                     status=EVIDENCE_FOUND, obligations=resolved, unverified=unverified,
                     reason="every obligation in this requirement resolved to direct "
                            "confirmed evidence")


def named_technologies(text: str) -> list[str]:
    """The controlled tool names a requirement mentions, matched on whole tokens.

    Whole tokens because `Terra` is in the vocabulary and `Terraform` is not the same thing.
    """
    return [tool for tool in posting_sections.TOOL_TERMS
            if re.search(rf"(?<![A-Za-z0-9+#]){re.escape(tool)}(?![A-Za-z0-9+#])", text, re.I)]


def _resolve(obligation: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
    """One obligation against the facts, carrying its own fact ids."""
    named = named_technologies(obligation)
    if named:
        strengths, fact_ids = [], []
        for tool in named:
            found = match_requirement(tool, facts)
            strengths.append(found["strength"])
            fact_ids.extend(found["fact_ids"])
        weakest = min(strengths, key=lambda name: EVIDENCE_ORDER[name])
        return {"obligation": obligation, "kind": "named_technology",
                "strength": weakest, "fact_ids": sorted(set(fact_ids))}
    found = match_requirement_prose(obligation, facts)
    return {"obligation": obligation,
            "kind": "concept" if found.get("recognized") else "unparsed",
            "strength": found.get("strength", "none") if found.get("recognized") else "none",
            "fact_ids": sorted(set(found.get("fact_ids") or []))}


# ---- credentials are matched by name, never by level --------------------------------

# Each credential with the ways a posting or a transcript writes it. A profile records
# "Master of Public Health"; a posting asks for "MPH". Matching only the abbreviation made
# the one that was held invisible and sent a real requirement to manual review.
CREDENTIAL_FORMS = {
    "MD": (r"MD", r"doctor of medicine"),
    "DO": (r"DO", r"doctor of osteopath\w*"),
    "PharmD": (r"PharmD", r"doctor of pharmacy"),
    "DVM": (r"DVM", r"doctor of veterinary medicine"),
    "NP": (r"NP", r"nurse practitioner"),
    "PA": (r"PA-C", r"physician assistant"),
    "RN": (r"RN", r"registered nurse"),
    "MPH": (r"MPH", r"master of public health"),
    "MPP": (r"MPP", r"master of public policy"),
    "MBA": (r"MBA", r"master of business administration"),
    "MSN": (r"MSN", r"master of science in nursing"),
    "MS": (r"M\.?S\.?", r"MSc", r"master of science"),
    "MA": (r"M\.?A\.?", r"master of arts"),
    "PhD": (r"Ph\.?D\.?", r"doctor of philosophy"),
    "DrPH": (r"DrPH", r"doctor of public health"),
    "ScD": (r"ScD", r"doctor of science"),
    "BSN": (r"BSN", r"bachelor of science in nursing"),
    "BS": (r"B\.?S\.?", r"bachelor of science"),
    "BA": (r"B\.?A\.?", r"bachelor of arts"),
}
CREDENTIAL_PATTERN = {
    name: re.compile("|".join(rf"(?<![A-Za-z]){form}(?![A-Za-z])" for form in forms), re.I)
    for name, forms in CREDENTIAL_FORMS.items()}

# "Spartanburg, SC" and "Princeton, NJ" are places. A two-letter token after a comma is a
# state, not a credential, and reading one as a credential would claim a qualification.
STATE_SHAPED = re.compile(r",\s*[A-Z]{2}\b")


def credentials_in(text: str) -> list[str]:
    cleaned = STATE_SHAPED.sub(" ", str(text or ""))
    return [name for name, pattern in CREDENTIAL_PATTERN.items()
            if pattern.search(cleaned)]


def _credential_proposal(text: str, facts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """An explicit credential requirement, satisfied only by holding what it names.

    Two ways a requirement can name one, and they behave differently:

    - **By credential** — "MD, PharmD, NP, PA, RN, MPH" is a list of alternatives, and only
      holding one of *those* satisfies it. Reading it as "a master's or higher" let an MBA
      answer a clinical-credential requirement.
    - **By level** — "Bachelor's degree required" names no credential, so the level ladder is
      the right comparison and anything at or above it qualifies.

    A named field narrows either: "MS in Statistics" is not satisfied by an MS in something
    else. And nothing here proposes `does_not_meet`. Not finding a degree among the recorded
    education facts is not the same as the candidate not holding it, and no fact in this
    schema asserts that the education list is complete.
    """
    wanted_credentials = credentials_in(text)
    wanted_levels = named_levels(text)
    if not wanted_credentials and not wanted_levels:
        return None
    wanted_fields = [term for term in FIELD_TERMS
                     if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text.casefold())]

    reviewed, matches = [], []
    for fact in facts:
        if fact.get("type") not in ("education", "certification") \
                or fact.get("status") not in USABLE:
            continue
        value = str(fact.get("value") or "")
        reviewed.append(fact["id"])
        held_credentials = credentials_in(value)
        held_levels = named_levels(value)
        if wanted_credentials:
            hit = next((name for name in held_credentials if name in wanted_credentials), None)
        elif held_levels and DEGREE_LEVELS.index(held_levels[-1]) \
                >= DEGREE_LEVELS.index(wanted_levels[0]):
            hit = f"{held_levels[-1]} degree"
        else:
            hit = None
        if not hit:
            continue
        if wanted_fields and not (set(degree_fields(value)) & set(wanted_fields)):
            # The right credential in the wrong subject. Not a match, and not a mismatch
            # either — an open list may still admit it, and the user reads the field.
            continue
        matches.append((hit, fact["id"]))

    if matches:
        return _proposal(MEETS, confidence="medium",
                         fact_ids=[fid for _, fid in matches], evidence_class="direct",
                         status=EVIDENCE_FOUND,
                         reason=f"the profile holds {matches[0][0]}, which this requirement "
                                "names" + (f" in {', '.join(wanted_fields)}"
                                           if wanted_fields else ""))
    asked = " or ".join(wanted_credentials or [f"a {wanted_levels[0]} degree"])
    field_note = f" in {' or '.join(wanted_fields)}" if wanted_fields else ""
    return _proposal(None, confidence="none", fact_ids=sorted(set(reviewed)),
                     status=NO_EVIDENCE,
                     reason=f"asks for {asked}{field_note}; the recorded education facts do "
                            "not name it, and nothing in the profile asserts that the "
                            "education list is complete")


def _duration_proposal(text: str, span: dict[str, Any]) -> dict[str, Any] | None:
    if span.get("years") is None:
        return None
    match = DURATION.search(text)
    if not match:
        return None
    wanted = int(match.group(1))
    # No fact says the employment history is complete, so the earliest recorded start is not
    # the start of a career. The span is reported; the comparison is the user's.
    return _proposal(None, confidence="none", fact_ids=span["fact_ids"],
                     status=NO_EVIDENCE,
                     reason=f"asks for {wanted} years; the recorded experience headers span "
                            f"{span['years']} years ({span['earliest']} to {span['latest']}), "
                            "and nothing asserts that the employment record is complete")


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
    """Postings ordered by how little is left to judge.

    Not by how likely they are to be rejected — nothing here establishes a rejection. The
    posting with two open questions is read before the one with thirteen, because it is the
    one a person can finish.
    """
    def weight(entry: dict[str, Any]) -> tuple:
        items = entry.get("unread_requirements", [])
        open_questions = sum(1 for item in items
                             if item.get("proposed_disposition") in (None, PARTIALLY_MEETS))
        return (open_questions, len(items), entry["lane_rank"])
    return sorted(sheet.get("survivors", []), key=weight)


def render(sheet: dict[str, Any]) -> str:
    counts = sheet["proposals"]["counts"]
    total = sum(counts.values())
    lines = [
        "# Disposition proposals", "",
        f"Built {sheet['proposals']['built_at'][:19]}Z against the active CandidateSnapshot.",
        "",
        f"**{total} requirements read. {counts.get(MEETS, 0)} the facts fully answer, "
        f"{counts.get(PARTIALLY_MEETS, 0)} they partly answer, "
        f"{counts.get('unproposed', 0)} they cannot speak to.** Nothing is decided: every row "
        "needs your confirmation, and no queue row or application state has been touched.",
        "",
        "**Nothing is proposed as `does_not_meet`.** No fact in this profile asserts that the "
        "education, employment or skills record is complete, so a requirement the facts do "
        "not answer is unrecorded, not unmet. Until a scoped completeness assertion exists, "
        "the only honest answers are what the facts *do* support and silence.",
        "",
        "**`meets` requires every obligation in the sentence to resolve.** A qualifier — "
        "executive-level, senior, non-technical, lead teams, multiple projects, fast-paced, "
        "coder, modular — is an obligation of its own, and stays listed as unverified rather "
        "than disappearing because the noun it modifies matched. A compound requirement is "
        "held to its weakest necessary part, never its strongest.",
        "",
        "Postings are ordered by how little is left to judge.", "",
    ]
    for entry in decisive_first(sheet):
        items = entry.get("unread_requirements", [])
        open_questions = [item for item in items
                          if item.get("proposed_disposition") in (None, PARTIALLY_MEETS)]
        lines += ["", f"## {entry['employer']} — {entry['title']}", "",
                  f"{entry['location']} · {entry['lane']} #{entry['lane_rank']} · "
                  f"{entry['parsed_lines']} of {entry['stated_lines']} must-have lines "
                  f"evaluated by the queue · **{len(open_questions)} of {len(items)} still "
                  "open**", "",
                  "| requirement | proposal | evidence | still to verify | why |",
                  "|---|---|---|---|---|"]
        for item in items:
            requirement = item["requirement"].replace("|", "\\|")[:130]
            proposal = item.get("proposed_disposition") or "—"
            evidence = ", ".join(item.get("supporting_fact_ids") or []) or "—"
            unverified = ", ".join(item.get("unverified_obligations") or []) or "—"
            reason = (item.get("short_reason") or "").replace("|", "\\|")[:150]
            lines.append(f"| {requirement} | **{proposal}** | {evidence} | {unverified} "
                         f"| {reason} |")
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
