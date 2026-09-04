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

## Resume variants

A SearchPortfolio allocates the user's applications across every direction they pursue. A
**ResumeVariant** allocates one resume's own share across the subset of directions it can
honestly answer. These are two separate weightings over the same directions and they do not
have to agree: a portfolio may send 20% of applications to consulting while a healthcare
research resume covers no consulting at all.

Register a variant with one user-provided file and a coverage list of one to twenty
directions, each naming its exact approved profile SHA-256 and a weight; weights must total
100. Registration creates **one immutable ResumeVersion per covered direction** from the same
bytes, each with its own physical snapshot, all sharing `variant_id` and `source_mode:
user_provided`. Selection, binding, locking, and readiness stay per-direction and unchanged —
a variant is an approval and coverage object, not a new resume kind.

Approval is one user action over the exact coverage hash. Every member still runs the full
per-version approval: active user-registered candidate snapshot, unchanged direction profile,
and a claims manifest whose every claim resolves to an available fact. If any member fails,
none is approved and the manifest snapshots written before the failure are removed. Revoking
the variant revokes every member with it, so coverage cannot outlive the variant.

`variant_allocation_status` reports rolling deficits over the variant's own directions and
weights, from the same persisted review pool `portfolio_allocation_status` reads. A direction
the resume does not cover is not the variant's business and never appears in its targets.

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

## Carrying a resume across a change of CandidateSnapshot

Registering a new profile invalidates every material lock bound to the old snapshot, and it
should: a lock says "these exact bytes were approved against this exact profile", and one half
of that has moved. The other side of it had no path. An approved ResumeVersion records the
snapshot it was approved against and `_require_resume_authorized_for_application` requires that
snapshot to be the active one, so afterwards the resume is refused at binding, at locking and
at selection — while `approve_version` accepts only a draft and the recorded hash is not
editable, correctly.

`resume_migration.py` is the way out, and it is a **successor**, not a rebind:

```text
prepare -> the same file registered again as its own draft, bytes hash-verified
approve -> the predecessor's claims manifest revalidated against the new snapshot,
           approved by the user
bind    -> ready_to_fill -> materials_in_progress -> bind -> lock -> ready_to_fill
```

The user is being asked a real question — *these claims were checked against who you were; are
they still true of who you are now?* — and a button that skipped it would be answering it for
them.

- **No `parent_version_id`.** That column means "derived from" in the generation chain, and
  `_validate_parent` refuses it for a `user_provided` resume so a supplied PDF cannot claim a
  lineage it never earned. A snapshot migration is a different relation and gets its own
  record in `resume_migrations`.
- **`user_provided` only.** A `generated` resume is bound to an adaptation plan and a
  `direction_baseline` one to a baseline plan, both carrying their own snapshot hashes;
  migrating either means deciding what a stale plan becomes, which is a separate design. The
  other two modes are refused by name rather than half-handled.
- **The bytes are taken from the predecessor's own stored snapshot** and checked against its
  recorded hash, then checked again on the registered copy. No caller supplies a file.
- **The manifest is the predecessor's stored one**, verified to be the file that was recorded
  and then revalidated in full. A snapshot that only *added* facts passes, which is what a
  profile round does. One that drops a cited fact, weakens its evidence, or **locks** a fact a
  claim cited without promising to preserve its exact value does not.
- **Nothing is migrated automatically.** `stranded` names every approved resume the active
  snapshot has left behind, says which can take this path and which application went quiet,
  and migrates none. Which resume is worth carrying is a question about the user's week.
- **A failure leaves the application in `materials_in_progress`.** The sequence is not one
  transaction — `transition` commits as it goes, by design — so it is ordered instead so that
  every stopping point is a true statement. Stuck in preparation means the materials really
  are not ready; running the migration again continues from there; and the move back to
  `ready_to_fill` re-runs `require_active_material_lock` on its own.

- **A bound cover letter is not left behind.** If the application binds one that was approved
  against the old snapshot, the carry stops by name — before the application moves — rather
  than failing later inside `lock_materials` with an error about a document nobody was
  thinking about. Carrying a cover letter across is the same successor dance for a second
  document and is deliberately not done as a side effect of the resume's, which would approve
  something the user was never shown.

**What the revalidation does not promise.** A manifest pins the exact value only of a fact
that is *locked*. A confirmed fact whose wording changed still satisfies the check, so the
guarantee is "every cited fact still exists at the strength claimed", not "every cited fact
still says the same thing". What catches the rest is the review the user is being asked for.
