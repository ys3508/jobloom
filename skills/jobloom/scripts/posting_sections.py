#!/usr/bin/env python3
"""Pull JobCard fields out of the text of a posting the user has open.

Rules, not a model. A posting is written for a human in a shape that repeats: a heading
names a section, and the lines under it belong to that section until the next heading. So
the extractor looks for a controlled set of headings and reads what follows, rather than
guessing at meaning.

Everything here is a proposal. The card it feeds carries `requirements_reviewed: false`
until a person looks at it, because a heading that was misread would otherwise become a
requirement the candidate is judged against.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import capability_ontology  # noqa: E402
import pattern_matcher  # noqa: E402


SECTION_HEADINGS: dict[str, tuple[str, ...]] = {
    "required_skills": (
        "required", "requirements", "required qualifications", "minimum qualifications",
        "basic qualifications", "qualifications", "what you'll need", "what you need",
        "must have", "required skills",
    ),
    "preferred_skills": (
        "preferred", "preferred qualifications", "nice to have", "nice-to-have",
        "bonus points", "desired qualifications", "preferred skills", "a plus",
    ),
    "responsibilities": (
        "responsibilities", "key responsibilities", "what you'll do", "what you will do",
        "the role", "duties", "essential functions", "job summary", "position overview",
        "the successful applicant will",
        "in a highly collaborative environment, the successful applicant will",
    ),
    "compensation_structure": ("compensation", "salary", "pay", "pay range", "benefits"),
}
# Headings that end a section without starting one we keep.
CLOSING_HEADINGS = (
    "about us", "about the company", "about the team", "people you can reach out to",
    "meet the hiring team", "eeo", "equal opportunity", "physical requirements",
    "additional job details", "why work here", "follow us", "how to apply", "our values",
    "diversity", "accommodation", "legal", "notice",
)
BULLET = re.compile(r"^\s*(?:[-•·*▪◦]|\d+[.)])\s+")
SALARY_RANGE = re.compile(
    r"(?P<currency>[$€£])\s?(?P<low>\d[\d,]*(?:\.\d+)?)"
    r"\s*(?:-|–|—|to)\s*[$€£]?\s?(?P<high>\d[\d,]*(?:\.\d+)?)", re.I)
# "beginning at a minimum of $55,000 per year and a maximum of $60,000 per year"
SALARY_BOUNDS = re.compile(
    r"minimum\s+of\s+(?P<currency>[$€£])\s?(?P<low>\d[\d,]*(?:\.\d+)?)"
    r".{0,80}?maximum\s+of\s+[$€£]?\s?(?P<high>\d[\d,]*(?:\.\d+)?)", re.I | re.S)
SALARY_SINGLE = re.compile(r"(?P<currency>[$€£])\s?(?P<low>\d[\d,]*(?:\.\d+)?)")
# Order matters: a hybrid posting almost always says "remote" somewhere too — often in the
# label "Remote Type" directly above the word Hybrid — so the specific value is tested first.
WORK_ARRANGEMENTS = (("hybrid", "hybrid"), ("on-site", "on_site"), ("onsite", "on_site"),
                     ("on site", "on_site"), ("in-office", "on_site"), ("in office", "on_site"),
                     ("fully remote", "remote"), ("remote", "remote"))
EMPLOYMENT_TYPE_TABLE = (("full-time", "full_time"), ("full time", "full_time"),
                         ("part-time", "part_time"), ("part time", "part_time"),
                         ("internship", "internship"), ("intern", "internship"),
                         ("contract", "contract"), ("temporary", "contract"))

SPONSORSHIP_NEGATIVE = ("not able to sponsor", "unable to sponsor", "no sponsorship",
                        "will not sponsor", "does not sponsor", "without sponsorship now or in the future")
SPONSORSHIP_POSITIVE = ("visa sponsorship", "will sponsor", "sponsorship available",
                        "we sponsor", "h-1b sponsorship")
MAX_ITEMS = 40
MAX_ITEM_CHARS = 1_000
REQUIREMENT_CUE = re.compile(
    r"\b(?:require(?:d|s|ments?)?|qualifications?|experience|skills?|degree|"
    r"proficien(?:t|cy)|knowledge|ability|years?|must|preferred|familiar(?:ity)?|"
    r"expertise|background|communication|writing)\b", re.I)
FALLBACK_EXCLUSION = re.compile(
    r"\b(?:equal opportunity|accommodation|benefits?|compensation|salary|pay range)\b", re.I)


def _heading_key(line: str) -> str | None:
    text = line.strip().strip(":").casefold()
    if not text or len(text) > 100:
        return None
    for key, headings in SECTION_HEADINGS.items():
        if text in headings:
            return key
    if any(text.startswith(closing) for closing in CLOSING_HEADINGS):
        return "__close__"
    return None


def split_sections(text: str) -> dict[str, list[str]]:
    """Group the lines of a posting under the controlled headings they follow."""
    sections: dict[str, list[str]] = {key: [] for key in SECTION_HEADINGS}
    current: str | None = None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.search(r"\b(?:we are an )?equal opportunity employer\b|\bVEVRAA\b", line, re.I):
            current = None
            continue
        key = _heading_key(line)
        if key == "__close__":
            current = None
            continue
        if key:
            current = key
            continue
        if current is None:
            continue
        item = BULLET.sub("", line).strip(" ;.")
        if not item or len(item) > MAX_ITEM_CHARS:
            continue
        if len(sections[current]) < MAX_ITEMS:
            sections[current].append(item)
    return sections


def fallback_requirement_lines(text: str) -> dict[str, list[str]]:
    """Read requirement-like sentences when a page exposes prose without section markup.

    This is deliberately a fallback, not a new evaluator. It only restores sentences the
    employer actually wrote so the existing controlled term distillation and evidence
    logic can receive them.
    """
    required: list[str] = []
    preferred: list[str] = []
    for raw in str(text or "").splitlines():
        line = BULLET.sub("", raw).strip(" ;")
        if not line:
            continue
        pieces = re.split(r"(?<=[.!?;])\s+", line) if len(line) > MAX_ITEM_CHARS else [line]
        for piece in pieces:
            sentence = piece.strip(" ;")
            if not 12 <= len(sentence) <= MAX_ITEM_CHARS:
                continue
            if FALLBACK_EXCLUSION.search(sentence) or not REQUIREMENT_CUE.search(sentence):
                continue
            target = preferred if re.search(r"\b(?:preferred|nice to have|a plus|desired)\b",
                                             sentence, re.I) else required
            if sentence not in target and len(target) < MAX_ITEMS:
                target.append(sentence)
    return {"required_skills": required, "preferred_skills": preferred}


def _salary(text: str) -> dict[str, Any] | None:
    body = str(text or "")
    match = SALARY_BOUNDS.search(body) or SALARY_RANGE.search(body) or SALARY_SINGLE.search(body)
    if not match:
        return None

    def number(value: str | None) -> float | None:
        if not value:
            return None
        return float(value.replace(",", ""))

    groups = match.groupdict()
    low, high = number(groups.get("low")), number(groups.get("high"))
    if low is None or low < 1000:  # a bare "$60" is not an annual figure worth reporting
        return None
    currency = {"$": "USD", "€": "EUR", "£": "GBP"}.get(match.group("currency"), "USD")
    return {"currency": currency, "minimum": round(low),
            "maximum": round(high) if high else None, "period": "year"}


def _first_match(text: str, table: tuple[tuple[str, str], ...]) -> str | None:
    lowered = str(text or "").casefold()
    for surface, value in table:
        if re.search(rf"(?<![a-z]){re.escape(surface)}(?![a-z])", lowered):
            return value
    return None


def _sponsorship(text: str) -> str:
    lowered = str(text or "").casefold()
    if any(phrase in lowered for phrase in SPONSORSHIP_NEGATIVE):
        return "does_not_support"
    if any(phrase in lowered for phrase in SPONSORSHIP_POSITIVE):
        return "supports"
    return "unknown"


# Named technologies a posting lists as a requirement. Kept explicit: a controlled list of
# things a person either has used or has not, so an unknown tool is reported as unrecognised
# instead of being guessed at.
TOOL_TERMS = (
    "R", "Python", "SQL", "SAS", "SPSS", "Stata", "MATLAB", "Java", "Scala", "C++",
    "Linux", "Unix", "Bash", "shell scripting", "Git", "GitHub", "Docker", "AWS", "Azure",
    "Excel", "Tableau", "Power BI", "Looker", "Spark", "Hadoop", "Snowflake", "dbt",
    "Epic", "Clarity", "Caboodle", "Cogito", "REDCap", "EDC", "CTMS", "CDISC", "SDTM",
    "ADaM", "HL7", "FHIR", "ICD-10", "CPT coding", "Terra", "DNAnexus", "UK Biobank",
    "RNA-seq", "ChIP-seq", "ATAC-seq", "QGIS", "GeoDa", "HPC", "Tidyverse", "ggplot2",
)


def distill_terms(lines: list[str], *, ontology: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn requirement sentences into the terms evidence can actually be matched against.

    A requirement is written as a sentence, but evidence resolves per capability, so a
    twenty-word line matches nothing and would read as a gap the candidate does not have.
    Two controlled sources decide what a line names: the named technologies above, and the
    capability patterns already in the ontology. A line that yields neither is reported as
    unrecognised rather than silently dropped, because a requirement nobody parsed is not
    the same as a requirement nobody has.
    """
    ontology = ontology or capability_ontology.load_ontology()
    terms: list[str] = []
    capabilities: list[str] = []
    unrecognised: list[str] = []
    for line in lines:
        found: list[str] = []
        for tool in TOOL_TERMS:
            if re.search(rf"(?<![A-Za-z0-9+#]){re.escape(tool)}(?![A-Za-z0-9+#])", line, re.I):
                found.append(tool)
        matched_capability = False
        for capability in ontology["capabilities"]:
            if capability["layer"] != "SKILL":
                continue
            for pattern in capability["evidence_patterns"]:
                if pattern["type"] == "semantic_anchor":
                    continue
                if pattern_matcher.match(pattern, {"value": line}):
                    # The capability's own name is ours, not the posting's. Judging the
                    # candidate against "Statistical programming" when the posting said
                    # "programming skills in R" invents a requirement and then fails them
                    # on it, which is how someone who has R ends up short of a skill R is.
                    capabilities.append(capability["capability_id"])
                    matched_capability = True
                    break
        terms.extend(found)
        if not found and not matched_capability:
            unrecognised.append(line)
    ordered: list[str] = []
    for term in terms:
        if term not in ordered:
            ordered.append(term)
    return {"terms": ordered, "capabilities": sorted(set(capabilities)),
            "unrecognised": unrecognised}


def extract(text: str, *, title: str | None = None) -> dict[str, Any]:
    """Return the JobCard fields a posting states outright. Absent fields stay absent."""
    sections = split_sections(text)
    extraction_strategy = "posting_sections"
    if not sections["required_skills"] and not sections["preferred_skills"]:
        fallback = fallback_requirement_lines(text)
        if fallback["required_skills"] or fallback["preferred_skills"]:
            sections["required_skills"] = fallback["required_skills"]
            sections["preferred_skills"] = fallback["preferred_skills"]
            extraction_strategy = "job_description_fallback"
    # Employers often put a final "preferred but not required" sentence inside the
    # Required Skills block. Its own wording wins over the surrounding heading; treating
    # it as mandatory manufactures a hard gap the employer explicitly denied.
    expanded_required = [piece.strip() for line in sections["required_skills"]
                         for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line)
                         if piece.strip()]
    sections["required_skills"] = expanded_required
    softened = [line for line in sections["required_skills"]
                if re.search(r"\bpreferred\b.*\bnot required\b", line, re.I)]
    if softened:
        sections["required_skills"] = [line for line in sections["required_skills"]
                                       if line not in softened]
        sections["preferred_skills"].extend(line for line in softened
                                             if line not in sections["preferred_skills"])
    fields: dict[str, Any] = {}
    distilled: dict[str, dict[str, Any]] = {}
    unassessed: dict[str, list[str]] = {}
    requirement_lines: dict[str, list[dict[str, Any]]] = {}
    ontology = capability_ontology.load_ontology()
    for key in ("required_skills", "preferred_skills"):
        if not sections[key]:
            continue
        distilled[key] = distill_terms(sections[key], ontology=ontology)
        fields[key] = distilled[key]["terms"]
        fields[f"{key}_stated"] = sections[key]
        # A capability match helps routing, but the capability's internal label is not
        # the requirement the employer wrote. Keep every line that yielded no explicit
        # term visible for human review instead of silently calling the recognised terms
        # the complete posting.
        line_results = [(line, distill_terms([line], ontology=ontology)) for line in sections[key]]
        requirement_lines[key] = [
            {"text": line, "recognized_terms": result["terms"]}
            for line, result in line_results
        ]
        unassessed[key] = [line for line, result in line_results if not result["terms"]]
        if distilled[key]["capabilities"]:
            fields[f"{key}_capabilities"] = distilled[key]["capabilities"]
    for key in ("responsibilities", "compensation_structure"):
        if sections[key]:
            fields[key] = sections[key]
    if title and title.strip():
        fields["title"] = title.strip()
    salary = _salary(text)
    if salary:
        fields["salary"] = salary
    arrangement = _first_match(text, WORK_ARRANGEMENTS)
    if arrangement:
        fields["work_arrangement"] = arrangement
    employment = _first_match(text, EMPLOYMENT_TYPE_TABLE)
    if employment:
        fields["employment_type"] = employment
    fields["sponsorship"] = _sponsorship(text)
    fields["extraction"] = {
        "strategy": extraction_strategy,
        "needs_user_review": True,
        "sections_found": sorted(key for key in SECTION_HEADINGS if sections[key]),
        "unrecognised_requirements": {key: value["unrecognised"]
                                      for key, value in distilled.items() if value["unrecognised"]},
        "unassessed_requirements": {key: lines for key, lines in unassessed.items() if lines},
        "requirement_lines": requirement_lines,
        # A parser saying "success" after seeing one requirements sentence is more
        # dangerous than a clean failure: it invites a confident judgement from a
        # fragment. Responsibilities are the work itself and must travel with the
        # qualification lines whenever the page supplies a short structured extract.
        "read_status": (
            "partial"
            if (sections["required_skills"] or sections["preferred_skills"])
            and not sections["responsibilities"]
            and len([line for line in str(text or "").splitlines() if line.strip()]) <= 2
            else "complete"
        ),
        "body_characters": len(str(text or "").strip()),
        "responsibility_lines": len(sections["responsibilities"]),
    }
    return fields
