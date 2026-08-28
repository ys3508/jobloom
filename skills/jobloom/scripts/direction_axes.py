"""Independent Career Direction axes, readiness gates, and stable ranking."""

from __future__ import annotations

from typing import Any

from evidence_matcher import EVIDENCE_ORDER
import career_intent
import facet_taxonomy


READINESS_ORDER = {"ready_now": 0, "near_term": 1, "unverified_market": 2,
                   "build_toward": 3, "stretch": 4, "unsupported": 5}


def build_direction(function: dict[str, Any], hypothesis: dict[str, Any], refs: list[dict[str, Any]],
                    *, goals: dict[str, Any] | None = None, market: dict[str, Any] | None = None) -> dict[str, Any]:
    reconciliation = facet_taxonomy.reconcile_function(function, refs)
    selected = reconciliation["evidence_refs"]
    by_strength = {key: [] for key in ("direct", "strongly_related", "transferable", "mention_only")}
    for ref in selected:
        if ref["relation_strength"] != "none":
            by_strength[ref["relation_strength"]].append(ref)
    diversity = hypothesis["diversity"]
    coherence = None if diversity["distinct_facts"] < 3 else round(
        min(1.0, 0.4 + 0.15 * diversity["distinct_facts"]
            + 0.1 * min(diversity["distinct_employers"], 2)), 2)
    intent, excluded = career_intent.score(function, goals)
    sufficient_market = market if market and market.get("sample", {}).get("sufficient") else None
    required_terms = [str(item["term"]).casefold() for item in (sufficient_market or {}).get("required_terms", [])]
    matched_terms = {str(ref.get("matched_term", "")).casefold() for ref in selected}
    covered_terms = [term for term in required_terms if term in matched_terms]
    accessibility = round(100 * len(covered_terms) / len(required_terms)) if required_terms else (100 if sufficient_market else None)
    growth = None
    if goals and goals.get("skills_to_build"):
        gap_ids = {gap["capability_id"].casefold() for gap in reconciliation["gaps"]}
        growth = round(100 * sum(item.casefold() in gap_ids for item in goals["skills_to_build"]) / len(goals["skills_to_build"]))
    sponsorship = (sufficient_market or {}).get("sponsorship_distribution", {})
    visa = "unknown"
    if sponsorship:
        if sponsorship.get("supports", 0) >= .5: visa = "supported"
        elif sponsorship.get("does_not_support", 0) >= .5: visa = "hostile"
        elif sponsorship.get("supports", 0) or sponsorship.get("does_not_support", 0): visa = "mixed"
    axes = {
        "evidence_fit": {"value": reconciliation["evidence_fit"], "unit": "0-100", "rule_id": "R-EF-01"},
        "career_distance": {"value": 0, "unit": "facet_hops", "rule_id": "R-CD-01"},
        "narrative_coherence": ({"value": coherence, "unit": "0-1", "rule_id": "R-NC-01"}
                                if coherence is not None else {"value": None, "unit": "0-1", "null_reason": "supporting_units_below_minimum", "rule_id": "R-NC-01"}),
        "market_capacity": ({"value": sufficient_market["sample"]["postings_after_dedupe"], "unit": "postings_per_window", "rule_id": "R-MC-01"}
                            if sufficient_market else {"value": None, "unit": "postings_per_window", "null_reason": "market_profile_unavailable", "rule_id": "R-MC-01"}),
        "accessibility": ({"value": accessibility, "unit": "0-100", "rule_id": "R-AC-01"} if sufficient_market else {"value": None, "unit": "0-100", "null_reason": "market_profile_unavailable", "rule_id": "R-AC-01"}),
        "user_intent": ({"value": intent, "unit": "0-100", "rule_id": "R-UI-01"} if intent is not None else {"value": None, "unit": "0-100", "null_reason": "career_goals_not_supplied", "rule_id": "R-UI-01"}),
        "career_growth": ({"value": growth, "unit": "0-100", "rule_id": "R-CG-01"} if growth is not None else {"value": None, "unit": "0-100", "null_reason": "career_goals_not_supplied", "rule_id": "R-CG-01"}),
        "visa_compatibility": {"value": visa, "unit": "enum", "rule_id": "R-VI-01"},
    }
    signature = function["capability_signature"]
    core_ids = [item["capability_id"] for item in signature if item.get("core")]
    covered_ids = {ref["capability_id"] for ref in selected if ref["relation_strength"] != "none"}
    unsupported_core = sorted(set(core_ids) - covered_ids)
    coverage = {
        "capability": {"required": len(signature), "covered": len(covered_ids & {i["capability_id"] for i in signature})},
        "core_capability": {"required": len(core_ids), "covered": len(set(core_ids) & covered_ids)},
        "domain": {"required": len(function.get("domains") or []),
                   "covered": len({ref.get("domain") for ref in selected if ref.get("domain")})},
        "seniority": {"band": function.get("seniority_band"), "candidate_band": hypothesis.get("seniority_band"),
                      "delta": 0 if function.get("seniority_band") == hypothesis.get("seniority_band") else None},
    }
    gate = []
    if diversity["distinct_facts"] < 3: gate.append("insufficient_fact_count")
    if diversity.get("employer_data_available") and diversity["distinct_employers"] < 2:
        gate.append("insufficient_evidence_diversity")
    if diversity["max_single_fact_share"] > 0.6: gate.append("single_fact_dominates")
    if not by_strength["direct"] and not by_strength["strongly_related"]: gate.append("no_direct_or_strong_evidence")
    no_evidence_market = not selected and sufficient_market is not None
    readiness = "build_toward" if no_evidence_market else ("unsupported" if gate else ("unverified_market" if not sufficient_market else (
        "ready_now" if reconciliation["evidence_fit"] >= 75 else "near_term" if reconciliation["evidence_fit"] >= 60 else "build_toward" if reconciliation["evidence_fit"] >= 35 else "stretch"))
    )
    reasons = list(gate)
    if not sufficient_market: reasons.append("market_profile_unavailable")
    if goals is None: reasons.append("career_goals_not_supplied")
    if excluded: readiness = "unsupported"; reasons.append("user_excluded")
    if no_evidence_market: reasons.append("no_candidate_evidence")
    return {"direction_id": f"dir-{function['function_id'][3:]}", "function_id": function["function_id"],
            "name": function["canonical_label"], "role_family": function["role_family"],
            "supporting_fact_ids": hypothesis["supporting_fact_ids"], "evidence": {"by_strength": by_strength, "refs": selected, "gaps": reconciliation["gaps"],
                         "diversity": diversity, "coverage": coverage,
                         "unsupported_core_signals": unsupported_core},
            "axes": axes, "readiness": readiness, "review_reasons": sorted(set(reasons)),
            "ranking": {"method": "lexicographic_v1",
                        "key": ["readiness", "user_intent", "evidence_fit", "market_capacity", "direction_id"],
                        "explanation": "readiness 先分档；user_intent 与 market_capacity 为 null 时排在有值项之后"}}


def rank(directions: list[dict[str, Any]], mode: str = "by_intent") -> list[dict[str, Any]]:
    def null_last(value): return -1 if value is None else value
    if mode == "by_evidence": key = lambda d: (READINESS_ORDER[d["readiness"]], -d["axes"]["evidence_fit"]["value"], d["direction_id"])
    elif mode == "by_market": key = lambda d: (READINESS_ORDER[d["readiness"]], -null_last(d["axes"]["market_capacity"]["value"]), d["direction_id"])
    else: key = lambda d: (READINESS_ORDER[d["readiness"]], -null_last(d["axes"]["user_intent"]["value"]), -d["axes"]["evidence_fit"]["value"], d["direction_id"])
    return sorted(directions, key=key)


def converge(directions: list[dict[str, Any]], max_shown: int = 6, threshold: float = .6) -> list[dict[str, Any]]:
    def similarity(a, b):
        af, bf = set(a["supporting_fact_ids"]), set(b["supporting_fact_ids"])
        union = af | bf
        fact_sim = len(af & bf) / len(union) if union else 0.0
        return (0.8 if a["function_id"] == b["function_id"] else 0.0) + 0.2 * fact_sim
    selected = []
    for direction in directions:
        if not any(similarity(direction, prior) >= threshold for prior in selected):
            selected.append(direction)
        if len(selected) >= max_shown: break
    return selected
