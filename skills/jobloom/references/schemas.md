# MVP schemas

## Contents

1. Candidate profile
2. Candidate fact
3. Job input
4. Evaluation output
5. Future core entities

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
  "salary": {"currency": "USD", "min": 120000, "max": 150000},
  "status": "open",
  "sponsorship": "unknown",
  "citizenship_required": null,
  "security_clearance_required": null,
  "required_certifications": [],
  "required_skills": ["Python", "SQL"],
  "preferred_skills": ["AWS"],
  "already_applied": false,
  "high_value": false
}
```

Allowed sponsorship values: `supports`, `does_not_support`, `historical_support`, `unknown`, `conflicting`.

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

## Future core entities

Keep separate records for `AnswerEntry`, `ResumeVersion`, `Authorization`, `Application`, `ApplicationEvent`, and `SubmissionEvidence`. Do not collapse them into the job record. Every application must preserve the exact resume/cover-letter snapshots and material answer snapshot used at submission time.
