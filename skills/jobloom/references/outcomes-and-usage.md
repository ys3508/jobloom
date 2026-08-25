# Outcomes, usage, and conversion analysis

## Verified outcomes

OutcomeRecord is separate from application state. First move the application through the guarded state machine; then record a structured outcome only when the corresponding state exists in application history.

Allowed outcomes are recruiter response, screening call, interview, final interview, offer, rejection, withdrawal, and no response. Post-application outcomes require a submitted application and an attributed resume version.

A user-confirmed outcome needs actor `user`. A system-recorded email, applicant-tracking-system record, or recruiter message requires a source reference. Store only the reference SHA-256, never message contents or raw external identifiers in outcome or audit records.

## Model usage

Record model usage as metadata only:

- workflow and operation
- model tier and model name
- input, output, and cached tokens
- optional cost in micro-USD and latency in milliseconds
- cache-hit status
- optional application and job IDs

Never store prompts, responses, candidate data, JobCards, or answer values in usage records. A `none` tier represents deterministic work and must have zero model name, tokens, cost, and latency. Cached tokens cannot exceed input tokens.

## User time

Record timer-derived or user-reported duration with a bounded activity category. Do not infer user time from browser or model latency. One event may be linked to an application or job.

## Funnel

Generate counts from backend job records and distinct guarded application transitions:

1. discovered jobs
2. hard-filter passes and recommendations
3. approvals
4. confirmed submissions
5. employer responses
6. screening calls
7. interviews
8. final interviews
9. offers

Rates always contain numerator and denominator. A zero denominator produces `null`, not zero. Current MVP dimensions are application category, ResumeVersion, resume direction, source, and ATS.

## Statistical caution

When fewer than thirty applications have been submitted, label the sample `insufficient_sample`. At any sample size, reports are descriptive: they do not prove causation and cannot authorize automatic strategy changes. Strategy changes always require user approval.

## Private storage

Keep outcome reports under `.jobloom/`. Reports contain application metadata but no answer values or model content. Never commit private reports by default.
