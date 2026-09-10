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


---

# Second round — spot check failed, 2026-09-09

The owner's spot check of ranks 1, 2, 4, 8, 12, 30, 60 and 90 rejected the first queue. The
tier framework held; the ordering was amplifying three classification faults. All three are
fixed below, the ordering rule changed, and the queue was rebuilt as
`.jobloom/review-queue-20260909-v2.json`. **The first queue
(`review-queue-20260909.json`) is an experiment and must not be used to choose applications.**

## What was wrong

**One skill named repeatedly was paid for repeatedly.** Komodo Health's Infrastructure
Engineer showed `must_have.direct = 3` from a single covered term: GitHub, named in three
separate AI and CI paragraphs. That carried it to rank 1 past its own uncovered AWS, Docker,
Snowflake, Spark and dbt. Ordering now reads `unique_direct` — unique canonical
requirements — and line counts remain for describing the posting.

**A weight word inside a bracket downgraded the requirement in front of it.**
"3+ years in Life Sciences Consulting (Business or Management Consulting preferred)" was
read as `preferred`, discarding a mandatory three years. The scope of a bracketed cue cannot
be determined, so the line is `unknown`. A bracket that *opens* a line is different — it
labels the line — and still applies.

**Sub-headings inside a requirement list did not end or re-weight it.** The same Komodo
posting ran an AI-expectations sub-heading, an "Additional skills and experience we'll
prioritize…" transition, two salary sub-headings, an AI-policy section and a location section
all under one Required heading: 27 must-haves, most of them not requirements. A heading-shaped
line — one ending in a colon or an ellipsis — now ends the requirement list, unless it states
a weight, in which case it reopens at that weight.

**And one nobody had noticed.** 21 of the 112 postings write "What You’ll Do" and "Where
You’ll Work" with U+2019, and `posting_sections`' heading tables are written with the
straight apostrophe. Those headings matched nothing, so those postings' *responsibilities*
were being tiered as requirements. Apostrophes are normalised before every heading test.

## Ordering, as decided by the owner

1. direction weight
2. whether any must-have gap is known — none first
3. unique direct must-have coverage
4. number of known must-have gaps
5. the existing evidence keys

The asymmetry is deliberate: a known gap is a strong negative, while "no gap" is a weak
positive because most requirement text is not parsed. `parsed / stated` is displayed and is
**not** sorted on.

## Second hand audit — the top 20, not a random sample

Reviewing the new top 20 found two further faults, both in the top three, and both fixed:

- **Sponsorship statements were must-have requirements.** "We are currently unable to consider
  candidates who require sponsorship for work authorization" sat among the must-haves of the
  two highest-ranked postings. It is a requirement and not this kind: `field_policy` puts the
  sponsorship domain in `ALWAYS_MANUAL_DOMAINS` and routing already gates on it.
- **Bare pay ranges survived in other postings.** `$195,000—$225,000 USD` contains none of the
  words `FALLBACK_EXCLUSION` matches. The corpus-wide check found this; the 40-line hand
  sample never showed it, because in Komodo a salary sub-heading happened to end the section
  first.

Two filters were **measured and rejected**:

- **Requiring a `REQUIREMENT_CUE` word** would drop 21.6% of tiered lines, including
  `Python (FastAPI, Pydantic, Pandas)` and `REST APIs and other web service backend
  technologies` — precisely the parseable technical requirements. It would trade real
  requirements for boilerplate.
- **Using bullet markers** to tell a list from trailing prose: only 13 of 112 postings retain
  bullets; the ATS strips them.

## Result

- Komodo Infrastructure Engineer: **rank 1 → rank 83** (unique coverage 1, unique gaps 2).
- Komodo's must-have count: **27 → 8**, all eight real requirements.
- Tiered lines across the queue: 2,383 → 1,372.
- The top 20 contains no salary line, tracking tag, sponsorship statement or benefit line.

## Still wrong, and reported rather than fixed

**Company boilerplate is still tiered.** "At Beghou, you'll join a highly collaborative,
values-driven team where technical excellence…" is a must-have in the top three. The only
general filter available is the requirement-cue test measured above, which costs more than it
saves. A narrow rule for this sentence shape would fit one employer. Left, and named.

**Ordering now favours postings nobody could read.** 59 of 112 rows have `parsed 0/0` — no
coverage, no gaps, nothing distilled — and gap-first ordering places them above any posting
with a known gap. Unlearn.AI's Biostatistician, with 2 unique covered must-haves and 3 known
gaps, sits at **rank 76**, below 59 postings about which nothing is known. This follows
exactly from the rule as specified and from the distillation gap; it is a product decision,
not a defect, and it is the strongest argument for closing the distillation gap next.

## Regression tests

`tests/test_requirement_tiers_real_postings.py` runs against complete real descriptions from
the private corpus, pinned to `job-f93ad94ccd33` — the exact card that ranked 1, since several
Komodo postings share that title and the first match was a different variant. The postings are
**not committed**: `docs/implementation-plan-2026-08-31.md` forbids complete job descriptions
in git-tracked fixtures, so the tests skip when `.jobloom/jobs-wide-20260907` is absent, and
`tests/test_requirement_tiers.py` pins the same behaviours in structural fixtures that always
run.


---

# Third round — the classifier passed, the ordering did not, 2026-09-10

The owner accepted the tier classifier and rejected the v2 ordering. The diagnosis was not
another classification bug: **zero parsed requirements was being treated like zero known
gaps, so ignorance outranked evaluation.** In the v2 queue several top-20 rows were
`parsed 0/0`; the Associate Partner ranked 3 on `R`/`SAS` with 13 of 14 must-have lines
unread; and Unlearn.AI's Biostatistician, with 4 of 5 parsed and three gaps genuinely found,
sat at 76 for having found them.

No further heading regexes were added.

## Evaluation state

`requirement_tiers.summarize` now reports, per tier, which of three states the must-have list
reached. Unparsed is never read as covered, preferred, or gap.

| state | when |
| --- | --- |
| `unassessed` | no must-have requirement reached a deterministic evidence outcome |
| `partially_assessed` | at least one did, and at least one line remains unread |
| `fully_assessed` | every tiered must-have line reached an outcome |

The unread lines are kept **verbatim** as `unrecognised_requirements`, not just counted. A
count of 13 looks like a small number; the thirteen sentences show that the posting was not
evaluated.

## Three lanes

The queue no longer puts these on one scale. `review_queue.lane()` assigns:

1. `assessed_no_known_gap` — read, and the confirmed facts cover what was found
2. `assessed_with_known_gaps` — read, and something mandatory is missing or only adjacent
3. `unassessed_needs_manual_review` — nothing readable; needs a person

Adjacent-only evidence on a mandatory requirement counts as a known shortfall, not a pass.
Within a lane the order is unchanged: direction weight, unique must-have coverage, known gap
count, then the older evidence keys. `parsed / stated` decides the lane and is never a
tiebreak inside one. Each row carries `lane`, `lane_rank`, `parsed_lines`, `stated_lines`,
`unique_direct`, `unique_adjacent`, `unique_gaps`, `unrecognised_lines` and the unread lines
themselves; the markdown renders one section per lane and lists the unread requirements under
each.

## Result

| lane | postings |
| --- | ---: |
| assessed, no known must-have gap | 11 |
| assessed, with known must-have gaps | 42 |
| not assessed | 59 |

- **Unlearn.AI Biostatistician: overall #76 → #1 of the assessed-with-gaps lane.** Four of
  five must-haves parsed, two covered, three real gaps.
- The 59 unreadable postings no longer compete with either assessed lane.
- 17 of those 59 state no must-have line at all.

## The finding that matters most

**Not one posting of the 112 is `fully_assessed`.** Every row in the "no known gap" lane is
partially assessed: `2/12`, `1/14`, `1/8`, `1/5`. So lane 1 does not mean "this fits" — it
means *no gap was found in the one or two lines that could be read*, and the eight to
thirteen unread lines are printed underneath so nobody has to take the lane on faith. The
Associate Partner's unread lines include "Minimum of 10 years of managing and leading
analytics", which is very likely a hard gap.

That is the argument for the next project rather than for more rules here. 566 of 652
must-have lines never reach controlled evidence matching, and no amount of lane structure
changes what is known about them. Improving requirement distillation should be its own task,
measured against a hand-labelled set of real postings for coverage and misclassification
rate — not approached with more ad-hoc patterns.

## Queues

`.jobloom/review-queue-20260910-lanes.json` and `.md`. The 09-09 v1 and v2 queues remain
**experimental** and must not be used to choose applications.


---

# Fourth round — the lane heading contradicted its own table, 2026-09-10

The classifier and the lane mechanism were accepted. One reporting defect had to be fixed
before the queue could be used for anything: Lane 1's heading read

> Every must-have line these postings state was read

directly above rows showing `2/12`, `1/14`, `1/8`. **None of the 112 postings is fully
assessed.** The sentence was written when the lane was designed and was never checked against
what the lane actually contained — the same failure `references/known-liabilities.md` records
twice: a component reporting what it was meant to do rather than what it did.

## Lanes renamed to what is known

| lane | meaning |
| --- | --- |
| `partial_no_known_gap` | at least one must-have was evaluated; none of the evaluated ones produced a gap. Unevaluated requirements may still contain blockers. |
| `partial_with_known_shortfall` | at least one evaluated must-have is missing or supported only by adjacent evidence. Unevaluated requirements may contain more. |
| `no_requirement_assessment` | no must-have reached deterministic evidence evaluation. |

`partially_assessed` and `fully_assessed` remain explicit per-row states and are printed in
the table, so two rows in one lane are never presented as fully comparable when they read
different fractions of their posting.

## The prose is now derived from the rows

`lane_description()` computes its claim from the rows in the lane. A heading cannot disagree
with its table if the heading is calculated from it. It says "11 of 11 are only partially
assessed — 13 of 97 stated must-have lines reached an evidence outcome", and it says
"every row here is fully assessed" only when that is true of every row. A rendering test
asserts the forbidden phrases never appear.

The queue header now leads with **"0 of 112 postings are fully assessed"** and states plainly
that a lane says which question was answered, never that a posting fits — and that sponsorship,
seniority, location and required experience are separate hard filters not applied here.

## How the queue is to be used

As a manual triage list, not a ranking to apply from. **Both** of the first two lanes are
read: Unlearn.AI's Biostatistician is the proof, sitting in the shortfall lane precisely
because enough of it was evaluated for shortfalls to be found. A relevant role is not excluded
for having known gaps. Every unread must-have is read before an application is chosen, and
the reviewer's disposition for each is recorded.

The first five supervised applications are what produce the hand-labelled requirement set the
next distiller needs. They are not postponed until the distiller exists.
