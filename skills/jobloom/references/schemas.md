# MVP schemas

## Contents

1. Candidate profile
2. Candidate review packet
3. Candidate fact
4. Job input
5. Evaluation output
6. Answer entry
7. Standing authorization
8. Application core
9. Resume version
10. Search direction and adaptation plan
11. Cover-letter version
12. Fill-only execution
13. Pre-submission review
14. Submission archive
15. Outcomes and usage
16. Future core entities

## Candidate profile

```json
{
  "profile_id": "candidate-1",
  "work_authorization": {
    "country": "US",
    "authorized_now": true,
    "sponsorship_now": false,
    "sponsorship_future": true,
    "employer_action_required": false,
    "confirmed": true,
    "expires_at": "2027-01-01"
  },
  "search": {
    "countries": ["US"],
    "locations": ["New York, NY"],
    "work_arrangements": ["remote", "hybrid"],
    "employment_types": ["full_time"],
    "salary_floor": 100000,
    "salary_currency": "USD",
    "excluded_employers": []
  },
  "citizenships": [],
  "security_clearances": [],
  "certifications": [],
  "facts": []
}
```

Dates use ISO 8601. Country values use a consistent code chosen by the implementation. Salary values in the MVP are normalized annual amounts. If a posting's currency differs from `search.salary_currency`, require review instead of comparing the amounts.

## Candidate review packet

Resume extraction produces proposed facts, never immediately reusable facts. Each proposed fact contains a document SHA-256, line locator, excerpt SHA-256, evidence strength, and a `pending` decision. Every decision must become `confirmed` or `rejected` before finalization. Confirmed protected facts become locked; rejected facts are excluded. The final `candidate.json` includes a deterministic content hash. CandidateSnapshot registration stores a read-only physical copy and file hash, active/superseded status, user registration metadata, and an exact CandidateFact row for every fact. Only one snapshot is active.

## Candidate fact

```json
{
  "id": "fact-python-1",
  "type": "skill",
  "value": "Python",
  "keywords": ["python"],
  "source": "master-resume.pdf#experience-2",
  "evidence_strength": "direct",
  "status": "locked",
  "confirmed_at": "2026-08-25",
  "expires_at": null,
  "invalidation_triggers": []
}
```

Allowed evidence strengths: `direct`, `strongly_related`, `transferable`, `mention_only`, `none`.

## Job input

```json
{
  "job_id": "job-1",
  "canonical_url": "https://example.com/jobs/1",
  "employer": "Example",
  "title": "Backend Engineer",
  "country": "US",
  "location": "New York, NY",
  "work_arrangement": "hybrid",
  "employment_type": "full_time",
  "salary": {"currency": "USD", "min": 120000, "max": 150000, "unit": "YEAR"},
  "status": "open",
  "sponsorship": "unknown",
  "citizenship_required": null,
  "security_clearance_required": null,
  "required_certifications": [],
  "required_skills": ["Python", "SQL"],
  "preferred_skills": ["AWS"],
  "already_applied": false,
  "high_value": false,
  "requirements_reviewed": false,
  "description_sha256": "...",
  "extraction": {"strategy": "json_ld", "needs_user_review": true}
}
```

Allowed sponsorship values: `supports`, `does_not_support`, `historical_support`, `unknown`, `conflicting`.

An extracted JobCard always begins with `requirements_reviewed: false`. A user or reviewer must compare required skills and hard eligibility fields with the complete JD before changing it to `true`. The evaluator treats an unreviewed card as uncertain.

## Evaluation output

The deterministic evaluator emits the normalized job card plus:

```json
{
  "eligibility": "pass",
  "match": "worth_applying",
  "action": "broad",
  "reasons": [],
  "hard_filter_failures": [],
  "uncertainties": [],
  "evidence_matches": [],
  "main_gap": null,
  "user_decision_required": null
}
```

Never interpret `uncertain` as `pass`.

## Answer entry

An AnswerEntry stores a stable answer ID, canonical question ID and meaning, JSON answer value, answer type, valid source, confirmation timestamp, effective/expiration/review dates, validity class, scope, preconditions, exclusions, automatic-fill and automatic-submit permissions, sensitivity, invalidation triggers, dependent fact IDs, supersession, status, and ambiguity notes.

Valid sources are user confirmation, a verified candidate fact, an approved resume, a user-defined rule, or deterministic derivation. Model inference alone is invalid. Legal commitments and voluntary disclosures cannot enable automatic submission in the MVP.

Question forms live separately from answers. Each exact or user-verified semantic form maps normalized text to one canonical meaning.

## Standing authorization

A StandingAuthorization contains an ID, confirmation and expiration timestamps, scope, revocation timestamp, and status. It may last at most fourteen days. It controls Channel A only and cannot alter an AnswerEntry's Channel B status.

## Application core

Keep jobs, job sources, applications, application events, and submission evidence in separate tables. Job records contain normalized identity and the reusable JobCard. Application records contain current state, category, submission policy, material version IDs, authorization ID, pre-submit status, attempt/lease data, failure code, and submission confirmation metadata.

Application events contain no answer values or full JobCards. Submission evidence is recorded separately and is required before the `submitted` state. The complete state and transition rules live in `application-state.md`.

## Resume version

A ResumeVersion stores an immutable snapshot path, file SHA-256 and size, kind, direction, parent version, optional adaptation-plan ID and direction-profile hash, status, candidate profile hash, claims-manifest hash, approval metadata, and revocation metadata. Kinds are `master_source`, `direction`, `lightweight`, and `precision`. Registration always creates `draft`; only the user actor may move it to `approved` or `revoked`.

The claims manifest maps each exact resume claim to one or more confirmed CandidateFact IDs. Its evidence strength cannot exceed the strongest supporting fact. A claim using any locked fact must assert exact locked-value preservation.

When the CandidateSnapshot registry is initialized, ResumeVersion approval requires the same active user-registered candidate content hash. Selecting, binding, and locking a resume rechecks that its candidate hash is still active.

A material lock records the exact approved resume version and file hash, plus the optional approved cover-letter version and file hash, selected for one application. It is required before `ready_to_fill` and is revalidated before filling, pre-submit readiness, and submission. Resume usage records preserve `prepared`, `locked`, and `submitted` use separately.

## Search direction and adaptation plan

SearchDirection stores an immutable structured profile, optional approved parent, deterministic profile SHA-256, status, user approval metadata, and revocation metadata. The profile contains target titles, role family, keyword groups, and bounded direction criteria. Registration creates `draft`; only actor `user` may approve or revoke it.

ResumeAdaptationPlan stores one approved direction, reviewed JobCard, approved base ResumeVersion, candidate/profile/job hashes, deterministic value-free plan JSON and SHA-256, recommended kind, status, user approval metadata, and invalidation metadata. Its evidence records preserve original strength and CandidateFact IDs without values.

After direction enforcement is initialized, every non-master ResumeVersion requires an approved plan whose direction, recommended kind, base parent, candidate hash, JobCard hash, and direction-profile hash match. Plan approval and physical ResumeVersion approval remain separate.

## Cover-letter version

A CoverLetterVersion stores an immutable snapshot path, file SHA-256 and size, kind, optional approved parent, status, optional application and job scope, candidate profile hash, claims-manifest path and hash, approval metadata, and revocation metadata. Kinds are `reusable_template` and `application_specific`.

Registration always creates `draft`. Only actor `user` may approve or revoke a version. An application-specific letter may be registered only during material preparation and may be bound only to its exact application and job. Claims obey the same CandidateFact evidence ceiling and locked-value preservation rules as resumes.

Cover-letter usage records preserve `prepared`, `locked`, and `submitted` use with the file hash. When bound, its exact version and hash become part of the application material lock and pre-submit summary. Rebinding or revocation invalidates affected locks and reviews.

## Fill-only execution

FillSession stores one application, worker, immutable `fill_only` mode, initial form URL and observed identity, known-form status, authorization ID and allowlisted context, current page, submit-control observation, status, pause reason codes, and timestamps.

FillPage stores a unique session/page ID and index, same-origin page URL, value-free observation JSON and hash, legal items, restricted requests, status, and completion time. Browser selectors and text remain untrusted data.

FillStep stores one field operation, verified source kind and ID, source status, sensitivity, protected local value JSON, expected value/file SHA-256, and completion state. Event metadata never stores the value. File steps resolve only the locked resume or optional cover letter. Submit controls never create steps.

FillCheckpoint stores a stable checkpoint ID and deterministic hash of the verified steps for one page. A paused session preserves completed pages. Completion deterministically creates FormInventory and stops before submission.

## Pre-submission review

FormInventory stores the form URL, observed job identity, known-form status, required field IDs, allowlisted legal items, mandatory-pause requests, upload version IDs, a deterministic hash, status, and invalidation metadata. It contains no field values.

PreSubmitReview stores one application and inventory, authorization and material-lock IDs, value-free summary JSON and SHA-256, generated/approved/invalidated status, timestamps, approver, and invalidation reason. Only the user may approve the exact summary hash. The application stores the active review ID while `pre_submit_ready` and submission revalidates it.

## Submission archive

ApplicationField records preserve the exact locally stored field value, its application and field IDs, question, source kind and ID, source status at use, sensitivity class, and recording time. Values remain in the protected database. Archive output applies the redaction contract in `submission-archive.md`.

A SubmissionArchive stores a stable archive ID, one application ID, immutable directory and manifest paths, manifest SHA-256, archive time, verification time, and verification status. Its manifest lists physical artifacts with byte size and SHA-256, submission metadata, evidence type, unresolved-uncertainty status, and aggregate redaction counts.

The master tracker is generated from archive, application, and job backend records. It contains no answer values and does not become a separately editable source of truth.

## Outcomes and usage

OutcomeRecord stores an outcome ID, application ID, allowed outcome type, timezone-aware occurrence time, source type, optional source-reference SHA-256, user-verification flag, ResumeVersion ID, application category, and creation time. It can be created only after the corresponding guarded application state exists.

ModelUsageEvent stores workflow, operation, model tier and name, token counts, optional micro-USD cost and latency, cache status, and optional application/job attribution. It never stores prompts or responses. UserTimeEvent stores a bounded activity, duration, source type, and optional application/job attribution.

Conversion reports contain backend-derived funnel counts, numerator/denominator/rate triples, basic dimensions, usage totals, and an explicit statistical-caution status.

## Future core entities

Future entities may extend browser form execution, cache management, and richer evaluation telemetry. Do not collapse them into existing records.
