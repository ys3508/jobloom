# Submission archive

## Purpose

Every confirmed submission must have a local, human-readable archive created without a model call. The archive preserves physical copies of what was sent, a redacted answer snapshot, the JobCard, positive submission evidence, and a hash manifest. It is not created for a click, dry run, failed submission, or unresolved submission uncertainty.

## Archive eligibility

An application is archivable only when all of these are true:

- its state belongs to the confirmed-submission lineage
- `submitted_at` exists
- positive submission evidence exists
- a `submitted` ResumeVersion usage record exists
- the resume snapshot, registered hash, and submitted-use hash agree
- the resume claims-manifest snapshot still matches its hash
- if a cover letter is declared, its registry record and `submitted` usage exist
- cover-letter snapshot, registered hash, submitted-use hash, and claims-manifest hash agree

Do not fabricate a placeholder cover letter or confirmation. Missing, changed, or unsubmitted material pauses the archive. A later revocation does not erase a submission that already passed the approval and hash gates; its exact submitted bytes remain archivable.

## Recorded application fields

Record a field only during filling or submission preparation. Every field must map to an active AnswerEntry or a locked CandidateFact. An answer-backed value must exactly equal the library value. Store the exact value only in the protected local database together with source ID, source status, sensitivity, and timestamp.

Sensitivity classes are `normal`, `address`, `sensitive_personal`, `date_of_birth`, `identity_document`, `tax_identifier`, `banking`, and `credential`. Known sensitive field names cannot be classified as normal.

## Redacted answers snapshot

The archive applies logging redaction:

- include `normal` values
- replace `address` and `sensitive_personal` values with `[REDACTED]`
- omit `date_of_birth`, `identity_document`, `tax_identifier`, `banking`, and `credential` fields entirely

The snapshot records only aggregate counts for omitted fields. It must not preserve their field names, questions, source IDs, or values.

## Physical artifacts and verification

Each application directory contains:

- `resume_used.<original-format>`
- `resume_claims_manifest.json`
- optional `cover_letter_used.<original-format>`
- optional `cover_letter_claims_manifest.json`
- `answers_snapshot.json`
- `job_card.json`
- `confirmation.<allowlisted-format>` or `confirmation.txt`
- `archive_manifest.json`

The manifest records SHA-256 and byte size for every artifact plus submission metadata and redaction counts. Files become read-only after creation. Verification rehashes the manifest and every artifact and rejects missing, modified, unsafe-path, or untracked files.

Archive creation is idempotent by application ID. Never overwrite an existing archive directory or create a second archive record for the same application.

## Master tracker

Generate `applications.xlsx` from backend archive, application, and job state—never by hand-editing it as a data source. The tracker contains submission time, employer, role, location, work arrangement, source, ATS, application URL, resume and cover-letter versions, category, current status, confirmation ID, follow-up date, model usage, archive ID, and archive path.

The workbook contains no answer values. Regeneration is deterministic with respect to stored backend state; unsupported metrics remain blank rather than guessed.

## Private storage

Keep archives, tracker source JSON, and `applications.xlsx` under `.jobloom/`. Archived contents must not be injected into model context. Do not commit them by default.
