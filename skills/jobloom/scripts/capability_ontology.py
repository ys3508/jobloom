"""Versioned, deterministic Capability and FunctionNode ontology loading."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import pattern_matcher  # noqa: E402


ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"
DEFAULT_ONTOLOGY_PATH = ASSET_DIR / "capability-ontology.json"
DEFAULT_GOLDEN_PATH = ASSET_DIR / "capability-pattern-golden.json"
SAFE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
ONTOLOGY_KEYS = {"schema_version", "ontology_version", "capabilities", "function_nodes"}
CAPABILITY_KEYS = {
    "capability_id", "layer", "canonical_label", "aliases", "rollup_to",
    "evidence_patterns",
}
FUNCTION_KEYS = {
    "function_id", "canonical_label", "parents", "role_family", "source_refs",
    "capability_signature",
}
SIGNATURE_KEYS = {"capability_id", "weight", "core"}
SOURCE_REF_KEYS = {"source_id", "code", "confidence"}
GOLDEN_KEYS = {"schema_version", "ontology_version", "samples"}
GOLDEN_SAMPLE_KEYS = {"pattern_id", "facts"}


def _safe_id(value: Any, label: str, prefix: str) -> str:
    if (not isinstance(value, str) or not SAFE_ID.fullmatch(value)
            or not value.startswith(prefix)):
        raise ValueError(f"{label} is invalid")
    return value


def _strings(value: Any, label: str, *, max_items: int = 100) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ValueError(f"{label} must be a bounded list")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 200:
            raise ValueError(f"{label} contains an invalid value")
        result.append(item.strip())
    if len({item.casefold() for item in result}) != len(result):
        raise ValueError(f"{label} values must be unique")
    return result


def _validate_capabilities(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw or len(raw) > 1000:
        raise ValueError("capability ontology requires a bounded capability list")
    result, seen_ids, seen_terms, pattern_ids = [], set(), {}, set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != CAPABILITY_KEYS:
            raise ValueError("capability has missing or unknown fields")
        capability_id = _safe_id(item["capability_id"], "capability_id", "cap.")
        if capability_id in seen_ids:
            raise ValueError("capability IDs must be unique")
        seen_ids.add(capability_id)
        if item["layer"] not in {"DOMAIN", "SKILL"}:
            raise ValueError("TOOL/EVIDENCE values cannot be capability nodes")
        label = item["canonical_label"]
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise ValueError("capability canonical_label is invalid")
        aliases = _strings(item["aliases"], "capability.aliases")
        if label.strip().casefold() in {alias.casefold() for alias in aliases}:
            raise ValueError("capability canonical_label cannot repeat as an alias")
        for surface in [label.strip(), *aliases]:
            key = surface.casefold()
            owner = seen_terms.get(key)
            if owner and owner != capability_id:
                raise ValueError(f"capability label or alias is not globally unique: {surface}")
            seen_terms[key] = capability_id
        rollups = _strings(item["rollup_to"], "capability.rollup_to")
        for target in rollups:
            _safe_id(target, "rollup_to", "cap.")
        patterns = item["evidence_patterns"]
        if not isinstance(patterns, list) or len(patterns) > 100:
            raise ValueError("capability evidence_patterns must be a bounded list")
        if item["layer"] == "SKILL" and not patterns:
            raise ValueError("every SKILL capability requires evidence_patterns")
        if item["layer"] == "DOMAIN" and patterns:
            raise ValueError("DOMAIN capabilities cannot participate in precise pattern matching")
        normalized_patterns = []
        for pattern in patterns:
            normalized = pattern_matcher.validate_pattern(pattern)
            pattern_id = _safe_id(normalized["pattern_id"], "pattern_id", "pat.")
            if pattern_id in pattern_ids:
                raise ValueError("evidence pattern IDs must be globally unique")
            pattern_ids.add(pattern_id)
            normalized["pattern_id"] = pattern_id
            normalized_patterns.append(normalized)
        result.append({
            "capability_id": capability_id,
            "layer": item["layer"],
            "canonical_label": label.strip(),
            "aliases": aliases,
            "rollup_to": rollups,
            "evidence_patterns": normalized_patterns,
        })

    by_id = {item["capability_id"]: item for item in result}
    for item in result:
        for target in item["rollup_to"]:
            if target not in by_id:
                raise ValueError(f"capability rollup target does not exist: {target}")
            if by_id[target]["layer"] != "DOMAIN":
                raise ValueError("capability rollup targets must be DOMAIN nodes")
    visiting, visited = set(), set()

    def visit(capability_id: str) -> None:
        if capability_id in visiting:
            raise ValueError("capability rollup_to relationships must form a DAG")
        if capability_id in visited:
            return
        visiting.add(capability_id)
        for target in by_id[capability_id]["rollup_to"]:
            visit(target)
        visiting.remove(capability_id)
        visited.add(capability_id)

    for capability_id in sorted(by_id):
        visit(capability_id)
    return result


def _validate_functions(raw: Any, capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw or len(raw) > 1000:
        raise ValueError("capability ontology requires a bounded function node list")
    capability_by_id = {item["capability_id"]: item for item in capabilities}
    result, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != FUNCTION_KEYS:
            raise ValueError("function node has missing or unknown fields")
        function_id = _safe_id(item["function_id"], "function_id", "fn.")
        if function_id in seen:
            raise ValueError("function node IDs must be unique")
        seen.add(function_id)
        label = item["canonical_label"]
        role_family = item["role_family"]
        if not isinstance(label, str) or not label.strip():
            raise ValueError("function node canonical_label is required")
        if not isinstance(role_family, str) or not role_family.strip():
            raise ValueError("function node role_family is required")
        parents = _strings(item["parents"], "function.parents")
        for parent in parents:
            _safe_id(parent, "function parent", "fn.")
        refs = item["source_refs"]
        if not isinstance(refs, list) or len(refs) > 100:
            raise ValueError("function source_refs must be a bounded list")
        normalized_refs = []
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != SOURCE_REF_KEYS:
                raise ValueError("function source_ref has missing or unknown fields")
            if any(not isinstance(ref[key], str) or not ref[key].strip() for key in SOURCE_REF_KEYS):
                raise ValueError("function source_ref values are required")
            normalized_refs.append({key: ref[key].strip() for key in SOURCE_REF_KEYS})
        signature = item["capability_signature"]
        if not isinstance(signature, list) or not signature or len(signature) > 100:
            raise ValueError("function node requires a bounded capability_signature")
        normalized_signature, signature_ids = [], set()
        for entry in signature:
            if not isinstance(entry, dict) or set(entry) != SIGNATURE_KEYS:
                raise ValueError("capability signature has missing or unknown fields")
            capability_id = _safe_id(entry["capability_id"], "signature capability_id", "cap.")
            capability = capability_by_id.get(capability_id)
            if not capability or capability["layer"] != "SKILL":
                raise ValueError("capability signatures may reference existing SKILL nodes only")
            if capability_id in signature_ids:
                raise ValueError("capability signature entries must be unique")
            signature_ids.add(capability_id)
            weight = entry["weight"]
            if isinstance(weight, bool) or not isinstance(weight, int) or not 1 <= weight <= 10:
                raise ValueError("capability signature weight must be an integer from 1 to 10")
            if not isinstance(entry["core"], bool):
                raise ValueError("capability signature core must be Boolean")
            normalized_signature.append({
                "capability_id": capability_id, "weight": weight, "core": entry["core"],
            })
        result.append({
            "function_id": function_id,
            "canonical_label": label.strip(),
            "parents": parents,
            "role_family": role_family.strip(),
            "source_refs": normalized_refs,
            "capability_signature": normalized_signature,
        })

    by_id = {item["function_id"]: item for item in result}
    for item in result:
        for parent in item["parents"]:
            if parent not in by_id:
                raise ValueError(f"function parent does not exist: {parent}")

    visiting, visited = set(), set()

    def visit(function_id: str) -> None:
        if function_id in visiting:
            raise ValueError("function node parents must form a DAG")
        if function_id in visited:
            return
        visiting.add(function_id)
        for parent in by_id[function_id]["parents"]:
            visit(parent)
        visiting.remove(function_id)
        visited.add(function_id)

    for function_id in sorted(by_id):
        visit(function_id)
    return result


def validate_ontology(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != ONTOLOGY_KEYS:
        raise ValueError("capability ontology has missing or unknown fields")
    if value["schema_version"] != "0.1.0":
        raise ValueError("unsupported capability ontology schema_version")
    ontology_version = value["ontology_version"]
    if not isinstance(ontology_version, str) or not ontology_version.strip():
        raise ValueError("capability ontology requires ontology_version")
    capabilities = _validate_capabilities(value["capabilities"])
    functions = _validate_functions(value["function_nodes"], capabilities)
    return {
        "schema_version": "0.1.0",
        "ontology_version": ontology_version.strip(),
        "capabilities": capabilities,
        "function_nodes": functions,
    }


def validate_golden_samples(value: dict[str, Any], ontology: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != GOLDEN_KEYS:
        raise ValueError("capability golden samples have missing or unknown fields")
    if value["schema_version"] != "0.1.0":
        raise ValueError("unsupported capability golden schema_version")
    if value["ontology_version"] != ontology["ontology_version"]:
        raise ValueError("capability golden samples use a different ontology_version")
    samples = value["samples"]
    if not isinstance(samples, list) or not samples:
        raise ValueError("capability golden samples are required")
    by_pattern: dict[str, list[str]] = {}
    seen = set()
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != GOLDEN_SAMPLE_KEYS:
            raise ValueError("capability golden sample has missing or unknown fields")
        pattern_id = _safe_id(sample["pattern_id"], "golden pattern_id", "pat.")
        if pattern_id in seen:
            raise ValueError("golden pattern IDs must be unique")
        seen.add(pattern_id)
        by_pattern[pattern_id] = _strings(sample["facts"], "golden facts")
        if not by_pattern[pattern_id]:
            raise ValueError("golden pattern requires at least one fact")
    patterns = [pattern for cap in ontology["capabilities"]
                for pattern in cap["evidence_patterns"]]
    known_ids = {pattern["pattern_id"] for pattern in patterns}
    if set(by_pattern) - known_ids:
        raise ValueError("golden samples reference unknown patterns")
    missing = pattern_matcher.unmatched_pattern_ids(patterns, by_pattern)
    if missing:
        raise ValueError(f"patterns without a matching golden fact: {', '.join(missing)}")
    return {
        "schema_version": "0.1.0",
        "ontology_version": ontology["ontology_version"],
        "samples": [{"pattern_id": key, "facts": by_pattern[key]} for key in sorted(by_pattern)],
    }


def load_ontology(
    path: Path | str = DEFAULT_ONTOLOGY_PATH,
    golden_path: Path | str = DEFAULT_GOLDEN_PATH,
) -> dict[str, Any]:
    ontology = validate_ontology(json.loads(Path(path).read_text(encoding="utf-8")))
    golden = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    validate_golden_samples(golden, ontology)
    return ontology


def capability(ontology: dict[str, Any], capability_id: str) -> dict[str, Any]:
    return next(item for item in ontology["capabilities"]
                if item["capability_id"] == capability_id)


def function_node(ontology: dict[str, Any], function_id: str) -> dict[str, Any]:
    return next(item for item in ontology["function_nodes"]
                if item["function_id"] == function_id)
