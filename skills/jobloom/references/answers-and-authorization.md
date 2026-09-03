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

Reviewed question forms may be applied in bulk from a manifest — `skills/jobloom/assets/question-forms.json` — with `answer_library.py import-question-forms`. This is safe to automate because a form is a question and a meaning and never an answer, which is also why such a manifest can live in the repository while every answer stays in the private root. The import is deliberately a command and not a side effect of connecting: a file appearing in a checkout is not a person deciding to trust it. It applies wholly or not at all, is idempotent so a manifest that grew by one form can be re-applied, refuses a paraphrase in bulk, and refuses a question this library already maps elsewhere. Provenance is required and is read as strictly as the manifest it vouches for: it must prove, per question, both the fixtures that recorded the wording and the meaning it was reviewed as. Checking the wording alone proves a question is real and proves nothing about what it means, which is how `Email address` could once be imported as `work_authorized_now` — the label existed, the fixtures matched, and the mapping was nonsense. A manifest calling itself `exact` and user-verified is not a person confirming it. The approval is checked against the corpus it names, by reading it. Each recorded fixture is parsed and its controls compared with the approval in both directions — no control unreviewed, none reviewed twice, none reviewed that nothing records — and each entry's wording, role and digest must equal what the control actually holds. A digest alone proves a file has not changed and proves nothing about whether the approval describes what is inside it: with every hash correct, an approval could rename a control's wording and a manifest could use the renamed wording, and the two files would agree with each other about a form no employer ships.

What may be imported is pinned in code — `answer_library.TASK14_QUESTION_FORM_TARGETS` — because a permission an input file can widen is not a permission. The value-free intake plan is checked against a pinned expected shape rather than trusted to define anything: which meanings, which answer type, which scope exactly, which validity class, which application. A copy of the plan with one meaning added is refused, and so is one with a meaning dropped so the remainder would import quietly. It is written as a whole shape rather than a list of rules because the rules were patched one at a time and the gaps between them were the holes — a plan moved wholesale to another application passed every rule there was, since the entries agreed with the top-level id and nothing said which application had been reviewed. Widening the set is an edit under review, never an argument. A reviewed disposition is a floor beneath the pin rather than the permission. `answer` and `fact` between them cover every contact fact, every profile URL and every employment fact the corpus records, so gating on disposition alone admitted `profile.github`, `profile.website`, `profile.portfolio`, `contact.full_name` and `employment.current_company` beside the three contact meanings that were actually reviewed. A control reviewed as `always_manual` can never become a question form under any permission, because the whole purpose of that disposition is that the question reaches the user — a half-applied vocabulary pauses on some questions and not others, which is worse than none. What it returns and what it writes to the audit log name meanings and counts and hash the wording.

Resolve multiple applicable answers by scope specificity. A more specific applicable scope overrides a global answer. Two equally specific active answers with different values are a conflict and must pause.

## How an answer gets into the library

`answer_library.py propose-answers` and `confirm-answers`. Everything downstream of a
confirmed answer was built and tested long before this existed; the step where a person is
asked the question and says yes was a hand-written JSON file, so the library held nothing and
an application sat unfilled.

A round is a **named set** of meanings, not an argument. Proposing reads the pinned intake
shape, reads the facts a meaning depends on, and writes a worksheet at 0600 in the private
root carrying a proposed answer for the user to check — a proposal rather than a blank form.
Nothing is written to the library, and the terminal gets meanings and counts.

Confirmation is **per entry and never inferred**: `confirmed_by_user` false leaves that entry
out entirely, which is a choice the user is allowed to make and not a half-finished worksheet.
Confirming an entry with nothing in it is refused rather than filed as the user's word.

The worksheet cannot be swapped, edited into another shape, or replayed. The proposal is
recorded in the database with a hash over the reviewed arrangement — meaning, answer type,
scope, dependency — and deliberately **not** over the answer, since writing an answer there is
the point. It is single-use, because replaying a worksheet is how a value the user has since
replaced comes back. It is bound to the snapshot it was proposed from, because a proposed
value describes a fact as it was when it was read.

Writing is atomic, in one explicit transaction rather than by hoping. A library holding three
of five confirmed answers is one where nobody can tell which two are missing on purpose, and
`add_answer` commits per row — so a batch built on it could not be undone, and an interruption
on the third answer left the first two on file. The batch uses an insert that does not commit,
claims the proposal inside the same transaction, and writes the answers, the supersessions,
the audit row and the proposal's own status together or not at all. Claiming inside the
transaction is also what makes two callers racing on one proposal produce one set of answers
rather than two.

Every read the decision rests on happens inside that transaction, not only the writes.
Reading the active snapshot before it began left a window in which another connection could
register a new profile after the check passed, so a worksheet proposed against the old one
would be confirmed against the new; re-checking afterwards would not have closed it either,
as long as any authorising judgement had already been made.

A worksheet belongs to one proposal. The digest covers its `proposal_id`, and that alone was
not enough: two proposals of the same round over the same snapshot are otherwise identical
documents, so a swap changes the id and the recomputed digest together and lands exactly on
the other's stored hash. Each proposal therefore carries a nonce, recorded in the database and
not derivable from anything in the worksheet.

The worksheet itself is created where private things belong, exclusively, already unreadable:
inside the private root, with `O_NOFOLLOW` so a path replaced between the check and the open
is refused rather than followed, with every component between the root and the file walked as
given rather than as resolved — resolving is exactly what removes the links one is looking
for, and a link pointing elsewhere inside the root resolves to a contained path — refusing to
replace a file somebody may be part-way through, and with the mode given at creation rather
than narrowed afterwards, which is a window in which anyone on the machine can read it.

The file is written inside the proposal's transaction and the commit is inside it too, so
neither can outlive the other: a commit that failed after the file was written used to leave a
worksheet that can never be confirmed and looks exactly like one that can. On that failure the
file is removed — the exact file that call created by exclusive open, and nothing else.

Two worksheet fields are the user's: `answer` and `confirmed_by_user`. Everything else, down
to the headings, is covered by the digest — a worksheet whose explanation could be edited is
one that can be made to ask a different question than the one it records, and
`canonical_meaning` went straight into the database, so editing it relabelled a meaning. The
meaning written is taken from the validated plan and never from the worksheet. Confirming a value already on file changes nothing rather than creating a second
active answer for one meaning — which `match_answer` reports as a conflict — and a changed
value supersedes the old one instead of joining it. `auto_submit_allowed` is false on
everything this writes, without exception.

Answers live only in the private root. The command's output, the audit log and every refusal
name meanings and counts; none of them carries a value.

## What makes a stored answer go stale

`candidate_core.register_snapshot` compares the old and new snapshots fact by fact and marks
any active answer stale when a fact it named in `dependent_fact_ids` has changed. The
comparison walks the **union** of the old and new fact ids, so a fact that was deleted or
renamed counts as changed too — the dependency cannot be left dangling while the old value
keeps filling forms. It is per-fact: editing an unrelated fact leaves the answer alone.

Naming the dependency is what connects an answer to that chain. An answer with an empty
`dependent_fact_ids` is never invalidated by a profile edit, whatever else it declares, and an
answer naming a fact the active snapshot does not hold can never be invalidated at all,
because the intersection is empty forever — so that is refused when the answer is written.

`invalidation_triggers` is the other half of the schema and currently has no production
emitter: `invalidate_by_trigger` is reached only from an operator CLI. Answers written today
therefore leave it empty rather than declaring a trigger nobody raises. A dependency that
looks handled and is not is worse than one that plainly is not, and one chain that runs beats
two that look complete.

**Recorded conservative behaviour.** The candidate's email, phone and LinkedIn URL live in one
composite `contact` fact, so changing any one of them marks all three answers stale together.
Splitting that fact would need a new CandidateSnapshot and the re-approval of every resume
version bound behind it. Over-invalidating costs a re-confirmation; under-invalidating fills
an employer's form with a value the user has replaced.

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
reviewed. A page supplies both halves of an option, so a page can lie about either:
`<option value="Asian">Prefer not to answer</option>` offers the reviewed label and submits a
category. Not being able to read a value is not the same as the value being safe, so the pair
must be **checkable, not merely opaque** — and checkable by provenance, not by address. A
loopback URL is a network location: any local process can listen on `127.0.0.1` and compute a
published hash. Trust therefore requires a `replay_surfaces` record the backend issued when it
started the renderer, bound to that exact origin and carrying a **session nonce** that only
Jobloom and its own renderer hold; option values are derived from the label under that nonce,
so a rogue local server cannot produce a matching pair. The record expires and is revoked when
the server stops. Any other surface needs an approved adapter mapping with a fixed hash and
version; none exists, so the control is the user's. One reviewed label mapping to more than one value also pauses. Classification runs before every source branch including uploads, so a control
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
**`not_present` is never written in v1.** A completed page chain is self-reported — index,
predecessor hash and final-page flag all come from the observer — and an observer that never
saw a page cannot report its absence. Naming the evidence honestly made the weak claim honest
without making it strong, so the status stays `unknown`. "No voluntary-disclosure control
appeared in the self-reported chain" is reportable; "this form has none" is not. The marker
stays in the vocabulary because a surface that can enumerate a whole form could one day earn
it.
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
