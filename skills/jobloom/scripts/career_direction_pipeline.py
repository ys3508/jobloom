"""Career Direction Engine V2 deterministic S1-S7 orchestration."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path: sys.path.insert(0, SCRIPT_DIR)
import capability_ontology, career_hypothesis, career_intent, direction_axes, evidence_units, facet_taxonomy, resume_core, title_ontology  # noqa: E402


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
      CREATE TABLE IF NOT EXISTS evidence_units (
        unit_id TEXT PRIMARY KEY, snapshot_sha256 TEXT NOT NULL, fact_id TEXT NOT NULL,
        source_strength TEXT NOT NULL, surface_terms_json TEXT NOT NULL,
        temporal_json TEXT NOT NULL, source_kind TEXT NOT NULL, content_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS evidence_facets (
        unit_id TEXT NOT NULL, facet_kind TEXT NOT NULL, facet_value TEXT NOT NULL,
        relation_strength TEXT NOT NULL, rule_id TEXT NOT NULL, assigned_by TEXT NOT NULL,
        content_json TEXT NOT NULL, PRIMARY KEY(unit_id, facet_kind, facet_value));
      CREATE TABLE IF NOT EXISTS career_hypotheses (
        hypothesis_id TEXT PRIMARY KEY, snapshot_sha256 TEXT NOT NULL, content_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS title_ontology_versions (
        ontology_version TEXT PRIMARY KEY, content_sha256 TEXT NOT NULL, loaded_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS market_requirement_profiles (
        profile_id TEXT PRIMARY KEY, title_id TEXT NOT NULL, sufficient INTEGER NOT NULL,
        content_json TEXT NOT NULL, collected_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS direction_proposals (
        proposal_id TEXT PRIMARY KEY, snapshot_sha256 TEXT NOT NULL, ontology_version TEXT NOT NULL,
        mode TEXT NOT NULL, content_json TEXT NOT NULL, content_sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS direction_proposal_axes (
        proposal_id TEXT NOT NULL, direction_id TEXT NOT NULL, axis TEXT NOT NULL,
        value_json TEXT NOT NULL, null_reason TEXT, rule_id TEXT NOT NULL,
        PRIMARY KEY(proposal_id, direction_id, axis));
    """)
    connection.commit()


def persist(connection: sqlite3.Connection, proposal: dict[str, Any]) -> None:
    initialize(connection)
    content = {k: v for k, v in proposal.items() if k not in {"created_at", "proposal_sha256"}}
    if resume_core.canonical_hash(content) != proposal.get("proposal_sha256"):
        raise ValueError("cannot persist a proposal with an invalid hash")
    with connection:
        connection.execute("INSERT INTO direction_proposals VALUES (?,?,?,?,?,?,?)", (
            proposal["proposal_id"], proposal["source_sha256"], proposal["ontology_version"],
            proposal["mode"], json.dumps(content, sort_keys=True, ensure_ascii=False),
            proposal["proposal_sha256"], proposal["created_at"]))
        for direction in proposal["recommendations"]:
            for axis, value in direction["axes"].items():
                connection.execute("INSERT INTO direction_proposal_axes VALUES (?,?,?,?,?,?)", (
                    proposal["proposal_id"], direction["direction_id"], axis,
                    json.dumps(value.get("value")), value.get("null_reason"), value["rule_id"]))


def persist_artifacts(connection: sqlite3.Connection, units: list[dict[str, Any]],
                      refs: list[dict[str, Any]], hypotheses: list[dict[str, Any]]) -> None:
    initialize(connection)
    with connection:
        for unit in units:
            connection.execute("INSERT OR REPLACE INTO evidence_units VALUES (?,?,?,?,?,?,?,?)", (
                unit["unit_id"], unit["snapshot_sha256"], unit["fact_id"], unit["source_strength"],
                json.dumps(unit["surface_terms"]), json.dumps(unit["temporal"]), unit["source_kind"],
                json.dumps(unit, sort_keys=True)))
        for ref in refs:
            connection.execute("INSERT OR REPLACE INTO evidence_facets VALUES (?,?,?,?,?,?,?)", (
                ref["unit_id"], "capability", ref["capability_id"], ref["relation_strength"],
                ref["rule_id"], ref["assigned_by"], json.dumps(ref, sort_keys=True)))
        for hypothesis in hypotheses:
            snapshot = next((unit["snapshot_sha256"] for unit in units
                             if unit["unit_id"] in hypothesis["unit_ids"]), "")
            connection.execute("INSERT OR REPLACE INTO career_hypotheses VALUES (?,?,?)", (
                hypothesis["hypothesis_id"], snapshot, json.dumps(hypothesis, sort_keys=True)))


def _profile(direction: dict[str, Any], proposal_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    titles = title_ontology.titles_for(direction["function_id"])
    search = candidate.get("search") or {}
    criteria = {key: value for key, value in search.items() if key in {
        "countries", "locations", "work_arrangements", "employment_types", "salary_floor", "salary_currency"} and value not in (None, [], "")}
    return {"schema_version": "0.2.0", "direction_id": f"auto-{proposal_id}-{direction['function_id'][3:]}",
            "name": direction["name"], "role_family": direction["role_family"],
            "target_titles": titles[:5] or [direction["name"]], "auxiliary_titles": titles[5:],
            "positive_keywords": [], "negative_keywords": [], "precision_keywords": [],
            "discovery_keywords": [], "hard_exclusion_keywords": ["commission-only"],
            "warning_keywords": [], "criteria": criteria, "parent_direction_id": None}


def generate(*, proposal_id: str, candidate: dict[str, Any], facts: list[dict[str, Any]],
             mode: str, snapshot_sha256: str, goals: dict[str, Any] | None = None,
             market_profiles: dict[str, dict[str, Any]] | None = None, max_directions: int = 6,
             today: date | None = None, created_at: datetime | None = None,
             connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    if mode not in {"verified", "provisional"}: raise ValueError("invalid pipeline mode")
    ontology = capability_ontology.load_ontology(); goals = career_intent.validate(goals)
    units = evidence_units.build_units(facts, snapshot_sha256, mode=mode, today=today)
    usable_ids = {u["fact_id"] for u in units}; usable_facts = [f for f in facts if f.get("id") in usable_ids]
    refs = facet_taxonomy.assign_capabilities(usable_facts, units, ontology, mode=mode)
    hypotheses = career_hypothesis.generate(ontology, refs)
    function_by_id = {f["function_id"]: f for f in ontology["function_nodes"]}
    existing = {h["function_id"] for h in hypotheses}
    for function_id, market in (market_profiles or {}).items():
        if function_id in function_by_id and function_id not in existing and market.get("sample", {}).get("sufficient"):
            hypotheses.append({"hypothesis_id": f"market-{function_id[3:]}", "function_id": function_id,
                               "role_family": function_by_id[function_id]["role_family"], "unit_ids": [],
                               "supporting_fact_ids": [], "diversity": {"distinct_employers": 0,
                               "employer_data_available": False, "distinct_facts": 0,
                               "max_single_fact_share": 0.0}})
    directions = [direction_axes.build_direction(function_by_id[h["function_id"]], h, refs,
                  goals=goals, market=(market_profiles or {}).get(h["function_id"])) for h in hypotheses]
    for direction in directions:
        direction["review_reasons"] = sorted(set(direction["review_reasons"] + [
            axis["null_reason"] for axis in direction["axes"].values() if axis.get("value") is None
        ]))
    directions = direction_axes.converge(
        [d for d in direction_axes.rank(directions) if d["readiness"] != "unsupported"],
        max_shown=max_directions)
    reasons = []
    if len(directions) < 3: reasons.append("fewer_directions_than_target")
    if goals is None: reasons.append("career_goals_not_supplied")
    if mode == "provisional": reasons.append("candidate_facts_require_confirmation")
    recommendations = []
    for direction in directions:
        profile = _profile(direction, proposal_id, candidate)
        recommendations.append({"archetype_id": direction["function_id"], **direction,
                                "profile": profile, "profile_sha256": resume_core.canonical_hash(profile)})
    questions = []
    for direction in directions:
        for gap in direction["evidence"]["gaps"]:
            if gap.get("core"):
                questions.append({"kind": "evidence_probe", "target": gap["capability_id"],
                                  "affected_direction_ids": [direction["direction_id"]]})
    if goals is None:
        questions.append({"kind": "intent_probe", "target": "career_goals",
                          "affected_direction_ids": [d["direction_id"] for d in directions]})
    questions = sorted(questions, key=lambda q: (-len(q["affected_direction_ids"]), q["kind"], q["target"]))[:5]
    content = {"schema_version": "0.2.0", "engine_version": "v2", "proposal_id": proposal_id,
               "mode": mode, "source_sha256": snapshot_sha256, "ontology_version": ontology["ontology_version"],
               "goals_sha256": resume_core.canonical_hash(goals) if goals else None,
               "goals_supplied": goals is not None, "recommendations": recommendations,
               "elicitation_questions": questions,
               "adoptable": mode == "verified" and bool(recommendations), "review_required": True,
               "review_reasons": sorted(set(reasons)), "evidence_unit_fingerprint": resume_core.canonical_hash(units)}
    proposal = {**content, "created_at": (created_at or datetime.now(timezone.utc)).isoformat(),
                "proposal_sha256": resume_core.canonical_hash(content)}
    if connection is not None:
        persist_artifacts(connection, units, refs, hypotheses)
        persist(connection, proposal)
    return proposal
