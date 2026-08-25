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

Entering `ready_to_fill` requires an active material lock for the application's bound, approved ResumeVersion. The lock must match the registered resume hash and the current snapshot bytes. Acquisition repeats these checks; a missing, revoked, rebound, or modified resume cannot enter the fill queue.

Reset the pre-submit check whenever the application returns to materials, filling, user-answer, failed, or takeover states.

## Submission gates

Entering `submitting` requires:

- a passed pre-submit check
- an active, hash-valid material lock matching the bound approved resume
- a real active, unrevoked, unexpired authorization in the shared local database
- satisfaction of the selected submission policy

Entering `submitted` requires stored positive evidence: success page, confirmation ID, account record, or confirmation email. Record evidence only while `submitting` or `submission_uncertain`.

`submission_uncertain` is never part of the automatic work queue. Only an explicit user resolution may move it to `submitted` or `submission_failed`; moving it to `submitted` still requires positive evidence.

## Failure handling

Use stable failure codes rather than free-text sensitive payloads. Known failures may return to `ready_to_fill` while attempts remain. CAPTCHA, new questions, safety restrictions, and user pauses should normally move to a user-controlled waiting state rather than loop.
