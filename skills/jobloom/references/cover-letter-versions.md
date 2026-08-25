# Cover-letter versions

## Purpose

A CoverLetterVersion is an immutable private snapshot linked to verified CandidateFacts. It prevents a mutable document pointer, unsupported claim, or letter for the wrong employer from reaching an application or its archive.

## Version kinds and scope

- `reusable_template`: an approved reusable base that is not bound to one application.
- `application_specific`: a letter created for exactly one application and its backend JobCard.

Register an application-specific version only while that application is `approved` or `materials_in_progress`. It may never be bound to another application, including another role at the same employer.

A version may name an approved parent. Never derive from a draft or revoked parent. Register every changed file under a new version ID; do not overwrite snapshots.

## Registration, evidence, and approval

Registration accepts PDF, DOCX, TXT, or Markdown, copies exact bytes into the private cover-letter store, records SHA-256 and size, and makes the snapshot read-only. It always creates `draft` status.

Approval uses the claims-manifest contract in `facts-and-evidence.md` and `resume-versions.md`:

- every factual claim names one or more confirmed or locked CandidateFact IDs
- claim evidence strength cannot exceed the strongest supporting fact
- any locked supporting fact requires exact-value preservation
- `candidate.json` must pass its deterministic content-hash check
- the physical letter snapshot must still match its registered hash

Only actor `user` may approve or revoke a letter. Approval means the user reviewed that exact snapshot; a generated draft or successful validation is not approval.

## Binding and material lock

Bind only an approved version during application material preparation. Binding records `prepared` usage and invalidates an older active material lock or pre-submit review for the application.

The material lock stores the exact cover-letter version and file SHA-256 together with the resume. If no cover letter is bound, both cover-letter lock fields remain null. Fill acquisition, pre-submit review, submission, and archive recheck:

- the bound version still equals the locked version
- the version remains approved
- an application-specific version still matches the application and job
- snapshot and claims-manifest bytes still match their hashes

Revocation invalidates every active material lock and pre-submit review that depends on the version. Never substitute another letter after user approval without returning to material preparation and creating a new lock and review.

## Usage and archive

Record `prepared`, `locked`, and `submitted` usage separately. A confirmed submission with a cover letter is archivable only when `submitted` usage exists and its hash matches both the registry and physical snapshot.

The archive contains `cover_letter_used.<original-format>` and `cover_letter_claims_manifest.json`. These are physical read-only copies, not version pointers.

## Private storage

Keep the database, source-derived snapshots, claims manifests, and archived letters under `.jobloom/`. Never commit or place their contents in model telemetry.
