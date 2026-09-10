#!/usr/bin/env python3
"""Order the review queue by the evidence a posting's requirements actually have.

`route_job` answers whether a posting survives a direction's hard gates. It does not
answer which surviving posting to read first, and its `ranking_score` is the wrong key
for that: measured over one real pull, postings with no extracted requirements scored
*higher* on average (125.8, n=42) than postings that had them (121.2, n=51), and the top
eight were all requirement-free. The reason is structural — most of these postings enter
on `direction_context_without_title_match`, and the score rewards direction-context
density, which clinical *operations* prose is saturated with while asking for no analysis
at all. Ranking on it promotes exactly the postings the queue should bury.

So the queue is ordered on evidence instead: how many of a posting's stated requirements
the candidate has a confirmed fact for, at what strength. That number is already computed
by `route_job` as `required_skill_evidence`; nothing here re-derives it, and no model is
called. `ranking_score` stays on as a late tiebreak, where context density is harmless.

Ordering never changes a decision. Every posting here is `review` — the rules did not
exclude it — and every card is still `requirements_reviewed: false`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import application_core  # noqa: E402
import direction_core  # noqa: E402
import requirement_tiers  # noqa: E402

SCHEMA_VERSION = "0.1.0"
# Evidence the candidate can actually stand behind. `mention_only` is deliberately worth
# less than `direct` and more than nothing, and never adds up to `direct`: a transferable
# or mentioned requirement is not a met one.
DIRECT = "direct"
COVERED_STRENGTHS = ("direct", "mention_only")


def stated_requirement_count(card: dict[str, Any]) -> int:
    """Requirement lines the posting wrote, recognised or not.

    `required_skill_evidence` is derived from the card's routable `required_skills`, which
    holds only controlled terms the distiller recognised. A posting stating its
    requirements as capabilities rather than tool names contributes nothing there while
    still stating requirements, so the lines themselves are counted from the extraction
    report where they were preserved.
    """
    sections = (card.get("extraction") or {}).get("sections") or {}
    return sum(len(sections.get(key) or [])
               for key in ("required_skills_stated", "preferred_skills_stated"))


def evidence_summary(routing: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    entries = routing.get("required_skill_evidence") or []
    direct = [item for item in entries
              if item.get("covered_by_candidate_fact") and item.get("strength") == DIRECT]
    covered = [item for item in entries if item.get("covered_by_candidate_fact")]
    return {
        "recognized_requirements": len(entries),
        "stated_requirements": stated_requirement_count(card),
        "direct": len(direct),
        "covered": len(covered),
        "uncovered": len(entries) - len(covered),
        "direct_requirements": [str(item.get("requirement")) for item in direct],
        "technical_hits": int((routing.get("field_hit_totals") or {}).get("discovery_keywords", 0)),
    }


def tier_summary(card: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Must-have, preferred and unknown coverage, counted separately.

    The undifferentiated `stated_requirements` count above cannot tell "we need R" from "R a
    plus", so a posting listing twelve wishes reads as harder than one listing two musts.
    `requirement_tiers` reads the employer's own words for the weight; nothing here decides
    it, and a requirement whose weight the posting never stated stays in its own column
    rather than being folded into either of the others.
    """
    report = requirement_tiers.summarize(card.get("description", "") or "",
                                         candidate.get("facts") or [])
    return report["tiers"]


LANE_CLEAR = "assessed_no_known_gap"
LANE_GAPPED = "assessed_with_known_gaps"
LANE_UNASSESSED = "unassessed_needs_manual_review"
LANES = (LANE_CLEAR, LANE_GAPPED, LANE_UNASSESSED)
LANE_ORDER = {name: index for index, name in enumerate(LANES)}


def lane(row: dict[str, Any]) -> str:
    """Which of three incomparable groups a posting belongs to.

    One ordered score across all postings was the defect. A posting whose requirements could
    not be parsed has zero known gaps, and zero known gaps sorted like "nothing is missing",
    so postings nobody had evaluated outranked postings that had been. `parsed 0/0` rows sat
    in the top twenty while a Biostatistician with four of five must-haves parsed sat at 76
    for having three gaps that were actually found.

    These are not points on one scale. "We checked and it fits", "we checked and it does not"
    and "we could not read it" answer different questions, so they are three lanes and the
    queue never compares across them.
    """
    must = (row.get("tiers") or {}).get(requirement_tiers.MUST_HAVE) or {}
    if must.get("assessment", requirement_tiers.UNASSESSED) == requirement_tiers.UNASSESSED:
        return LANE_UNASSESSED
    if must.get("unique_gaps", must.get("gaps", 0)) or must.get("unique_adjacent", 0):
        # Adjacent evidence is a known shortfall on a mandatory requirement, not a pass.
        return LANE_GAPPED
    return LANE_CLEAR


def sort_key(row: dict[str, Any]) -> tuple:
    """Lane first, then weight, then evidence — and never a comparison across lanes.

    Within a lane the order is deterministic and unchanged in spirit: direction weight, then
    unique must-have coverage, then how many known gaps, then the older evidence keys.
    Coverage is counted in **unique** requirements, because a posting naming one tool in
    three paragraphs is not three requirements met.

    `parsed / stated` decides the lane and is never a tiebreak inside one. A posting that
    parsed one line of fourteen is in the same lane as one that parsed all fourteen only if
    both are otherwise in the same state, and the row says which it is.
    """
    evidence = row["evidence"]
    tiers = row.get("tiers") or {}
    must = tiers.get(requirement_tiers.MUST_HAVE) or {}
    unique_gaps = must.get("unique_gaps", must.get("gaps", 0))
    return (
        LANE_ORDER[lane(row)],
        -row["weight_percent"],
        -must.get("unique_direct", 0),
        unique_gaps,
        -evidence["direct"],
        -evidence["covered"],
        -evidence["technical_hits"],
        -row["ranking_score"],
        row["employer"].casefold(),
        row["title"].casefold(),
    )


def annotate_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark openings that share an employer and a normalized title.

    A group is a reading aid, never a merge. Two Recursion postings differing only in
    "Oncology" against "Immunology" score 0.997 on their descriptions and land in the same
    group; they are two jobs with two application forms. Nothing here may collapse them,
    and the count travels with the group so the interface says "N independent openings"
    rather than implying one job listed N times. `references/known-liabilities.md` records
    why no similarity threshold can tell those two cases apart.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (application_core.normalize_text(row["employer"]),
               application_core.normalize_text(row["title"]))
        groups.setdefault(key, []).append(row)
    for index, (key, members) in enumerate(
            sorted(groups.items(), key=lambda item: min(row["rank"] for row in item[1])), 1):
        if len(members) < 2:
            continue
        group_id = f"group-{index}"
        for row in members:
            row["group"] = {
                "group_id": group_id,
                "independent_openings": len(members),
                "shares": "employer and title only",
                # Every sibling keeps its own identity and its own way in. A group that
                # offered one link would be the merge this queue refuses.
                "siblings": [{"job_id": other["job_id"], "location": other["location"],
                              "rank": other["rank"], "apply_url": other["apply_url"],
                              "canonical_url": other["canonical_url"]}
                             for other in members if other["job_id"] != row["job_id"]],
            }
    return rows


def load_cards(cards_dir: Path) -> list[dict[str, Any]]:
    """One card per opening. A role posted in forty cities is one opening, not forty."""
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(Path(cards_dir).glob("job-*.json")):
        card = json.loads(path.read_text(encoding="utf-8"))
        fingerprint = card.get("description_sha256")
        if fingerprint and fingerprint in seen:
            continue
        if fingerprint:
            seen.add(fingerprint)
        cards.append(card)
    return cards


def build_queue(cards: list[dict[str, Any]], candidate: dict[str, Any],
                allocations: list[dict[str, Any]], profiles: dict[str, dict[str, Any]],
                *, route: Any = None) -> dict[str, Any]:
    router = route or direction_core.route_job
    matches: dict[str, list[dict[str, Any]]] = {}
    by_id = {card["job_id"]: card for card in cards}
    hits_per_direction: dict[str, int] = {}
    for allocation in allocations:
        direction_id = allocation["direction_id"]
        profile = profiles[direction_id]
        hits = 0
        for card in cards:
            routing = router(profile, candidate, card)
            if routing.get("decision") != "review":
                continue
            hits += 1
            matches.setdefault(card["job_id"], []).append({
                "direction_id": direction_id,
                "weight_percent": allocation["weight_percent"],
                "ranking_score": routing.get("ranking_score", 0),
                "evidence": evidence_summary(routing, card),
                "review_reasons": list(routing.get("review_reasons") or []),
            })
        hits_per_direction[direction_id] = hits

    rows: list[dict[str, Any]] = []
    for job_id, found in matches.items():
        # A posting matching several directions belongs to the heaviest one; the others are
        # recorded, because a posting two directions want is worth knowing about.
        found.sort(key=lambda item: (-item["weight_percent"], -item["evidence"]["direct"]))
        primary, card = found[0], by_id[job_id]
        ats = card.get("extraction", {}).get("ats", {})
        rows.append({
            "job_id": job_id,
            "direction_id": primary["direction_id"],
            "weight_percent": primary["weight_percent"],
            "ranking_score": primary["ranking_score"],
            "evidence": primary["evidence"],
            # Once per posting, not once per direction that matched it: what the employer
            # wrote about a requirement's weight does not depend on which direction is reading.
            "tiers": tier_summary(card, candidate),
            # Assigned after the row is assembled, because it reads the tier summary above.
            "lane": None,
            "review_reasons": primary["review_reasons"][:4],
            "also_matches": [item["direction_id"] for item in found[1:]],
            "employer": card["employer"],
            "title": card["title"],
            "location": card["location"],
            "country": card["country"],
            "work_arrangement": card["work_arrangement"],
            "employment_type": card["employment_type"],
            "salary": card["salary"],
            "required_skills": card["required_skills"],
            "canonical_url": card["canonical_url"],
            "apply_url": ats.get("apply_url"),
            "posted_at": (ats.get("posted_at") or "")[:10],
        })
    for row in rows:
        row["lane"] = lane(row)
    rows.sort(key=sort_key)
    for index, row in enumerate(rows, 1):
        row["rank"] = index
    # Rank within the lane as well as overall: a reader comparing two postings needs to know
    # they are in the same lane before the numbers mean anything.
    seen: dict[str, int] = {}
    for row in rows:
        seen[row["lane"]] = seen.get(row["lane"], 0) + 1
        row["lane_rank"] = seen[row["lane"]]
    # After ranking, never before: grouping annotates the order, it does not change it.
    annotate_groups(rows)
    grouped = sum(1 for row in rows if row.get("group"))
    evidenced = sum(1 for row in rows if row["evidence"]["direct"])

    return {
        "schema_version": SCHEMA_VERSION,
        "openings_routed": len(cards),
        "openings_in_queue": len(rows),
        # Reported separately because they are different numbers: a posting matching two
        # directions is one opening but two hits, and summing the hits overstates the queue.
        "direction_hits": hits_per_direction,
        "with_direct_evidence": evidenced,
        # Openings sharing an employer and title with another. Reported, never collapsed.
        "in_title_groups": grouped,
        # Not "no requirements" — these state plenty. Nothing computed here distinguishes a
        # posting the distiller could not recognise from one that wants a different job, so
        # the tail is reported as one unordered group rather than given a false ordering.
        "without_direct_evidence": len(rows) - evidenced,
        "rows": rows,
    }


LANE_HEADINGS = {
    LANE_CLEAR: ("Assessed — no known must-have gap",
                 "Every must-have line these postings state was read, and the confirmed facts "
                 "cover what was found. Only `direct` evidence counts here."),
    LANE_GAPPED: ("Assessed — a must-have is known to be missing",
                  "These were read and something mandatory is not covered, or is covered only "
                  "by adjacent evidence. A known gap is a real finding, which is why they are "
                  "below the lane above and above the one below."),
    LANE_UNASSESSED: ("Not assessed — the requirements could not be read",
                      "No must-have requirement in these postings reached a deterministic "
                      "evidence outcome. **They have no known gaps because nothing was "
                      "checked, not because nothing is missing.** They need reading by hand."),
}


def render(queue: dict[str, Any], labels: dict[str, str] | None = None) -> str:
    labels = labels or {}

    def label(direction_id: str) -> str:
        return labels.get(direction_id, direction_id)

    counts = {name: sum(1 for row in queue["rows"] if row.get("lane") == name)
              for name in LANES}
    lines = ["# Review queue", "",
             f"{queue['openings_in_queue']} openings out of {queue['openings_routed']} routed.",
             "",
             "**Three lanes, and the queue never compares across them.** "
             "\"We read it and it fits\", \"we read it and it does not\" and \"we could not "
             "read it\" answer different questions. Ordering them on one scale let postings "
             "nobody had evaluated outrank postings that had been, because unparsed "
             "requirements produce zero known gaps and zero known gaps sorted like nothing "
             "was missing.", "",
             f"- {counts[LANE_CLEAR]} assessed with no known must-have gap",
             f"- {counts[LANE_GAPPED]} assessed with a known must-have gap",
             f"- {counts[LANE_UNASSESSED]} not assessed — requirements could not be read", "",
             "`parsed/stated` is how many of the posting's must-have lines reached a "
             "deterministic outcome. It decides the lane and is never a tiebreak inside one. "
             "`review` means the rules did not exclude the posting — never that it is a match.",
             "",
             f"{queue['in_title_groups']} openings share an employer and title with another "
             "and are marked below. They are **not** one job listed several times: two "
             "postings differing only in a single word score 0.997 on their text, so nothing "
             "here merges them. Each has its own application.", ""]

    for name in LANES:
        rows = [row for row in queue["rows"] if row.get("lane") == name]
        heading, explanation = LANE_HEADINGS[name]
        lines += ["", f"# {heading} ({len(rows)})", "", explanation, ""]
        if not rows:
            lines += ["_None._", ""]
            continue
        current = None
        for row in rows:
            if row["direction_id"] != current:
                current = row["direction_id"]
                lines += ["", f"## {label(current)} — weight {row['weight_percent']}%", "",
                          "| # | must parsed | covered | adjacent | gaps | employer | title "
                          "| location | covered terms | gap terms |",
                          "|---:|---:|---:|---:|---:|---|---|---|---|---|"]
            must = (row.get("tiers") or {}).get(requirement_tiers.MUST_HAVE) or {}
            title = (f"[{row['title']}]({row['canonical_url']})"
                     if str(row["canonical_url"]).startswith("http") else row["title"])
            group = row.get("group")
            mark = (f" <br>**{group['independent_openings']} independent openings** share this "
                    f"employer and title — same title is not the same job, and each has its own "
                    f"application: "
                    + ", ".join(f"#{s['rank']} {s['location']}" for s in group["siblings"])
                    if group else "")
            lines.append(
                f"| {row['lane_rank']} | {must.get('parsed_lines', 0)}"
                f"/{must.get('stated_lines', 0)} | {must.get('unique_direct', 0)} "
                f"| {must.get('unique_adjacent', 0)} | {must.get('unique_gaps', 0)} "
                f"| {row['employer']} | {title}{mark} | {row['location']} "
                f"| {', '.join(must.get('direct_terms') or []) or '—'} "
                f"| {', '.join(must.get('gap_terms') or []) or '—'} |")
        # The lines the distiller could not read, verbatim. A count of them looks small; the
        # sentences are what show that a posting was not actually evaluated.
        unread = [(row, (row.get("tiers") or {}).get(requirement_tiers.MUST_HAVE, {})
                   .get("unrecognised_requirements") or []) for row in rows]
        unread = [(row, items) for row, items in unread if items]
        if unread:
            lines += ["", f"### Must-have requirements not read ({len(unread)} postings)", ""]
            for row, items in unread:
                lines.append(f"- **#{row['lane_rank']} {row['employer']} — {row['title']}** "
                             f"({len(items)} unread)")
                for item in items:
                    lines.append(f"  - {item}")
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cards", required=True, type=Path, help="directory of pulled JobCards")
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--portfolio", required=True, type=Path)
    parser.add_argument("--directions", required=True, type=Path, nargs="+",
                        help="direction profile files named by direction_id")
    parser.add_argument("--output", type=Path, help="write the queue JSON here")
    parser.add_argument("--markdown", type=Path, help="write a readable queue here")
    args = parser.parse_args()

    profiles = {}
    for path in args.directions:
        profile = json.loads(path.read_text(encoding="utf-8"))
        profiles[profile["direction_id"]] = profile
    portfolio = json.loads(args.portfolio.read_text(encoding="utf-8"))
    allocations = sorted(portfolio["allocations"], key=lambda item: -item["weight_percent"])
    missing = [item["direction_id"] for item in allocations if item["direction_id"] not in profiles]
    if missing:
        raise ValueError(f"no profile supplied for: {', '.join(missing)}")
    queue = build_queue(load_cards(args.cards),
                        json.loads(args.candidate.read_text(encoding="utf-8")),
                        allocations, profiles)
    for target, payload in ((args.output, json.dumps(queue, indent=1, ensure_ascii=False) + "\n"),
                            (args.markdown, render(queue))):
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
            os.chmod(target, 0o600)
    print(json.dumps({key: value for key, value in queue.items() if key != "rows"},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
