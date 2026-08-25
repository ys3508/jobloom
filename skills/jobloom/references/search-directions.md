# Search directions and resume adaptation plans

## Purpose

A SearchPortfolio is the user-approved weighted strategy across multiple job families. Each SearchDirection remains an independent routing and resume boundary. A ResumeAdaptationPlan is a deterministic, value-free explanation of how verified evidence may be emphasized for one reviewed JobCard. None of these objects is a resume, and no approval authorizes unreviewed file bytes.

This separation creates the required chain:

`approved portfolio → member direction → reviewed eligible JobCard → evidence plan → user-approved plan hash → immutable ResumeVersion draft → claims manifest → user-approved file`

## Weighted portfolio

Register one to twenty immutable SearchDirections, then create a private portfolio from `assets/search-portfolio.template.json`. Every allocation names the exact registered direction ID and profile SHA-256. IDs must be unique, weights are positive integers, and the total must equal 100.

The portfolio is the single user approval surface. Approval requires actor `user` and the exact displayed portfolio SHA-256; it atomically approves unchanged draft member directions. A newly approved portfolio supersedes the previous active portfolio. Individual directions stay separate so jobs and ResumeVersions cannot cross role-family boundaries.

Portfolio registration and approval never invent a direction, alter a profile, approve resume bytes, or grant application/submission authorization. Revocation removes the active routing scope and invalidates affected plans and material locks.

## Direction profile

Create each private profile from `assets/search-direction.template.json`. It contains a stable ID, name, role family, target titles, positive/negative/precision keywords, bounded criteria, and optional approved parent direction.

Registration creates an immutable draft and deterministic profile SHA-256. Only actor `user` may approve that exact hash. A parent must already be approved. Use a new direction ID for a material change; never mutate an approved profile in place.

Direction criteria organize one portfolio member. They may narrow but never widen CandidateProfile hard filters, work authorization, AnswerLibrary scope, or application safety gates. `route_job` reports a status for every criteria key on every call, so a key that cannot be enforced says so rather than implying enforcement: `industries`, `company_sizes`, `target_companies` and `travel_limit` are `no_jobcard_field` because no JobCard carries them, and `seniority` is `title_token_gate_only` because the registered lists hold job families rather than levels. A null travel limit means travel does not automatically reject a job. An empty criteria list means no constraint, never "nothing allowed".

Field scope belongs to code, term lists belong to the profile. Each keyword group matches only its own allowed JobCard fields, and a group cannot widen its own scope by being added to a profile. `target_titles` and `auxiliary_titles` match the `title` field only: a target title appearing in responsibilities or skills is a contextual reference that routes to review, never a title match. Terms match as contiguous token runs, so `sales` does not match `Salesforce`, `CRO` does not match `Microsoft`, and a hyphenated exclusion such as `commission-only` still catches `commission only`.

`hard_exclusion_keywords` are hard-stop phrases, but only in the structured fields `title`, `employment_type`, `compensation_structure` and `required_certifications`. The same phrase found in `summary` or `responsibilities` raises `hard_exclusion_context_review` instead, because prose carries negation the matcher cannot read: "we do not offer commission-only compensation" describes a salaried job. That reason is deliberately distinct from `direction_soft_negative_keyword`: it is an unresolved hard exclusion awaiting a human read, not an ordinary demotion, and it is never auto-resolved to safe. Every hit records its term, its field, and `matched_excerpt`, the bounded sentence carrying it, so a reviewer can see the negation. Automatic negation detection may be added later, but until it is reliable a hit may never be judged safe without a person.

A target title that names its own industry ("Clinical Data Analyst") carries its context with it. A bare title that does not ("Sales Operations Analyst") must find the direction's declared `criteria.industries` elsewhere in the job — title, summary, responsibilities, required skills, employer name, or one of the direction's own positive keywords — before it can reach `match`; otherwise it routes to review as `target_title_without_direction_context`. This is what keeps moving a qualifier out of a target title from silently widening the direction. A direction that declares no industries imposes no such requirement. `negative_keywords` are soft demotion/review signals. `discovery_keywords` find jobs and expose skill gaps but never create CandidateFacts. `auxiliary_titles` route a job to review, with or without industry context, and never fail it.

Seniority is read as title tokens with domain guards, so `Senior Care`, `Lead Generation`, `Principal Component` and `Staff Nurse` are not rank signals. Stated 0-3 year experience is read from the structured `experience` field and is never inferred from a title. Credentials are compared generally against `candidate["certifications"]` with no hardcoded licence list: a required credential the candidate does not hold fails whatever it is, a preferred one never rejects, and a credential held under a different spelling passes with a review reason asking the user to add the alias.

Apply routing before portfolio allocation. Use the portfolio weights over a rolling pool of about twenty jobs entering review, not as a daily quota. Sponsorship is a core ranking signal: explicit support is strongest, historical support is next, unknown remains eligible for investigation, and explicit non-support fails when the candidate will require sponsorship.

## Persisted routing records

`record_routing` routes one JobCard against one approved direction and stores the decision with the exact JobCard hash, direction profile hash, and active portfolio ID. It is idempotent per job, direction, and JobCard hash: re-recording an unchanged card returns the stored record. A changed JobCard invalidates the previous record for that job and direction with reason `job_card_changed` rather than mutating it, so history stays append-only and auditable.

Only `match` and `review` decisions enter the review pool. A hard failure is still persisted for audit, with a null `entered_pool_at`, and is invisible to allocation: a portfolio weight can therefore never rescue a job that routing rejected. `portfolio_allocation_status` computes deficits from that persisted pool rather than a caller-supplied list, ordering only jobs that already passed routing, and surfaces the job IDs whose sponsorship needs investigation.

Routing events record the job ID, JobCard hash, and counts only. Matched text, token offsets, and sponsorship evidence segments may be shown to the user but are never written to an events table.

## Deterministic adaptation plan

Generate a plan only when:

- the SearchDirection belongs to the active approved SearchPortfolio
- the backend JobCard has `requirements_reviewed: true`
- the job title matches an approved target title
- deterministic hard filters and evidence evaluation yield `pass`
- the recommended action is `broad` or `precision`
- an approved, hash-valid master or direction resume exists

The plan contains no CandidateFact values. It records evidence IDs and original strength, the selected base resume, supported terminology, terms missing from the base claims, transferable-only and unsupported requirements, locked fact IDs that must remain exact, and allowed/forbidden transformation lists.

Recommended outcomes:

- `direction`: build the first reusable direction resume from the approved master.
- `direct_reuse`: use the existing direction resume without creating a file.
- `lightweight`: create a limited child only for supported terminology missing from the direction resume.
- `precision`: create a deeper evidence-constrained child for a high-value eligible job.

Transferable, mention-only, and unsupported evidence never enters the supported-terminology list and never becomes a direct claim.

## Two approvals

Plan approval and ResumeVersion approval are independent:

1. Show the plan to the user. Approval requires actor `user` and the exact displayed plan SHA-256.
2. Revalidate candidate profile, direction profile, JobCard, and base resume hashes.
3. Prepare the actual file according to the approved plan. `direct_reuse` skips this step.
4. Register the new version with its exact plan ID and base parent. The recommended kind and direction must match.
5. Show the rendered resume, material changes, removals/compression, reordering, attention claims, and evidence manifest to the user.
6. Approve the immutable file through `resume_core.py`. This independently rechecks candidate/plan/direction/job hashes and every claim.

A changed candidate profile or JobCard blocks plan approval. A later JobCard change does not rewrite an already approved physical resume; its claims remain governed by CandidateFacts, manifest, direction authorization, and revocation.

## Application enforcement and revocation

After `direction_core.py init`, master-source resumes remain provenance artifacts and cannot be selected, bound, or locked for an application. Derived resumes require an approved matching plan and active approved direction.

Revoking the active portfolio invalidates generated/approved plans and every active material lock using its member resume versions. Revoking an individual direction has the same effect for that member. Existing immutable files remain as local history but are no longer authorized for new application use.

## Privacy

Store direction profiles, plans, candidate artifacts, and resume snapshots under `.jobloom/`. Plans may contain job identity and evidence IDs but no CandidateFact values, resume text, secrets, or full job descriptions. Events contain hashes and counts only.
