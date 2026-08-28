"""Assign controlled Capability relations to EvidenceUnits without grade inflation."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from evidence_matcher import EVIDENCE_ORDER, STRENGTH_FACTORS  # noqa: E402
import capability_ontology  # noqa: E402
import pattern_matcher  # noqa: E402


FACT_TYPE_CAPS = {
    "professional_summary": "mention_only", "resume_claim": "mention_only",
    "skill": "mention_only", "certification": "strongly_related",
    "education": "strongly_related", "experience_header": "strongly_related",
}


def weaker(*strengths: str) -> str:
    return min(strengths, key=lambda value: EVIDENCE_ORDER[value])


def classify_gap(fact_store_hit: bool, resume_hit: bool | None, quantified: bool) -> str:
    if not fact_store_hit:
        return "real_gap"
    if resume_hit is None:
        return "not_yet_presented"
    if not resume_hit:
        return "hidden_strength"
    return "resume_gap" if quantified else "evidence_gap"


def assign_capabilities(
    facts: list[dict[str, Any]], units: list[dict[str, Any]], ontology: dict[str, Any],
    *, mode: str, resume_fact_ids: set[str] | None = None,
    semantic_results: dict[tuple[str, str], dict[str, Any] | bool] | None = None,
) -> list[dict[str, Any]]:
    unit_by_fact = {unit["fact_id"]: unit for unit in units}
    refs = []
    for cap in ontology["capabilities"]:
        if cap["layer"] != "SKILL":
            continue
        for pattern in cap["evidence_patterns"]:
            for fact in facts:
                unit = unit_by_fact.get(fact.get("id"))
                if not unit:
                    continue
                semantic = (semantic_results or {}).get((pattern["pattern_id"], fact["id"]))
                if not pattern_matcher.match(pattern, fact, semantic_result=semantic,
                                             verified=mode == "verified"):
                    continue
                cap_strength = FACT_TYPE_CAPS.get(unit["source_kind"], "direct")
                if mode == "provisional":
                    cap_strength = weaker(cap_strength, "transferable")
                pattern_cap = pattern.get("max_grade", "direct")
                relation = weaker(unit["source_strength"], cap_strength, pattern_cap)
                matched_term = (" ".join(pattern["tokens"]) if pattern["type"] == "token_run"
                                else pattern.get("text") or pattern.get("anchor"))
                refs.append({
                    "unit_id": unit["unit_id"], "fact_id": unit["fact_id"],
                    "snapshot_sha256": unit["snapshot_sha256"],
                    "capability_id": cap["capability_id"],
                    "source_strength": unit["source_strength"],
                    "relation_strength": relation, "relation_source": "pattern_hit",
                    "pattern_id": pattern["pattern_id"], "rule_id": "R-FACET-PATTERN-01",
                    "matched_term": matched_term,
                    "assigned_by": "controlled_rule", "strength": unit["strength"],
                    "employer": unit.get("employer"),
                    "signals_fired": unit["signals_fired"],
                    "resume_hit": None if resume_fact_ids is None else unit["fact_id"] in resume_fact_ids,
                })
    return sorted(refs, key=lambda ref: (ref["capability_id"], ref["fact_id"], ref["pattern_id"]))


def reconcile_function(function: dict[str, Any], refs: list[dict[str, Any]]) -> dict[str, Any]:
    by_cap: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        by_cap.setdefault(ref["capability_id"], []).append(ref)
    total = sum(sig["weight"] for sig in function["capability_signature"])
    earned, gaps, selected = 0.0, [], []
    for sig in function["capability_signature"]:
        hits = by_cap.get(sig["capability_id"], [])
        if not hits:
            gaps.append({"capability_id": sig["capability_id"], "core": sig["core"],
                         "kind": "real_gap"})
            continue
        best = max(hits, key=lambda ref: (EVIDENCE_ORDER[ref["relation_strength"]],
                                          ref["strength"], ref["fact_id"]))
        selected.extend(hits)
        earned += sig["weight"] * STRENGTH_FACTORS[best["relation_strength"]] * best["strength"]
    return {"function_id": function["function_id"], "evidence_refs": selected, "gaps": gaps,
            "evidence_fit": round(100 * earned / total) if total else 0}


def load_and_assign(facts: list[dict[str, Any]], units: list[dict[str, Any]], *, mode: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ontology = capability_ontology.load_ontology()
    return ontology, assign_capabilities(facts, units, ontology, mode=mode)
