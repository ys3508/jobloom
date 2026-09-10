# Requirement tiering — measurement and hand audit

Recorded 2026-09-09 against the 112-posting review queue pulled 2026-09-07. Tests passing is
not evidence that a classifier reads real postings correctly, so this is the sampling record:
what was measured before the rules were written, what a hand audit of real lines found, what
was fixed as a result, and what is still wrong and was deliberately left.

## What was measured before anything was built

2,241 requirement lines across the 112 postings, grouped by the heading they sat under and
whether the line itself stated a weight:

| | lines |
| --- | ---: |
| under a required-ish heading | 1,307 |
| under a preferred heading | 934 |
| **under a Required heading, but the line says preferred** | **77** |
| under a Required heading, and the line also says required | 49 |
| stating both weights in one line | 8 |
| under a Preferred heading but saying required | 1 (a boilerplate disclaimer) |

The asymmetry — 77 against 1 — is why the line beats the heading. Everything else in
`requirement_tiers` follows from this table.

Heading frequency over the same postings decided which headings count as explicit:
`requirements` 55, `nice to have` 41, `qualifications` 19, `about you` 10,
`knowledge, skills, and abilities` 10. The last three name a requirement list without
weighting it, and the previous behaviour classed all of them as required.

## Result on the real queue

| tier | lines | covered (direct) | adjacent | gap | unparsed |
| --- | ---: | ---: | ---: | ---: | ---: |
| `must_have` | 652 | 23 | 29 | 34 | 566 |
| `preferred` | 429 | 16 | 26 | 30 | 357 |
| `unknown` | 469 | 11 | 11 | 28 | 419 |

101 of 112 rows changed position; the largest move was 64 places.

## Hand audit

40 tiered lines sampled at random from the queue (seed 31), judged one at a time.

**First pass — 40 lines:**

- 22 real requirements, **0 clearly mis-tiered**
- **18 (45%) were not requirements at all**: PTO, retirement programs, wage ranges,
  "a conversation with the hiring manager", a third-party personality assessment.

The tiering was faithfully tiering things that were never requirements. Three headings were
responsible: `perks & benefits` (48 occurrences), `interviewing with` (49),
`what we offer` (13), `compensation & total rewards` (8). None of them ended a requirement
section, so every line beneath them inherited the tier above.

**Second pass, after adding those as section-ending headings — 40 fresh lines:**

- 35 real requirements, **1 mis-tiered**
- 5 (12.5%) not requirements

The one mis-tier: *"Interest in learning more about life science (prior knowledge is not
required)"* became `must_have`, because the cue matched the very word being negated.

## Fixed as a result of the audit

Each of these was found by reading real lines, not by a failing test, and each now has one:

1. **Negated cues.** `not required` / `not a requirement` no longer create a must-have.
2. **Benefit and process headings end a requirement list.** 2,383 tiered lines fell to
   1,550 — 833 lines that were never requirements. `preferred` fell hardest, 1,032 → 429,
   because the benefits lists were landing there.
3. **`bonus` alone was too loose.** It fired on "eligible for our Annual Performance Bonus
   Plan", a compensation sentence with no opinion about a skill. Narrowed to
   `bonus points` / `bonus if`.
4. **Headings written as sentences were read as lines.** `BONUS IF YOU HAVE` and
   `Desirable, but not required:` are headings; their lines were inheriting `qualifications`.
5. **Two requirements on one line.** `Bachelor's degree required; advanced degree a plus`
   was `unknown`, discarding a plainly stated must-have. Split on the semicolon only, and
   only when every clause carries exactly one kind of cue. 4 lines became 8 clauses; 3 lines
   remain genuinely ambiguous.
6. **Coverage counted terms, not lines.** "Experience with SAS" distils to both the tool
   `SAS` and the capability `cap.statistical-programming`, so one requirement was
   simultaneously covered and a gap. Counts are per requirement line now.

## Known wrong, and left alone

**The distiller reads a minority of requirement lines.** 566 of 652 must-have lines yield no
controlled term, so the coverage and gap columns describe about 13% of what the employers
actually wrote. The tiering is correct about which tier those 566 lines are in; nothing here
can say whether the candidate meets them. **This is the largest limitation of the feature and
the numbers above must not be read as if it were not there.** It is a `posting_sections`
distillation gap, not a tiering one, and closing it is its own task.

**Roughly one tiered line in eight is not a requirement.** What remains after the section-
ending fix: salary-location labels (`San Francisco Bay Area and New York City:`), company
boilerplate under a requirement heading, and stray headings read as lines (`OUR OPPORTUNITY`).
These come from `posting_sections.split_sections` accepting any non-heading line under a
recognised heading. Fixing it means changing what lands in `required_skills` for every card,
which changes routing for the whole corpus — a measured change of its own, not a side effect
of this one.

**Ordering rewards long postings.** The rule implemented is the one specified: must-have
coverage first, then must-have gaps. Komodo Health's Infrastructure Engineer sits at #1 with
3 covered must-haves *and 4 real must-have gaps*, above Beghou's Associate Consultant at 2
covered and 0 gaps. On the stated rule that is correct. Whether it is what a person wants at
the top of the queue is a product decision, and changing it — to gaps-first, or to a ratio —
is a decision for the owner rather than a fix.

**4 postings yield no tiered line at all** (2 × Beghou "Consultant, Data Analytics", Headway
"Revenue Operations Manager", Inizio "Global Benefits Director"). They state requirements in
prose without a recognised heading. `posting_sections.fallback_requirement_lines` exists for
that shape and is not wired into the tiering; doing so would need its own measurement, since
the fallback's own preferred/required split is cruder than this one.

## Reproducing

```bash
python3 skills/jobloom/scripts/requirement_tiers.py --card <card.json> --lines
```

The queue recomputation that produced the table above is in
`.jobloom/review-queue-20260909.json`.
