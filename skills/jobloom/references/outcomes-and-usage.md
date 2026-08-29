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

## Jobs kept for later, and what the funnel can honestly count

`saved_jobs.py` records the one decision a person would press a button for: *not now, keep
it*. Skipping a job means moving to the next one, so a control meaning "do not apply" would
be pressed by nobody and is not offered.

It follows that the funnel's denominator is jobs **kept**, never jobs **seen**. Reporting a
view rate would require logging every posting the panel opens, which is a different promise
from the one it makes, and nothing infers one from what is recorded here.

A kept job is not an application: an application row describes what happened after something
was sent, and a kept job has no after. They live in separate tables and separate sheets,
joined on the posting's URL when the tracker is built, so a job later applied to is reported
from each side once rather than counted twice.

Two dates travel with a kept job, both stated by the employer: when the posting opened, and
its deadline where one was given. Days open is computed when the sheet is written rather
than stored, because it changes daily. **No "apply by" date is derived.** Employers state a
deadline on a small minority of postings, and the interval that would make one up for the
rest — apply within N days — is not a number this system has measured. It becomes available
when the user's own reply data can calibrate it, and not before.
