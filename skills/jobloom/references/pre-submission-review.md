# Pre-submission review

## Purpose

A caller-supplied `check_passed: true` is not evidence. Jobloom requires a persisted, deterministic summary of the exact form, fields, materials, authorization, and safety checks, followed by explicit user approval of that summary's hash.

## Form inventory

Register the inventory only while a fill lease is active. `fill_core.py` creates it deterministically after all observed pages are checkpointed and before releasing the application from `filling`. It records:

- form URL and observed employer/role
- whether the workflow is known
- every required field ID
- legal items displayed
- mandatory-pause requests encountered
- exact ResumeVersion and cover-letter version selected for upload

The inventory contains no field values. Registering a newer inventory invalidates the older one. Unsupported legal items are rejected immediately. A fill session with any unresolved mandatory pause cannot finish; known pause types remain in incomplete session history for recovery and explanation.

## Deterministic review checks

Generate the review only after filling reaches `waiting_for_submission_approval`. Require:

1. observed employer and role match the JobCard
2. every required field was recorded
3. fact-backed fields remain locked and their exact stored values still match the active CandidateSnapshot bound through the material-locked ResumeVersion
4. answer-backed fields still match active, applicable, unexpired AnswerEntries
5. authorization remains active, unexpired, and scoped to backend-derived application/job context
6. resume upload matches the active material lock and physical hashes
7. any cover-letter upload matches the declared immutable version
8. no mandatory pause or unsupported legal item
9. known-form policy is satisfied
10. duplicate-application check still passes

Do not allow caller-provided context to override application ID, company, country, or employment type derived from backend records.

## Summary and approval

The summary contains employer, role, form URL, material version IDs and hashes, field/source IDs and freshness status, authorization ID, submission policy, legal-item labels, and deterministic check results. It contains no field or answer values.

Only actor `user` may approve. The caller must supply the exact summary SHA-256 shown to the user. Approval binds the summary, authorization, inventory, and material lock.

## Application-state enforcement

Entering `pre_submit_ready` requires the approved review ID; the old Boolean is ignored. Entering `submitting` revalidates the review hash, user approval, active inventory, material lock, and authorization. The submission authorization must be the same authorization approved in the summary.

Returning to materials, filling, a user-answer state, failure, takeover, withdrawal, or closure clears the application review pointer and invalidates generated or approved reviews. A changed form or answer therefore requires a new summary and user approval.

The submission archive preserves the review ID, summary hash, and authorization ID as part of its evidence chain.
