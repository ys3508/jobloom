"""Aggregate authorized structured JobCards into fail-closed market profiles."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def aggregate(cards: list[dict[str, Any]], *, profile_id: str, title_id: str,
              region: dict[str, Any], seniority_band: str, window: dict[str, Any]) -> dict[str, Any]:
    deduped, seen = [], set()
    for card in cards:
        key = card.get("canonical_url") or card.get("requisition_id") or (
            str(card.get("employer", "")).casefold(), str(card.get("title", "")).casefold(),
            str(card.get("location", "")).casefold(), card.get("description_sha256"))
        if key in seen: continue
        seen.add(key); deduped.append(card)
    employers = [str(c.get("employer") or "unknown").casefold() for c in deduped]
    employer_counts = Counter(employers); distinct = len(employer_counts)
    max_share = max(employer_counts.values(), default=0) / len(deduped) if deduped else 0
    term_posts, term_employers = Counter(), defaultdict(set)
    for card, employer in zip(deduped, employers):
        terms = {str(x).strip() for x in (card.get("required_skills") or []) + (card.get("required_certifications") or []) if str(x).strip()}
        for term in terms: term_posts[term] += 1; term_employers[term].add(employer)
    required, preferred = [], []
    for term in sorted(term_posts, key=str.casefold):
        es = len(term_employers[term]) / distinct if distinct else 0
        ps = term_posts[term] / len(deduped) if deduped else 0
        item = {"term": term, "employer_support": round(es, 3), "posting_support": round(ps, 3), "obligation": "required"}
        if es >= .30: required.append(item)
        elif es >= .15: preferred.append(item)
    reasons = []
    if len(deduped) < 25: reasons.append("market_postings_below_minimum")
    if distinct < 10: reasons.append("market_employers_below_minimum")
    return {"schema_version": "0.1.0", "profile_id": profile_id, "title_id": title_id,
            "region": region, "seniority_band": seniority_band, "window": window,
            "sample": {"postings_collected": len(cards), "postings_after_dedupe": len(deduped),
                       "distinct_employers": distinct, "max_employer_share": round(max_share, 3),
                       "single_employer_risk": max_share > .30, "sufficient": not reasons,
                       "insufficient_reasons": reasons},
            "required_terms": required, "preferred_terms": preferred,
            "sponsorship_distribution": {"supports": 0.0, "does_not_support": 0.0, "unknown": 1.0}}


def unavailable(profile_id: str, title_id: str, region: dict[str, Any]) -> dict[str, Any]:
    return aggregate([], profile_id=profile_id, title_id=title_id, region=region,
                     seniority_band="unknown", window={"days": 90})
