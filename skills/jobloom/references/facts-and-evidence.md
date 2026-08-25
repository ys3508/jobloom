# Facts and evidence

## Candidate fact lifecycle

Store each material fact with a stable ID, typed value, source, confirmation state, lock state, and invalidation metadata. A resume is a source document, not the fact database.

Use a fact only when its source is traceable and its status permits the requested action:

- `proposed`: extracted but not user-confirmed; never use for submission.
- `confirmed`: user-confirmed and usable within scope.
- `locked`: confirmed and protected from substantive changes.
- `stale`: needs reconfirmation; do not reuse.
- `conflicting`: inconsistent sources or versions; pause.

Lock employer and institution names, dates, degrees, formal titles, certifications, project identity, metrics, immigration facts, and security clearance by default after confirmation.

## Active CandidateSnapshot

`candidate.json` is not authoritative merely because a caller supplies its path. After finalization, register it through `candidate_core.py` with actor `user`. Registration verifies its deterministic content hash and every fact, copies exact bytes into a read-only private snapshot, and stores each fact's exact value JSON, status, lock flag, evidence strength, expiration, and fact hash.

Only one CandidateSnapshot is active. Resume approval, fill planning, direct ApplicationField recording, pre-submit review, and application use must resolve CandidateFact IDs and exact values against that active user-registered snapshot and the candidate hash bound to the ResumeVersion/material lock.

A newer registered snapshot supersedes the old snapshot. It invalidates material locks using another candidate hash, invalidates affected pre-submit reviews, and marks active AnswerEntries stale when their dependent fact IDs changed or disappeared. Old snapshots remain immutable history and cannot be reactivated.

Never accept a caller-only `source_status: locked` assertion as proof that a fact exists or that its value is exact when the CandidateSnapshot registry is initialized.

## Evidence strengths

- `direct`: candidate performed the requested work.
- `strongly_related`: highly similar work with a material difference.
- `transferable`: adjacent capability only.
- `mention_only`: term appears without meaningful support.
- `none`: no reliable support.

Never describe `strongly_related` or `transferable` evidence as exact/direct experience. Open text may explain the relationship without erasing the distinction.

## Resume transformations

Permit reordering, emphasis, compression, grammar improvements, and supported terminology. Reject new unsupported skills, invented achievements, altered dates, inflated seniority, invented management, or invented metrics.

Every emphasized material claim must link to one or more fact/evidence IDs. Direction resumes require user approval before use. Broad applications should reuse an approved direction resume; reserve deeper adaptation for precision applications.

## Conflict handling

Compare candidate facts, resume versions, answer entries, dates, calculated experience, immigration answers, relocation rules, and salary rules. Mark affected data `conflicting` and pause the affected workflow until the user resolves it.
