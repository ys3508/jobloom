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
