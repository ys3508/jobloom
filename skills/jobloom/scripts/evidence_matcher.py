"""Shared, deterministic resolution of job requirements to candidate facts."""

from __future__ import annotations

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from _common import parse_time  # noqa: E402


EVIDENCE_ORDER = {
    "none": 0,
    "mention_only": 1,
    "transferable": 2,
    "strongly_related": 3,
    "direct": 4,
}

# Numeric contribution factors share the same five-level vocabulary.  Keep the
# ordering and factors together so routing, resume claims, and career discovery
# cannot silently drift into separate evidence scales.
STRENGTH_FACTORS = {
    "direct": 1.0,
    "strongly_related": 0.85,
    "transferable": 0.6,
    "mention_only": 0.35,
    "none": 0.0,
}

# A declared grade cannot promote the surface that mentioned a capability into a kind of
# evidence that surface does not contain. A skill row can confirm that Python was listed
# and a course can establish exposure; neither establishes demonstrated Python work.
FACT_TYPE_STRENGTH_CAPS = {
    "professional_summary": "mention_only",
    "resume_claim": "mention_only",
    "skill": "mention_only",
    "education": "transferable",
    "certification": "strongly_related",
    "experience_header": "strongly_related",
}

# Deliberately small and curated. Add domain aliases here with regression tests; do not
# use fuzzy matching for evidence decisions.
TOKEN_ALIASES = {
    "statistical": "statistic",
    "statistics": "statistic",
}

# Requirement prose names capabilities, not always products. The ordinary matcher stays
# deliberately strict for explicit terms (R means R); this controlled layer lets a whole
# sentence such as "research, writing, and analysis skills" consult the fact store by the
# capabilities it actually asks for. Every surface is curated and every hit still comes
# from one confirmed fact -- no fuzzy or model-authored evidence.
REQUIREMENT_CONCEPTS = {
    "degree": {
        "requirement": r"\b(?:bachelor'?s?|master'?s?|degree)\b",
        "evidence": ("bachelor", "master", "mph", "degree"),
    },
    "research": {
        "requirement": r"\bresearch\b",
        "evidence": ("research", "literature review", "clinical trial"),
    },
    "writing": {
        "requirement": r"\b(?:writ(?:e|ing|ten)|written deliverables?|manuscripts?)\b",
        "evidence": ("writing", "drafted", "manuscript", "publication", "published", "report"),
    },
    "analysis": {
        "requirement": r"\b(?:analys(?:is|es)|analy(?:tic|tical|ze|zed|zing))\b",
        "evidence": ("analysis", "analytic", "analyzed", "statistical"),
    },
    "database_creation": {
        "requirement": r"\b(?:database (?:creation|management)|create databases?)\b",
        "evidence": ("built a database", "building comprehensive databases", "database management",
                     "clinical trial database"),
    },
    "office": {
        "requirement": r"\bmicrosoft office\b",
        "evidence": ("microsoft office", "advanced excel"),
    },
    "presentation": {
        "requirement": r"\b(?:present(?:ation|ations|ed)?|communicat(?:e|ion))\b",
        "evidence": ("presentation", "presentations", "communicating analysis results",
                     "communicated results"),
    },
    "synthesis": {
        "requirement": r"\b(?:summari[sz]e|synthesi[sz]e)\b",
        "evidence": ("synthesized", "reports", "literature review"),
    },
    "interpersonal": {
        "requirement": r"\binterpersonal\b",
        "evidence": ("stakeholders", "focus groups", "communicated", "collaborat"),
    },
    "organization": {
        "requirement": r"\borganizational\b",
        "evidence": ("project management", "coordinat", "organized", "management"),
    },
    "neuroimaging": {
        "requirement": r"\b(?:neuroimaging|multimodal mri|mri)\b",
        "evidence": ("neuroimaging", "mri", "magnetic resonance imaging"),
    },
}


def expired_or_invalid(value: str | None, *, today: date | None = None) -> bool:
    """Treat malformed expiry data as unusable evidence instead of crashing routing."""
    if not value:
        return False
    try:
        parsed = parse_time(value)
    except (TypeError, ValueError):
        return True
    if parsed is None:
        return False
    if "T" not in value:
        return parsed.date() < (today or date.today())
    now = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) if today else datetime.now(timezone.utc)
    return parsed < now


def tokens(value: str) -> list[str]:
    raw = re.findall(r"[^\W_]+(?:[+#]+)?", str(value).casefold())
    return [TOKEN_ALIASES.get(token, token) for token in raw]


def fact_tokens(fact: dict[str, Any]) -> set[str]:
    """Return terms from one evidence unit; tokens never leak across facts."""
    surfaces = [fact.get("value", ""), *(fact.get("keywords") or [])]
    return {token for surface in surfaces for token in tokens(str(surface))}


def fact_supports(requirement: str, fact: dict[str, Any]) -> bool:
    wanted = tokens(requirement)
    available = fact_tokens(fact)
    return bool(wanted) and all(token in available for token in wanted)


def _match_quality(requirement: str, fact: dict[str, Any]) -> int:
    wanted = str(requirement).strip().casefold()
    surfaces = [fact.get("value", ""), *(fact.get("keywords") or [])]
    return 2 if any(str(surface).strip().casefold() == wanted for surface in surfaces) else 1


def effective_strength(fact: dict[str, Any]) -> str:
    """Return declared strength capped by what the fact's source kind can prove."""
    declared = fact["evidence_strength"]
    cap = FACT_TYPE_STRENGTH_CAPS.get(
        str(fact.get("type") or "experience_claim"), "direct")
    return min((declared, cap), key=lambda strength: EVIDENCE_ORDER[strength])


def related_facts(requirement: str, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [fact for fact in facts if fact_supports(requirement, fact)]


def match_requirement(requirement: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [
        fact for fact in related_facts(requirement, facts)
        if fact.get("status") in {"confirmed", "locked"}
        and fact.get("evidence_strength") in EVIDENCE_ORDER
        and fact.get("evidence_strength") != "none"
        and not expired_or_invalid(fact.get("expires_at"))
    ]
    if not matches:
        return {"requirement": requirement, "strength": "none", "fact_ids": []}
    effective = [(fact, effective_strength(fact)) for fact in matches]
    best_strength = max((strength for _, strength in effective),
                        key=lambda strength: EVIDENCE_ORDER[strength])
    strongest = [fact for fact, strength in effective if strength == best_strength]
    best_quality = max(_match_quality(requirement, fact) for fact in strongest)
    fact_ids = sorted(
        fact["id"] for fact in strongest if _match_quality(requirement, fact) == best_quality
    )
    return {"requirement": requirement, "strength": best_strength, "fact_ids": fact_ids}


def match_requirement_prose(requirement: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve a prose requirement through controlled capability surfaces.

    `recognized` distinguishes "the user lacks this" from "the adapter has no safe rule
    for this sentence". Only the second belongs in manual review.
    """
    concepts = [name for name, rule in REQUIREMENT_CONCEPTS.items()
                if re.search(rule["requirement"], requirement, re.I)]
    if re.search(r"\b\d+\s*(?:\+\s*)?years?\b.*\bexperience\b", requirement, re.I):
        concepts.append("dated_experience")
    if not concepts:
        return {"requirement": requirement, "recognized": False,
                "strength": "none", "fact_ids": [], "concepts": []}

    usable = [fact for fact in facts
              if fact.get("status") in {"confirmed", "locked"}
              and fact.get("evidence_strength") in EVIDENCE_ORDER
              and fact.get("evidence_strength") != "none"
              and not expired_or_invalid(fact.get("expires_at"))]
    hits: dict[str, list[dict[str, Any]]] = {}
    for concept in concepts:
        if concept == "dated_experience":
            hits[concept] = [fact for fact in usable
                             if fact.get("type") == "experience_header"
                             and re.search(r"\b(?:research|data|database|analyst|clinical)\b",
                                           str(fact.get("value", "")), re.I)]
            continue
        aliases = REQUIREMENT_CONCEPTS[concept]["evidence"]
        hits[concept] = [fact for fact in usable
                         if any(all(token in fact_tokens(fact) for token in tokens(alias))
                                for alias in aliases)]
    if any(not hits[concept] for concept in concepts):
        return {"requirement": requirement, "recognized": True,
                "strength": "none", "fact_ids": sorted({fact["id"] for group in hits.values()
                                                          for fact in group}),
                "concepts": concepts}
    best_per_concept = [max(group, key=lambda fact: EVIDENCE_ORDER[effective_strength(fact)])
                        for group in hits.values()]
    strongest_hits = [fact for group in hits.values()
                      for fact in group
                      if effective_strength(fact) == max(
                          (effective_strength(item) for item in group),
                          key=lambda value: EVIDENCE_ORDER[value])]
    # All named concepts have evidence at this point. A skill-list fact may carry only a
    # mention grade while another concept in the same employer bullet is backed by a
    # direct accomplishment (for example, Microsoft Office plus building three databases).
    # Preserve the strongest actual accomplishment; the UI still shows every supporting
    # fact and therefore does not turn a mention into invented work.
    strength = max((effective_strength(fact) for fact in best_per_concept),
                   key=lambda value: EVIDENCE_ORDER[value])
    return {"requirement": requirement, "recognized": True, "strength": strength,
            "fact_ids": sorted({fact["id"] for fact in strongest_hits}),
            "concepts": concepts,
            "quantification_expected": not set(concepts).issubset(
                {"degree", "dated_experience", "office", "interpersonal", "organization"})}
