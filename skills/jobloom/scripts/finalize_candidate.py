#!/usr/bin/env python3
"""Validate confirmed resume facts and write the canonical candidate.json."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any


PROTECTED_TYPES = {"identity", "education", "experience_header", "certification", "immigration", "security_clearance"}
EVIDENCE_STRENGTHS = {"direct", "strongly_related", "transferable", "mention_only", "none"}


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_profile_settings(settings: dict[str, Any]) -> None:
    for key in ("profile_id", "work_authorization", "search"):
        if key not in settings:
            raise ValueError(f"profile settings missing required field: {key}")
    auth = settings["work_authorization"]
    required = {
        "country", "authorized_now", "sponsorship_now", "sponsorship_future",
        "employer_action_required", "confirmed",
    }
    missing = sorted(required - set(auth))
    if missing:
        raise ValueError(f"work_authorization missing required fields: {', '.join(missing)}")
    if not isinstance(settings["profile_id"], str) or not settings["profile_id"].strip():
        raise ValueError("profile_id must be a non-empty string")
    if not isinstance(auth["country"], str) or not auth["country"].strip():
        raise ValueError("work_authorization.country must be a non-empty string")
    for field in ("authorized_now", "sponsorship_now", "sponsorship_future", "employer_action_required", "confirmed"):
        if not isinstance(auth[field], bool):
            raise ValueError(f"work_authorization.{field} must be true or false")
    if not auth["confirmed"]:
        raise ValueError("work_authorization must be explicitly confirmed")
    search = settings["search"]
    for field in ("countries", "work_arrangements", "employment_types", "excluded_employers"):
        if not isinstance(search.get(field), list):
            raise ValueError(f"search.{field} must be a list")
    if search.get("salary_floor") is not None and not search.get("salary_currency"):
        raise ValueError("search.salary_currency is required when salary_floor is set")


def finalize(review: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    validate_profile_settings(settings)
    facts = review.get("facts")
    if not isinstance(facts, list) or not facts:
        raise ValueError("review packet contains no facts")
    pending = [fact.get("id", "unknown") for fact in facts if fact.get("decision") == "pending"]
    invalid = [fact.get("id", "unknown") for fact in facts if fact.get("decision") not in {"pending", "confirmed", "rejected"}]
    if invalid:
        raise ValueError(f"facts have invalid decisions: {', '.join(invalid)}")
    if pending:
        raise ValueError(f"facts still pending review: {', '.join(pending)}")

    confirmed = []
    for fact in facts:
        if fact["decision"] == "rejected":
            continue
        if fact.get("evidence_strength") not in EVIDENCE_STRENGTHS:
            raise ValueError(f"fact {fact.get('id')} has invalid evidence_strength")
        output = {key: fact.get(key) for key in (
            "id", "type", "value", "keywords", "source", "evidence_strength", "expires_at", "invalidation_triggers"
        )}
        locked = bool(fact.get("lock_on_confirm") or fact.get("type") in PROTECTED_TYPES)
        output["status"] = "locked" if locked else "confirmed"
        output["locked"] = locked
        output["confirmed_at"] = fact.get("confirmed_at") or date.today().isoformat()
        output["review_note"] = fact.get("review_note")
        confirmed.append(output)

    source_document = review.get("source_document", {})
    result = {
        "schema_version": "0.2.0",
        "profile_id": settings["profile_id"],
        "generated_at": date.today().isoformat(),
        "source_documents": [source_document],
        "work_authorization": settings["work_authorization"],
        "search": settings["search"],
        "citizenships": settings.get("citizenships", []),
        "security_clearances": settings.get("security_clearances", []),
        "certifications": settings.get("certifications", []),
        "facts": confirmed,
    }
    result["content_sha256"] = canonical_hash(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    review = json.loads(args.review.read_text(encoding="utf-8"))
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    args.output.write_text(json.dumps(finalize(review, settings), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
