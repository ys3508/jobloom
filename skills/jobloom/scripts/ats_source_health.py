#!/usr/bin/env python3
"""Notice when a job source quietly stops working, before its silence looks like a market.

An ATS adapter rarely fails loudly. It returns 200 with an empty employer, or keeps
parsing titles after a field moved, or yields nothing at all - and a search that finds
fewer jobs reads exactly like a market with fewer jobs. This measures each source against
what it did last time and says which reading applies.

Measured over one real 2,454-card pull before this was written, which changed its shape:

* A signal that fires on every card from a source is not drift, it is that adapter's
  shape. `requisition_id` is absent from 100% of lever and ashby cards and present in
  98% of greenhouse ones. Reported as drift it would be 1,883 false alarms; what it
  actually says is that two adapters never extract a field that is sitting in the URL,
  and that the id is one of the deduplication keys.
* A canonical URL that is not on the ATS host is usually not a bug either: four
  greenhouse boards serve from the employer's own domain. Compared against the ATS name
  it looked like 101 broken URLs; compared against the registered board URL it is a
  hosting choice.
* So a threshold cannot be written in advance. The first run records a baseline profile
  per source; later runs compare against it, and drift is a *change* in a rate, never
  a rate on its own.

Read-only and offline. It reads a corpus of already-fetched JobCards and the source
registry, and makes no request to any board: a rate limit is not evidence of breakage,
and the cheapest way to never mistake one for the other is not to ask.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

PROFILE_SCHEMA_VERSION = "ats-source-profile-v1"
# A rate this far from its baseline is drift rather than sampling. Deliberately loose:
# with a handful of cards from a small board, a stricter bar reports noise every run.
DRIFT_DELTA = 0.25
MIN_CARDS_FOR_DRIFT = 5

ENTITY = re.compile(r"&(?:amp|lt|gt|quot|nbsp|#\d+);")
ID_LIKE = re.compile(r"[0-9a-f]{8,}(?:-[0-9a-f]{4,})*|\d{4,}")

VERDICTS = ("healthy", "unchanged", "suspect_parser_drift", "no_yield", "never_yielded",
            "employer_name_mismatch", "not_in_baseline", "insufficient_data")


def sha256_text(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def host_of(url: str | None) -> str:
    return re.sub(r"^https?://", "", url or "").split("/")[0].casefold()


def blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def load_registry(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["sources"]


def load_corpus(path: Path) -> list[dict]:
    """Every JobCard in a directory. A file without an `ats` is not a card."""
    cards = []
    for file in sorted(path.glob("*.json")):
        card = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(card, dict) and card.get("ats"):
            cards.append(card)
    return cards


def source_key(company: str, ats: str) -> str:
    return f"{company.strip().casefold()}::{ats}"


def signal_rates(cards: list[dict], board_host: str) -> dict:
    """Per-source rates. Rates, not counts, so a board that grew is not read as degraded."""
    total = len(cards)
    counts = collections.Counter()
    for card in cards:
        for field in ("employer", "title", "location", "canonical_url", "description"):
            if blank(card.get(field)):
                counts[f"blank_{field}"] += 1
        for field in ("employer", "title", "description"):
            value = card.get(field)
            if isinstance(value, str) and ENTITY.search(value):
                counts[f"undecoded_entities_{field}"] += 1
        if not card.get("requisition_id"):
            counts["missing_requisition_id"] += 1
            # The id is often the last path segment the adapter did not read.
            tail = (card.get("canonical_url") or "").rstrip("/").rsplit("/", 1)[-1]
            if ID_LIKE.fullmatch(tail):
                counts["requisition_id_recoverable_from_url"] += 1
        if board_host and host_of(card.get("canonical_url")) != board_host:
            counts["url_off_registered_board_host"] += 1
        for field in ("required_skills", "preferred_skills", "responsibilities"):
            if field in card and not isinstance(card[field], list):
                counts[f"{field}_not_a_list"] += 1
    return {name: round(value / total, 4) for name, value in sorted(counts.items())} if total else {}


def mass_posting_clusters(cards: list[dict]) -> dict:
    """One description repeated across locations. A distribution pattern, never an accusation.

    It says how a listing is being spread, not that an employer is doing anything wrong -
    hiring the same role in several cities is ordinary. It matters here because a review
    queue that counts these as separate openings overstates the market it is showing.
    """
    groups = collections.defaultdict(set)
    for card in cards:
        if card.get("description_sha256"):
            groups[card["description_sha256"]].add(card.get("location"))
    spread = {key: locations for key, locations in groups.items() if len(locations) > 1}
    return {
        "clusters": len(spread),
        "cards_in_clusters": sum(len(locations) for locations in spread.values()),
        "widest_cluster_locations": max((len(v) for v in spread.values()), default=0),
    }


def profile_sources(registry: list[dict], cards: list[dict]) -> dict:
    """One profile per registered source, plus what the corpus holds that nobody registered."""
    by_source = collections.defaultdict(list)
    unregistered = collections.Counter()
    known = {source_key(s["company"], s["ats"]): s for s in registry}
    for card in cards:
        key = source_key(str(card.get("employer") or ""), card.get("ats"))
        if key in known:
            by_source[key].append(card)
        else:
            unregistered[key] += 1

    sources = {}
    for source in registry:
        key = source_key(source["company"], source["ats"])
        mine = by_source.get(key, [])
        sources[key] = {
            "company": source["company"], "ats": source["ats"],
            "enabled": bool(source.get("enabled")),
            "board_host": host_of(source.get("board_url")),
            "cards": len(mine),
            "signal_rates": signal_rates(mine, host_of(source.get("board_url"))),
            "mass_posting": mass_posting_clusters(mine),
        }
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "corpus_cards": len(cards),
        "registered_sources": len(registry),
        "sources": sources,
        # An employer string the registry does not know. Never silently folded into a
        # source: it is either a renamed company or a card from somewhere unregistered,
        # and both are things to look at rather than average away.
        "unregistered_employers": dict(unregistered.most_common()),
        "corpus_mass_posting": mass_posting_clusters(cards),
    }


def likely_rename(source: dict, unregistered: dict) -> str | None:
    """An unregistered employer on the same ATS whose name overlaps the registered one.

    Measured on the real registry, all three zero-yield boards were this: `Inizio (incl.
    Putnam Associates)` against `inizio`, `Evidation Health` against `evidation`, `Seer`
    against `seer inc.`. Every one of them was working. Reporting a name mismatch as a
    dead board is the false alarm that makes a health check worth ignoring.
    """
    words = set(re.findall(r"\w+", source["company"].casefold()))
    for key in unregistered:
        employer, _, ats = key.partition("::")
        if ats == source["ats"] and words & set(re.findall(r"\w+", employer)):
            return key
    return None


def compare(baseline: dict, current: dict) -> dict:
    """Drift is a change in a rate. A rate on its own is a shape, and shapes are not faults."""
    findings = {}
    for key, now in current["sources"].items():
        was = baseline["sources"].get(key)
        if was is None:
            findings[key] = {"verdict": "not_in_baseline", "reasons": ["source_not_in_baseline"]}
            continue
        if now["cards"] == 0:
            # Zero cards is three different states, and only one of them is a fault.
            rename = likely_rename(now, current["unregistered_employers"])
            if rename:
                findings[key] = {"verdict": "employer_name_mismatch", "cards": 0,
                                 "reasons": [f"cards_present_under_{rename}"]}
            elif was["cards"] > 0:
                # The one signal that needs no threshold: it worked before, and does not now.
                # No rate deltas alongside it - every rate over an empty source is zero by
                # construction, and printing those buries the one line that matters.
                findings[key] = {"verdict": "no_yield", "cards": 0,
                                 "reasons": [f"yield_fell_to_zero_from_{was['cards']}"]}
            else:
                findings[key] = {"verdict": "never_yielded", "cards": 0,
                                 "reasons": ["no_cards_in_baseline_either"]}
            continue
        if now["signal_rates"] == was["signal_rates"] and now["cards"] == was["cards"]:
            # Identical to the baseline. Saying "insufficient data" about a corpus that did
            # not move would make every rerun of the same pull look unresolved.
            findings[key] = {"verdict": "unchanged", "reasons": [], "cards": now["cards"]}
            continue
        if now["cards"] < MIN_CARDS_FOR_DRIFT:
            findings[key] = {"verdict": "insufficient_data", "cards": now["cards"],
                             "reasons": [f"only_{now['cards']}_cards"]}
            continue
        reasons = []
        for signal, rate in now["signal_rates"].items():
            before = was["signal_rates"].get(signal, 0.0)
            if rate - before >= DRIFT_DELTA:
                reasons.append(f"{signal}:{before:.2f}->{rate:.2f}")
        for signal, before in was["signal_rates"].items():
            if before - now["signal_rates"].get(signal, 0.0) >= DRIFT_DELTA:
                reasons.append(f"{signal}:{before:.2f}->{now['signal_rates'].get(signal, 0.0):.2f}")
        findings[key] = {"verdict": "suspect_parser_drift" if reasons else "healthy",
                         "reasons": sorted(reasons), "cards": now["cards"]}
    return {"findings": findings,
            "counts": dict(collections.Counter(f["verdict"] for f in findings.values()))}


def known_gaps(profile: dict) -> list[dict]:
    """Signals at 100% for a source: the adapter's shape, not a fault to raise every run."""
    gaps = []
    for key, source in profile["sources"].items():
        for signal, rate in source["signal_rates"].items():
            if rate == 1.0 and source["cards"] >= MIN_CARDS_FOR_DRIFT:
                gaps.append({"source": key, "signal": signal, "cards": source["cards"]})
    return gaps


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", type=Path, default=Path(".jobloom/ats-sources.json"))
    parser.add_argument("--corpus", type=Path, default=Path(".jobloom/jobs-wide"))
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("profile", help="measure the corpus; print the profile")
    record = sub.add_parser("baseline", help="record this profile as the last known good")
    record.add_argument("--output", required=True, type=Path)
    against = sub.add_parser("compare", help="this corpus against a recorded baseline")
    against.add_argument("--baseline", required=True, type=Path)
    args = parser.parse_args()

    registry = load_registry(args.registry)
    cards = load_corpus(args.corpus)
    profile = profile_sources(registry, cards)

    if args.mode == "baseline":
        write_private(args.output, json.dumps(profile, indent=2, ensure_ascii=False))
        print(f"baseline {args.output} (0600)   sources={len(profile['sources'])} "
              f"cards={profile['corpus_cards']}")
        return

    if args.mode == "compare":
        result = compare(json.loads(args.baseline.read_text(encoding="utf-8")), profile)
        print(json.dumps(result["counts"], indent=2))
        for key, finding in sorted(result["findings"].items()):
            if finding["verdict"] not in {"healthy", "unchanged"}:
                print(f"  {finding['verdict']:22} {key:44} {finding['reasons']}")
        return

    print(f"corpus {profile['corpus_cards']} cards over {profile['registered_sources']} "
          f"registered sources")
    mass = profile["corpus_mass_posting"]
    print(f"mass posting: {mass['clusters']} clusters, {mass['cards_in_clusters']} cards "
          f"({100 * mass['cards_in_clusters'] / max(profile['corpus_cards'], 1):.0f}%), "
          f"widest spans {mass['widest_cluster_locations']} locations")
    if profile["unregistered_employers"]:
        print(f"employers not in the registry: {sum(profile['unregistered_employers'].values())} "
              f"cards across {len(profile['unregistered_employers'])} names")
        for key, count in profile["unregistered_employers"].items():
            match = next((s["company"] for s in profile["sources"].values()
                          if s["cards"] == 0 and likely_rename(s, {key: count})), None)
            print(f"  {key:44} {count:4}"
                  + (f"   likely the registered `{match}`" if match else "   no registered match"))
    print("\nadapter shape (a signal at 1.00 is what this adapter always does):")
    for gap in known_gaps(profile):
        print(f"  {gap['source']:44} {gap['signal']:38} n={gap['cards']}")
    print("\nper source:")
    for key, source in sorted(profile["sources"].items()):
        notable = {k: v for k, v in source["signal_rates"].items() if 0 < v < 1.0}
        print(f"  {source['company'][:28]:28} {source['ats']:16} cards={source['cards']:4} "
              f"{notable if notable else ''}")


if __name__ == "__main__":
    main()
