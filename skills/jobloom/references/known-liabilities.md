# Known liabilities

Defects that are understood, measured, and deliberately not yet fixed. A liability stays
here until it is paid or disproved. The point of writing one down is that an unfixed
defect nobody recorded becomes a load-bearing assumption; the ones below are unfixed on
purpose, with the measurement that would settle each.

---

## `ranking_score` is anti-correlated with evidence

Recorded 2026-08-29. Not fixed. Owner decision: leave the engine alone, account for it.

**What was measured.** Over one real pull — 1,199 distinct openings from 17 registered ATS
boards, routed against the approved v4 portfolio and the real candidate profile — the
`ranking_score` returned by `direction_core.route_job` ran *against* the evidence:

| | n | mean `ranking_score` |
| --- | ---: | ---: |
| openings with a requirement the candidate's facts cover directly | 35 | 119.9 |
| openings with no directly covered requirement | 58 | **125.3** |

The effect is not an artefact of which key splits the set. Under the weaker split — whether
any requirement was extracted at all — it is 121.2 (n=51) against 125.8 (n=42), and the
eight highest-scoring openings carry no evidence under either key. The direct-evidence
split is the one reported because it is the key the queue actually sorts on.

The top eight by score carried no requirements. Ranks 1–3 were a Clinical Study Design
Manager and two Site/Field Operations roles; the Biostatistician and the SQL Health Data
Analysts sat below them.

**Why.** 68 of the 93 reviewed openings enter on `direction_context_without_title_match` —
the title does not match and the posting is admitted on direction-context density in its
prose. `ranking_score` then ranks on that same signal. Clinical *operations* prose is
saturated with `clinical / study / site / patient / trial` while asking for no analysis at
all, so the score rewards precisely the dimension on which the false positives are
strongest. Admission and ordering are reading the same evidence for different questions,
and it is only correct for the first.

**Why it is not fixed here.** `ranking_score` is written into `routing_records` and read by
the review pool and the rolling allocation logic. Changing it changes stored records and
the allocation tests, and nobody has measured what the score actually drives on that side.
Rewriting a contract whose downstream consumers are unmeasured is the same mistake as
committing to eight ATS adapters before measuring which failures were adapter gaps.

**Where it is currently harmless.** `scripts/review_queue.py` orders on evidence coverage
and demotes `ranking_score` to a late tiebreak, so the queue a person actually reads is not
affected. The exposure is entirely on the database side.

**What would settle it.** Measure what `ranking_score` decides in `routing_records`: how
`review_pool` orders on it, whether `rolling_allocation_targets` is sensitive to it, and
whether any stored decision would change if it were replaced by evidence coverage. If the
answer is "ordering only", the fix is small. If allocation depends on it, the fix needs a
migration plan for existing records.

**How it could bite.** Any new consumer that reads `ranking_score` as a quality signal
inherits the inversion silently — including a future ranked view built straight off the
database rather than off `review_queue.py`.

---

## A direction can be carried entirely by its weakest terms

Recorded 2026-08-29. A concrete instance was fixed; the general check does not exist.

`biostatistics-statistical-analytics-v2` admitted 8 openings, and 7 entered on
`positive_keywords` containing the bare tokens `"R"` and `"SAS"` — tool names that every
commercial-analytics posting mentions and that say nothing about statistical practice. Its
genuinely discriminating vocabulary fired **zero** times across all 1,199 openings:
`precision_keywords` and `discovery_keywords` never matched once, and `target_titles`
matched once. The multi-word phrases in them (`"longitudinal analysis"`,
`"survival analysis"`, `"mixed effects models"`) do not appear verbatim in real postings,
which say `"longitudinal"`; `_token_run` needs the whole sequence.

The ontology already has this rule for capabilities — *a skill never hit by a real resume is
a dead node*. Nothing applies it to direction terms. A calibration pass that reports, per
direction, which keyword groups never fire and which single term is carrying the direction
would have surfaced this without anyone reading routing output by hand.

**Trap worth recording:** `"SAP"` appears in 48 of the 1,199 openings and is almost always
the software company, not a statistical analysis plan. It is not usable as a term.

---

## A cross-city duplicate key does not exist, and must not be built

Recorded 2026-08-29. Measured, decided, closed. Evidence:
`.jobloom/dedup-cluster-evidence.json`.

**The problem it would have solved.** `description_sha256` catches a role posted verbatim
in forty cities, but not one posted in three cities with the location swapped into the
text. Over 1,199 fingerprint-deduplicated openings, 219 clusters share an employer and a
normalized title, covering 606 openings; merging them all would remove 387.

**Why no threshold works.** The proposal was a similarity gate on the description. Each
retreat was measured and each one failed:

1. *Employer + title alone.* Veeva's "Product Manager" in Dalian, Kiryat Ono and Lyndhurst
   are three different jobs at Jaccard 0.33. Merging on the bare key deletes two real
   openings.
2. *Gate on high similarity.* Two genuinely different Recursion roles — Senior
   Computational Biologist for Early Discovery **Oncology** and for Early Discovery
   **Immunology** — score **0.997** across 9,250-character descriptions.
3. *Add a length guard.* Those descriptions are 9,250 characters. Restricting to
   descriptions over 1,500 characters leaves the maximum at 1.00.
4. *The metric points the wrong way.* Genuine multi-city duplicates sit at 0.71–0.95. The
   false pairs sit at 0.99+. **Any threshold that merges a true duplicate merges a
   different job first.**

333 pairs share an employer, differ in title, and exceed 0.90 similarity. That band mixes
two kinds that no text metric separates: titles that should collapse ("Senior Consultant -
Commercial Operations" vs "Sr Consultant, Commercial Operations", 1.000) and titles that
must not ("Senior Director - CTMS Strategy" vs "Director - CTMS Strategy", 0.996). The
second kind is a seniority difference, and seniority is a hard gate in routing —
`seniority_outside_portfolio` was the third most common failure over this pull.

**Why a better metric does not rescue it.** The discriminating information is one word:
*oncology* against *immunology*, *Senior Director* against *Director*. No order-free text
metric can know which word is load-bearing without domain knowledge, and reaching for a
model to deduplicate would put the most expensive rung of the ladder under the cheapest
question in the system.

**What it was worth.** Four openings. The evidenced head of the review queue holds 35
openings; safe handling leaves 31, the unsafe bare key leaves 26. Four openings do not buy
the risk of silently deleting a real one.

**Decision.** No merge key. `application_core` already handles the same-city case the right
way — `normalized_identity` returns `review`, not a merge — and that mechanism extends to
the cross-city case unchanged. Group in presentation; never collapse identity.

**The rule this is an instance of.** *Being near the seed is not belonging to the
direction* and *sharing a title is not being the same job* are the same rule at two layers:
a surface feature matching is not the substance matching, so nothing may be silently
promoted or merged on it.

**Implementation guardrail.** A display group must state that it holds N independent
openings, and every member keeps its own `job_id`, `canonical_url` and `apply_url`. A group
that reads as "one job in N cities" would perform in the interface exactly the merge this
entry refuses — and the Recursion pair would be the first thing it hid.

**Number this corrects.** "Openings worth applying to" has been reported three times on
three different keys: ≈30 (estimated), 27 (bare employer+title, now known unsafe), and
**31** (safe handling). Only the last was computed with a key that survived measurement.
