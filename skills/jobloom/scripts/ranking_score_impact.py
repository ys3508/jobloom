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
import hashlib
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


def covered_requirement_count(routing: dict, card: dict) -> int:
    """Requirements a candidate fact actually covers.

    The length of `required_skill_evidence` is how many requirements were recognised, which
    a posting can raise just by listing more of them. Only `covered_by_candidate_fact`
    counts evidence.
    """
    return sum(1 for item in (routing.get("required_skill_evidence") or [])
               if item.get("covered_by_candidate_fact"))


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
        route=route_with_score(covered_requirement_count))

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
        # `build_queue` returns `direction_hits`. Reading a key it does not return compared
        # None with None and called it "identical", which is how a claim goes unmeasured
        # while looking measured.
        "direction_hits": {"baseline": baseline["direction_hits"],
                           "score_neutralised": neutral["direction_hits"]},
        "direction_hits_identical": baseline["direction_hits"] == neutral["direction_hits"],
    }


# --------------------------------------------------------------------------- assist bridge

def assist_bridge_ordering(cards, candidate, profiles) -> dict:
    """The consumer that sorts on the score at position two, not five.

    `review_queue` demotes it to a late tiebreak and says so. The assist bridge orders a
    job's directions by `(decision, -ranking_score, direction_id)`, where only the decision
    bucket comes first - so within a bucket the score decides outright. Measured here
    because a liability recorded as database-only exposure would otherwise miss it.
    """
    changed_buckets = 0
    moved_directions = 0
    buckets_examined = 0
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
            buckets_examined += 1
            # Scores differing is not an order changing. Compare the order the bridge
            # produces against the order it would produce with the score removed.
            with_score = [d for _, d in sorted(members, key=lambda m: (-m[0], m[1]))]
            without_score = sorted(direction_id for _, direction_id in members)
            if with_score != without_score:
                changed_buckets += 1
                moved_directions += sum(1 for a, b in zip(with_score, without_score) if a != b)
    return {"jobs": len(cards), "buckets_with_two_or_more_directions": buckets_examined,
            "changed_buckets": changed_buckets, "moved_directions": moved_directions,
            "sort_position": 2, "sort_key": "(decision, -ranking_score, direction_id)",
            "neutral_sort_key": "(decision, direction_id)", "source": "assist_bridge.py"}


RELEVANT_TO_THE_NUMBERS = ("skills/jobloom/scripts/direction_core.py",
                           "skills/jobloom/scripts/evidence_matcher.py",
                           "skills/jobloom/scripts/review_queue.py",
                           # Its ordering rule is reimplemented here rather than called, so
                           # its bytes are part of what these numbers depend on.
                           "skills/jobloom/scripts/assist_bridge.py")

# Rows the routing actually reads. The database file's own hash moves with WAL state and
# with tables nothing here touches, so it would report change that changed no result.
RELEVANT_DB_QUERIES = (
    ("routing_records", "SELECT record_id, job_id, direction_id, decision, ranking_score "
                        "FROM routing_records ORDER BY record_id"),
    ("search_portfolios", "SELECT portfolio_id, portfolio_sha256, status FROM search_portfolios "
                          "WHERE status='approved' ORDER BY portfolio_id"),
    ("search_directions", "SELECT direction_id, profile_sha256, status FROM search_directions "
                          "WHERE status='approved' ORDER BY direction_id"),
)


def relevant_db_hash(db: Path) -> str | None:
    """A canonical hash of the rows the measurement reads, not of the file it lives in."""
    if not db.is_file():
        return None
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        payload = {name: [list(row) for row in connection.execute(query)]
                   for name, query in RELEVANT_DB_QUERIES}
    except sqlite3.Error as error:
        return f"unavailable:{type(error).__name__}"
    finally:
        connection.close()
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        .encode("utf-8")).hexdigest()


def input_snapshot(db: Path, candidate: Path, cards: Path) -> dict:
    """Everything the numbers depend on, hashed at one moment."""
    manifest = cards / "manifest.json"
    return {
        "routing_source_sha256": {path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                                  for path in RELEVANT_TO_THE_NUMBERS if Path(path).is_file()},
        "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()
        if candidate.is_file() else None,
        "relevant_database_rows_sha256": relevant_db_hash(db),
        "cards_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()
        if manifest.is_file() else None,
    }


def git_state() -> dict:
    import subprocess

    def run(*args):
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=20,
                                  check=False).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""

    dirty = [line[2:].strip() for line in run("git", "status", "--porcelain").splitlines()
             if line.strip()]
    relevant = sorted(set(dirty) & set(RELEVANT_TO_THE_NUMBERS))
    return {"git_head": run("git", "rev-parse", "HEAD"),
            "working_tree_dirty_files": sorted(dirty),
            "dirty_files_relevant_to_these_numbers": relevant,
            # What the commit alone can account for. Never "reproducible": the candidate,
            # the database and the cards are not in Git, so a HEAD cannot reproduce a
            # number computed from them however clean the tree is.
            "code_state": "dirty" if relevant else "matches_head"}


def provenance(before: dict, after: dict, git: dict, cards: Path) -> dict:
    stable = before == after
    return {
        **git,
        "external_inputs_hash_recorded": True,
        "external_inputs": before,
        # Hashes taken before the measurement and again after it. Taking them once would
        # let a card, a row or a source file change midway and pair an old hash with a new
        # result, which reads as evidence and is not.
        "inputs_stable_across_measurement": stable,
        "observation_reproducibility": "inputs_not_preserved",
        "assist_bridge_ordering": "source_rule_reimplemented",
        "cards_directory": str(cards),
        "status": ("input_changed_during_measurement" if not stable
                   else "provisional_dirty_input" if git["dirty_files_relevant_to_these_numbers"]
                   else "provisional_clean_code"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=Path(".jobloom/jobloom.db"))
    parser.add_argument("--candidate", type=Path, default=Path(".jobloom/candidate.json"))
    parser.add_argument("--cards", type=Path, default=Path(".jobloom/jobs-wide"))
    parser.add_argument("--limit", type=int, help="cap the corpus, for a quick pass")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    git = git_state()
    before = input_snapshot(args.db, args.candidate, args.cards)
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
    report["provenance"] = provenance(before,
                                      input_snapshot(args.db, args.candidate, args.cards),
                                      git, args.cards)
    report["conclusion"] = {
        "gates_pool_entry": not report["live_counterfactual"]["pool_membership_identical"],
        "changes_allocation": not report["live_counterfactual"]["direction_hits_identical"],
        "changes_review_order": report["live_counterfactual"]["rank_changes_when_neutralised"] > 0,
        "orders_directions_in_assist_bridge":
            report["assist_bridge"]["changed_buckets"] > 0,
        # Named so nobody reads a count as a cause. What moved is what moved.
        "note": "counts of what would change, not evidence that changing it would improve anything",
        "usable": report["provenance"]["status"] != "input_changed_during_measurement",
    }
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
