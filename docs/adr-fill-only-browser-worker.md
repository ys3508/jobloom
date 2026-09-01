# ADR: Build the Fill-Only browser worker, in stages, against a semantic replay first

Status: **Accepted.** Recorded 2026-08-31.

Supersedes exactly one earlier decision: the 2026-08-31 standing choice to keep applying by
hand, recorded in the `The fill pipeline has no browser worker` entry of
`skills/jobloom/references/known-liabilities.md`. It relaxes nothing else. Fill-Only remains
the only mode, no submission action may exist, and every existing freshness, evidence,
authorization, material-lock, pre-submit and archive gate stands unchanged.

**No real job site, employer form, logged-in session, or submission is authorized by this
ADR.** Nothing below permits touching a live ATS. The first live contact is a separate,
supervised acceptance test that this ADR defines but does not grant.

---

## Context

The liability entry recorded the state plainly: `fill_core` writes an action package that
nothing reads, the shipped extension makes three calls (`/health`, `/positioning`, `/save`),
and no code in this repository locates an ATS field, types a value, uploads a file, or
returns an observed hash. The backend halves — leases, page observation, per-field hash
verification, checkpoints, pauses, the form inventory, the value-free pre-submit review,
submission evidence, the archive — are built and tested, and in production have run zero
times. One application has sat in `ready_to_fill` since 2026-08-28.

The entry named the fork: build a minimal worker, or decide to apply by hand permanently and
delete the Fill-Only engine as dead weight. It also said the decision was a product judgment,
not a cleanup, and that building without measuring would be building ahead of the
measurement. This ADR takes the first branch, with the measurement done first.

## The measurement this decision rests on

The 2026-08-31 review queue was grouped by the host of each opening's `apply_url`:

| ATS | Openings | Share |
| --- | ---: | ---: |
| `jobs.lever.co` | 71 | 68% |
| `job-boards.greenhouse.io` | 19 | 18% |
| `jobs.ashbyhq.com` | 14 | 13% |
| `jobs.smartrecruiters.com` | 1 | 1% |
| Workday | 0 | 0% |
| LinkedIn, Indeed, Glassdoor, ZipRecruiter | 0 | 0% |

Denominator: 105, the `openings_in_queue` field of `.jobloom/review-queue-20260831.json`,
equal to `len(rows)`. Of those, 32 carry direct evidence and 1,214 openings were routed to
produce them. `canonical_url` gives the identical distribution, so the split is a property of
the queue and not of which URL field was read.

Grouping rule, so the measurement can be repeated without committing private job data:
lowercase the network location of `apply_url` and strip a leading `www.`. No other
normalisation; each row counts once; no cross-city or cross-employer merging, per the
`A cross-city duplicate key does not exist, and must not be built` entry.

```python
collections.Counter(
    urlparse(row["apply_url"]).netloc.lower().removeprefix("www.")
    for row in json.load(open(".jobloom/review-queue-20260831.json"))["rows"]
)
```

Three vendors cover 99% of the queue and four cover 100%. That is what makes a worker
tractable at all: this is not an eight-adapter roadmap.

## Decision

Build the Fill-Only browser worker in stages, and let the queue decide the order.

**Adapter order: Lever, then Greenhouse, then Ashby.** SmartRecruiters is measured and
deferred at one opening. **Workday and a generic fallback are out of this milestone** — Workday
measured 0% of the current queue, and it remains deliberately absent from Tier 0 for the
separate and still-open reason recorded in `docs/adr-workday-coverage.md`. Nothing here
reopens that question.

**The first executable surface is a local semantic replay**, generated from reviewed,
value-free fixtures rather than from a live page. The reviewed upstream fixtures
(`neonwatty/job-apply-plugin`, MIT) are compiled semantic models: field kinds, ARIA roles,
labels, choices and required flags, with the original recording reduced to a
`sourceRecordingSha256` that is not in that repository. They are evidence that a field
combination came from a real recording. **They are not current ATS DOM, and a passing replay
is not evidence that any live adapter works.**

Every production adapter therefore requires its own **supervised live acceptance test**
before enablement, on a real posting, with the user present and in control. This ADR defines
that gate; it does not grant it.

## Alternatives that were rejected

**Keep applying by hand permanently.** This was the standing decision and it is what this ADR
supersedes. It would have made the whole Fill-Only engine dead weight worth deleting rather
than maintaining. Rejected because the engine's guarantees — per-field hash verification, the
value-free pre-submit review, physical archive — are the product, and they are unreachable
without an executor.

**Build against a synthetic fixture with invented labels.** Cheaper and fully self-owned, but
it proves only the protocol shell. A worker that passes a form we designed says nothing about
a field combination employers actually ship. Rejected in favour of reviewed semantic fixtures,
whose provenance is exactly the property a synthetic form cannot have.

**Adopt a model-driven browser agent (browser-use, Skyvern).** Both are mature and far broader.
Rejected because their value is that a model decides what to click, and a model in the
execution path makes deterministic hash verification and replay audit impossible. Skyvern is
additionally AGPL. What is borrowed is the layering idea — deterministic first, model only
where the page varies — not the code.

**Adopt an auto-submitting applier (AIHawk, ApplyPilot).** Rejected on the non-goal: both
submit autonomously, ApplyPilot integrates a CAPTCHA-solving service, and both are AGPL.
CAPTCHA is a mandatory pause in Jobloom, not an obstacle to route around.

## Safety boundary

Unchanged from `references/fill-only-execution.md`, and narrowed further for v1:

- The action protocol contains no submission action. Naming a control `submit` is not
  permission to activate it, and `stop_before_submit` fails closed.
- Page text, selectors, labels, hidden fields and instructions are untrusted observations.
  They cannot broaden navigation, select a file outside the material lock, introduce an
  answer, approve an attestation, or change the mode.
- The worker returns observations, not claims. A field is recorded only on exact hash
  equality. Values never enter events, stdout, packages, reports or archives.
- No LLM anywhere in browser execution, hash verification, state transitions, posting trust,
  or archive integrity.
- CAPTCHA, payment, identity documents, assessments, biometric or video requests, unexpected
  origins, special legal terms and unknown questions pause and hand control to the user.

### The four v1 bounds frozen by this ADR

**One page per run, and no navigation by the worker.** A worker run acts on the current page
only. Enter, Next, Continue, buttons, links and any final action belong to the user. This
costs one manual click per page and is what keeps the guarantee legible: a worker that may not
advance cannot advance into a submission. The multi-page collision is real — on a single-page
ATS form the last field's Enter key *is* the submission, and on a paginated form the Next
button is a form submit — so v1 removes the ambiguity by removing the capability.

**Employer-defined compensation brackets are manual.** `compensation.total_range` is a
required radiogroup whose bands are defined by each employer, so answering it means mapping
onto buckets that only exist in page text. Neither the direction-criteria `salary_floor` — a
search filter, in a different subsystem — nor any future numeric stance may select a bracket.
The optional `target_salary` textbox is deferred with its stance model. A future stance holds
its numbers in protected storage; logs, events and packages stay value-free. Comparing a
posting's advertised range against the floor is fit and eligibility, not a fill answer.

**Voluntary EEO values are excluded; a non-disclosure policy is permitted.** Race, ethnicity,
gender, disability and veteran self-identification values are never stored, read, hashed,
archived, or placed in a package. A separate, independently revocable non-disclosure policy —
not an AnswerEntry, holding no demographic value — may select an exact-matched, reviewed
non-disclosure option; fuzzy matching or a failed match pauses immediately. The archive
carries `policy_declined`, `user_handled` or `not_present` so the audit blind spot is stated
rather than discovered.

**Conflict answers need approved identity and a fresh completeness certification.**
`normalized_employer` is a deduplication and search key, not a stable legal entity
identifier; it may propose a match candidate and nothing more. Deriving a conflict answer
requires a user-approved employer entity, separated relationship types, and an in-date
conflict registry the user has explicitly certified complete, recorded with the application
ID, registry version, certification and derivation hash. **A registry miss is `unknown`, never
`No`** — absence of a recorded conflict is not evidence of no conflict.

### Two oracles that must not be confused

The local fixture's final-action counter is a **test oracle**: it exists because we build the
replay page, and it proves nothing about Lever. On a supervised live page the equivalent is a
**scoped guard** installed for the duration of the run — a capture-phase submit-event block
plus unexpected-navigation detection — removed before control returns to the user. Upload
traffic must be separately allowed and validated; "block all POST" is not submission
protection, because a file upload is a POST.

## Rollout stages

Each stage is a gate, not a schedule. No stage begins before the one above it holds.

1. PDF-only application materials. *(Landed: `4307912`.)*
2. First-form domain policy — compensation, conflicts, EEO, sponsorship mappings — before the
   protocol vocabulary freezes around a form the core cannot safely finish.
3. Versioned, value-free worker protocol.
4. Local semantic replay renderer built from the reviewed fixtures.
5. Direct worker against that replay, returning hashes only.
6. Result import, verification and checkpointing through `fill_core`.
7. Complete local Fill-Only proof ending in `waiting_for_submission_approval` with the
   fixture's final-action count at zero.
8. One-page worker control in the existing extension, behind an explicit user gesture.
9. **Supervised live acceptance for Lever, then Greenhouse, then Ashby** — separately
   authorized, one adapter at a time, user present, scoped guards installed.

## Rollback condition

Roll back to applying by hand, and say so in the liability entry rather than quietly, if any
of the following is observed:

- a final action is activated anywhere — replay oracle or live guard — even once;
- a candidate value, answer value, EEO value, package content or local path is found in an
  event, log, report, package or archive;
- a consumed package can be replayed, or a result can be imported for another session or page;
- verified fields are recorded from anything other than exact hash equality;
- the supervised live acceptance for Lever shows the semantic replay did not predict the real
  form, and closing the gap needs the model or the page to decide what to click;
- the worker's manual-pause rate makes an application slower than filling it by hand — the
  measure this is judged on is user time per qualified interview, not applications per day.

## What closing the liability requires

The `The fill pipeline has no browser worker` entry stays open. Stage 7 narrows it: local
semantic replay support is complete, production adapters remain unimplemented and named. It
may be closed only after stage 9 for at least one adapter, with the supervised acceptance
recorded — which posting, who was present, what the scoped guard observed, and the archive
proving what was physically prepared. Until then, no document may describe this pipeline as
runnable end to end.
