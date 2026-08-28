"""Fail-closed TitleSurface resolution and deterministic query generation."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path: sys.path.insert(0, SCRIPT_DIR)
import title_surface  # noqa: E402

ASSET_PATH = Path(__file__).resolve().parents[1] / "assets" / "title-surfaces.json"


def load(path: Path | str = ASSET_PATH) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("schema_version") != "0.1.0" or not isinstance(value.get("surfaces"), list):
        raise ValueError("invalid title ontology")
    return value


def _context(job: dict[str, Any]) -> str:
    fields = [job.get("title", ""), job.get("summary", ""), *(job.get("responsibilities") or []),
              *(job.get("required_skills") or []), str(job.get("employer", ""))]
    return " ".join(str(x) for x in fields).casefold()


def resolve(job: dict[str, Any], ontology: dict[str, Any] | None = None) -> dict[str, Any]:
    ontology = ontology or load()
    base, level = title_surface.normalize(job.get("title", ""))
    candidates = [s for s in ontology["surfaces"] if title_surface.normalize(s["raw"])[0] == base]
    if not candidates:
        body = " ".join([str(job.get("summary") or ""), *(job.get("responsibilities") or [])]).casefold()
        contextual = sorted({s["surface_id"] for s in ontology["surfaces"] if s["raw"].casefold() in body})
        return {"status": "unmapped", "surface": base, "level_token": level, "function_ids": [],
                "notes": ["contextual_title_reference_only"] if contextual else [],
                "contextual_surface_ids": contextual}
    surface = candidates[0]; context = _context(job)
    for exclusion in surface["excluded_senses"]:
        if any(term.casefold() in context for term in exclusion["disambiguator_terms"]):
            return {"status": exclusion["action"], "surface_id": surface["surface_id"], "level_token": level, "function_ids": []}
    eligible = [m for m in surface["maps_to"] if m["confidence"] >= 0.5 and
                (m.get("confirmed_by_user") or surface["provenance"].get("distinct_employers", 0) >= 3)]
    if len(eligible) > 1:
        guarded = [mapping for mapping in eligible if any(
            term.casefold() in context for term in mapping.get("guard_terms", []))]
        if len(guarded) != 1:
            return {"status": "ambiguous", "surface_id": surface["surface_id"], "level_token": level, "function_ids": []}
        eligible = guarded
    return {"status": "bound", "surface_id": surface["surface_id"], "level_token": level,
            "function_ids": sorted({m["function_id"] for m in eligible[:1]})}


def titles_for(function_id: str, ontology: dict[str, Any] | None = None) -> list[str]:
    ontology = ontology or load()
    return [s["raw"] for s in ontology["surfaces"] if any(m["function_id"] == function_id and m["confidence"] >= .5 for m in s["maps_to"])]


def generate_queries(function_id: str, ontology: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    ontology = ontology or load(); output = []
    for raw in titles_for(function_id, ontology):
        text = f'"{raw}"'; output.append({"query_id": hashlib.sha256(text.encode()).hexdigest()[:16],
                                          "text": text, "ontology_version": ontology["ontology_version"]})
    return output
