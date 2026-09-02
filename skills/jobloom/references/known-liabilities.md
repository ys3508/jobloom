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

---

## Nothing stops a non-PDF resume from being submitted

Recorded 2026-08-31. **Paid 2026-08-31.** Kept here with its closure evidence because the
measurement below is what the gate is answerable to.

**What was measured.** `resume_core.register` accepts `.pdf`, `.docx`, `.txt` and `.md`
(`resume_core.py:390`). Neither `bind_version` nor `lock_materials` looks at the format:
both check approval, authorization and file hash and nothing else. `fill_core._plan_upload`
then hands the material lock's `snapshot_path` to the browser worker exactly as registered.
The only thing refused on its way to a submission is a `master_source`, and that is refused
by kind, not by suffix — so an approved `direction` DOCX walks the whole path.

Today's registry is one approval away from exercising it:

| version | kind | status | format |
| --- | --- | --- | --- |
| `lsc-baseline-2026-08-25-v1` | direction | **draft** | docx |
| `resume-a-v13-pdf--*` (×3) | direction | approved | pdf |
| `master-canonical-2026-08-25-v2` | master_source | approved | docx |

Nothing is exposed at this moment, but not because a gate exists: the three approved DOCX
direction versions happen to be revoked, and the approved DOCX left is a `master_source`
that kind already refuses. Approving `lsc-baseline` makes a DOCX submittable that day.

**Why it is not fixed here.** The fix is small — refuse at `bind_version`, again at
`lock_materials`, and again in `fill_core._plan_upload`, on the version's kind, its suffix
and its leading bytes, since a renamed DOCX passes the first two. It was written and it
works. It also fails 65 tests across nine test files whose fixtures bind a `resume.txt`,
and one of those files, `tests/test_direction_core.py`, is open in a concurrent session.
Updating those fixtures is mechanical, but not while someone else is editing one of them,
and shipping only the `fill_core` third of the gate would be worse than none: it would let
a DOCX bind and lock, then refuse at upload, after the material lock recorded it.

**How it was closed.** `_common.require_application_material_format` is the single
validator; there is deliberately no second copy. It checks the `.pdf` suffix and the leading
`%PDF-` bytes, because a renamed DOCX passes the suffix alone. It is called at
`resume_core.bind_version`, at `resume_core.lock_materials` for both the resume and the bound
cover letter, at `cover_letter_core.bind_version`, and at `fill_core._plan_upload` — all four,
because shipping only the `fill_core` third would have let a DOCX bind and lock and then be
refused at upload, after the material lock had already recorded it.

`tests/test_material_format.py` holds the closure evidence: a valid PDF binds, locks and plans
an upload; a `.pdf` holding ZIP bytes and a `.docx` holding PDF bytes are both refused; no
material lock row exists after a refused bind; an approved DOCX `master_source` stays
registrable and is refused at selection; a bound cover letter must be a PDF; the lock and
upload gates are exercised independently of binding; and replacing an approved PDF with a
different valid PDF still fails on the hash, proving the format check was added to hash
validation rather than substituted for it.

**What is not closed.** Registration formats are unchanged on purpose — `.docx`, `.txt` and
`.md` still register, and the DOCX `master_source` is still the canonical career record. The
gate is on selection, not on the registry. `artifact_integrity_audit`'s
`AUDIT_ASSUMPTIONS["format_gate_absent_in_bind_and_lock"]` records this hole from the other
side and its canary should now be re-pointed; that is not done here.

**What it would have cost.** An employer receiving a DOCX where the ATS expected a PDF, or a
locked artifact whose text layer was never checked because `artifact_integrity_audit` only
knows how to read PDFs. Nothing was exposed at the time of recording, but only because the
three approved DOCX direction versions happened to be revoked.

---

## The fill pipeline has no browser worker, and reads as if it does

Recorded 2026-08-31. Not fixed; the fork below was decided the same day. Not a defect in the
fill engine — the missing half was never built, and every document describing the engine
describes it as if it were.

**What was measured.** `action_package` appears in exactly one place that writes it,
`fill_core.py`'s `private_action_package_written` event, and in `mvp_core.py`'s
`action-packages` directory name. **Nothing reads it.** The shipped extension is five
files on every branch — `background.js`, `manifest.json`, `panel.css`, `panel.html`,
`panel.js` — and `panel.js` makes three outbound calls in total: `/health`,
`/positioning`, `/save`. There is no code anywhere in this repository that locates an
ATS field, types a value, uploads a file, or returns an observed hash to `fill_core`.

The backend halves that do exist and are tested: worker leases, page observation, the
pending-action package, per-field hash verification, checkpoints, pauses, the form
inventory, the value-free pre-submit review, submission evidence, and the archive. In
production they have run **zero** times: `fill_sessions`, `fill_pages`, `fill_steps`,
`fill_checkpoints`, `form_inventories`, `pre_submit_reviews`, `application_fields` and
`submission_archives` are all empty, and the one application has sat in `ready_to_fill`
since 2026-08-28.

**Why it reads as complete.** `mvp_core readiness` returns `fill_queue: ready` with no
blockers, because that gate asks whether an approved material-locked application is in
the queue — which is true, and says nothing about whether anything can act on it.
`SKILL.md`'s Fill-Only section is written in the imperative throughout ("Observe one page
at a time", "After each browser action, compare…"), addressed to a worker that does not
exist. README:45 is the only place that says otherwise, in one clause, in a paragraph
about deployment: operational use "still requires … browser integration".

Two readers have now concluded from the code and the readiness report that the pipeline
was runnable end to end. That is the cost of leaving it unrecorded.

**Why it is not fixed here.** Building the worker is a product decision, not a cleanup.
The owner's standing decision (2026-08-31) is to apply by hand: the panel judges, the
user fills the employer's form themselves, and `saved_jobs` records the decision and
what came back. Under that mode the worker is not on the critical path, and neither is
the contact-fact migration nor the AnswerLibrary question mappings that a worker would
need. Building it anyway would be building ahead of the measurement.

**What would settle it.** Either a minimal browser worker that consumes one action
package, fills one page, and returns observed hashes — at which point the backend halves
get their first real exercise and the format gate, the contact facts and the question
mappings all become due at once — or a decision to keep applying by hand permanently,
which would make the whole Fill-Only engine dead weight worth deleting rather than
maintaining.

**Which branch was taken.** The first, on 2026-08-31:
`docs/adr-fill-only-browser-worker.md` accepts a staged worker built against a local semantic
replay, with the adapter order set by the measured queue — Lever 71/105, Greenhouse 19/105,
Ashby 14/105, SmartRecruiters 1/105, Workday 0/105. The ADR supersedes the standing
apply-by-hand decision and nothing else.

**Why this entry stays open.** Accepting a decision is not building the thing. Nothing reads
an action package today, and a passing semantic replay is evidence that a field combination
came from a real recording — not that current Lever DOM can be filled. The entry narrows at
rollout stage 7 to "local replay complete, production adapters unimplemented and named". It
closes only after a supervised live acceptance test passes for at least one adapter, recorded
with which posting, who was present, what the scoped submit guard observed, and the archive
proving what was physically prepared. Until then no document may call this pipeline runnable
end to end.

**Where it stands after rollout stage 8.** Narrowed again, and still open. An action package
is now read: the panel can ask the bridge to run one page, and the bridge runs it through
`fill_worker` in a separate headed guarded window — the **extension-controlled separate
guarded worker** mode. The extension does not fill anything itself and does not touch the
user's tab. What has not changed is the part this entry is about: **production ATS adapters
remain unimplemented**, and every run so far has been against the local semantic replay. A
green panel is not evidence about Lever, Greenhouse or Ashby. The closing condition is
unchanged — a supervised live acceptance for at least one adapter, recorded.

**How it could bite.** It already has, twice, as a wrong answer to "what is finished".
The operational risk is smaller than the reporting risk: nothing can submit anything, so
nothing unsafe happens. What breaks is planning — work sequenced behind a stage that
does not exist. The ADR adds a third way for it to bite: reading a green semantic replay as
though it were a live adapter.
