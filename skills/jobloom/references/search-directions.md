# Search directions and resume adaptation plans

## Purpose

A SearchDirection is a user-approved routing and strategy profile. A ResumeAdaptationPlan is a deterministic, value-free explanation of how verified evidence may be emphasized for one reviewed JobCard. Neither object is a resume, and neither approval authorizes unreviewed file bytes.

This separation creates the required chain:

`approved direction → reviewed eligible JobCard → evidence plan → user-approved plan hash → immutable ResumeVersion draft → claims manifest → user-approved file`

## Direction profile

Create a private profile from `assets/search-direction.template.json`. It contains a stable ID, name, role family, target titles, positive/negative/precision keywords, bounded criteria, and optional approved parent direction.

Registration creates an immutable draft and deterministic profile SHA-256. Only actor `user` may approve that exact hash. A parent must already be approved. Use a new direction ID for a material change; never mutate an approved profile in place.

Direction criteria organize one search direction. They do not override CandidateProfile hard filters, work authorization, AnswerLibrary scope, or application safety gates.

## Deterministic adaptation plan

Generate a plan only when:

- the SearchDirection is approved
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

Revoking a direction invalidates generated/approved plans and every active material lock using its resume versions. Existing immutable files remain as local history but are no longer authorized for new application use.

## Privacy

Store direction profiles, plans, candidate artifacts, and resume snapshots under `.jobloom/`. Plans may contain job identity and evidence IDs but no CandidateFact values, resume text, secrets, or full job descriptions. Events contain hashes and counts only.
