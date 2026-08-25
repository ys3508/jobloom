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
10. Future core entities

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

Resume extraction produces proposed facts, never immediately reusable facts. Each proposed fact contains a document SHA-256, line locator, excerpt SHA-256, evidence strength, and a `pending` decision. Every decision must become `confirmed` or `rejected` before finalization. Confirmed protected facts become locked; rejected facts are excluded. The final `candidate.json` includes a deterministic content hash.

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

A ResumeVersion stores an immutable snapshot path, file SHA-256 and size, kind, direction, parent version, status, candidate profile hash, claims-manifest hash, approval metadata, and revocation metadata. Kinds are `master_source`, `direction`, `lightweight`, and `precision`. Registration always creates `draft`; only the user actor may move it to `approved` or `revoked`.

The claims manifest maps each exact resume claim to one or more confirmed CandidateFact IDs. Its evidence strength cannot exceed the strongest supporting fact. A claim using any locked fact must assert exact locked-value preservation.

A material lock records the exact approved resume version and file hash selected for one application. It is required before `ready_to_fill` and is revalidated before filling, pre-submit readiness, and submission. Resume usage records preserve `prepared`, `locked`, and `submitted` use separately.

## Future core entities

Add separate archive-manifest and outcome records next. Do not collapse them into the job or application records. Every submitted application must eventually preserve exact resume/cover-letter snapshots and a redacted material-answer snapshot.
