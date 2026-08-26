# Resume versions and material locks

## Purpose

A ResumeVersion is an immutable local file snapshot, not a mutable filename or an external document pointer. It preserves the exact bytes selected for an application and links every resume claim to confirmed CandidateFacts.

## Version kinds

- `master_source`: the complete candidate archive. It may be longer than one page and its layout is reference material, not an application template. It has no parent.
- `direction`: a reusable resume for one role direction. Its parent is an approved master or earlier direction version.
- `lightweight`: a small, evidence-preserving adaptation within one direction.
- `precision`: a high-value job adaptation within one direction.

Every application-facing `direction` resume must be one page. There are two explicit source modes:

- `generated`: Jobloom derives the file from an approved parent and an approved adaptation plan tied to a real, user-reviewed JobCard.
- `user_provided`: the user supplies an already targeted direction resume. It has no generated parent and no adaptation plan, because claiming either would create false provenance.

`lightweight` and `precision` versions are always generated and require an approved parent. A generated child cannot silently switch directions within a version chain.

When the SearchDirection registry is initialized, every application-facing version must belong to the active approved portfolio. Generated versions also require the approved adaptation plan described in `search-directions.md`; its recommended kind, direction, and base resume must match registration exactly. A user-provided direction version skips only plan generation. It does not skip direction scope, CandidateFact evidence, immutable hashing, rendered one-page review, or explicit user approval. Master sources remain provenance and cannot be used directly for an application after this enforcement is active.

## Registration and immutability

Registration copies PDF, DOCX, TXT, or Markdown bytes into the private resume store. It records SHA-256, size, format, provenance mode, parent, kind, and direction, then makes the snapshot read-only. Registration always creates a `draft`; it never implies approval.

For DOCX or PDF direction resumes, render the entire file before approval and confirm the output contains exactly one page. Registration records provenance; it does not make a page-count claim.

Never overwrite a snapshot directory or reuse a version ID. If a file changes, register a new child version.

## Claims manifest

Approval requires a private JSON manifest containing at least one claim. Every claim has:

- a unique `claim_id`
- the exact `claim_text` present in the resume
- one or more supporting CandidateFact IDs
- an `evidence_strength` no stronger than its supporting facts
- `exact_locked_value_preserved: true` whenever a supporting fact is locked

All referenced facts must be confirmed or locked in a hash-valid `candidate.json`. A transferable fact cannot support a direct claim. The manifest assertion does not replace human review of the rendered resume; it creates an auditable evidence contract for that review.

When `candidate_core.py` is initialized, the `candidate.json` content hash must also identify the active user-registered CandidateSnapshot. File-level validity alone is insufficient.

## Approval and revocation

Only the `user` actor may approve or revoke a resume. Approval verifies:

1. snapshot bytes still match the registered hash
2. `candidate.json` matches its deterministic content hash
3. every claim resolves to usable CandidateFacts
4. evidence strength is not inflated
5. locked values have an explicit exact-preservation assertion

Approval snapshots the claims manifest beside the resume and records both candidate and manifest hashes. Revocation invalidates every active material lock using that version.

For a planned derived version, approval also rechecks the exact approved adaptation plan, CandidateProfile hash, SearchDirection hash, and JobCard hash. Plan approval does not approve the file: the user must still review the rendered immutable snapshot and its material changes.

## Application binding and material lock

Bind an approved version while an application is `approved` or `materials_in_progress`. Binding another resume invalidates the previous lock. While the application is `materials_in_progress`, create a material lock that freezes the resume version and file hash plus the optional bound cover-letter version and file hash described in `cover-letter-versions.md`.

An application cannot enter `ready_to_fill`, be acquired by a fill worker, enter `pre_submit_ready`, or submit unless its active lock:

- matches the application's bound resume version
- points to a still-approved ResumeVersion
- matches the registered hash
- matches the current snapshot bytes
- matches the bound, still-approved cover-letter version and bytes when one is present

Record `prepared`, `locked`, and `submitted` usage separately. Never place resume content or CandidateFact values in event metadata.

## Private storage

Keep the database, resume snapshots, claims manifests, and CandidateFact artifacts under `.jobloom/`. They contain personal data and must not be committed by default.

## Direction baselines

A direction carries one standing one-page resume. It serves the direction, not a posting,
so it needs no JobCard and no ResumeAdaptationPlan. Its evidence record is a **BaselinePlan**.

Three source modes now exist for a `direction` resume:

| `source_mode` | Origin | JobCard | Plan |
|---|---|---|---|
| `generated` | tailored to one real posting | required | `ResumeAdaptationPlan` |
| `direction_baseline` | the direction's standing one-pager | none | `BaselinePlan` |
| `user_provided` | a one-page resume the user supplies | none | none |

The master is the fact and evidence archive, not a layout template. A baseline is composed
afresh from the approved master, the CandidateFacts and the approved direction; it may
follow the master's style but does not inherit its layout limits.

A BaselinePlan records the exact approved master version and file hash, the CandidateProfile
hash, the direction profile hash, the selected fact IDs with their order, the excluded fact
IDs, a controlled reason code for every selection and exclusion, any unsupported terminology,
and an explicit prohibition on promoting evidence strength. Every confirmed fact must be
either selected or explicitly excluded, so nothing is dropped silently. The plan holds fact
IDs and reason codes only — never fact values — so approving it can never approve wording.

Approving a BaselinePlan approves the selection and ordering, nothing else. Its `plan_sha256` is recomputed from the stored payload on every use, so an edited `plan_json` is refused even when the recorded hash is left untouched. `unsupported_terms` is always empty: a baseline plan carries no free text. `locked_fact_ids_must_remain_exact` lists only the locked facts the resume actually carries. Revoking the direction, its portfolio, or the master the plan was built on retires dependent plans through one cascade, recording an event for each rather than leaving them to fail at the next use. `invalidate-baseline-plan` retires a plan deliberately, recording the actor, the reason and the superseding plan. Never retire one by editing the table. The system may retire an unapproved plan, which is scaffolding; a user-approved plan is a decision and only the user may retire it directly, or an authorized revocation may cascade over it.

The single-page rule lives in the application authorization gate, not beside it, so readiness and fill acquisition cannot drift apart: an approved baseline whose recorded `rendered_page_count` is anything but 1 is refused by both.

Every use re-validates the whole chain, not just the plan row: the payload is re-hashed, the direction must still be approved and inside the active portfolio at the same profile hash, the approved master must be the planned one and must itself have been approved against the planned candidate profile, and the active CandidateSnapshot file is re-hashed. Readiness counting runs the same validation, so a plan edited after its resume was approved stops counting immediately, and a baseline only counts while its recorded `rendered_page_count` is 1.

At approval, the rendered file's claims manifest must reference **exactly** the plan's selected fact IDs: nothing missing, and nothing that the plan excluded. The approver also confirms `rendered_page_count`, which must be 1 for a direction baseline and is recorded on the version. Approving a plan therefore cannot approve a resume that carries different content or spills onto a second page. The rendered
one-page DOCX, its claims manifest and its file hash are approved separately by the user, as
for any other resume. Registration fails closed when the plan is unapproved, belongs to
another direction, binds a JobCard adaptation plan, names a parent other than the planned
master, or when the master, candidate profile or direction profile has moved since review.

Do not select baseline content by keyword-matching the direction profile. Those keywords are
job-posting vocabulary and the fact library is the candidate's own; matching one against the
other misses genuinely relevant experience. Selection is a judgment that belongs in a
reviewable BaselinePlan.
