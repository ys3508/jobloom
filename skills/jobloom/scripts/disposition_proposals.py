#!/usr/bin/env python3
"""Proposing what the confirmed facts say about a requirement nothing could parse.

Fifty-seven blank cells is a memory test, not a review, and the answers are largely already in
the fact library. So this reads each unread requirement against the active CandidateSnapshot
and proposes a disposition, with the fact ids each part of it rests on.

Nothing here decides anything. Every proposal carries `requires_user_confirmation: true` and
`final_disposition: null`, and no queue row or application state is touched.

**A sentence cannot prove anything; a reviewed parse of it can.** Splitting requirement prose
on `or` and `and` cannot recover what a modifier written once applies to. `(Python or R) and
SQL` becomes `Python` / `R and SQL`, and Python alone satisfies it; `5+ years of production
experience with Python or R` leaves an alternative consisting of the letter R. Each fix to the
splitter moved the next wrong reading somewhere else, so the structure changed instead:
`parse_requirement` produces a reviewable artifact — source text and hash, parse version,
`or`-branches of `and`-obligations with exact spans into the sentence, any modifier whose
scope nobody has attributed, and a `parse_status`. Two statuses may yield `meets`, and they
are different claims: `closed_template` says a machine recognised one of two shapes with
nowhere for a modifier to hide — a bare degree level, or a flat list of credential
alternatives — where "nothing else" is checked against every character in the span, not
against the words the scan happened to read; `reviewed_complete` says a person approved this
parse, recorded in a registry against the sentence's hash, the distiller version and the tree
they saw. `ambiguous` and `unreviewed` conclude nothing. The invariant reads that provenance
rather than merely writing it, and `_proposal` recomputes it rather than believing a caller
who says there is nothing wrong.

Inside a branch, specialized resolvers — credential, duration, named technology, prose concept
— answer *one obligation*, and none may answer the sentence around it. Inside an obligation,
every concept keeps its own strength and its own fact ids, and the obligation is worth the
weakest of them: an obligation naming two capabilities asks for both. Taking a minimum after a
sentence-level matcher had already reported the strongest concept does not recover this — by
then the promotion has happened.

**`meets` requires the requirement text to be consumed.** Each resolver records the span it
read, and whatever is left over is residue. Substantive residue caps the answer at
`partially_meets`, whatever else matched. This replaces the curated list of qualifiers that
came before it: a list can only catch wording somebody already thought of, and "Advanced",
"in Mandarin" and every phrasing after them were disappearing silently. A qualifier is
evidenced by the facts supporting *its own* obligation, never borrowed from an unrelated one.

**Nothing is proposed as `does_not_meet`.** No fact in this schema asserts that the education,
employment or skills record is complete, so a requirement the facts do not answer is
unrecorded rather than unmet. Three things would license it — a confirmed negative fact, a
scoped completeness assertion, or a contradiction between positive facts — and none exists
yet. `unclear_ask_employer` is likewise never proposed: whether an employer wrote ambiguously
is a judgement about their prose, not a fact about the candidate.
"""

from __future__ import annotations

import argparse
import hashlib
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
from evidence_matcher import REQUIREMENT_CONCEPTS  # noqa: E402
from evidence_matcher import concept_evidence, match_requirement  # noqa: E402

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
              unresolved: list[str] | None = None, residue: list[str] | None = None,
              parse: dict[str, Any] | None = None,
              problems: list[str] | None = None) -> dict[str, Any]:
    # Not `problems` as passed in: a caller that hands this an empty list would be granting
    # itself the licence the invariant exists to withhold. For a `meets` the only list that
    # counts is the one recomputed here, from the parse and the obligations being reported.
    if disposition == MEETS:
        problems = meets_invariant_problems(parse or {}, obligations or [])
    proposal = {"proposed_disposition": disposition, "confidence": confidence,
                "supporting_fact_ids": sorted(fact_ids or []),
                "evidence_class": evidence_class, "short_reason": reason,
                "candidate_evidence_status": status,
                "requirement_parse": parse,
                "parse_status": (parse or {}).get("parse_status"),
                "meets_invariant_problems": problems or [],
                "obligations": obligations or [],
                "unresolved_obligations": unresolved or [],
                "unverified_obligations": sorted(set((unresolved or []) + (residue or []))),
                "unresolved_text": residue or [],
                "requires_user_confirmation": True, "final_disposition": None}
    # The one place a `meets` can leave this module, so the one place worth asserting at.
    if disposition == MEETS and proposal["meets_invariant_problems"]:
        raise AssertionError("meets proposed while the invariant reports "
                             f"{proposal['meets_invariant_problems']}")
    return proposal


# ---- credentials, matched by name -----------------------------------------------------

# Each credential with the ways a posting or a transcript writes it. A profile records
# "Master of Public Health"; a posting asks for "MPH". Matching only the abbreviation made
# the one that was held invisible.
CREDENTIAL_FORMS = {
    "MD": (r"MD", r"doctor of medicine"),
    "DO": (r"DO", r"doctor of osteopath\w*"),
    "PharmD": (r"PharmD", r"doctor of pharmacy"),
    "DVM": (r"DVM", r"doctor of veterinary medicine"),
    "NP": (r"NP", r"nurse practitioner"),
    # Bare "PA" is a physician assistant here and a state everywhere else; `STATE_SHAPED`
    # removes ", PA" before this runs, which is what keeps Philadelphia out.
    "PA": (r"PA-C", r"physician assistant", r"PA"),
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
# A university awards a degree; a board issues a licence. They live in different records, and
# reading both out of `held_degrees` meant a licence could be found in an education line —
# "Philadelphia PA" made the profile hold a physician assistant — while a licence the profile
# genuinely holds, recorded as a certification, could not be found at all.
ACADEMIC_CREDENTIALS = frozenset({"MD", "DO", "PharmD", "DVM", "MPH", "MPP", "MBA", "MSN",
                                  "MS", "MA", "PhD", "DrPH", "ScD", "BSN", "BS", "BA"})
LICENCE_CREDENTIALS = frozenset({"NP", "PA", "RN"})
CREDENTIAL_RECORD = {"education": ACADEMIC_CREDENTIALS, "certification": LICENCE_CREDENTIALS}
CREDENTIAL_RECORD_NAME = {name: record for record, names in CREDENTIAL_RECORD.items()
                          for name in names}

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


def held_credentials(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every credential the confirmed facts carry, from the record type that can carry it.

    Academic credentials come from education facts and licences from certification facts.
    Neither record answers for the other: an education line naming a state is not a licence,
    and a licence nobody recorded is absent from the record, not absent from the world.
    """
    held = []
    for fact in facts:
        allowed = CREDENTIAL_RECORD.get(str(fact.get("type") or ""))
        if not allowed or fact.get("status") not in USABLE:
            continue
        value = str(fact.get("value") or "")
        for name in credentials_in(value):
            if name not in allowed:
                continue
            held.append({"credential": name, "fact_id": fact["id"], "source": fact["type"],
                         "fields": degree_fields(value) if fact["type"] == "education" else [],
                         "value": value})
    return held


def named_technologies(text: str) -> list[str]:
    """The controlled tool names a requirement mentions, matched on whole tokens.

    Whole tokens because `Terra` is in the vocabulary and `Terraform` is not the same thing.
    """
    return [tool for tool in posting_sections.TOOL_TERMS
            if re.search(rf"(?<![A-Za-z0-9+#]){re.escape(tool)}(?![A-Za-z0-9+#])", text, re.I)]


# ---- a requirement is a parse, and the parse is reviewable ---------------------------

# The version of the distiller that produced a parse. It is written into every artifact so a
# reviewed parse cannot be silently inherited by a later, differently-behaved splitter.
PARSE_VERSION = "requirement-parse/2026-09-10"

# `reviewed_complete` used to be granted by a regex, which is not what the word means. A
# template match establishes that a machine recognised a closed shape; whether a person looked
# at this sentence is a separate fact, and only a registry entry records it. Both may conclude
# — the closed templates because there is nowhere in them for a modifier to hide, a reviewed
# parse because somebody checked this one — but they are no longer the same claim.
CLOSED_TEMPLATE = "closed_template"
REVIEWED_COMPLETE = "reviewed_complete"
AMBIGUOUS = "ambiguous"
UNREVIEWED = "unreviewed"
MEETS_ELIGIBLE = frozenset({CLOSED_TEMPLATE, REVIEWED_COMPLETE})

# Words that carry no obligation of their own: articles, conjunctions, prepositions, and the
# head nouns a concept surface already implies. Everything else left unconsumed is residue.
# Intensity and scope words are deliberately absent — "Advanced", "Executive-level" and
# "in Mandarin" are exactly what must survive as residue.
FUNCTION_WORDS = frozenset("""
a an the and or of in with for to on at as is are be been being both either any all that this
these those you your our we us their they it its from by into within across including include
have has had must should will would can could may might plus etc e.g i.e via using use used
skill skills ability abilities experience experiences knowledge proficiency proficient
background understanding demonstrated demonstrable proven track record required requirement
requirements year years plus_years work working
""".split())

OR_JOIN = re.compile(r",\s*or\s+|\s+or\s+|,(?=\s)", re.I)
AND_JOIN = re.compile(r";|,\s*and\s+|\s+and\s+|,(?!\s*(?:e\.g\.|i\.e\.))", re.I)

# "Education:", "Analytical Rigor:", "Communication:" — a label naming what the sentence is
# about, not an obligation inside it. And "or higher" extends a level rather than offering an
# alternative to it; splitting there produced a branch consisting of the word "higher".
LABEL_PREFIX = re.compile(r"^[A-Z][A-Za-z /&'-]{2,34}:\s+")
OR_HIGHER = re.compile(r"\s+or\s+(?:higher|above|greater|more)\b", re.I)

# The two closed templates that may produce `meets`. Both are shapes with nowhere for a
# modifier to hide: a bare degree level, and a flat list of credential alternatives. Anything
# with a field, a domain, a duration or a qualifier attached is not on this list, because in
# those shapes what the modifier attaches to is a judgement nobody has recorded.
DEGREE_WORDS = frozenset("""
bachelor bachelors master masters associate associates doctoral doctorate phd ms msc ma bs bsc
ba mph mba degree degrees or s
""".split())
# Words a posting adds around a level that constrain nothing: they mark the line as a
# requirement, which the tier already recorded. A word that narrows what is being asked for —
# a field, a domain, an intensity — is deliberately not here, and keeps the line off the
# template.
OBLIGATION_FREE = frozenset("require required requires requirement requirements minimum".split())
# `or higher` extends a level upward and constrains nothing the ladder does not already
# handle. It is the only phrase allowed to survive inside the template's span.
EXTENDS_UPWARD = frozenset("higher above greater more".split())
LEVEL_WORDS = DEGREE_WORDS - {"degree", "degrees", "or", "s"}
COMMA_LIST = re.compile(r",\s*(?:or\s+)?", re.I)
DURATION_HEAD = re.compile(r"^\s*(?:a\s+)?(?:minimum\s+of\s+|at\s+least\s+)?\d", re.I)
BRACKETED_ALTERNATIVE = re.compile(r"\([^)]*\bor\b[^)]*\)")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ast_sha256(parse: dict[str, Any]) -> str:
    """A fingerprint of the tree, so approving a sentence is not approving a later split."""
    shape = [[obligation["span"] for obligation in branch["obligations"]]
             for branch in parse.get("branches") or []]
    return _sha256(json.dumps([parse.get("source_sha256"), parse.get("parse_version"),
                               parse.get("template"), shape], sort_keys=True))


# What may be left between the pieces a template read: whitespace, and the punctuation that
# writes a degree name. A digit, a symbol or a character from another script is none of those,
# and `Bachelor's degree, 5+` was reaching the template because the word scan could not see
# the 5 — a duration, invisible, in a shape claiming to be closed over its own text.
TEMPLATE_FILLER = re.compile(r"[\s'\u2019.\-\u2013\u2014]*$")
WORD = re.compile(r"[A-Za-z][A-Za-z.]*")


def _covers_everything(text: str, start: int, end: int,
                       covered: list[tuple[int, int]]) -> bool:
    """True when nothing but filler sits outside the pieces a template claims to have read."""
    cursor, gaps = start, []
    for first, last in sorted(covered):
        gaps.append(text[cursor:first])
        cursor = max(cursor, last)
    gaps.append(text[cursor:end])
    return all(TEMPLATE_FILLER.fullmatch(gap) for gap in gaps)


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] in " \t•-–—;.,":
        start += 1
    while end > start and text[end - 1] in " \t•-–—;.,":
        end -= 1
    return start, end


def _depth_at(text: str, index: int) -> int:
    return text.count("(", 0, index) - text.count(")", 0, index)


def _split(text: str, pattern: re.Pattern[str], start: int, end: int,
           *, skip: re.Pattern[str] | None = None) -> list[tuple[int, int]]:
    """Segments of `text[start:end]`, as spans into the original string.

    Spans rather than substrings because a parse whose pieces cannot be pointed back at the
    sentence they came from is not reviewable: nobody can check the scope of a modifier they
    cannot locate. Matches inside brackets are left alone — a bracket is the one piece of
    scope the author wrote down explicitly, and cutting through it discards it.
    """
    pieces, cursor = [], start
    for match in pattern.finditer(text, start, end):
        if _depth_at(text, match.start()) > 0:
            continue
        if skip and skip.match(text, match.start()):
            continue
        pieces.append((cursor, match.start()))
        cursor = match.end()
    pieces.append((cursor, end))
    spans = [_trim(text, first, last) for first, last in pieces]
    return [(first, last) for first, last in spans if last > first]


def _obligation(text: str, span: tuple[int, int]) -> dict[str, Any]:
    start, end = span
    return {"text": text[start:end], "span": [start, end]}


def _branch(text: str, span: tuple[int, int],
            obligations: list[dict[str, Any]]) -> dict[str, Any]:
    start, end = span
    return {"text": text[start:end], "span": [start, end], "obligations": obligations}


def _degree_template(text: str, start: int, end: int) -> list[dict[str, Any]] | None:
    """`Bachelor's degree`, `Master's degree or higher` — a level, and nothing else at all.

    "Nothing else" is checked against every character in the span, not against the words the
    scan happened to recognise. A comma fails it too, which is right: a comma list is a list
    of alternatives, and the template below is the one that reads those.
    """
    covered, cleaned = [], []
    for match in WORD.finditer(text, start, end):
        covered.append(match.span())
        cleaned.append(match.group(0).replace(".", "").casefold())
    # An apostrophe splits `Bachelor's` into two words for the scan; the `s` is filler.
    cleaned = [word for word in cleaned if word != "s"]
    allowed = DEGREE_WORDS | OBLIGATION_FREE | EXTENDS_UPWARD
    if not cleaned or not all(word in allowed for word in cleaned):
        return None
    if not any(word in LEVEL_WORDS for word in cleaned):
        return None
    if not _covers_everything(text, start, end, covered):
        return None
    return [_branch(text, (start, end), [_obligation(text, (start, end))])]


def _credential_list(text: str, start: int, end: int
                     ) -> tuple[list[dict[str, Any]], list[str]] | None:
    """`MD, PharmD, NP, or 5+ years in clinical health IT` — alternatives, one per element.

    Complete only while every element is a bare credential, plus optionally a final
    alternative of a different kind — a duration, say — whose own modifiers cannot be read as
    applying to the credentials before it. `MD, PharmD, or MPH in public health` fails that:
    the field is written once, next to the last credential, and whether it governs all three
    is exactly the question a parse is not allowed to guess at.
    """
    spans = _split(text, COMMA_LIST, start, end)
    if len(spans) < 2 or not re.search(r",\s*or\s+", text[start:end], re.I):
        return None
    problems: list[str] = []
    for index, (first, last) in enumerate(spans):
        piece = text[first:last]
        names = credentials_in(piece)
        bare = len(names) == 1 and CREDENTIAL_PATTERN[names[0]].fullmatch(piece.strip())
        if bare:
            continue
        if index < len(spans) - 1:
            return None
        if names:
            # A credential with a tail: the tail may or may not distribute backwards.
            problems.append(f"modifier {piece!r} sits on the last credential alternative and "
                            "may govern the ones before it; nobody has attributed it")
        elif not DURATION_HEAD.match(piece):
            return None
    branches = [_branch(text, span, [_obligation(text, span)]) for span in spans]
    return branches, problems


def _asymmetric_scope(text: str, branches: list[dict[str, Any]]) -> list[str]:
    """Constraints one alternative carries and another does not.

    `5+ years of production experience with Python or R` splits into a branch holding the
    years, the production and the experience, and a branch holding the word `R`. Written out
    that way the second alternative asks for nothing, which is not what the sentence says —
    but which of the leading words reach across the `or` is a reading, not a fact. So the
    parse records the asymmetry and stops.
    """
    if len(branches) < 2:
        return []
    problems = []
    fields = [_fields_in(branch["text"]) for branch in branches]
    if any(fields) and not all(fields):
        named = sorted({term for group in fields for term in group})
        problems.append(f"field {', '.join(named)} is written next to one alternative only")
    durations = [bool(DURATION.search(branch["text"])) for branch in branches]
    if any(durations) and not all(durations):
        problems.append("a duration is written next to one alternative only")
    substantive = [residue_of(branch["text"], named_technologies(branch["text"])
                              + credentials_in(branch["text"]) + named_levels(branch["text"]))
                   for branch in branches]
    if any(substantive) and not all(substantive):
        extra = sorted({token for group in substantive for token in group})
        problems.append(f"{', '.join(extra)} qualifies one alternative only")
    return problems


def load_review_registry(path: Path | None) -> dict[str, dict[str, Any]]:
    """Parses a person has approved, keyed by the sha256 of the sentence they read.

    An entry names the distiller version and the tree it was approved against, so approving a
    sentence does not silently approve whatever a later splitter makes of it. No file means no
    reviewed parses, which is the honest default: nobody has reviewed anything yet.
    """
    if not path or not Path(path).exists():
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = raw.get("parses", raw) if isinstance(raw, dict) else {}
    return {str(key): value for key, value in entries.items() if isinstance(value, dict)}


def _apply_registry(parse: dict[str, Any], registry: dict[str, dict[str, Any]] | None
                    ) -> dict[str, Any]:
    entry = (registry or {}).get(parse["source_sha256"])
    if not entry:
        return parse
    if entry.get("parse_version") != parse["parse_version"]:
        parse["registry_note"] = (
            f"a review exists for this sentence against {entry.get('parse_version')!r}; this "
            f"parse is {parse['parse_version']!r}, so the approval does not carry over")
        return parse
    if entry.get("ast_sha256") != parse["ast_sha256"]:
        parse["registry_note"] = ("a review exists for this sentence but against a different "
                                  "tree; the approval does not carry over")
        return parse
    if not entry.get("approved_by"):
        parse["registry_note"] = "a registry entry without an approver approves nothing"
        return parse
    parse.update({"parse_status": REVIEWED_COMPLETE, "reviewed_by": entry["approved_by"],
                  "reviewed_at": entry.get("approved_at")})
    return parse


def parse_requirement(requirement: str, *,
                      registry: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The reviewable artifact between a sentence and a proposal.

    A regex that cuts on `or` cannot know what a modifier written once applies to, and each
    time it guessed wrong the guess arrived as a `meets`. Extending the heuristics would only
    move the next wrong guess somewhere else, so the parse now says which of three states it
    is in, and only the first may be used to conclude that a requirement is met:

    - `reviewed_complete` — the sentence is one of the closed templates below, which have
      nowhere for an unattributed modifier to sit.
    - `ambiguous` — a modifier's scope is genuinely undecided, and the parse says which one.
    - `unreviewed` — split for reading, by rules nobody has reviewed as sound for this shape.

    The AST is produced in all three cases, because the obligations and their evidence are
    what a person reads while deciding. What changes is what may be concluded from it.
    """
    text = requirement.strip()
    label = LABEL_PREFIX.match(text)
    start, end = _trim(text, label.end() if label else 0, len(text))
    parse: dict[str, Any] = {
        "source_text": text, "source_sha256": _sha256(text), "parse_version": PARSE_VERSION,
        "label": text[:label.end()].strip() if label else None,
        "template": None, "parse_status": UNREVIEWED, "unattributed_scope": [], "branches": [],
        "reviewed_by": None, "reviewed_at": None, "registry_note": None, "ast_sha256": None,
    }

    def settle(**fields: Any) -> dict[str, Any]:
        parse.update(fields)
        parse["ast_sha256"] = _ast_sha256(parse)
        return _apply_registry(parse, registry)

    if end <= start:
        return settle(branches=[_branch(text, (0, len(text)),
                                        [_obligation(text, (0, len(text)))])])

    degree = _degree_template(text, start, end)
    if degree:
        return settle(template="bare_degree_level", parse_status=CLOSED_TEMPLATE,
                      branches=degree)

    credentials = _credential_list(text, start, end)
    if credentials:
        branches, problems = credentials
        return settle(template="credential_alternatives", branches=branches,
                      unattributed_scope=problems,
                      parse_status=AMBIGUOUS if problems else CLOSED_TEMPLATE)

    branch_spans = _split(text, OR_JOIN, start, end, skip=OR_HIGHER)
    parse["branches"] = [
        _branch(text, span, [_obligation(text, piece)
                             for piece in _split(text, AND_JOIN, span[0], span[1])]
                or [_obligation(text, span)])
        for span in branch_spans]
    problems = _asymmetric_scope(text, parse["branches"])
    if BRACKETED_ALTERNATIVE.search(text[start:end]):
        problems.append("a bracketed alternative sits inside a longer requirement; what the "
                        "text outside the brackets applies to is not written down")
    return settle(unattributed_scope=problems,
                  parse_status=AMBIGUOUS if problems else UNREVIEWED)


def _fields_in(text: str) -> list[str]:
    return [term for term in FIELD_TERMS
            if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text.casefold())]


def _tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-z][A-Za-z+#\-]*", text.casefold())]


def residue_of(text: str, consumed: list[str]) -> list[str]:
    """Words of an obligation that no resolver read.

    The proof `meets` needs is that the requirement was consumed, not that it failed to trip
    a known pattern. Anything substantive left here means some part of the sentence was never
    answered, whatever else matched.
    """
    read = set()
    for span in consumed:
        read.update(_tokens(span))
    return [token for token in _tokens(text)
            if token not in read and token not in FUNCTION_WORDS and len(token) > 1]


# ---- what `meets` requires, in one place --------------------------------------------


def meets_invariant_problems(parse: dict[str, Any],
                             obligations: list[dict[str, Any]]) -> list[str]:
    """Why this branch may not be called met. Empty is the only licence to say `meets`.

    Checked wherever a `meets` is produced, so the conditions cannot drift apart from the
    sentence that describes them: the parse was reviewed and complete, no modifier's scope is
    unattributed, every obligation in the branch is directly evidenced, every mandatory
    concept inside those obligations has facts of its own, and no part of the text is left
    unread.
    """
    problems = []
    if parse.get("parse_status") not in MEETS_ELIGIBLE:
        problems.append("parse_not_eligible_to_conclude")
    # Provenance was being written and never read, which made it decoration. A parse from
    # another distiller version, or one whose text has moved under its hash, or whose spans no
    # longer cut the sentence they claim to, cannot license anything.
    required = ("source_text", "source_sha256", "parse_version", "ast_sha256", "branches")
    if any(not parse.get(name) for name in required):
        problems.append("missing_provenance")
    else:
        if parse["parse_version"] != PARSE_VERSION:
            problems.append("parse_version_mismatch")
        if parse["source_sha256"] != _sha256(parse["source_text"]):
            problems.append("source_hash_mismatch")
        if parse["ast_sha256"] != _ast_sha256(parse):
            problems.append("ast_hash_mismatch")
        source = parse["source_text"]
        for branch in parse["branches"]:
            for entry in branch["obligations"]:
                first, last = entry["span"]
                if source[first:last] != entry["text"]:
                    problems.append("span_does_not_match_source")
    if parse.get("unattributed_scope"):
        problems.append("shared_scope_unattributed")
    if not obligations:
        problems.append("no_obligation")
    for entry in obligations:
        if entry.get("strength") not in requirement_tiers.COVERING:
            problems.append("obligation_not_directly_evidenced")
        if entry.get("residue"):
            problems.append("unresolved_span")
        for concept in entry.get("concepts") or []:
            if not concept.get("fact_ids"):
                problems.append("concept_without_facts")
    return sorted(set(problems))


# ---- resolving one obligation, inside its branch ------------------------------------


def _resolve(obligation: str, facts: list[dict[str, Any]],
             degrees: list[dict[str, Any]], branch: str) -> dict[str, Any]:
    """One obligation against the facts, carrying its own fact ids and what it consumed.

    `branch` is the whole branch text, so a credential can find the field it is paired with
    without any resolver seeing outside its own alternative.
    """
    for resolver in (_credential_obligation, _duration_obligation, _technology_obligation,
                     _concept_obligation):
        found = resolver(obligation, facts, degrees, branch)
        if found:
            found["residue"] = residue_of(obligation, found.get("consumed") or [])
            return found
    return {"obligation": obligation, "kind": "unparsed", "strength": "none",
            "fact_ids": [], "consumed": [], "residue": residue_of(obligation, []),
            "note": "no controlled resolver reads this"}


def _credential_obligation(obligation: str, facts: list[dict[str, Any]],
                           degrees: list[dict[str, Any]], branch: str
                           ) -> dict[str, Any] | None:
    """A credential named in this obligation, paired with a field named in its own branch."""
    wanted_credentials = credentials_in(obligation)
    wanted_levels = named_levels(obligation)
    if not wanted_credentials and not wanted_levels:
        return None
    wanted_fields = [term for term in FIELD_TERMS
                     if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", branch.casefold())]
    consumed = list(wanted_credentials) + list(wanted_levels) + wanted_fields + [
        "degree", "degrees", "higher", "above", "education", "equivalent"]

    if wanted_credentials:
        for entry in held_credentials(facts):
            if entry["credential"] not in wanted_credentials:
                continue
            if wanted_fields and not (set(entry["fields"]) & set(wanted_fields)):
                continue
            return {"obligation": obligation, "kind": "credential", "strength": "direct",
                    "fact_ids": [entry["fact_id"]], "consumed": consumed,
                    "note": f"the profile holds {entry['credential']} "
                            f"({entry['source']} fact {entry['fact_id']})"}
    else:
        for entry in degrees:
            held_levels = named_levels(entry["value"])
            if not held_levels or DEGREE_LEVELS.index(held_levels[-1]) \
                    < DEGREE_LEVELS.index(wanted_levels[0]):
                continue
            if wanted_fields and not (set(entry["fields"]) & set(wanted_fields)):
                continue
            return {"obligation": obligation, "kind": "credential", "strength": "direct",
                    "fact_ids": [entry["fact_id"]], "consumed": consumed,
                    "note": f"the profile holds a {held_levels[-1]} degree "
                            f"(education fact {entry['fact_id']})"}

    asked = " or ".join(wanted_credentials or [f"a {wanted_levels[0]} degree"])
    field_note = f" in {' or '.join(wanted_fields)}" if wanted_fields else ""
    records = sorted({CREDENTIAL_RECORD_NAME[name] for name in wanted_credentials
                      if name in CREDENTIAL_RECORD_NAME}) or ["education"]
    consulted = sorted({fact["id"] for fact in facts
                        if fact.get("type") in records and fact.get("status") in USABLE})
    listed = " or ".join(records)
    return {"obligation": obligation, "kind": "credential", "strength": "none",
            "fact_ids": consulted, "consumed": consumed,
            "note": f"asks for {asked}{field_note}; the recorded {listed} facts do not name "
                    f"it, and nothing asserts the {listed} record is complete"}


def _duration_obligation(obligation: str, facts: list[dict[str, Any]],
                         degrees: list[dict[str, Any]], branch: str) -> dict[str, Any] | None:
    match = DURATION.search(obligation)
    if not match:
        return None
    span = career_span_years(facts)
    return {"obligation": obligation, "kind": "duration", "strength": "none",
            "fact_ids": span["fact_ids"], "consumed": [match.group(0)],
            "note": (f"asks for {match.group(1)} years; the recorded experience headers span "
                     f"{span['years']} years and nothing asserts the employment record is "
                     "complete") if span.get("years") is not None
            else "no dated experience header to compare against"}


def _technology_obligation(obligation: str, facts: list[dict[str, Any]],
                           degrees: list[dict[str, Any]], branch: str
                           ) -> dict[str, Any] | None:
    named = named_technologies(obligation)
    if not named:
        return None
    concepts, fact_ids = [], []
    for tool in named:
        found = match_requirement(tool, facts)
        concepts.append({"concept": tool, "strength": found["strength"],
                         "fact_ids": sorted(found["fact_ids"])})
        fact_ids.extend(found["fact_ids"])
    weakest = min((entry["strength"] for entry in concepts),
                  key=lambda name: EVIDENCE_ORDER[name])
    missing = [entry["concept"] for entry in concepts if entry["strength"] == "none"]
    return {"obligation": obligation, "kind": "named_technology", "strength": weakest,
            "fact_ids": sorted(set(fact_ids)), "consumed": named, "concepts": concepts,
            "note": ("no fact for " + ", ".join(missing)) if missing
            else "covered: " + ", ".join(named)}


# The concept for `degree` answers on any education fact naming a degree, which is a second
# and much looser route to a credential the resolver above declined: "advanced degree in
# Engineering" came back directly evidenced by a degree in something else. Degrees are settled
# by the credential resolver or not at all.
CONCEPTS_HANDLED_ELSEWHERE = frozenset({"degree"})


def _concept_obligation(obligation: str, facts: list[dict[str, Any]],
                        degrees: list[dict[str, Any]], branch: str) -> dict[str, Any] | None:
    """Every controlled concept in the obligation, each answered on its own facts.

    An obligation naming two capabilities asks for both, so it is worth what the weaker of
    them is worth. The sentence-level matcher reports the stronger — the right answer to a
    different question, and the reason a skill-list mention of Analysis was arriving as
    directly evidenced next to a presentation somebody actually gave.

    The consumed span is what makes the residue check work: the concept for communication
    reads "communication", so "Executive-level" is left over and the sentence cannot be met
    on a fact about presentations.
    """
    consumed, names = [], []
    for name, rule in REQUIREMENT_CONCEPTS.items():
        if name in CONCEPTS_HANDLED_ELSEWHERE:
            continue
        match = re.search(rule["requirement"], obligation, re.I)
        if match:
            consumed.append(match.group(0))
            names.append(name)
    if not names:
        return None
    concepts = [concept_evidence(name, facts) for name in names]
    weakest = min((entry["strength"] for entry in concepts),
                  key=lambda name: EVIDENCE_ORDER[name])
    missing = [entry["concept"] for entry in concepts if entry["strength"] == "none"]
    detail = ", ".join(f"{entry['concept']}: {entry['strength']}" for entry in concepts)
    return {"obligation": obligation, "kind": "concept", "strength": weakest,
            "fact_ids": sorted({fid for entry in concepts for fid in entry["fact_ids"]}),
            "consumed": consumed, "concepts": concepts,
            "note": ("no fact for " + ", ".join(missing)) if missing else detail}


def propose(requirement: str, facts: list[dict[str, Any]], *,
            degrees: list[dict[str, Any]] | None = None,
            span: dict[str, Any] | None = None,
            registry: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """One requirement, read against the confirmed facts. Decides nothing."""
    degrees = held_degrees(facts) if degrees is None else degrees
    text = requirement.strip()

    if MARKETING.search(text) or posting_sections.FALLBACK_EXCLUSION.search(text) \
            or requirement_tiers.ELIGIBILITY_STATEMENT.search(text):
        return _proposal(NOT_A_REQUIREMENT, confidence="high", status=NOT_APPLICABLE,
                         reason="company, eligibility or compensation prose rather than "
                                "something the candidate's evidence answers")

    parse = parse_requirement(text, registry=registry)
    evaluated = []
    for branch in parse["branches"]:
        resolved = [_resolve(item["text"], facts, degrees, branch["text"])
                    for item in branch["obligations"]]
        evaluated.append({"obligations": resolved,
                          "residue": sorted({token for entry in resolved
                                             for token in entry["residue"]}),
                          "unmet": [entry for entry in resolved
                                    if entry["strength"] == "none"],
                          "problems": meets_invariant_problems(parse, resolved)})

    satisfied = next((branch for branch in evaluated if not branch["problems"]), None)
    if satisfied:
        obligations, residue, unresolved = satisfied["obligations"], satisfied["residue"], []
        problems: list[str] = []
    else:
        # Nothing satisfied, so every alternative is still live and the reader needs all of
        # them. Reporting only the closest branch hid the obligations of the others: a
        # requirement whose second branch was nearly met stopped mentioning `coder` at all.
        obligations = [entry for branch in evaluated for entry in branch["obligations"]]
        residue = sorted({token for branch in evaluated for token in branch["residue"]})
        unresolved = [entry["obligation"] for branch in evaluated
                      for entry in branch["unmet"]]
        problems = min((branch["problems"] for branch in evaluated), key=len) \
            if evaluated else ["no_obligation"]
    fact_ids = [fid for entry in obligations for fid in entry["fact_ids"]]
    # The resolvers' own notes, which say what was found or what is missing. Without them the
    # reason was a generic sentence and the specific finding — "the profile holds MPH", "the
    # recorded headers span 4.3 years" — never reached the reader.
    notes = [entry["note"] for entry in obligations if entry.get("note")]

    if satisfied:
        weakest = min((entry["strength"] for entry in obligations),
                      key=lambda name: EVIDENCE_ORDER[name])
        return _proposal(MEETS, confidence="medium", fact_ids=fact_ids, parse=parse,
                         evidence_class=weakest, status=EVIDENCE_FOUND, problems=problems,
                         obligations=obligations, unresolved=unresolved, residue=residue,
                         reason="; ".join(notes) + " — every obligation in this branch "
                                "resolved and the requirement text was fully read")
    detail = []
    if unresolved:
        detail.append("unresolved: " + "; ".join(dict.fromkeys(unresolved)))
    if residue:
        detail.append("not read: " + ", ".join(residue))
    if parse["unattributed_scope"]:
        detail.append("scope not attributed: " + "; ".join(parse["unattributed_scope"]))
    if notes:
        detail.append("; ".join(dict.fromkeys(notes)))
    if not any(entry["strength"] != "none" for entry in obligations):
        return _proposal(None, confidence="none", fact_ids=fact_ids, status=NO_EVIDENCE,
                         obligations=obligations, unresolved=unresolved, residue=residue,
                         parse=parse, problems=problems,
                         reason="; ".join(detail) or "no obligation resolved to a confirmed "
                                                     "fact")
    weakest = min((entry["strength"] for entry in obligations
                   if entry["strength"] != "none"),
                  key=lambda name: EVIDENCE_ORDER[name])
    if weakest not in requirement_tiers.COVERING:
        detail.append(f"weakest evidence is {weakest}")
    elif "parse_not_reviewed_complete" in problems:
        detail.append("every obligation resolved, but no reviewed parse establishes what the "
                      "sentence's modifiers apply to, so this is a reading for you to confirm "
                      "rather than a conclusion")
    return _proposal(PARTIALLY_MEETS, confidence="medium", fact_ids=fact_ids,
                     evidence_class=weakest, status=EVIDENCE_FOUND, parse=parse,
                     obligations=obligations, unresolved=unresolved, problems=problems,
                     residue=residue, reason="; ".join(detail) or "partly covered")


def annotate(sheet: dict[str, Any], facts: list[dict[str, Any]],
             at: datetime | None = None,
             registry: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Add a proposal to every unread requirement in a worksheet. Changes nothing else."""
    degrees, span = held_degrees(facts), career_span_years(facts)
    counts: dict[str, int] = {}
    for entry in sheet.get("survivors", []):
        for item in entry.get("unread_requirements", []):
            proposal = propose(item["requirement"], facts, degrees=degrees, span=span,
                               registry=registry)
            item.update(proposal)
            key = proposal["proposed_disposition"] or "unproposed"
            counts[key] = counts.get(key, 0) + 1
    sheet["proposals"] = {
        "built_at": (at or now_utc()).isoformat(),
        "candidate_snapshot_sha256": sheet.get("candidate_snapshot_sha256"),
        "counts": counts,
        "parse_version": PARSE_VERSION,
        "reviewed_parses_available": len(registry or {}),
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
    """The sheet a person confirms from. Nothing on it is shortened.

    The previous version cut requirements at 130 characters and reasons at 150, with no
    ellipsis and nowhere to expand — and what it cut was the deciding half: "…while
    documenting changes", "…manage multiple projects simultaneously". A confirmation sheet
    that hides the clause the answer turns on is worse than no sheet.
    """
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
        "not answer is unrecorded, not unmet.",
        "",
        "**`meets` comes from a parse, not from a sentence.** Splitting prose on `or` "
        "cannot recover what a modifier written once applies to, so every requirement here "
        "carries a parse with a status. Only two conclude: `closed_template`, a shape with "
        "nowhere for a modifier to hide — a bare degree level, or a flat list of credential "
        "alternatives — recognised by rule with nobody reviewing the line; and "
        "`reviewed_complete`, a parse you approved against its hash and tree. Either way "
        "every obligation in the branch must be directly evidenced, every concept inside it "
        "must have facts of its own, and nothing substantive may be left unread. Everything "
        "else is read and reported for you to decide.",
        "",
        "Full text below — nothing is shortened.", "",
    ]
    for entry in decisive_first(sheet):
        items = entry.get("unread_requirements", [])
        open_questions = [item for item in items
                          if item.get("proposed_disposition") in (None, PARTIALLY_MEETS)]
        lines += ["", f"## {entry['employer']} — {entry['title']}", "",
                  f"{entry['location']} · {entry['lane']} #{entry['lane_rank']} · "
                  f"{entry['parsed_lines']} of {entry['stated_lines']} must-have lines "
                  f"evaluated by the queue · **{len(open_questions)} of {len(items)} still "
                  "open**", ""]
        for index, item in enumerate(items, 1):
            proposal = item.get("proposed_disposition") or "unproposed"
            lines += [f"### {index}. `{proposal}`", "",
                      f"> {item['requirement']}", ""]
            obligations = item.get("obligations") or []
            if obligations:
                lines += ["| obligation | read as | evidence | facts | unread |",
                          "|---|---|---|---|---|"]
                for ob in obligations:
                    unread = ", ".join(ob.get("residue") or []) or "—"
                    facts = ", ".join(ob.get("fact_ids") or []) or "—"
                    lines.append(
                        f"| {ob['obligation']} | {ob.get('kind', '—')} "
                        f"| {ob.get('strength', '—')} | {facts} | {unread} |")
                lines.append("")
            still = list(dict.fromkeys(
                (item.get("unresolved_obligations") or []) + (item.get("unresolved_text") or [])))
            parse = item.get("requirement_parse") or {}
            if parse:
                status = parse.get("parse_status")
                note = {CLOSED_TEMPLATE: f"closed template `{parse.get('template')}`, "
                                         "recognised by rule — nobody reviewed this line",
                        REVIEWED_COMPLETE: f"reviewed by {parse.get('reviewed_by')} on "
                                           f"{parse.get('reviewed_at')}",
                        AMBIGUOUS: "a modifier's scope is not attributable",
                        UNREVIEWED: "no reviewed parse of this shape"}.get(status, status)
                lines.append(f"- **parse:** `{status}` — {note}"
                             + ("; nothing here concludes `meets`"
                                if status not in MEETS_ELIGIBLE else ""))
                if parse.get("registry_note"):
                    lines.append(f"  - {parse['registry_note']}")
                for problem in parse.get("unattributed_scope") or []:
                    lines.append(f"  - {problem}")
            lines += [f"- **still to verify:** {'; '.join(still) if still else 'nothing'}",
                      f"- **why:** {item.get('short_reason') or '—'}",
                      f"- **your disposition:** {item.get('final_disposition') or '____'}",
                      ""]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--worksheet", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--parse", metavar="TEXT",
                        help="print the parse artifact for one requirement and stop. This is "
                             "what a reviewer approves: it carries the source hash, the tree "
                             "hash and the spans, which is what a registry entry pins.")
    parser.add_argument("--review-registry", type=Path,
                        help="JSON: source_sha256 -> {parse_version, ast_sha256, approved_by, "
                             "approved_at}. Absent means nobody has reviewed a parse yet.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()

    if args.parse:
        print(json.dumps(parse_requirement(args.parse), indent=2, ensure_ascii=False))
        return
    if not args.worksheet or not args.candidate:
        parser.error("--worksheet and --candidate are required unless --parse is given")

    sheet = json.loads(args.worksheet.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    sheet["candidate_snapshot_sha256"] = candidate.get("content_sha256")
    annotate(sheet, candidate.get("facts") or [],
             registry=load_review_registry(args.review_registry))
    if args.output:
        args.output.write_text(json.dumps(sheet, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(render(sheet), encoding="utf-8")
    print(json.dumps(sheet["proposals"]["counts"], indent=2))


if __name__ == "__main__":
    main()
