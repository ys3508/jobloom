#!/usr/bin/env python3
"""Measure what `ranking_score` actually decides, before anyone decides what to do about it.

`references/known-liabilities.md` records that the score runs against the evidence: over one
real pull, openings with a directly covered requirement averaged 119.9 while openings with
none averaged 125.3. The liability was left unpaid on purpose, with the note that settling
it means measuring what the score drives rather than rewriting a contract whose consumers
are unmeasured. This is that measurement.

Three questions, answered apart, because they fail differently:

  reachability   which code paths read the score at all, and at what position in a sort.
                 Answered by behaviour - rows built to differ in exactly one key - not by
                 reading the source and trusting the reading.
  stored data    what would change in records already written. Bounded by how few there are,
                 and reported as bounded rather than extrapolated.
  live corpus    what changes across the pulled corpus when the score is neutralised or
                 replaced by evidence coverage. The counterfactual with enough n to mean
                 something, and still a counterfactual, not a causal claim.

It writes no database, changes no weight, and proposes no fix.
"""

from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import direction_core  # noqa: E402
import review_queue  # noqa: E402

AUDIT_SCHEMA_VERSION = "ranking-score-impact-v1"
NEUTRAL_SCORE = 0


# --------------------------------------------------------------------------- reachability

def sort_key_position(build_row, keys: list[str]) -> dict:
    """Where `ranking_score` sits, established by making it the only thing that differs.

    Reading a sort tuple and counting commas would answer this too, until someone reorders
    it. Two rows that differ in exactly one key cannot be misread.
    """
    findings = {}
    for key in keys:
        low, high = build_row(key, 0), build_row(key, 1)
        findings[key] = "decides" if review_queue.sort_key(low) != review_queue.sort_key(high) \
            else "ignored"
    ordered = []
    for key in keys:
        low, high = build_row(key, 0), build_row(key, 1)
        ordered.append((key, [i for i, (a, b) in
                              enumerate(zip(review_queue.sort_key(low), review_queue.sort_key(high)))
                              if a != b]))
    return {"per_key": findings,
            "tuple_positions": {key: positions for key, positions in ordered if positions}}


def queue_row(weight_percent=50, direct=1, covered=1, technical_hits=1, ranking_score=100,
              employer="acme", title="analyst"):
    return {"weight_percent": weight_percent, "ranking_score": ranking_score,
            "employer": employer, "title": title,
            "evidence": {"direct": direct, "covered": covered,
                         "technical_hits": technical_hits}}


def reachability() -> dict:
    keys = ["weight_percent", "direct", "covered", "technical_hits", "ranking_score"]

    def build_row(key, value):
        overrides = {"weight_percent": 50, "direct": 1, "covered": 1, "technical_hits": 1,
                     "ranking_score": 100}
        overrides[key] = {"ranking_score": (100, 200)}.get(key, (1, 9))[value]
        return queue_row(**overrides)

    positions = sort_key_position(build_row, keys)
    # Rows identical on every earlier key: the only situation where the score can decide.
    tie = review_queue.sort_key(queue_row(ranking_score=200)) < review_queue.sort_key(
        queue_row(ranking_score=100))
    # And one where an earlier key disagrees with it: evidence must win.
    dominated = review_queue.sort_key(queue_row(direct=2, ranking_score=1)) < \
        review_queue.sort_key(queue_row(direct=1, ranking_score=999))
    return {"sort_key": positions,
            "decides_only_on_a_full_tie": bool(tie),
            "outranked_by_direct_evidence": bool(dominated)}


# --------------------------------------------------------------------------- stored data

def stored_records(db_path: Path) -> dict:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in connection.execute(
            "SELECT job_id, direction_id, decision, ranking_score FROM routing_records")]
    finally:
        connection.close()
    by_job = collections.Counter(row["job_id"] for row in rows)
    return {
        "records": len(rows),
        "distinct_jobs": len(by_job),
        "decisions": dict(collections.Counter(row["decision"] for row in rows)),
        # A counterfactual over this many records answers nothing, and saying so is the
        # finding. Extrapolating from it would be the mistake the liability warns about.
        "counterfactual": "insufficient_data" if len(by_job) < 30 else "computable",
        "reason": f"{len(rows)} records across {len(by_job)} job(s)",
    }


# --------------------------------------------------------------------------- live corpus

def load_inputs(db_path: Path, candidate_path: Path) -> tuple[dict, list, dict]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        portfolio_row = connection.execute(
            "SELECT portfolio_json FROM search_portfolios WHERE status='approved'").fetchone()
        if portfolio_row is None:
            raise ValueError("no approved portfolio")
        portfolio = json.loads(portfolio_row["portfolio_json"])
        profiles = {row["direction_id"]: json.loads(row["profile_json"])
                    for row in connection.execute(
                        "SELECT direction_id, profile_json FROM search_directions "
                        "WHERE status='approved'")}
    finally:
        connection.close()
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    allocations = sorted(portfolio["allocations"], key=lambda item: -item["weight_percent"])
    return candidate, allocations, profiles


def route_with_score(replacement):
    """Wrap the real router, substituting only `ranking_score`. Everything else is untouched,
    so a change downstream can only have come from the score."""
    def router(profile, candidate, card):
        routing = direction_core.route_job(profile, candidate, card)
        return {**routing, "ranking_score": replacement(routing, card)}
    return router


def ranks(queue: dict) -> dict:
    return {row["job_id"]: row["rank"] for row in queue["rows"]}


def tiebreak_reached(queue: dict) -> dict:
    """How often consecutive entries are separated only by the score."""
    rows = queue["rows"]
    reached = 0
    for first, second in zip(rows, rows[1:]):
        a, b = review_queue.sort_key(first), review_queue.sort_key(second)
        if a[:4] == b[:4] and a[4] != b[4]:
            reached += 1
    return {"adjacent_pairs": max(len(rows) - 1, 0), "separated_only_by_ranking_score": reached}


def live_counterfactual(cards, candidate, allocations, profiles) -> dict:
    baseline = review_queue.build_queue(cards, candidate, allocations, profiles)
    neutral = review_queue.build_queue(
        cards, candidate, allocations, profiles,
        route=route_with_score(lambda routing, card: NEUTRAL_SCORE))
    by_evidence = review_queue.build_queue(
        cards, candidate, allocations, profiles,
        route=route_with_score(
            lambda routing, card: len(routing.get("required_skill_evidence") or [])))

    base_ranks, neutral_ranks, evidence_ranks = ranks(baseline), ranks(neutral), ranks(by_evidence)
    moved_neutral = [job for job, rank in base_ranks.items()
                     if neutral_ranks.get(job) != rank]
    moved_evidence = [job for job, rank in base_ranks.items()
                      if evidence_ranks.get(job) != rank]
    return {
        "queue_rows": len(baseline["rows"]),
        "entered_review_pool": {"baseline": len(base_ranks), "score_neutralised": len(neutral_ranks),
                                "score_replaced_by_evidence": len(evidence_ranks)},
        # If pool membership is identical under all three, the score gates nothing.
        "pool_membership_identical": set(base_ranks) == set(neutral_ranks) == set(evidence_ranks),
        "tiebreak": tiebreak_reached(baseline),
        "rank_changes_when_neutralised": len(moved_neutral),
        "rank_changes_when_replaced_by_evidence": len(moved_evidence),
        "direction_hits_identical": (baseline.get("hits_per_direction")
                                     == neutral.get("hits_per_direction")),
    }


# --------------------------------------------------------------------------- assist bridge

def assist_bridge_ordering(cards, candidate, profiles) -> dict:
    """The consumer that sorts on the score at position two, not five.

    `review_queue` demotes it to a late tiebreak and says so. The assist bridge orders a
    job's directions by `(decision, -ranking_score, direction_id)`, where only the decision
    bucket comes first - so within a bucket the score decides outright. Measured here
    because a liability recorded as database-only exposure would otherwise miss it.
    """
    decided = 0
    ties_in_bucket = 0
    for card in cards:
        routed = []
        for direction_id, profile in profiles.items():
            try:
                routing = direction_core.route_job(profile, candidate, card)
            except Exception:  # noqa: BLE001 - a card the router rejects is not this audit's subject
                continue
            routed.append((routing.get("decision"), routing.get("ranking_score") or 0, direction_id))
        buckets = collections.defaultdict(list)
        for decision, score, direction_id in routed:
            buckets[decision].append((score, direction_id))
        for members in buckets.values():
            if len(members) < 2:
                continue
            ties_in_bucket += 1
            if len({score for score, _ in members}) > 1:
                decided += 1
    return {"jobs": len(cards), "buckets_with_two_or_more_directions": ties_in_bucket,
            "buckets_ordered_by_ranking_score": decided,
            "sort_position": 2, "sort_key": "(decision, -ranking_score, direction_id)",
            "source": "assist_bridge.py"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=Path(".jobloom/jobloom.db"))
    parser.add_argument("--candidate", type=Path, default=Path(".jobloom/candidate.json"))
    parser.add_argument("--cards", type=Path, default=Path(".jobloom/jobs-wide"))
    parser.add_argument("--limit", type=int, help="cap the corpus, for a quick pass")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = {"schema_version": AUDIT_SCHEMA_VERSION,
              "reachability": reachability(),
              "stored_data": stored_records(args.db)}

    candidate, allocations, profiles = load_inputs(args.db, args.candidate)
    cards = review_queue.load_cards(args.cards)
    if args.limit:
        cards = cards[:args.limit]
    report["corpus"] = {"cards_after_deduplication": len(cards),
                        "directions": len(profiles), "allocations": len(allocations)}
    report["live_counterfactual"] = live_counterfactual(cards, candidate, allocations, profiles)
    report["assist_bridge"] = assist_bridge_ordering(cards, candidate, profiles)
    report["conclusion"] = {
        "gates_pool_entry": not report["live_counterfactual"]["pool_membership_identical"],
        "changes_allocation": not report["live_counterfactual"]["direction_hits_identical"],
        "changes_review_order": report["live_counterfactual"]["rank_changes_when_neutralised"] > 0,
        "orders_directions_in_assist_bridge":
            report["assist_bridge"]["buckets_ordered_by_ranking_score"] > 0,
        # Named so nobody reads a count as a cause. What moved is what moved.
        "note": "counts of what would change, not evidence that changing it would improve anything",
    }
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
