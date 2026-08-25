# MVP initialization and readiness

## Purpose

`mvp_core.py` initializes every private backend component in dependency order and reports what is actually ready. It keeps software completeness separate from real-world operational readiness so an empty database, synthetic test, or successful schema migration cannot be mistaken for permission to apply.

## Unified initialization

Run:

```bash
python3 skills/jobloom/scripts/mvp_core.py \
  --db .jobloom/jobloom.db \
  --private-root .jobloom \
  init
```

This initializes application state, answers/authorization, resumes/material locks, CandidateSnapshots, SearchDirections/plans, cover letters, archive/tracker state, outcomes/usage, pre-submit reviews, and Fill-Only execution. The database is mode 0600; private directories are mode 0700.

Initialization creates schemas and empty directories only. It never creates a candidate, direction, resume, answer, job, authorization, application, approval, outcome, or submission.

## Readiness levels

- `implementation`: required tables and private-directory permissions exist.
- `onboarding`: exactly one active user-registered CandidateSnapshot, at least one user-approved SearchDirection, and at least one user-approved direction ResumeVersion with its approved plan exist.
- `live_job_evaluation`: onboarding is ready and at least one real JobCard has been reviewed against its full JD.
- `fill_queue`: live-job evaluation is ready, a current standing authorization exists, and an approved material-locked application is in the fill queue.

Every level returns stable blockers and non-sensitive counts. A higher level includes all lower-level blockers.

The report always sets `submission_authorized: false`. Readiness does not replace application approval, the Fill-Only stop, the exact pre-submit summary, authorization scope recheck, user approval, positive submission evidence, or archive verification.

## MVP implementation map

| MVP capability | Enforcing component |
|---|---|
| Master resume ingestion | `extract_candidate_facts.py`, ResumeVersion master snapshot |
| Candidate fact extraction | traceable review packet with source/excerpt hashes |
| User confirmation and locking | `finalize_candidate.py`, `candidate_core.py` |
| One search direction | user-approved SearchDirection |
| One approved direction resume | approved adaptation plan + immutable ResumeVersion + claims manifest |
| Work authorization and preferences | CandidateProfile plus independent answer meanings |
| Answer reuse | AnswerLibrary exact/user-reviewed semantic forms and two-channel freshness |
| Job URL ingestion | `ingest_job.py`, review-required JobCard |
| Deduplication | multi-key job identity and application uniqueness |
| Hard filters | zero-model deterministic evaluator |
| Compact job cards | normalized reusable JobCard |
| Keyword coverage | evidence-strength-preserving requirement matches and adaptation plan |
| Fill-Only workflow | worker lease, page plan, private action package, hashes, checkpoints, pause/resume |
| Pre-submission summary | persisted value-free summary, exact SHA-256 user approval |
| Duplicate application prevention | backend application state and related-job checks |
| Submission evidence | positive evidence required for `submitted` |
| Local archive and spreadsheet | physical materials, redaction, manifest verification, deterministic tracker |
| Outcome tracking | guarded OutcomeRecord and backend-derived funnel |
| Model-usage tracking | token/cost/cache metadata without prompts or responses |

## Acceptance audit

- Unsupported or inflated claims fail claims-manifest validation or remain attention items; transferable evidence never becomes direct.
- Locked facts require the same active CandidateSnapshot value and exact-preservation review.
- The four work-authorization/sponsorship meanings remain separate canonical IDs.
- Exact answers and deterministic gates use no model; ambiguity, conflicts, expiration, and scope mismatch pause.
- Standard attestation requires current authorization and every covered field to remain fresh.
- Pre-submit review exposes source IDs and hashes before user approval.
- Duplicate applications, wrong/revoked materials, missing evidence, uncertain submission, and tampering are blocked.
- Submission usage and archive manifest preserve the exact resume and optional cover-letter bytes.
- Evaluations preserve reasons, gaps, evidence IDs, and user decisions.
- Repeated known work reuses zero-model rules, exact question forms, immutable artifacts, and stored workflow state; model-usage events make remaining cost measurable.

## Real user-owned blockers

The software cannot safely clear these itself:

1. reviewing and confirming real resume-derived facts and work authorization
2. registering the resulting CandidateSnapshot
3. approving a real search direction profile
4. reviewing and approving the actual direction resume and claims manifest
5. reviewing a real JobCard against the full JD
6. approving a real application and current authorization scope
7. reviewing the exact pre-submit summary and resolving mandatory pauses
8. providing or observing genuine submission evidence and outcomes

Never substitute fixtures, example.com jobs, caller actor strings without actual user review, inferred approvals, or fabricated success evidence for these conditions.
