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

**Nothing here chooses.** A deterministic keyword scan may assign a display-only reading hint.
It never produces, suggests, defaults, or preselects a sponsorship verdict.

The distinction is the whole of it. `EMPLOYMENT_SENSE` below decides which of two senses of the
word "sponsor" a sentence reads as, and that decides *what order the page is in*. A scan over
"we do not provide sponsorship" that emitted a verdict would be the defect `350dd4f` removed one
layer down — a pattern allowed to conclude — and the reason the extractor stopped short of one.
No card is hidden by the scan, `sponsorship` stays `unknown` whatever it returns, and every card
offers all three choices. The page shows the employer's own sentence, and a person picks.

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
import re
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


# Vocabulary that only the employment sense of "sponsor" uses. In clinical research a sponsor
# is the organisation running a trial, and `ingest_job.SPONSORSHIP_SEGMENT_MARKERS` matches the
# bare word, so in this candidate's corpus most extracted segments are about trials. Measured
# over the 2026-09-10 queue: of the 28 cards carrying segments, 16 carry only employment
# sentences, 11 only trial ones, and 1 both.
#
# **This decides ordering and nothing else.** The upstream markers are deliberately left alone:
# they over-recall, which costs reading time, and narrowing them would risk dropping a real
# visa sentence — a false negative here is a wasted evening on an application that was never
# possible. Every card stays in the list whatever this returns, `sponsorship` stays `unknown`,
# and no verdict is suggested.
EMPLOYMENT_SENSE = re.compile(
    r"\bvisa\b|\bh-?1b\b|\bo-?1\b|\btn\b|\bopt\b|\bcpt\b|green card|permanent resident"
    r"|work authori[sz]|employment authori[sz]|authori[sz]ed to work|right to work"
    r"|sponsorship for (?:employment|work)|(?:require|provide|offer|need)\w*\s+"
    r"(?:visa\s+)?sponsorship|sponsorship (?:is |will )?(?:not )?(?:be )?"
    r"(?:available|provided|offered)|immigration status", re.I)

# Display hints, most decisive first. The order is the reading order the page uses.
SIGNAL_HINTS = ("employment_or_visa_signal", "mixed_signal", "possible_trial_sponsor_only")
HINT_RANK = {hint: rank for rank, hint in enumerate(SIGNAL_HINTS)}


def statement_hint(statement: str) -> str:
    """Which sense of "sponsor" this one sentence reads as. A hint, never a verdict."""
    return ("employment_or_visa_signal" if EMPLOYMENT_SENSE.search(statement or "")
            else "possible_trial_sponsor_only")


def card_hint(statements: list[dict[str, Any]]) -> str:
    """A card's reading priority, from the senses its sentences carry.

    `mixed_signal` is its own tier rather than being folded into either neighbour: a card where
    one sentence is about visas and another about trial sponsors is the case most likely to be
    misread in a hurry, so it is not sorted down among the ones that look irrelevant.
    """
    hints = {statement["hint"] for statement in statements}
    if hints == {"employment_or_visa_signal"}:
        return "employment_or_visa_signal"
    if "employment_or_visa_signal" in hints:
        return "mixed_signal"
    return "possible_trial_sponsor_only"


def locate(description: str, statement: str) -> tuple[int, int] | None:
    """Where this sentence sits in the posting, or nothing if it cannot be found.

    `ingest_job` stores the segments as text with no offsets, and a segment is capped at 500
    characters, so the first 120 are used to find it and the recorded span covers the stored
    text. A statement that cannot be located keeps its own block and says so rather than being
    silently given a position.
    """
    if not description or not statement:
        return None
    index = description.find(statement[:120])
    if index < 0:
        return None
    return index, index + len(statement)


def statement_context(description: str, statement: str) -> dict[str, Any]:
    """The sentence as the employer wrote it, plus enough either side to place it.

    A sponsorship sentence reads differently under a "Visa Sponsorship" heading than it does in
    the middle of a paragraph about relocation, and the segment alone cannot show which it was.
    """
    span = locate(description, statement)
    if span is None:
        return {"before": "", "after": "", "located": False, "span": None}
    start, end = span
    window_start = max(0, start - CONTEXT_WINDOW)
    window_end = min(len(description), end + CONTEXT_WINDOW)
    return {
        "before": ("…" if window_start else "") + description[window_start:start].strip(),
        "after": description[end:window_end].strip() + ("…" if window_end < len(description) else ""),
        "located": True,
        "span": [start, end],
    }


def display_blocks(description: str, statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Statements whose displayed context would overlap, shown once instead of twice.

    Two adjacent sentences each rendered with 220 characters either side means the second one's
    "before" is the first one — the reader sees the same paragraph twice and has to work out
    that it is the same paragraph. So they are merged when their context windows would overlap,
    which is exactly the condition under which the duplication happens.

    **A block is a way of showing sentences, not a sentence.** Its text is the contiguous run of
    the posting that spans them, never a concatenation of the segments, and it carries the
    hashes of every statement inside it. Nothing binds to a block: a verdict binds to the
    statements and the JobCard, so a block gaining or losing a member changes what is displayed
    and not what was decided.
    """
    located = sorted((s for s in statements if s["located"]), key=lambda s: s["span"][0])
    blocks: list[dict[str, Any]] = []
    for statement in located:
        start, end = statement["span"]
        if blocks and start - blocks[-1]["span"][1] < CONTEXT_WINDOW:
            blocks[-1]["span"][1] = max(blocks[-1]["span"][1], end)
            blocks[-1]["statement_sha256s"].append(statement["statement_sha256"])
            continue
        blocks.append({"span": [start, end],
                       "statement_sha256s": [statement["statement_sha256"]]})
    for block in blocks:
        start, end = block["span"]
        window_start = max(0, start - CONTEXT_WINDOW)
        window_end = min(len(description), end + CONTEXT_WINDOW)
        block["text"] = description[start:end]
        block["before"] = ("…" if window_start else "") + description[window_start:start].strip()
        block["after"] = (description[end:window_end].strip()
                          + ("…" if window_end < len(description) else ""))
        block["merged"] = len(block["statement_sha256s"]) > 1
    for statement in statements:
        if not statement["located"]:
            # Kept, and kept visibly separate: a sentence the extractor stored but that cannot
            # be found in the description is a thing to look at, not a thing to drop.
            blocks.append({"span": None, "text": statement["text"], "before": "", "after": "",
                           "statement_sha256s": [statement["statement_sha256"]],
                           "merged": False, "located": False})
    return blocks


def needs_triage(card: dict[str, Any]) -> bool:
    """Unknown, and the posting said something about it. Neither half alone is worth a card."""
    return card.get("sponsorship") == "unknown" and bool(card.get("sponsorship_statements"))


def build(private_root: Path, limit: int | None = None,
          queue_path: Path | None = None) -> dict[str, Any]:
    """Every queued opening whose sponsorship a person could settle by reading one sentence.

    Returned in two views over the same cards, because reviewing and deciding are different
    sizes. `groups` is one entry per distinct sentence — over the 2026-09-10 queue, 52
    occurrences collapse to 21 sentences and three of them cover 26 of the occurrences — so a
    person reads a sentence once. `items` is still every card, because a decision is per
    opening: a group is a way to read, never a way to decide for eleven jobs at once.
    """
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
                "hint": statement_hint(statement),
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
            # What the card says today, carried as-is and unchanged by any hint below. The page
            # shows it so a person can see they are resolving an absence, not overriding a value.
            "sponsorship": card.get("sponsorship"),
            "statements": statements,
            # How this card reads, for ordering only. It is not written anywhere, does not
            # reach `evaluate_job` or a routing record, and does not become part of the card.
            "hint": card_hint(statements),
            # Merged only for display; every statement above keeps its own text, span and hash.
            "blocks": display_blocks(description, statements),
            # The two bindings a verdict will carry. Returned now, unused now, so the shape
            # being judged is the shape the writing version will persist.
            "job_card_sha256": resume_core.canonical_hash(card),
            "description_sha256": card.get("description_sha256"),
            "verdicts": list(VERDICTS),
        })

    items.sort(key=lambda item: (HINT_RANK[item["hint"]], item["employer"] or "",
                                 item["title"] or ""))
    if limit:
        items = items[:limit]
    return {
        "queue_file": queue_file.name,
        "queued": len(rows),
        "cards_missing": missing_cards,
        "needing_triage": len(items),
        "shown": len(items),
        "hint_counts": {hint: sum(1 for item in items if item["hint"] == hint)
                        for hint in SIGNAL_HINTS},
        "items": items,
        "groups": group_by_statement(items),
        "writes": False,
    }


def group_by_statement(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per distinct sentence, and every opening it appears in.

    Grouped on the hash of the exact text, so two sentences that differ by a word are two
    groups. Nothing looser: grouping by similarity, or by employer, is how "Beghou said no
    once" becomes "Beghou never sponsors" — a company-level rule nobody wrote, applied to
    openings nobody read.

    What a group supports is one *interpretation*: what this sentence, on its own, says. The
    decision is still per opening, which is why each member carries its own `job_id`,
    `job_card_sha256` and context, and why nothing here is selected by default.
    """
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        for statement in item["statements"]:
            digest = statement["statement_sha256"]
            group = groups.setdefault(digest, {
                "statement_sha256": digest,
                "text": statement["text"],
                "hint": statement["hint"],
                "verdicts": list(VERDICTS),
                "members": [],
            })
            group["members"].append({
                "job_id": item["job_id"],
                "employer": item["employer"],
                "title": item["title"],
                "location": item["location"],
                "canonical_url": item["canonical_url"],
                # Per opening, because the same sentence can sit under a different heading in
                # another posting and that is exactly what a reader needs to see before
                # agreeing the interpretation carries.
                "before": statement["before"],
                "after": statement["after"],
                "located": statement["located"],
                "job_card_sha256": item["job_card_sha256"],
                # Never pre-ticked. Applying an interpretation to an opening is an act, and the
                # page cannot perform it on the user's behalf by defaulting it to on.
                "selected": False,
            })
    ordered = sorted(groups.values(),
                     key=lambda group: (HINT_RANK[group["hint"]], -len(group["members"]),
                                        group["statement_sha256"]))
    for group in ordered:
        group["members"].sort(key=lambda member: (member["employer"] or "",
                                                  member["title"] or ""))
        group["occurrences"] = len(group["members"])
    return ordered


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
