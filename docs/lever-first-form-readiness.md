# Lever first-form readiness

Recorded 2026-08-31 from the reviewed semantic Lever fixture in
`neonwatty/job-apply-plugin`. This is a product-domain audit, not proof that a live Lever adapter
works. Counts describe the 27 controls in that fixture; employers may add different questions.

## What the fixture revealed

| Domain | Controls | Current Jobloom authority | Decision before worker protocol freezes |
| --- | ---: | --- | --- |
| Contact/profile/location | 11 | Candidate facts can represent values, but contact coverage and rendered mappings require audit | Define explicit contact/profile fact kinds and prove material-lock snapshot access |
| Career evidence | 2 | CandidateFact/EvidenceUnit | Keep evidence-bound; current/prior company questions require exact canonical meaning |
| Resume material | 1 | ResumeVersion/material lock | PDF-only gate and physical hash validation are prerequisites |
| Authorization/immigration | 4 | Four separate canonical answers exist | Broad upstream kinds must disambiguate or pause; never substitute meanings |
| Compensation | 2 | `salary_floor` preference exists, but no application-answer model | Make employer-defined `total_range` choices always-manual in v1; do not infer either field from salary floor |
| Employer-specific conflict | 2 | Answer scope supports `company` and `application_id`, but no approved employer entity model | Derive only from an approved employer identity and a complete, fresh conflict registry; otherwise pause |
| Discovery source | 1 | No canonical application-answer policy | Treat as user-confirmed application-specific/conditional preference; never infer acquisition source |
| Voluntary EEO | 4 | Voluntary answers could currently be stored/filled | Never store demographic values; optionally apply a separate exact-match non-disclosure policy |

## Compensation is not career evidence

`compensation.total_range` is a required radiogroup in the reviewed fixture. Its brackets are
defined by the employer, so Jobloom cannot map a user-owned number to one of them without making
a negotiation choice. It is always-manual in v1. `compensation.target_salary` is an optional
textbox and is deferred until a protected compensation-stance model exists.

That later model must hold its numeric value in protected storage. “Value-free” applies to logs,
events, packages, and archives, which carry only an opaque reference and approved hash; it cannot
describe the stance record itself. A future model needs:

- currency and compensation basis;
- desired base vs total compensation;
- range, floor, target, and willingness to answer;
- geography, employment type, level, and application scope;
- source `user_confirmed` or explicit deterministic derivation from a user rule;
- review/expiration policy because market and priorities change;
- no derivation from the posting's advertised maximum;
- no use of `salary_floor` as the submitted expected-compensation answer without an approved rule.

Until that model is approved, target salary also pauses. Comparing an advertised range with the
user's search floor is a separate fit/eligibility signal and never supplies an application answer.

## Employer-specific conflict answers

The library technically accepts both `company` and `application_id`, but neither a free company
string nor `normalized_employer` is a stable employer identity. The latter is intentionally a
deduplication/search key: name variants can still produce `review`, and parent/subsidiary identity
is unresolved. Therefore:

1. page text cannot supply scope; `normalized_employer` may propose a candidate but cannot decide;
2. the reusable object is a user-maintained conflict registry keyed to user-approved employer
   entities and separate relationship types, not a global No answer;
3. absence from that registry means No only while an explicit user certification that the registry
   is complete remains fresh; otherwise absence means unknown and pauses;
4. the application answer is deterministically derived and recorded with `application_id`, the
   approved employer-entity ID, registry version, certification, and derivation hash;
5. renamed employers, parent/subsidiary ambiguity, uncertain aliases, stale certification, or a
   differently worded relationship question pause;
6. “related person” and “customer/partner/reseller” are separate canonical meanings;
7. no standing authorization turns these into global answers.

## Voluntary EEO boundary

Race/ethnicity, gender, disability, and veteran self-identification are optional protected
disclosures. Jobloom will not:

- create AnswerEntries for their values;
- read a previously selected value;
- include a value/hash in an action package or result;
- archive or report the value;
- infer a value or treat a demographic choice as reusable.

Jobloom may separately store a revocable, user-approved non-disclosure policy. It is not an
AnswerEntry and contains no demographic value. The worker may apply it only when a visible option
exactly matches a reviewed, locale-specific allowlist such as “Prefer not to answer”; fuzzy or
model matching is forbidden and absence/ambiguity pauses. Pre-submit review and the archive
manifest record only `voluntary_disclosure_handling: policy_declined`, `user_handled`, or
`not_present`, never the selected value or its hash. This explicitly documents the intentional
archive blind spot.

## Required pre-protocol tests

1. conflict derivation requires an approved employer entity, fresh complete-registry certification,
   relationship type, and backend application context;
2. missing certification, parent/subsidiary ambiguity, and unapproved aliases fail closed;
3. the two conflict questions cannot share an answer;
4. salary floor never resolves an expected-compensation field;
5. `compensation.total_range` is always-manual and salary stance cannot select its bracket;
6. all four authorization meanings reject broad sponsorship substitution;
7. every voluntary EEO kind is manual unless an exact reviewed non-disclosure option matches;
8. no EEO value, value hash, AnswerEntry ID, or selected demographic choice reaches an action or result;
9. no EEO value/hash enters storage, logs, events, packages, or archives, while handling markers do;
10. worker actions cannot trigger submit or navigation; every Next/Continue/final action is user-owned.

## Product order consequence

The minimum domain order is now:

```text
PDF material gate
-> first-form domain policy (compensation, conflicts, EEO, sponsorship mappings)
-> worker protocol
-> semantic replay renderer
-> one-page direct Playwright worker
-> scoped live submit/navigation guards plus fixture final-action oracle
```

Building the protocol first would freeze an incomplete answer/source vocabulary around a form the
current core cannot safely finish.
