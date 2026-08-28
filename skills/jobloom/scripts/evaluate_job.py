#!/usr/bin/env python3
"""Deterministic Jobloom MVP job evaluator. Uses no model or network."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
import sys

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from evidence_matcher import (  # noqa: E402
    EVIDENCE_ORDER, expired_or_invalid, match_requirement, related_facts,
)


def _require(mapping: dict[str, Any], keys: list[str], label: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ValueError(f"{label} missing required fields: {', '.join(missing)}")


def validate_inputs(candidate: dict[str, Any], job: dict[str, Any]) -> None:
    _require(candidate, ["profile_id", "work_authorization", "search", "facts"], "candidate")
    _require(
        candidate["work_authorization"],
        ["country", "authorized_now", "sponsorship_now", "sponsorship_future", "employer_action_required", "confirmed"],
        "candidate.work_authorization",
    )
    _require(
        job,
        ["job_id", "canonical_url", "employer", "title", "country", "work_arrangement", "employment_type", "status", "sponsorship", "required_skills", "requirements_reviewed"],
        "job",
    )
    for fact in candidate["facts"]:
        _require(fact, ["id", "value", "evidence_strength", "status"], "candidate.fact")
        if fact["evidence_strength"] not in EVIDENCE_ORDER:
            raise ValueError(f"invalid evidence strength: {fact['evidence_strength']}")


def _match_skill(skill: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
    return match_requirement(skill, facts)


def _fact_issue(skill: str, facts: list[dict[str, Any]]) -> str | None:
    related = related_facts(skill, facts)
    if any(fact.get("status") == "conflicting" for fact in related):
        return f"candidate_evidence_conflict:{skill}"
    if any(fact.get("status") == "stale" or expired_or_invalid(fact.get("expires_at")) for fact in related):
        return f"candidate_evidence_stale:{skill}"
    return None


def evaluate(candidate: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    validate_inputs(candidate, job)
    failures: list[str] = []
    uncertainties: list[str] = []
    reasons: list[str] = []
    search = candidate["search"]
    auth = candidate["work_authorization"]

    if not job["requirements_reviewed"]:
        uncertainties.append("job_requirements_unreviewed")

    if job["status"] == "closed":
        failures.append("job_not_open")
    elif job["status"] != "open":
        uncertainties.append("job_status_unknown")
    if job.get("already_applied"):
        failures.append("duplicate_application")
    if job["employer"] == "unknown":
        uncertainties.append("employer_unknown")
    elif job["employer"].casefold() in {x.casefold() for x in search.get("excluded_employers", [])}:
        failures.append("excluded_employer")
    if job["country"] == "unknown":
        uncertainties.append("job_country_unknown")
    elif search.get("countries") and job["country"] not in search["countries"]:
        failures.append("country_outside_search_scope")
    if job["country"] == auth["country"]:
        if not auth["confirmed"] or expired_or_invalid(auth.get("expires_at")):
            uncertainties.append("work_authorization_stale_or_unconfirmed")
        elif not auth["authorized_now"]:
            failures.append("not_authorized_to_work_now")
        needs_employer_support = auth["sponsorship_now"] or auth["sponsorship_future"] or auth["employer_action_required"]
        if needs_employer_support and job["sponsorship"] == "does_not_support":
            failures.append("required_sponsorship_not_supported")
        elif needs_employer_support and job["sponsorship"] in {"unknown", "historical_support", "conflicting"}:
            uncertainties.append("sponsorship_requires_review")
    elif job["country"] != "unknown":
        uncertainties.append("work_authorization_country_mismatch")

    if job["work_arrangement"] == "unknown":
        uncertainties.append("work_arrangement_unknown")
    elif search.get("work_arrangements") and job["work_arrangement"] not in search["work_arrangements"]:
        failures.append("incompatible_work_arrangement")
    if job["work_arrangement"] != "remote" and search.get("locations"):
        if job.get("location") in (None, "unknown"):
            uncertainties.append("job_location_unknown")
        elif not any(location.casefold() in job["location"].casefold() or job["location"].casefold() in location.casefold() for location in search["locations"]):
            failures.append("location_outside_search_scope")
    if job["employment_type"] == "unknown":
        uncertainties.append("employment_type_unknown")
    elif search.get("employment_types") and job["employment_type"] not in search["employment_types"]:
        failures.append("incompatible_employment_type")

    salary = job.get("salary")
    floor = search.get("salary_floor")
    salary_currency = search.get("salary_currency")
    if salary and floor is not None and salary.get("max") is not None:
        unit = str(salary.get("unit", "YEAR")).upper()
        if unit not in {"YEAR", "ANNUAL"}:
            uncertainties.append("salary_unit_requires_review")
        elif salary_currency and salary.get("currency") != salary_currency:
            uncertainties.append("salary_currency_requires_review")
        else:
            try:
                if float(salary["max"]) < float(floor):
                    failures.append("salary_below_floor")
            except (TypeError, ValueError):
                uncertainties.append("salary_value_requires_review")

    citizenship = job.get("citizenship_required")
    if citizenship and citizenship not in candidate.get("citizenships", []):
        failures.append("citizenship_requirement_not_met")
    clearance = job.get("security_clearance_required")
    if clearance and clearance not in candidate.get("security_clearances", []):
        failures.append("security_clearance_requirement_not_met")
    held_certs = {item.casefold() for item in candidate.get("certifications", [])}
    missing_certs = [item for item in job.get("required_certifications", []) if item.casefold() not in held_certs]
    if missing_certs:
        failures.append("required_certification_missing")

    evidence = [_match_skill(skill, candidate["facts"]) for skill in job["required_skills"]]
    evidence_issues = [issue for skill in job["required_skills"] if (issue := _fact_issue(skill, candidate["facts"]))]
    uncertainties.extend(evidence_issues)
    missing = [item["requirement"] for item in evidence if item["strength"] in {"none", "mention_only"}]
    direct = sum(item["strength"] == "direct" for item in evidence)
    supported = sum(EVIDENCE_ORDER[item["strength"]] >= EVIDENCE_ORDER["strongly_related"] for item in evidence)
    total = len(evidence)

    if failures:
        eligibility, match, action = "fail", "not_recommended", "skip"
        reasons.extend(failures[:3])
    elif uncertainties:
        eligibility, match, action = "uncertain", "borderline", "review"
        reasons.extend(uncertainties[:3])
    elif missing:
        eligibility, match, action = "pass", "borderline", "review"
        reasons.append("mandatory_evidence_gap")
    elif total and direct == total:
        eligibility, match = "pass", "strong"
        action = "precision" if job.get("high_value") else "broad"
        reasons.append("all_required_skills_have_direct_evidence")
    elif total == 0 or supported == total:
        eligibility, match, action = "pass", "worth_applying", "broad"
        reasons.append("required_skills_have_supported_evidence")
    else:
        eligibility, match, action = "pass", "borderline", "review"
        reasons.append("requirements_rely_on_transferable_evidence")

    return {
        "job_card": {key: job.get(key) for key in (
            "job_id", "canonical_url", "employer", "title", "country", "location",
            "work_arrangement", "employment_type", "salary", "sponsorship",
        )},
        "eligibility": eligibility,
        "match": match,
        "action": action,
        "reasons": reasons[:3],
        "hard_filter_failures": failures,
        "uncertainties": uncertainties,
        "evidence_matches": evidence,
        "main_gap": missing[0] if missing else (failures[0] if failures else None),
        "user_decision_required": uncertainties[0] if uncertainties else ("resolve_evidence_gap" if missing else None),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    job = json.loads(args.job.read_text(encoding="utf-8"))
    rendered = json.dumps(evaluate(candidate, job), indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
