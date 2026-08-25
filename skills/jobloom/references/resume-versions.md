# Resume versions and material locks

## Purpose

A ResumeVersion is an immutable local file snapshot, not a mutable filename or an external document pointer. It preserves the exact bytes selected for an application and links every resume claim to confirmed CandidateFacts.

## Version kinds

- `master_source`: the unedited source resume. It has no parent.
- `direction`: a reusable resume for one role direction. Its parent is an approved master or earlier direction version.
- `lightweight`: a small, evidence-preserving adaptation within one direction.
- `precision`: a high-value job adaptation within one direction.

Except for `master_source`, every version requires an approved parent. A child cannot silently switch directions within a version chain.

## Registration and immutability

Registration copies PDF, DOCX, TXT, or Markdown bytes into the private resume store. It records SHA-256, size, format, parent, kind, and direction, then makes the snapshot read-only. Registration always creates a `draft`; it never implies approval.

Never overwrite a snapshot directory or reuse a version ID. If a file changes, register a new child version.

## Claims manifest

Approval requires a private JSON manifest containing at least one claim. Every claim has:

- a unique `claim_id`
- the exact `claim_text` present in the resume
- one or more supporting CandidateFact IDs
- an `evidence_strength` no stronger than its supporting facts
- `exact_locked_value_preserved: true` whenever a supporting fact is locked

All referenced facts must be confirmed or locked in a hash-valid `candidate.json`. A transferable fact cannot support a direct claim. The manifest assertion does not replace human review of the rendered resume; it creates an auditable evidence contract for that review.

## Approval and revocation

Only the `user` actor may approve or revoke a resume. Approval verifies:

1. snapshot bytes still match the registered hash
2. `candidate.json` matches its deterministic content hash
3. every claim resolves to usable CandidateFacts
4. evidence strength is not inflated
5. locked values have an explicit exact-preservation assertion

Approval snapshots the claims manifest beside the resume and records both candidate and manifest hashes. Revocation invalidates every active material lock using that version.

## Application binding and material lock

Bind an approved version while an application is `approved` or `materials_in_progress`. Binding another resume invalidates the previous lock. While the application is `materials_in_progress`, create a material lock that freezes the resume version and file hash.

An application cannot enter `ready_to_fill`, be acquired by a fill worker, enter `pre_submit_ready`, or submit unless its active lock:

- matches the application's bound resume version
- points to a still-approved ResumeVersion
- matches the registered hash
- matches the current snapshot bytes

Record `prepared`, `locked`, and `submitted` usage separately. Never place resume content or CandidateFact values in event metadata.

## Private storage

Keep the database, resume snapshots, claims manifests, and CandidateFact artifacts under `.jobloom/`. They contain personal data and must not be committed by default.
