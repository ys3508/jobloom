#!/usr/bin/env python3
"""The sheet a person fills in before choosing which jobs to apply to.

The queue can say which of a posting's must-have requirements it could evaluate. It cannot
say what the unevaluated ones mean, and in the 2026-09-10 queue that is most of them: not one
of 112 postings is fully assessed, and the "no known gap" lane reads two lines out of twelve.
So the step between a triage list and an application is a person reading the requirements
nothing could parse, and writing down what they decided.

Two things this is for, and the second is the reason it is a script rather than a one-off
file. It is the pre-application check — nobody applies on a lane label. And the dispositions
accumulate into a hand-labelled set of real requirement lines, which is what a better
distiller has to be measured against; a worksheet regenerated from scratch each time would
throw that away.

**A hard filter is recorded, not applied silently.** A posting the employer's own words
exclude gets a decision, the exact sentence it rests on, the candidate fact it contradicts,
and the condition under which it must be looked at again. Two rules govern it:

- *Per posting.* An employer stating a sponsorship policy on one posting has said nothing
  about another. Only the postings carrying the statement are excluded, never their siblings
  at the same company.
- *Not permanent.* Work-authorization facts carry an expiry, and a posting's text can change.
  Every exclusion names what would reopen it.

A blocked posting is not reviewed line by line. Its requirements are not the reason it is out,
and reading them would be work with nothing at the end of it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import requirement_tiers  # noqa: E402
import review_queue  # noqa: E402

SURVIVOR = "awaiting_disposition"
BLOCKED = "hard_reject"

# The dispositions a person may record against a requirement nothing could parse. Fixed, so
# the set that accumulates is countable rather than free text.
DISPOSITIONS = ("meets", "partially_meets", "does_not_meet", "not_a_requirement",
                "unclear_ask_employer")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def hard_reject(reason: str, candidate_fact: str, employer_evidence: str,
                recheck_trigger: str) -> dict[str, Any]:
    """One exclusion, with everything needed to disagree with it or revisit it."""
    for name, value in (("reason", reason), ("candidate_fact", candidate_fact),
                        ("employer_evidence", employer_evidence),
                        ("recheck_trigger", recheck_trigger)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"a hard reject requires {name}")
    return {"decision": BLOCKED, "reason": reason, "candidate_fact": candidate_fact,
            "employer_evidence": employer_evidence, "recheck_trigger": recheck_trigger}


def build(queue: dict[str, Any], cards: dict[str, dict[str, Any]],
          rejects: dict[str, dict[str, Any]], *, lanes: tuple[str, ...] | None = None,
          per_lane: int = 10, at: datetime | None = None) -> dict[str, Any]:
    """Survivors first with their unread requirements, then the blocked group's audit record.

    `rejects` is keyed by `job_id`, so an exclusion applies to the posting it was recorded
    against and to nothing else. A sibling posting at the same employer is a separate row and
    stays in the pool unless it carries its own evidence.
    """
    lanes = lanes or (review_queue.LANE_CLEAR, review_queue.LANE_GAPPED)
    pool = [row for row in queue["rows"]
            if row.get("lane") in lanes and row.get("lane_rank", 0) <= per_lane]

    survivors, blocked = [], []
    for row in pool:
        must = (row.get("tiers") or {}).get(requirement_tiers.MUST_HAVE) or {}
        entry = {
            "job_id": row["job_id"], "employer": row["employer"], "title": row["title"],
            "location": row["location"], "lane": row["lane"], "lane_rank": row["lane_rank"],
            "canonical_url": row["canonical_url"], "apply_url": row.get("apply_url"),
            "assessment": must.get("assessment"),
            "parsed_lines": must.get("parsed_lines", 0),
            "stated_lines": must.get("stated_lines", 0),
            "covered_terms": must.get("direct_terms") or [],
            "adjacent_terms": must.get("adjacent_terms") or [],
            "gap_terms": must.get("gap_terms") or [],
        }
        if row["job_id"] in rejects:
            # No requirement review: the requirements are not why it is out, and reading them
            # would be work with nothing at the end of it.
            blocked.append({**entry, **rejects[row["job_id"]]})
            continue
        entry["decision"] = SURVIVOR
        entry["unread_requirements"] = [
            {"requirement": line, "disposition": None, "note": None}
            for line in (must.get("unrecognised_requirements") or [])]
        survivors.append(entry)

    return {
        "schema_version": "0.1.0",
        "built_at": (at or now_utc()).isoformat(),
        "queue_openings": queue.get("openings_in_queue"),
        "dispositions_allowed": list(DISPOSITIONS),
        "counts": {"survivors": len(survivors), "blocked": len(blocked),
                   "unread_requirements": sum(len(entry["unread_requirements"])
                                              for entry in survivors)},
        "survivors": survivors,
        "blocked": blocked,
    }


def render(sheet: dict[str, Any]) -> str:
    counts = sheet["counts"]
    lines = [
        "# Disposition worksheet", "",
        f"Built {sheet['built_at'][:19]}Z from {sheet['queue_openings']} routed openings.", "",
        f"**{counts['survivors']} postings to review, {counts['unread_requirements']} "
        f"requirements to decide.** Not one posting in this queue was fully assessed, so a "
        "lane label is not a reason to apply. Every requirement below is one nothing could "
        "parse: the queue has no opinion about it, and yours is what goes in the record.",
        "",
        f"{counts['blocked']} postings are excluded by a hard filter and are listed at the "
        "end with the evidence. They are not reviewed line by line — their requirements are "
        "not why they are out.", "",
        "Disposition is one of: " + ", ".join(f"`{name}`" for name in DISPOSITIONS) + ".", "",
    ]

    for entry in sheet["survivors"]:
        lines += [
            "", f"## {entry['employer']} — {entry['title']}", "",
            f"{entry['location']} · {entry['lane']} #{entry['lane_rank']} · "
            f"**{entry['assessment']}**, {entry['parsed_lines']} of "
            f"{entry['stated_lines']} must-have lines evaluated", "",
            f"- covered: {', '.join(entry['covered_terms']) or '—'}",
            f"- adjacent only: {', '.join(entry['adjacent_terms']) or '—'}",
            f"- known shortfall: {', '.join(entry['gap_terms']) or '—'}",
            f"- posting: {entry['canonical_url']}", "",
        ]
        if not entry["unread_requirements"]:
            lines += ["_Every stated must-have was evaluated._", ""]
            continue
        lines += [f"| # | requirement nothing could parse | disposition | note |",
                  "|---:|---|---|---|"]
        for index, item in enumerate(entry["unread_requirements"], 1):
            requirement = item["requirement"].replace("|", "\\|")
            lines.append(f"| {index} | {requirement} | "
                         f"{item['disposition'] or ''} | {item['note'] or ''} |")
        lines.append("")

    lines += ["", "---", "", f"# Excluded by a hard filter ({counts['blocked']})", "",
              "Recorded under the current candidate snapshot, per posting. An employer "
              "stating a policy on one posting has said nothing about another, so only the "
              "postings carrying the statement are here. Each names what would reopen it.",
              ""]
    for entry in sheet["blocked"]:
        lines += [
            "", f"## {entry['employer']} — {entry['title']}", "",
            f"{entry['location']} · {entry['lane']} #{entry['lane_rank']}", "",
            f"- **decision:** `{entry['decision']}`",
            f"- **reason:** `{entry['reason']}`",
            f"- **candidate fact:** {entry['candidate_fact']}",
            f"- **employer evidence:** “{entry['employer_evidence']}”",
            f"- **recheck when:** {entry['recheck_trigger']}",
            f"- posting: {entry['canonical_url']}", "",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--cards", required=True, type=Path)
    parser.add_argument("--rejects", type=Path,
                        help="JSON: job_id -> {reason, candidate_fact, employer_evidence, "
                             "recheck_trigger}")
    parser.add_argument("--per-lane", type=int, default=10)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()

    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    cards = {}
    for path in args.cards.glob("job-*.json"):
        card = json.loads(path.read_text(encoding="utf-8"))
        cards[card["job_id"]] = card
    raw = json.loads(args.rejects.read_text(encoding="utf-8")) if args.rejects else {}
    rejects = {job_id: hard_reject(**value) for job_id, value in raw.items()}

    sheet = build(queue, cards, rejects, per_lane=args.per_lane)
    if args.output:
        args.output.write_text(json.dumps(sheet, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(render(sheet), encoding="utf-8")
    print(json.dumps(sheet["counts"], indent=2))


if __name__ == "__main__":
    main()
