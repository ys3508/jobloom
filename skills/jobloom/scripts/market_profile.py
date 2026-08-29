"""Aggregate authorized structured JobCards into fail-closed market profiles.

Note that this module aggregates only *platform-permitted* sources. `self_asserted` sources
are refused by name, not merely left off the list — see `REFUSED_AUTHORIZATION_BASES`.

This module aggregates; it does not collect. Query templates describe search intent and
are not an authorization to scrape any site, so a JobCard reaches a profile only when the
source it came from is listed in `assets/market-sources.json` with a recorded
authorization basis. The default list holds one entry: postings the user opened and had
structured into a reviewed JobCard themselves.

Thresholds are set so the market half of the engine can switch on for a narrow title in
one metro, while a single employer's stack can never become a direction's core
requirement. A ratio alone does not achieve the second: with five employers, two naming a
term is already 0.40 support. So a term must clear an absolute employer count as well, and
the ratio it must clear rises as the sample shrinks.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from _common import require_table  # noqa: E402

ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"
SOURCES_PATH = ASSET_DIR / "market-sources.json"

MIN_POSTINGS = 20
MIN_EMPLOYERS = 8
# Below this many employers each one swings the ratio too far, so the bar rises.
ROBUST_EMPLOYERS = 15
REQUIRED_SUPPORT = 0.30
REQUIRED_SUPPORT_SMALL_SAMPLE = 0.40
PREFERRED_SUPPORT = 0.15
# A term named by fewer employers than this is one shop's stack, whatever the ratio says.
MIN_EMPLOYERS_PER_TERM = 3
SINGLE_EMPLOYER_RISK = 0.30
DEFAULT_WINDOW_DAYS = 90
SPONSORSHIP_STATES = ("supports", "historical_support", "does_not_support", "unknown")
AUTHORIZATION_BASES = {"user_supplied", "official_api", "licensed_dataset", "employer_feed"}
# Bases that are recognised elsewhere in the system and are refused *here* specifically.
#
# This set is not decoration for what is already absent. A market profile is not a place a
# posting is displayed — it shapes which career directions get proposed and how their market
# and accessibility axes score, through `career_direction_core.profiles_for_functions`. Data
# read on the operator's own compliance judgement rather than the platform's permission must
# not reach that: a posting carrying a `self_asserted` label can still contaminate everything
# downstream of a profile it was allowed to shape, with its label sitting untouched on a card
# nobody downstream reads. Silent promotion is not only relabelling; it is also being
# consumed by something that never checks the label.
#
# Leaving `self_asserted` merely unlisted would be defence by omission: the next person
# wanting scraped postings to be more useful adds one word to AUTHORIZATION_BASES and cannot
# tell a deliberate exclusion from an oversight. Refusing it by name makes that edit fail a
# test that says which gate is being removed.
REFUSED_AUTHORIZATION_BASES = {"self_asserted"}
SOURCE_KEYS = {"source_id", "name", "authorization_basis", "terms_url", "recorded_at", "notes"}


def load_sources(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Sources whose terms permit collection. Unlisted sources contribute nothing."""
    value = json.loads((path or SOURCES_PATH).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "0.1.0":
        raise ValueError("market source registry has an unsupported schema_version")
    sources = value.get("sources")
    if not isinstance(sources, list):
        raise ValueError("market source registry requires a source list")
    by_id: dict[str, dict[str, Any]] = {}
    for item in sources:
        if not isinstance(item, dict) or set(item) - SOURCE_KEYS or "source_id" not in item:
            raise ValueError("market source entry has missing or unknown fields")
        if item.get("authorization_basis") in REFUSED_AUTHORIZATION_BASES:
            raise ValueError(
                f"market source {item['source_id']} declares "
                f"{item['authorization_basis']}, which may never shape a market profile: "
                "profiles decide which directions are proposed, and a source read on the "
                "operator's own compliance judgement must not define where to look for work")
        if item.get("authorization_basis") not in AUTHORIZATION_BASES:
            raise ValueError("market source requires a recorded authorization basis")
        if item["source_id"] in by_id:
            raise ValueError("market source IDs must be unique")
        by_id[item["source_id"]] = item
    return by_id


def _percentiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)

    def at(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return ordered[index]

    return {"p25": at(0.25), "p50": at(0.50), "p75": at(0.75)}


def _sponsorship(cards: list[dict[str, Any]]) -> dict[str, float]:
    counts = Counter(str(card.get("sponsorship") or "unknown") for card in cards)
    total = sum(counts.values())
    if not total:
        return {state: (1.0 if state == "unknown" else 0.0) for state in SPONSORSHIP_STATES}
    return {state: round(counts.get(state, 0) / total, 3) for state in SPONSORSHIP_STATES}


def _salary(cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    midpoints, currencies = [], set()
    for card in cards:
        salary = card.get("salary")
        if not isinstance(salary, dict):
            continue
        low, high = salary.get("minimum"), salary.get("maximum")
        values = [v for v in (low, high) if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not values:
            continue
        midpoints.append(sum(values) / len(values))
        if salary.get("currency"):
            currencies.add(str(salary["currency"]))
    if not midpoints or len(currencies) > 1:
        # Mixed currencies are not comparable; report nothing rather than a wrong number.
        return None
    percentiles = _percentiles(midpoints)
    return {"currency": next(iter(currencies), None), "reported_share": round(len(midpoints) / len(cards), 3),
            **{key: round(value) for key, value in (percentiles or {}).items()}}


def aggregate(cards: list[dict[str, Any]], *, profile_id: str, title_id: str,
              region: dict[str, Any], seniority_band: str, window: dict[str, Any],
              provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    deduped, seen = [], set()
    for card in cards:
        # A requisition id or canonical URL identifies one opening. Falling back to
        # employer/title/location alone would delete genuinely separate requisitions that
        # one employer posted for the same role in the same place.
        key = card.get("canonical_url") or card.get("requisition_id") or (
            str(card.get("employer", "")).casefold(), str(card.get("title", "")).casefold(),
            str(card.get("location", "")).casefold(), card.get("description_sha256"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(card)
    employers = [str(c.get("employer") or "unknown").casefold() for c in deduped]
    employer_counts = Counter(employers)
    distinct = len(employer_counts)
    max_share = max(employer_counts.values(), default=0) / len(deduped) if deduped else 0
    term_posts: Counter = Counter()
    term_employers: defaultdict[str, set] = defaultdict(set)
    for card, employer in zip(deduped, employers):
        terms = {str(x).strip() for x in (card.get("required_skills") or [])
                 + (card.get("required_certifications") or []) if str(x).strip()}
        for term in terms:
            term_posts[term] += 1
            term_employers[term].add(employer)
    required_ratio = (REQUIRED_SUPPORT if distinct >= ROBUST_EMPLOYERS
                      else REQUIRED_SUPPORT_SMALL_SAMPLE)
    required, preferred = [], []
    for term in sorted(term_posts, key=str.casefold):
        naming = len(term_employers[term])
        es = naming / distinct if distinct else 0
        ps = term_posts[term] / len(deduped) if deduped else 0
        item = {"term": term, "employer_support": round(es, 3), "posting_support": round(ps, 3),
                "naming_employers": naming, "obligation": "required"}
        if es >= required_ratio and naming >= MIN_EMPLOYERS_PER_TERM:
            required.append(item)
        elif es >= PREFERRED_SUPPORT:
            preferred.append({**item, "obligation": "preferred"})
    reasons = []
    if len(deduped) < MIN_POSTINGS:
        reasons.append("market_postings_below_minimum")
    if distinct < MIN_EMPLOYERS:
        reasons.append("market_employers_below_minimum")
    return {
        "schema_version": "0.1.0", "profile_id": profile_id, "title_id": title_id,
        "region": region, "seniority_band": seniority_band, "window": window,
        "sample": {"postings_collected": len(cards), "postings_after_dedupe": len(deduped),
                   "distinct_employers": distinct, "max_employer_share": round(max_share, 3),
                   "single_employer_risk": max_share > SINGLE_EMPLOYER_RISK,
                   "required_support_threshold": required_ratio,
                   "sufficient": not reasons, "insufficient_reasons": reasons},
        "required_terms": required, "preferred_terms": preferred,
        "sponsorship_distribution": _sponsorship(deduped),
        "salary": _salary(deduped),
        "provenance": provenance or {"sources": [], "collected_at": None},
    }


def unavailable(profile_id: str, title_id: str, region: dict[str, Any]) -> dict[str, Any]:
    return aggregate([], profile_id=profile_id, title_id=title_id, region=region,
                     seniority_band="unknown", window={"days": DEFAULT_WINDOW_DAYS})


def _within_window(created_at: str | None, *, days: int, now: datetime) -> bool:
    if not created_at:
        return False
    try:
        stamp = datetime.fromisoformat(str(created_at))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp >= now - timedelta(days=days)


def _matches(card: dict[str, Any], row: sqlite3.Row, *, title_terms: list[str],
             region: dict[str, Any], seniority_band: str | None) -> bool:
    normalized_title = str(row["normalized_title"] or "")
    if title_terms and not all(term in normalized_title for term in title_terms):
        return False
    country = region.get("country")
    if country and str(card.get("country") or "").casefold() != str(country).casefold():
        return False
    metro = region.get("metro")
    if metro:
        wanted = "".join(ch for ch in str(metro).casefold() if ch.isalnum() or ch.isspace())
        if wanted.strip() not in str(row["normalized_location"] or ""):
            return False
    if seniority_band and str(card.get("seniority") or "unknown") != seniority_band:
        return False
    return True


def build_from_store(connection: sqlite3.Connection, *, profile_id: str, title_id: str,
                     title_terms: list[str], region: dict[str, Any],
                     seniority_band: str | None = None, window_days: int = DEFAULT_WINDOW_DAYS,
                     sources: dict[str, dict[str, Any]] | None = None,
                     now: datetime | None = None) -> dict[str, Any]:
    """Aggregate reviewed JobCards already held locally, filtered to authorized sources.

    Fails closed in two independent ways: a card whose source is not in the registry is
    excluded whatever else is true of it, and a sample under the floor yields
    `sufficient: false` so every market axis stays null.
    """
    require_table(connection, "jobs")
    registry = load_sources() if sources is None else sources
    moment = now or datetime.now(timezone.utc)
    cards, used_sources, excluded = [], Counter(), Counter()
    for row in connection.execute("SELECT * FROM jobs"):
        source_id = row["source"] or "user_reviewed"
        if source_id not in registry:
            excluded[source_id] += 1
            continue
        try:
            card = json.loads(row["job_card_json"])
        except (TypeError, ValueError):
            excluded["unparseable_job_card"] += 1
            continue
        if not card.get("requirements_reviewed"):
            excluded["job_card_unreviewed"] += 1
            continue
        if not _within_window(row["created_at"], days=window_days, now=moment):
            excluded["outside_window"] += 1
            continue
        if not _matches(card, row, title_terms=[t.casefold() for t in title_terms],
                        region=region, seniority_band=seniority_band):
            excluded["outside_stratum"] += 1
            continue
        card = dict(card)
        card.setdefault("requisition_id", row["requisition_id"])
        cards.append(card)
        used_sources[source_id] += 1
    window = {"days": window_days, "to": moment.date().isoformat(),
              "from": (moment - timedelta(days=window_days)).date().isoformat()}
    provenance = {
        "sources": [{"source_id": source_id, "postings": count,
                     "authorization_basis": registry[source_id]["authorization_basis"]}
                    for source_id, count in sorted(used_sources.items())],
        "excluded": dict(sorted(excluded.items())),
        "collected_at": moment.isoformat(),
        "collector_version": "1.0.0",
    }
    return aggregate(cards, profile_id=profile_id, title_id=title_id, region=region,
                     seniority_band=seniority_band or "unknown", window=window,
                     provenance=provenance)


def profiles_for_functions(connection: sqlite3.Connection, ontology: dict[str, Any], *,
                           region: dict[str, Any], seniority_band: str | None = None,
                           window_days: int = DEFAULT_WINDOW_DAYS,
                           title_index: dict[str, list[str]] | None = None,
                           now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """One profile per FunctionNode, keyed the way the pipeline consumes them.

    A function with no authorized postings in the stratum still gets a profile: it carries
    `sufficient: false` and the reasons, so the market axes read null with a stated cause
    rather than silently disappearing.
    """
    profiles = {}
    for function in ontology.get("function_nodes", []):
        function_id = function["function_id"]
        terms = (title_index or {}).get(function_id)
        if terms is None:
            # Fall back to the discriminating words of the function's own label.
            terms = [word for word in str(function["canonical_label"]).casefold().split()
                     if word not in {"and", "or", "of", "the", "analysis", "analytics"}][:1]
        profiles[function_id] = build_from_store(
            connection, profile_id=f"mrp-{function_id[3:]}", title_id=function_id,
            title_terms=terms, region=region, seniority_band=seniority_band,
            window_days=window_days, now=now)
    return profiles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sources")
    build = commands.add_parser("build")
    build.add_argument("--db", required=True, type=Path)
    build.add_argument("--profile-id", required=True)
    build.add_argument("--title-id", required=True)
    build.add_argument("--title-term", action="append", default=[],
                       help="token that must appear in the normalized job title; repeatable")
    build.add_argument("--country")
    build.add_argument("--metro")
    build.add_argument("--seniority-band")
    build.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    build.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "sources":
        result = {"sources": sorted(load_sources().values(), key=lambda s: s["source_id"])}
    else:
        connection = sqlite3.connect(str(args.db))
        connection.row_factory = sqlite3.Row
        region = {key: value for key, value in
                  (("country", args.country), ("metro", args.metro)) if value}
        result = build_from_store(
            connection, profile_id=args.profile_id, title_id=args.title_id,
            title_terms=args.title_term, region=region, seniority_band=args.seniority_band,
            window_days=args.window_days)
        connection.close()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
