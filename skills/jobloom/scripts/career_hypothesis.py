"""Deterministic bottom-up career hypotheses from Capability evidence."""

from __future__ import annotations

from typing import Any


def generate(ontology: dict[str, Any], refs: list[dict[str, Any]], *, max_hypotheses: int = 30) -> list[dict[str, Any]]:
    output = []
    for function in ontology["function_nodes"]:
        cap_ids = {item["capability_id"] for item in function["capability_signature"]}
        supporting = [ref for ref in refs if ref["capability_id"] in cap_ids]
        fact_ids = sorted({ref["fact_id"] for ref in supporting})
        if not supporting:
            continue
        employers = {ref.get("employer") for ref in supporting if ref.get("employer")}
        counts = {fact_id: sum(ref["fact_id"] == fact_id for ref in supporting) for fact_id in fact_ids}
        max_share = max(counts.values(), default=0) / len(supporting)
        output.append({
            "hypothesis_id": f"hyp-{function['function_id'][3:]}",
            "function_id": function["function_id"], "role_family": function["role_family"],
            "unit_ids": sorted({ref["unit_id"] for ref in supporting}),
            "supporting_fact_ids": fact_ids,
            "diversity": {"distinct_employers": len(employers),
                          "employer_data_available": bool(employers),
                          "distinct_facts": len(fact_ids),
                          "max_single_fact_share": round(max_share, 3)},
        })
    return sorted(output, key=lambda item: (-len(item["supporting_fact_ids"]), item["function_id"]))[:max_hypotheses]
