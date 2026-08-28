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

# Deliberately small and curated. Add domain aliases here with regression tests; do not
# use fuzzy matching for evidence decisions.
TOKEN_ALIASES = {
    "statistical": "statistic",
    "statistics": "statistic",
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
    best_strength = max((fact["evidence_strength"] for fact in matches),
                        key=lambda strength: EVIDENCE_ORDER[strength])
    strongest = [fact for fact in matches
                 if fact["evidence_strength"] == best_strength]
    best_quality = max(_match_quality(requirement, fact) for fact in strongest)
    fact_ids = sorted(
        fact["id"] for fact in strongest if _match_quality(requirement, fact) == best_quality
    )
    return {"requirement": requirement, "strength": best_strength, "fact_ids": fact_ids}
