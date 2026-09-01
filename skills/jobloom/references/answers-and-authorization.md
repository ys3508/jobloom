# Answers, authorization, and pauses

## Answer reuse

Reuse an answer only when all checks pass:

1. The normalized question meaning is exact or previously reviewed as equivalent.
2. Country, company, role family, employment type, and other scope restrictions apply.
3. The answer and its dependent facts are active and unexpired.
4. No newer fact or answer conflicts.
5. The answer category permits automatic fill for the active operating mode.

Otherwise ask the user and offer to save the answer as one-time, global, scoped, conditional, or always-ask.

Keep these meanings separate:

- currently authorized to work in the country
- requires sponsorship now
- will require sponsorship in the future
- requires H-1B transfer or another employer action

Never use one as a proxy for another.

Use these canonical IDs for the four immigration meanings:

- `work_authorized_now`
- `sponsorship_now`
- `sponsorship_future`
- `employer_action_required`

Exact matching uses normalized text only. Semantic matching is not an open-ended similarity search: store a paraphrase as a verified question form only after the user confirms equivalence. A question form mapped to multiple meanings is a conflict.

Resolve multiple applicable answers by scope specificity. A more specific applicable scope overrides a global answer. Two equally specific active answers with different values are a conflict and must pause.

## Two-channel freshness

Both channels must pass:

- Channel A: standing authorization is current, scoped to this queue/action, and not revoked.
- Channel B: every individual answer and dependent fact is current, applicable, and conflict-free.

Channel A never extends or overrides Channel B. Immigration answers bind to real-world expiration dates and are rechecked whenever used. That recheck is enforced, not advisory: an immigration answer is auto-filled only when its scope names the application being filled, so a broadly scoped one pauses for the user at match time with `immigration_recheck_required` instead of filling and failing the pre-submit review later. Matching without an `application_id` in context pauses for the same reason.

Standing authorization expires no later than fourteen days after confirmation in the MVP. Revocation takes effect immediately. Authorization scope may include country, jurisdiction, company, role family, employment type, application, or approved queue.

## Attestation gate

Auto-check a standard truthfulness attestation only when Channel A is current and every covered field is a fresh locked fact or active answer. One stale, unknown, expired, or conflicting field returns the attestation to the user.

Always pause for arbitration, non-compete, IP assignment, special background-check terms, or signatures outside a standard application attestation.

The gate must query stored answer state by answer ID. Do not accept a browser or caller assertion that an answer is active. An empty covered-field set cannot pass the gate.

## Mandatory pauses

Pause for new or ambiguous questions, expired answers, conflicts, page/job-card discrepancies, CAPTCHA, assessments, payments, identity/tax/banking documents, camera/microphone/biometrics, unapproved uploads, unsafe pages, and uncertain submission outcomes.

Submission uncertainty is terminal pending user review. Never retry automatically.

## Field dispositions

Not every form field is an answer. `field_policy.disposition` returns one of five —
`fact`, `answer`, `material`, `always_manual`, `unsupported` — from the field's identifier,
question text, control and declared source, deterministically and with no model. Page text is
untrusted, so it is read in one direction only: a match may add caution and never remove it. A
page that declares a race question as `source_kind: "answer"` still lands in `always_manual`.

The audit behind these rules is `docs/lever-first-form-readiness.md`: of 27 controls in the
reviewed Lever fixture, 2 resolve to career evidence.

**Voluntary EEO.** Race, ethnicity, gender, disability and veteran self-identification values
are never stored, read, hashed, archived or placed in a package. A separate
`nondisclosure_policies` row — not an AnswerEntry — may select a reviewed non-disclosure
option, and only on an exact match against the option's **label**, which is what a person
reviewed. What gets submitted is the option's **value**, the page's own opaque string, carried
through unread — so a label match is evidence that the control offers a non-disclosure choice,
never evidence about what the form would send. One reviewed label mapping to more than one
value pauses. Classification runs before every source branch including uploads, so a control
declared `file` cannot outrun it. The row stores a **token and a vocabulary version**, never
a page-facing string: `NONDISCLOSURE_VOCABULARY` lives in code, is versioned, and is reviewed
per locale, because a free-text allowlist is precisely how a real demographic value would
reach the database. The page's option strings are never persisted either, only their count.
Zero or multiple matches pause. Selecting "prefer not to answer" discloses nothing, which is
why it may be automated at all while a value may not. Reason codes hash the field identifier
regardless of declared sensitivity, so neither question nor answer is legible in a pause
record.

A completed non-disclosure step records a **handling marker** — `policy_declined`,
`user_handled`, or `not_present` — in `nondisclosure_handling`, and never an
ApplicationField: `record_field` still accepts only `fact` or `answer`, because an
ApplicationField stores what was entered and here Jobloom must not be able to say.

Each marker carries the evidence it rests on, and **no marker may be inferred from something
not happening**. An empty handling table is `unknown`, not `not_present`: it is equally what a
half-observed form, a missed control, an abandoned session, or a failed write looks like.
`not_present` is written only by `finalize_handling`, from `finish_session`, where every page
is known to have been observed and checkpointed, and it stores a hash over those observations.
`user_handled` is written only by `confirm_user_handled`, which requires the user as actor: a
control that stopped appearing in the next observation may have been completed by the user, or
missed by the observer, or re-rendered away, and those are not the same event. The pre-submit
review carries a summary whose status may be `unknown`, so the blind spot is stated rather than
discovered as a short field list. Because option strings are never stored, a page that paused on one of these
controls cannot be replanned from its own record: resuming it requires a fresh live
observation, or the policy the user just registered would be silently skipped.

**Compensation.** `compensation.total_range` is a radiogroup whose bands are defined by each
employer, so it is always manual. The direction criteria `salary_floor` is a search filter in
another subsystem and never resolves an application answer. Comparing an advertised range
against the floor is fit and eligibility, not a fill answer.

**Employer conflict.** "A relative works here" and "I am a customer, partner or reseller" are
separate canonical meanings and may never share an answer. Derivation requires a user-approved
employer entity, the relationship type, and a conflict registry the user has explicitly
certified complete and in date. `jobs.normalized_employer` is a deduplication and search key:
it may propose a candidate, never establish identity. **A registry miss is `unknown`, never
`No`.** None of those objects exists, so every conflict field is manual today.

**Sponsorship.** One broad control may not stand in for four canonical meanings, and **page
wording may not settle which meaning is being asked**. Every sponsorship question pauses as
`sponsorship_meaning_ambiguous` regardless of which canonical answer would have matched. An
earlier version resolved the question when its wording named one point in time, which let
untrusted page text lower caution: an employer writing "now" while meaning "now or in the
future" would have had a narrower answer submitted than the question asked for.

**Discovery source.** How the user heard about a role is their statement. It is never inferred
from the posting URL, the ATS host, or which collector surfaced the opening — those record how
Jobloom found the job, a different fact about a different actor. Only a user-confirmed
`application_specific` or `conditional_preference` answer resolves it.
