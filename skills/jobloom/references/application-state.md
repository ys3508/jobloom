# Application state and deduplication

## Job identity

Canonicalize URLs by normalizing scheme and host, removing fragments, sorting meaningful query parameters, and dropping known tracking parameters. Resolve duplicates in this order:

1. Canonical URL: definite duplicate.
2. Normalized employer plus requisition ID: definite duplicate.
3. Description fingerprint plus normalized employer and title: definite duplicate.
4. Normalized employer, title, and location: possible duplicate requiring review.
5. Existing application on the job or a linked possible duplicate: block a new application.

Record additional source URLs against the existing job. Do not treat the same description hash across different employers as a duplicate.

## State transitions

Use `application_core.py`; do not edit SQLite state directly. Every transition writes an event containing state, actor, reason code, and allowlisted non-sensitive metadata.

User approval is required to enter `approved`. Use atomic acquisition to enter `filling`; a worker lease expires and may be recovered, but a live lease cannot be stolen. Limit automatic attempts and preserve the failed step.

Entering `ready_to_fill` requires an active material lock for the application's bound, approved ResumeVersion and optional approved CoverLetterVersion. The lock must match every registered version, hash, and current snapshot. Acquisition repeats these checks; missing, revoked, rebound, modified, or wrongly scoped material cannot enter the fill queue.

Generate and obtain user approval for the persisted summary in `pre-submission-review.md` before entering `pre_submit_ready`. A caller Boolean is not a check result. Reset and invalidate the review whenever the application returns to materials, filling, user-answer, failed, or takeover states.

## Submission gates

Entering `submitting` requires:

- a hash-valid, user-approved pre-submit summary bound to the current inventory, material lock, and authorization
- an active, hash-valid material lock matching the bound approved resume and optional cover letter
- a real active, unrevoked, unexpired authorization in the shared local database
- satisfaction of the selected submission policy

Entering `submitted` requires stored positive evidence: success page, confirmation ID, account record, or confirmation email. Record evidence only while `submitting` or `submission_uncertain`.

After a confirmed submission, create and verify the immutable local archive described in `submission-archive.md`. Archive state is separate from application state; an archive failure must remain visible and must not rewrite a confirmed application as unsubmitted.

`submission_uncertain` is never part of the automatic work queue. Only an explicit user resolution may move it to `submitted` or `submission_failed`; moving it to `submitted` still requires positive evidence.

## Failure handling

Use stable failure codes rather than free-text sensitive payloads. Known failures may return to `ready_to_fill` while attempts remain. CAPTCHA, new questions, safety restrictions, and user pauses should normally move to a user-controlled waiting state rather than loop.
