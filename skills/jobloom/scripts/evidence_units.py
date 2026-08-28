"""Normalize CandidateFacts into immutable, provenance-carrying EvidenceUnits."""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from evidence_matcher import EVIDENCE_ORDER, tokens  # noqa: E402
import quantity_extractor  # noqa: E402


STRENGTH_RULE_VERSION = "1.0.0"
OUTCOME = re.compile(r"\b(?:improved?|increased?|reduced?|delivered?|led|launched?|published?|built|created|analy[sz]ed)\b|提升|增长|降低|交付|主导|发布", re.I)
FIRST_OWNER = re.compile(r"\b(?:i|my)\s+(?:led|built|created|managed|developed|designed|analy[sz]ed)\b|我(?:主导|负责|建立|开发|设计|分析)", re.I)
SCOPE = re.compile(r"\b(?:team|audience|budget|region|site|patient|record|trial|product)s?\b|团队|受众|预算|地区|患者|记录|试验|产品", re.I)


def _months(start: str | None, end: str | None) -> int | None:
    try:
        a = datetime.fromisoformat(f"{start}-01" if start and len(start) == 7 else str(start))
        b = datetime.fromisoformat(f"{end}-01" if end and len(end) == 7 else str(end))
    except (TypeError, ValueError):
        return None
    return max(0, (b.year - a.year) * 12 + b.month - a.month)


def strength_signals(fact: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    text = str(fact.get("value", ""))
    fired, score = [], 0.30
    if quantity_extractor.is_quantified(text):
        fired.append("quantified(+0.25)"); score += 0.25
    if OUTCOME.search(text):
        fired.append("outcome_verb(+0.15)"); score += 0.15
    if SCOPE.search(text):
        fired.append("scope(+0.10)"); score += 0.10
    if FIRST_OWNER.search(text):
        fired.append("first_owner(+0.15)"); score += 0.15
    temporal = fact.get("temporal") or {}
    if temporal.get("start") and temporal.get("end"):
        fired.append("duration(+0.05)"); score += 0.05
    end = temporal.get("end")
    if isinstance(end, str) and len(end) >= 4 and end[:4].isdigit():
        age = (today or date.today()).year - int(end[:4])
        if age <= 3: fired.append("recency(+0.05)"); score += 0.05
        elif age > 8: fired.append("recency(-0.10)"); score -= 0.10
    if len([part for part in re.split(r"[.!?。！？]+", text) if part.strip()]) <= 1:
        fired.append("single_sentence_only(-0.15)"); score -= 0.15
    return {"strength": round(max(0.0, min(1.0, score)), 2),
            "signals_fired": fired, "strength_rule_version": STRENGTH_RULE_VERSION,
            "quantified": any(x.startswith("quantified") for x in fired)}


def normalize_fact(fact: dict[str, Any], snapshot_sha256: str, *, today: date | None = None) -> dict[str, Any]:
    if fact.get("evidence_strength") not in EVIDENCE_ORDER:
        raise ValueError("EvidenceUnit source_strength is invalid")
    if fact.get("status") not in {"confirmed", "locked", "proposed"}:
        raise ValueError("EvidenceUnit fact status is unusable")
    fact_id = str(fact.get("id") or "")
    if not fact_id:
        raise ValueError("EvidenceUnit requires fact_id")
    temporal = dict(fact.get("temporal") or {})
    temporal.setdefault("months", _months(temporal.get("start"), temporal.get("end")))
    surfaces = [fact.get("value", ""), *(fact.get("keywords") or [])]
    unit = {
        "unit_id": "eu-" + hashlib.sha256(f"{snapshot_sha256}:{fact_id}".encode()).hexdigest()[:16],
        "fact_id": fact_id, "snapshot_sha256": snapshot_sha256,
        "source_strength": fact["evidence_strength"],
        "surface_terms": sorted({token for surface in surfaces for token in tokens(str(surface))}),
        "temporal": temporal, "source_kind": str(fact.get("type") or "experience_claim"),
        "employer": fact.get("employer"), "locked": bool(fact.get("locked")),
        **strength_signals(fact, today=today),
    }
    return unit


def build_units(facts: list[dict[str, Any]], snapshot_sha256: str, *, mode: str = "verified", today: date | None = None) -> list[dict[str, Any]]:
    wanted = {"confirmed", "locked"} if mode == "verified" else {"proposed"}
    return [normalize_fact(fact, snapshot_sha256, today=today) for fact in facts if fact.get("status") in wanted]
