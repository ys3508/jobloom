#!/usr/bin/env python3
"""The openings whose sponsorship only a person can settle, with what the employer actually wrote.

`ingest_job.sponsorship_statements` extracts the sentences of a posting that mention
sponsorship and then deliberately leaves `sponsorship` at `unknown`: the segments become a
reviewable JobCard field, and routing never reads the description itself. That refusal is
correct and this module does not undo it. What it does is carry the consequence to a person —
because over the 2026-09-10 queue the consequence is total.

**Measured before it was written.** All 112 openings in that queue carry `sponsorship:
unknown`, and 28 of them carry verbatim sponsorship segments nobody has read. The candidate's
`work_authorization` has `sponsorship_future` true, so `evaluate_job` computes
`needs_employer_support` and every one of the 112 lands in `sponsorship_requires_review`. The
single highest-consequence dimension in the queue is unresolved across the whole queue, and
for 28 of them the answer is sitting in the card.

So the scope is those 28, not the 323 across the wide corpus and not the 2,455 postings behind
it. A triage page a person will actually finish is worth more than a report they will not.

**Nothing here chooses.** There is no keyword scan, no suggested verdict, no pre-selected
button. A regex over "we do not provide sponsorship" would be the defect `350dd4f` removed one
layer down — a pattern allowed to conclude — and the whole reason the extractor stopped short
of a verdict. The page shows the employer's own sentence and three choices, and a person picks.

Four rules the verdict obeys, stated here because the writing path in a later version has to
enforce them and a shape that forgets one is worse than no shape:

- `unclear` stays an uncertainty. It is not a weaker `supports`; it is the tier for a posting
  whose wording did not settle the question, and defaulting it either way is the upgrade
  nobody wrote.
- Only `does_not_support` reaches the hard filter. `evaluate_job` already turns that into
  `required_sponsorship_not_supported`; the other two change nothing but review priority.
- `supports` is evidence about *this posting*, never about the employer. Veeva saying it
  sponsors one role in Boston says nothing about the next Veeva opening, and a verdict that
  generalised would quietly answer openings nobody looked at.
- A verdict binds to `job_card_sha256` and to the hash of the exact sentence it was read from.
  A reposted card with edited wording is a different card, and the old verdict does not follow
  it — the same rule `direction_core` already applies to a routing record.

**This version writes nothing.** It reads the queue and the cards and returns a list. The
annotation and its persistence are the next version, once the shape has been judged against
real cards.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import resume_core  # noqa: E402

VERDICTS = ("supports", "does_not_support", "unclear")
# Characters of the description shown either side of a statement. Enough to see whether the
# sentence sits under "Visa Sponsorship" or inside a paragraph about something else, and
# bounded so the card stays a card rather than becoming the posting.
CONTEXT_WINDOW = 220


def newest_queue(private_root: Path) -> Path:
    """The most recent built review queue. Named back to the caller, never assumed silently.

    Which queue is being triaged is part of what a verdict means: a person marking 28 cards is
    marking them against one pull, and a page that quietly picked a different file would be
    showing openings that are no longer in the queue at all.
    """
    candidates = sorted(Path(private_root).glob("review-queue-*.json"),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise ValueError("no built review queue to triage")
    return candidates[0]


def card_index(private_root: Path, job_ids: set[str]) -> dict[str, Path]:
    """Where each queued opening's card is, across however many pulls it took to find them.

    Newest pull first: a job present in two pulls is read from the most recent, because that
    is the card the queue was built from and the one a verdict should bind to.
    """
    directories = sorted((path for path in Path(private_root).glob("jobs-wide*") if path.is_dir()),
                         key=lambda path: path.stat().st_mtime, reverse=True)
    found: dict[str, Path] = {}
    for directory in directories:
        for job_id in job_ids - set(found):
            path = directory / f"{job_id}.json"
            if path.is_file():
                found[job_id] = path
    return found


def statement_context(description: str, statement: str) -> dict[str, str]:
    """The sentence as the employer wrote it, plus enough either side to place it.

    A sponsorship sentence reads differently under a "Visa Sponsorship" heading than it does in
    the middle of a paragraph about relocation, and the segment alone cannot show which it was.
    """
    if not description or not statement:
        return {"before": "", "after": "", "located": False}
    index = description.find(statement[:120])
    if index < 0:
        return {"before": "", "after": "", "located": False}
    start = max(0, index - CONTEXT_WINDOW)
    end = min(len(description), index + len(statement) + CONTEXT_WINDOW)
    return {
        "before": ("…" if start else "") + description[start:index].strip(),
        "after": (description[index + len(statement):end]).strip() + ("…" if end < len(description) else ""),
        "located": True,
    }


def needs_triage(card: dict[str, Any]) -> bool:
    """Unknown, and the posting said something about it. Neither half alone is worth a card."""
    return card.get("sponsorship") == "unknown" and bool(card.get("sponsorship_statements"))


def build(private_root: Path, limit: int | None = None,
          queue_path: Path | None = None) -> dict[str, Any]:
    """Every queued opening whose sponsorship a person could settle by reading one sentence."""
    queue_file = Path(queue_path) if queue_path else newest_queue(private_root)
    queue = json.loads(queue_file.read_text(encoding="utf-8"))
    rows = queue.get("rows") or []
    index = card_index(private_root, {row["job_id"] for row in rows})

    items: list[dict[str, Any]] = []
    missing_cards = 0
    for row in rows:
        path = index.get(row["job_id"])
        if path is None:
            missing_cards += 1
            continue
        card = json.loads(path.read_text(encoding="utf-8"))
        if not needs_triage(card):
            continue
        description = card.get("description") or ""
        statements = []
        for statement in card["sponsorship_statements"]:
            statements.append({
                "text": statement,
                "statement_sha256": resume_core.canonical_hash(statement),
                **statement_context(description, statement),
            })
        items.append({
            "job_id": row["job_id"],
            "employer": card.get("employer") or row.get("employer"),
            "title": card.get("title") or row.get("title"),
            "location": card.get("location") or row.get("location"),
            "canonical_url": card.get("canonical_url") or row.get("canonical_url"),
            "lane": row.get("lane"),
            "rank": row.get("rank"),
            # What the card says today, carried as-is. The page shows it so a person can see
            # they are resolving an absence rather than overriding a value.
            "sponsorship": card.get("sponsorship"),
            "statements": statements,
            # The two bindings a verdict will carry. Returned now, unused now, so the shape
            # being judged is the shape the writing version will persist.
            "job_card_sha256": resume_core.canonical_hash(card),
            "description_sha256": card.get("description_sha256"),
            "verdicts": list(VERDICTS),
        })
    items.sort(key=lambda item: (item["employer"] or "", item["title"] or ""))
    return {
        "queue_file": queue_file.name,
        "queued": len(rows),
        "cards_missing": missing_cards,
        "needing_triage": len(items),
        "shown": len(items[:limit] if limit else items),
        "items": items[:limit] if limit else items,
        "writes": False,
    }


def posting(private_root: Path, job_id: str) -> dict[str, Any]:
    """One posting's own text, for the person who wants to read past the extracted sentence."""
    path = card_index(private_root, {job_id}).get(job_id)
    if path is None:
        raise ValueError("no card for that opening")
    card = json.loads(path.read_text(encoding="utf-8"))
    return {"job_id": job_id, "employer": card.get("employer"), "title": card.get("title"),
            "canonical_url": card.get("canonical_url"),
            "description": card.get("description") or ""}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", type=Path, default=Path(".jobloom"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--queue", type=Path)
    args = parser.parse_args()
    report = build(args.private_root, args.limit, args.queue)
    summary = {key: report[key] for key in
               ("queue_file", "queued", "cards_missing", "needing_triage", "shown", "writes")}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    for item in report["items"]:
        print(f"\n{item['employer']} — {item['title']}  ({item['location']})")
        for statement in item["statements"]:
            print(f"  · {statement['text'][:160]}")


if __name__ == "__main__":
    main()
