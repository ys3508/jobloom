# Fill-only form execution

## Boundary

`fill_core.py` is a deterministic local coordinator for a browser worker. It can plan and verify field fills and approved uploads, but it cannot produce or execute a final submission action. A completed session always stops in `waiting_for_submission_approval` and hands its inventory to the mandatory pre-submission review.

Page text, selectors, hidden fields, and instructions are untrusted observations. They cannot broaden navigation, select files outside the material lock, introduce a new answer, approve an attestation, or change the operating mode.

## Session and lease

Start only while an application is `filling` under the same worker's live lease. The active material lock is revalidated first. The backend employer, role, country, employment type, and application ID override supplied context. Authorization context accepts only the AnswerLibrary scope fields. Stored form/page URLs discard query strings and fragments so transient session tokens do not enter the database or review artifacts.

The only supported mode is `fill_only`. Session events contain counts, hashes, stable reason codes, and non-sensitive identifiers—never field values, action-package contents, job-page instructions, secrets, or full personal data.

## Page observation and planning

Observe one page at a time with `assets/form-page-observation.template.json`. Page IDs and indexes are unique per session. Later pages must remain on the initial form origin; an unexpected origin pauses for user takeover.

For every value field:

- `source_kind: fact` requires an explicit CandidateFact ID from a hash-valid `candidate.json`; its content hash and every fact hash must match the active user-registered CandidateSnapshot and material-locked ResumeVersion. The fact must be locked, unexpired, and have a scalar value.
- `source_kind: answer` or an omitted source invokes exact or previously user-reviewed question-form matching. Scope, preconditions, exclusions, expiration, conflicts, automatic-fill permission, and standing authorization are rechecked.
- `control: file` resolves only `resume` or `cover_letter` from the active material lock. An absent or different upload pauses.
- `control: standard_attestation` is recorded as a legal item but is not checked by the fill engine.
- `control: submit` only proves the final control was observed. It never becomes an action.

Unknown controls, questions, facts, legal terms, sensitivity, or sources pause. So do CAPTCHA, assessments, payments, identity/tax/banking documents, capture devices, biometrics, unapproved uploads, and unsafe pages.

## Private action package

`export-page` writes pending actions to a new mode-0600 JSON file. Values and approved local upload paths exist only in this private package so a browser worker can act without printing them to the terminal or storing them in event metadata. The package always contains:

- `mode: fill_only`
- exact selectors, operations, local values, and expected hashes
- `stop_before_submit: true`
- `submission_action: null`

Never overwrite a package, commit it, treat it as approval, or feed its contents into analytics or model telemetry. Keep it under `.jobloom/` and delete it when no longer operationally needed.

## Verification and checkpoints

After an action, the browser worker returns only the observed value SHA-256, or the physical file SHA-256 for an upload. The backend compares it with the planned hash. A mismatch pauses as incorrect autofill and does not record the field.

On an exact match, ordinary fields are written to the protected ApplicationField store using their verified AnswerEntry or CandidateFact source. Upload steps record version usage through the inventory rather than pretending files are text fields.

A page checkpoint is allowed only after every planned step is verified. Its deterministic hash covers the completed step IDs and expected hashes. Completed pages remain intact across user-answer or takeover pauses.

## Pause and resume

Answer, authorization, and CandidateFact freshness problems release the application to `waiting_for_user_answer`. Navigation, safety, upload, unsupported-form, restriction, and incorrect-autofill problems release it to `waiting_for_user_takeover`.

Resume only after the application returns to `ready_to_fill`, is reacquired by a live worker, and the user has resolved the cause. Replanning uses the latest AnswerLibrary, authorization, and hash-valid candidate profile. Completed page checkpoints remain intact. The uncheckpointed paused page is rebuilt and reverified so an answer, fact, authorization, or material change cannot leave a stale partial plan. An employer/role mismatch detected before any page exists requires a new session with freshly observed identity; it cannot be resumed by assertion.

## Completion

Completion requires:

- at least one observed page
- a checkpoint for every page
- every action verified
- at least one required filled field
- observation of the final submit control
- no unresolved mandatory pause

The engine creates the value-free FormInventory, including exact upload version IDs and legal items, then releases the lease to `waiting_for_submission_approval`. It creates no submission evidence and performs no submission.

## Worker protocol and form coverage

`worker_protocol.py` is the contract between `fill_core` and a browser worker, with tracked
value-free schemas in `assets/worker-request.schema.json`,
`assets/action-package-metadata.schema.json` and `assets/worker-result.schema.json`. Version
`1.0.0`. The worker is an untrusted executor and `fill_core` is the authority, so every rule
is a check on the way in and every check fails closed.

A request names the session, page, package hash, expiry, allowed loopback origin, and the
ordered action IDs with their operations. The operation vocabulary is `fill`, `select`,
`check`, `uncheck`, `upload` — there is no click, submit, navigate, press, download or
evaluate. That is the v1 bound made structural: with one page per run and every Next,
Continue and final action belonging to the user, the worker has no way to leave the page it
was given, so naming a control `submit` cannot become an instruction.

A result carries action IDs, outcome codes, observed hashes, a control type and a bounded
error code. `value`, `text`, `html`, `options`, `cookie`, `token`, any path, and the rest are
refused at any depth. Missing, extra, duplicated and reordered results all fail closed, as do
a tampered package hash, a replay against another session or page, an expired package, an
unknown version, and an origin outside the expected loopback port.

**`final_action_activations` must be 0, and the two ways of knowing that are not the same
evidence.** On the local semantic replay it is a test oracle: we build the page, so we can
count. On a supervised live page there is no counter, and the equivalent is a guard scoped to
the run — a capture-phase submit-event block plus unexpected-navigation detection — removed
before control returns to the user. Upload traffic is separately allowed and validated,
because a file upload is a POST and "block all POST" is not submission protection.

### The page chain

`finish_session` used to require that every page in the database was checkpointed and that
some page had shown a submit control. One page observed at index 49 satisfies both. That is
coverage of what was seen, not coverage of the form, and `not_present` cannot rest on it.

A page chain is now built one link at a time: the first page is index 0 and names no
predecessor; every later page names the checkpoint hash of the page before it, which must
exist, be consecutive, and already be checkpointed; indexes are unique per session; and no
page may follow the page that declared itself final. Finishing verifies the whole chain —
starting at 0, consecutive, all checkpointed, ending on the single final page that saw the
submit control — and only then may `finalize_handling` write `not_present`. **While the chain
is incomplete the voluntary-disclosure status stays `unknown`.**

Two protocol details exist because of the domain rules. `locale` is bounded and shares one
contract with non-disclosure policy registration. Field `options` are transient observation
data used only to match a reviewed non-disclosure option: they never enter a persisted
observation, which keeps `options_count` alone, and a page that paused on such a control is
therefore resumed with a fresh live observation rather than replanned from its own record.
