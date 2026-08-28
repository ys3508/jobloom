#!/usr/bin/env python3
"""Generate evidence-grounded career-direction proposals from uploaded material or CandidateFacts."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import direction_core  # noqa: E402
import extract_candidate_facts  # noqa: E402
import resume_core  # noqa: E402
import career_direction_pipeline  # noqa: E402
from evidence_matcher import EVIDENCE_ORDER, STRENGTH_FACTORS, fact_supports  # noqa: E402


CATALOG_PATH = Path(__file__).resolve().parents[1] / "assets" / "career-direction-catalog.json"
CATALOG_KEYS = {"schema_version", "archetypes"}
ARCHETYPE_KEYS = {
    "archetype_id", "name", "role_family", "target_titles", "auxiliary_titles",
    "industries", "evidence_signals", "career_signals", "positive_keywords",
    "negative_keywords", "precision_keywords", "discovery_keywords",
    "hard_exclusion_keywords", "warning_keywords",
}
SIGNAL_KEYS = {"term", "weight", "core"}
GOAL_KEYS = {
    "schema_version", "goal_id", "desired_roles", "desired_industries",
    "skills_to_build", "avoid_roles", "avoid_industries", "priorities",
}
PRIORITY_KEYS = {"current_fit", "career_value"}
# Until the Evidence Graph carries typed capability relationships, source type limits how
# strongly a text hit may support a direction. A summary, skill list, or course certificate is
# useful discovery evidence, but is not equivalent to demonstrated work.
FACT_TYPE_STRENGTH_CAPS = {
    "professional_summary": "mention_only",
    "resume_claim": "mention_only",
    "skill": "mention_only",
    "certification": "strongly_related",
    "education": "strongly_related",
    "experience_header": "strongly_related",
}


def canonical_hash(value: Any) -> str:
    return resume_core.canonical_hash(value)


def _bounded_strings(value: Any, label: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list) or (required and not value) or len(value) > 100:
        raise ValueError(f"{label} must be a bounded list")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 200 for item in value):
        raise ValueError(f"{label} contains an invalid value")
    result = [item.strip() for item in value]
    if len({item.casefold() for item in result}) != len(result):
        raise ValueError(f"{label} values must be unique")
    return result


def validate_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(catalog, dict) or set(catalog) != CATALOG_KEYS:
        raise ValueError("career direction catalog has missing or unknown fields")
    if catalog["schema_version"] != "0.1.0":
        raise ValueError("unsupported career direction catalog schema_version")
    archetypes = catalog["archetypes"]
    if not isinstance(archetypes, list) or not 1 <= len(archetypes) <= 100:
        raise ValueError("career direction catalog requires a bounded archetype list")
    normalized, seen = [], set()
    for item in archetypes:
        if not isinstance(item, dict) or set(item) != ARCHETYPE_KEYS:
            raise ValueError("career direction archetype has missing or unknown fields")
        archetype_id = item["archetype_id"]
        resume_core._require_safe_id(archetype_id, "archetype_id")
        if archetype_id in seen:
            raise ValueError("career direction archetype IDs must be unique")
        seen.add(archetype_id)
        if not isinstance(item["name"], str) or not item["name"].strip():
            raise ValueError("career direction archetype name is required")
        if not isinstance(item["role_family"], str) or not item["role_family"].strip():
            raise ValueError("career direction role_family is required")
        signals, signal_terms = [], set()
        for signal in item["evidence_signals"]:
            if not isinstance(signal, dict) or set(signal) != SIGNAL_KEYS:
                raise ValueError("evidence signal has missing or unknown fields")
            term = signal["term"]
            if not isinstance(term, str) or not term.strip() or len(term) > 100:
                raise ValueError("evidence signal term is invalid")
            if term.casefold() in signal_terms:
                raise ValueError("evidence signal terms must be unique per archetype")
            signal_terms.add(term.casefold())
            weight = signal["weight"]
            if isinstance(weight, bool) or not isinstance(weight, int) or not 1 <= weight <= 10:
                raise ValueError("evidence signal weight must be an integer from 1 to 10")
            if not isinstance(signal["core"], bool):
                raise ValueError("evidence signal core must be Boolean")
            signals.append({"term": term.strip(), "weight": weight, "core": signal["core"]})
        if not signals:
            raise ValueError("career direction archetype requires evidence signals")
        value = dict(item)
        value["name"] = item["name"].strip()
        value["role_family"] = item["role_family"].strip()
        value["evidence_signals"] = signals
        for key in ARCHETYPE_KEYS - {"archetype_id", "name", "role_family", "evidence_signals"}:
            value[key] = _bounded_strings(item[key], f"archetype.{key}",
                                          required=key == "target_titles")
        normalized.append(value)
    return {"schema_version": "0.1.0", "archetypes": normalized}


def validate_goals(goals: dict[str, Any] | None) -> dict[str, Any] | None:
    if goals is None:
        return None
    if not isinstance(goals, dict) or set(goals) != GOAL_KEYS:
        raise ValueError("career goals have missing or unknown fields")
    if goals["schema_version"] != "0.1.0":
        raise ValueError("unsupported career goals schema_version")
    resume_core._require_safe_id(goals["goal_id"], "goal_id")
    priorities = goals["priorities"]
    if not isinstance(priorities, dict) or set(priorities) != PRIORITY_KEYS:
        raise ValueError("career goal priorities are incomplete")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
           for value in priorities.values()) or sum(priorities.values()) != 100:
        raise ValueError("career goal priorities must be non-negative integers totaling 100")
    result = dict(goals)
    for key in GOAL_KEYS - {"schema_version", "goal_id", "priorities"}:
        result[key] = _bounded_strings(goals[key], f"career_goals.{key}")
    result["priorities"] = dict(priorities)
    goal_lists = GOAL_KEYS - {"schema_version", "goal_id", "priorities"}
    return result if any(result[key] for key in goal_lists) else None


def _usable_facts(facts: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == "verified":
        return [fact for fact in facts if fact.get("status") in {"confirmed", "locked"}
                and fact.get("evidence_strength") in EVIDENCE_ORDER]
    return [fact for fact in facts if fact.get("status") == "proposed"
            and fact.get("evidence_strength") in EVIDENCE_ORDER]


def _effective_strength(fact: dict[str, Any], mode: str) -> str:
    declared = fact["evidence_strength"]
    cap = "transferable" if mode == "provisional" else FACT_TYPE_STRENGTH_CAPS.get(
        str(fact.get("type") or "experience_claim"), "direct")
    return min((declared, cap), key=lambda strength: EVIDENCE_ORDER[strength])


def _signal_match(term: str, facts: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    matches = [fact for fact in facts if fact_supports(term, fact)]
    if not matches:
        return {"term": term, "strength": "none", "fact_ids": [], "source_evidence": []}
    effective = [(fact, _effective_strength(fact, mode)) for fact in matches]
    best = max((strength for _, strength in effective),
               key=lambda strength: EVIDENCE_ORDER[strength])
    return {"term": term, "strength": best,
            "fact_ids": sorted(fact["id"] for fact, strength in effective if strength == best),
            "source_evidence": sorted(({
                "fact_id": fact["id"],
                "fact_type": str(fact.get("type") or "experience_claim"),
                "declared_strength": fact["evidence_strength"],
                "effective_strength": strength,
            } for fact, strength in effective if strength == best), key=lambda item: item["fact_id"])}


def _text_matches(needle: str, values: list[str]) -> bool:
    wanted = direction_core._tokens(needle)
    return bool(wanted) and any(wanted == direction_core._tokens(value) for value in values)


def _career_value(archetype: dict[str, Any], goals: dict[str, Any] | None) -> int | None:
    if goals is None:
        return None
    role_space = archetype["target_titles"] + archetype["auxiliary_titles"]
    industry_space = archetype["industries"]
    skill_space = (archetype["career_signals"] + archetype["positive_keywords"]
                   + archetype["discovery_keywords"])
    positives = [
        *[(item, role_space) for item in goals["desired_roles"]],
        *[(item, industry_space) for item in goals["desired_industries"]],
        *[(item, skill_space) for item in goals["skills_to_build"]],
    ]
    matched = sum(_text_matches(item, space) for item, space in positives)
    avoid_hits = sum(_text_matches(item, role_space) for item in goals["avoid_roles"])
    avoid_hits += sum(_text_matches(item, industry_space) for item in goals["avoid_industries"])
    if not positives:
        return 0 if avoid_hits else None
    base = round(100 * matched / len(positives))
    return max(0, base - 35 * avoid_hits)


def _criteria(candidate: dict[str, Any], industries: list[str]) -> dict[str, Any]:
    search = candidate.get("search") or {}
    mapping = {
        "countries": "countries", "locations": "locations",
        "work_arrangements": "work_arrangements", "employment_types": "employment_types",
        "salary_floor": "salary_floor", "salary_currency": "salary_currency",
        "excluded_employers": "excluded_companies",
    }
    result: dict[str, Any] = {"industries": industries}
    for source, target in mapping.items():
        value = search.get(source)
        if value not in (None, [], ""):
            result[target] = value
    return result


def _score(archetype: dict[str, Any], facts: list[dict[str, Any]], goals: dict[str, Any] | None,
           candidate: dict[str, Any], proposal_id: str, mode: str) -> dict[str, Any]:
    evidence = []
    earned = total = 0.0
    for signal in archetype["evidence_signals"]:
        match = _signal_match(signal["term"], facts, mode)
        match.update({"weight": signal["weight"], "core": signal["core"]})
        evidence.append(match)
        total += signal["weight"]
        earned += signal["weight"] * STRENGTH_FACTORS[match["strength"]]
    current_fit = round(100 * earned / total) if total else 0
    career_value = _career_value(archetype, goals)
    priorities = goals["priorities"] if goals else {"current_fit": 100, "career_value": 0}
    overall = round((current_fit * priorities["current_fit"]
                     + (career_value or 0) * priorities["career_value"]) / 100)
    supported = [item for item in evidence if item["strength"] != "none"]
    fact_ids = sorted({fact_id for item in supported for fact_id in item["fact_ids"]})
    strong_count = sum(EVIDENCE_ORDER[item["strength"]] >= EVIDENCE_ORDER["strongly_related"]
                       for item in supported)
    core = [item for item in evidence if item["core"]]
    strong_core = sum(EVIDENCE_ORDER[item["strength"]] >= EVIDENCE_ORDER["strongly_related"]
                      for item in core)
    direct_core = sum(item["strength"] == "direct" for item in core)
    core_ratio = strong_core / len(core) if core else 0
    confidence = "high" if (mode == "verified" and strong_count >= 5
                             and len(fact_ids) >= 3 and direct_core == len(core)) else (
        "medium" if len(supported) >= 2 and len(fact_ids) >= 2 and core_ratio >= 0.5 else "low")
    tier = "primary" if overall >= 70 else ("adjacent" if overall >= 45 else (
        "exploratory" if overall >= 20 else "insufficient_evidence"))
    direction_id = f"auto-{proposal_id}-{archetype['archetype_id']}"
    profile = {
        "schema_version": "0.2.0", "direction_id": direction_id,
        "name": archetype["name"], "role_family": archetype["role_family"],
        "target_titles": archetype["target_titles"],
        "auxiliary_titles": archetype["auxiliary_titles"],
        "positive_keywords": archetype["positive_keywords"],
        "negative_keywords": archetype["negative_keywords"],
        "precision_keywords": archetype["precision_keywords"],
        "discovery_keywords": archetype["discovery_keywords"],
        "hard_exclusion_keywords": archetype["hard_exclusion_keywords"],
        "warning_keywords": archetype["warning_keywords"],
        "criteria": _criteria(candidate, archetype["industries"]),
        "parent_direction_id": None,
    }
    profile = direction_core.validate_profile(profile)
    return {
        "archetype_id": archetype["archetype_id"], "name": archetype["name"],
        "tier": tier, "current_fit": current_fit, "career_value": career_value,
        "overall_score": overall, "score_status": "provisional_heuristic",
        "decision_grade": False, "confidence": confidence,
        "supporting_fact_ids": fact_ids,
        "evidence": evidence,
        "core_gaps": [item["term"] for item in evidence
                      if item["core"] and item["strength"] == "none"],
        "profile": profile, "profile_sha256": canonical_hash(profile),
    }


def _weights(items: list[dict[str, Any]]) -> list[int]:
    scores = [max(1, item["overall_score"]) for item in items]
    remaining = 100 - len(items)
    raw = [remaining * score / sum(scores) for score in scores]
    floors = [int(value) for value in raw]
    weights = [1 + value for value in floors]
    leftover = 100 - sum(weights)
    order = sorted(range(len(items)), key=lambda i: (-(raw[i] - floors[i]), items[i]["archetype_id"]))
    for index in order[:leftover]:
        weights[index] += 1
    return weights


def generate_proposal(*, proposal_id: str, candidate: dict[str, Any], facts: list[dict[str, Any]],
                      mode: str, source_sha256: str, catalog: dict[str, Any],
                      goals: dict[str, Any] | None = None, max_directions: int = 6,
                      created_at: datetime | None = None) -> dict[str, Any]:
    resume_core._require_safe_id(proposal_id, "proposal_id")
    if mode not in {"provisional", "verified"}:
        raise ValueError("proposal mode must be provisional or verified")
    if isinstance(max_directions, bool) or not isinstance(max_directions, int) \
            or not 1 <= max_directions <= 20:
        raise ValueError("max_directions must be an integer from 1 to 20")
    catalog = validate_catalog(catalog)
    goals = validate_goals(goals)
    usable = _usable_facts(facts, mode)
    scored = [_score(item, usable, goals, candidate, proposal_id, mode)
              for item in catalog["archetypes"]]
    scored.sort(key=lambda item: (-item["overall_score"], -item["current_fit"], item["archetype_id"]))
    recommendations = [item for item in scored
                       if item["current_fit"] > 0 or (item["career_value"] or 0) > 0][:max_directions]
    weights = _weights(recommendations) if recommendations else []
    allocations = [
        {"direction_id": item["profile"]["direction_id"],
         "profile_sha256": item["profile_sha256"], "weight_percent": weight}
        for item, weight in zip(recommendations, weights)
    ]
    content = {
        "schema_version": "0.1.0", "proposal_id": proposal_id, "mode": mode,
        "source_sha256": source_sha256, "catalog_sha256": canonical_hash(catalog),
        "goals_sha256": canonical_hash(goals) if goals else None,
        "goals_supplied": goals is not None,
        "recommendations": recommendations,
        "suggested_portfolio": {
            "schema_version": "0.1.0", "portfolio_id": f"portfolio-{proposal_id}",
            "name": f"Career directions from {proposal_id}", "allocations": allocations,
        } if recommendations else None,
        "adoptable": mode == "verified" and bool(recommendations),
        "review_required": True,
        "review_reasons": (["user_must_choose_directions_and_weights"]
                           + ([] if goals else ["career_goals_not_supplied"])
                           + (["career_goals_unresolved"] if goals and any(
                               goals[key] for key in ("desired_roles", "desired_industries",
                                                      "skills_to_build")) and not any(
                               (item["career_value"] or 0) > 0 for item in recommendations) else [])
                           + (["candidate_facts_require_confirmation"] if mode == "provisional" else [])
                           + (["no_supported_direction_in_catalog"] if not recommendations else [])),
    }
    return {
        **content,
        "created_at": (created_at or datetime.now(timezone.utc)).isoformat(),
        "proposal_sha256": canonical_hash(content),
    }


def generate_v2_proposal(**kwargs: Any) -> dict[str, Any]:
    """Run the V2 evidence → capability → independent-axis pipeline."""
    return career_direction_pipeline.generate(**kwargs)


def propose_candidate(candidate_path: Path, catalog_path: Path, proposal_id: str, db_path: Path,
                      goals_path: Path | None = None, max_directions: int = 6,
                      engine: str = "v2", with_market: bool = False) -> dict[str, Any]:
    candidate, candidate_hash = resume_core.load_valid_candidate(candidate_path)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    snapshot = connection.execute(
        "SELECT * FROM candidate_snapshots WHERE content_sha256=? AND status='active' "
        "AND registered_by='user'", (candidate_hash,),
    ).fetchone()
    connection.close()
    if not snapshot:
        raise ValueError("verified direction proposals require the active user-registered CandidateSnapshot")
    snapshot_path = Path(snapshot["snapshot_path"])
    if not snapshot_path.is_file() or resume_core.file_sha256(snapshot_path) != snapshot["file_sha256"]:
        raise ValueError("active CandidateSnapshot file hash mismatch")
    goals = json.loads(goals_path.read_text(encoding="utf-8")) if goals_path else None
    if engine == "v2":
        market = None
        if with_market:
            import capability_ontology  # noqa: PLC0415  local: keeps the V1 path import-free
            import market_profile
            connection = sqlite3.connect(str(db_path))
            connection.row_factory = sqlite3.Row
            try:
                market = market_profile.profiles_for_functions(
                    connection, capability_ontology.load_ontology(),
                    region={key: value for key, value in
                            (("country", (candidate.get("search") or {}).get("countries", [None])[0]),)
                            if value})
            finally:
                connection.close()
        return generate_v2_proposal(
            proposal_id=proposal_id, candidate=candidate, facts=candidate["facts"],
            mode="verified", snapshot_sha256=candidate_hash, goals=goals,
            max_directions=max_directions, market_profiles=market,
        )
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    return generate_proposal(proposal_id=proposal_id, candidate=candidate,
                             facts=candidate["facts"], mode="verified",
                             source_sha256=candidate_hash, catalog=catalog, goals=goals,
                             max_directions=max_directions)


def propose_material(material_path: Path, catalog_path: Path, proposal_id: str,
                     goals_path: Path | None = None, max_directions: int = 6,
                     engine: str = "v2") -> tuple[dict[str, Any], dict[str, Any]]:
    packet = extract_candidate_facts.build_review_packet(material_path)
    goals = json.loads(goals_path.read_text(encoding="utf-8")) if goals_path else None
    candidate = {"search": {}}
    if engine == "v2":
        proposal = generate_v2_proposal(
            proposal_id=proposal_id, candidate=candidate, facts=packet["facts"], mode="provisional",
            snapshot_sha256=packet["source_document"]["sha256"], goals=goals,
            max_directions=max_directions)
    else:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        proposal = generate_proposal(
            proposal_id=proposal_id, candidate=candidate, facts=packet["facts"], mode="provisional",
            source_sha256=packet["source_document"]["sha256"], catalog=catalog, goals=goals,
            max_directions=max_directions)
    return proposal, packet


def materialize_selection(proposal: dict[str, Any], selection: dict[str, Any], *, actor: str,
                          expected_proposal_sha256: str,
                          current_snapshot_sha256: str | None = None,
                          current_ontology_version: str | None = None) -> dict[str, Any]:
    if actor != "user":
        raise ValueError("career direction selection requires the user actor")
    content = {key: value for key, value in proposal.items()
               if key not in {"created_at", "proposal_sha256"}}
    if canonical_hash(content) != proposal.get("proposal_sha256") \
            or proposal.get("proposal_sha256") != expected_proposal_sha256:
        raise ValueError("career direction proposal hash does not match reviewed content")
    if not proposal.get("adoptable") or proposal.get("mode") != "verified":
        raise ValueError("provisional career directions cannot be materialized")
    if proposal.get("engine_version") == "v2" and (
            current_snapshot_sha256 is None or current_ontology_version is None):
        raise ValueError("V2 materialization requires the active snapshot and ontology version")
    if current_snapshot_sha256 is not None and proposal.get("source_sha256") != current_snapshot_sha256:
        raise ValueError("career direction proposal is stale for the active candidate snapshot")
    if current_ontology_version is not None and proposal.get("ontology_version") != current_ontology_version:
        raise ValueError("career direction proposal ontology has been superseded")
    if not isinstance(selection, dict) or set(selection) != {"portfolio_id", "name", "allocations"}:
        raise ValueError("career direction selection has missing or unknown fields")
    resume_core._require_safe_id(selection["portfolio_id"], "portfolio_id")
    if not isinstance(selection["name"], str) or not selection["name"].strip():
        raise ValueError("career direction selection name is required")
    recommendations = {item["archetype_id"]: item for item in proposal["recommendations"]}
    profiles, allocations, seen, total = [], [], set(), 0
    for item in selection["allocations"]:
        if not isinstance(item, dict) or set(item) != {"archetype_id", "weight_percent"}:
            raise ValueError("career direction selection allocation is invalid")
        archetype_id, weight = item["archetype_id"], item["weight_percent"]
        if archetype_id in seen or archetype_id not in recommendations:
            raise ValueError("career direction selection references an invalid or duplicate archetype")
        if isinstance(weight, bool) or not isinstance(weight, int) or not 1 <= weight <= 100:
            raise ValueError("career direction selection weight must be an integer from 1 to 100")
        seen.add(archetype_id)
        recommendation = recommendations[archetype_id]
        profile = direction_core.validate_profile(recommendation["profile"])
        if canonical_hash(profile) != recommendation["profile_sha256"]:
            raise ValueError("career direction profile hash is invalid")
        profiles.append(profile)
        allocations.append({"direction_id": profile["direction_id"],
                            "profile_sha256": recommendation["profile_sha256"],
                            "weight_percent": weight})
        total += weight
    if not profiles or total != 100:
        raise ValueError("career direction selection weights must total exactly 100")
    portfolio = direction_core.validate_portfolio({
        "schema_version": "0.1.0", "portfolio_id": selection["portfolio_id"],
        "name": selection["name"].strip(), "allocations": allocations,
    })
    return {"proposal_id": proposal["proposal_id"], "proposal_sha256": proposal["proposal_sha256"],
            "profiles": profiles, "portfolio": portfolio,
            "portfolio_sha256": canonical_hash(portfolio), "registration_required": True,
            "approval_required": True}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("propose-candidate", "propose-material"):
        command = commands.add_parser(name)
        command.add_argument("--proposal-id", required=True)
        command.add_argument("--catalog", type=Path, default=CATALOG_PATH)
        command.add_argument("--goals", type=Path)
        command.add_argument("--max-directions", type=int, default=6)
        command.add_argument("--engine", choices=("v2", "v1"), default="v2")
        command.add_argument("--output", required=True, type=Path)
        if name == "propose-candidate":
            command.add_argument("--with-market", action="store_true",
                                 help="aggregate authorized JobCards already held locally into "
                                      "market profiles; axes stay null when the sample is short")
            command.add_argument("--candidate", required=True, type=Path)
            command.add_argument("--db", required=True, type=Path)
        else:
            command.add_argument("--material", required=True, type=Path)
            command.add_argument("--fact-review-output", required=True, type=Path)
    materialize = commands.add_parser("materialize-selection")
    materialize.add_argument("--proposal", required=True, type=Path)
    materialize.add_argument("--selection", required=True, type=Path)
    materialize.add_argument("--actor", required=True)
    materialize.add_argument("--proposal-sha256", required=True)
    materialize.add_argument("--current-snapshot-sha256")
    materialize.add_argument("--current-ontology-version")
    materialize.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "propose-candidate":
        result = propose_candidate(args.candidate, args.catalog, args.proposal_id, args.db,
                                   args.goals, args.max_directions, args.engine,
                                   with_market=args.with_market)
        _write_json(args.output, result)
    elif args.command == "propose-material":
        result, packet = propose_material(args.material, args.catalog, args.proposal_id,
                                          args.goals, args.max_directions, args.engine)
        _write_json(args.output, result)
        _write_json(args.fact_review_output, packet)
    else:
        proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
        selection = json.loads(args.selection.read_text(encoding="utf-8"))
        result = materialize_selection(proposal, selection, actor=args.actor,
                                       expected_proposal_sha256=args.proposal_sha256,
                                       current_snapshot_sha256=args.current_snapshot_sha256,
                                       current_ontology_version=args.current_ontology_version)
        for profile in result["profiles"]:
            _write_json(args.output_dir / f"{profile['direction_id']}.json", profile)
        _write_json(args.output_dir / f"{result['portfolio']['portfolio_id']}.json",
                    result["portfolio"])
        _write_json(args.output_dir / "adoption-manifest.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
