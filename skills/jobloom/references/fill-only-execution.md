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

- `source_kind: fact` requires a `canonical_id` — what the field *means*, never an internal CandidateFact ID. A browser has never heard of `fact-0002` and must not be the thing that decides which fact a form field reaches, so an observation carrying `source_id` is refused outright rather than accepted as a legacy path. The planner resolves the meaning with `candidate_profile.resolve_canonical_fact` inside the snapshot the application's materials are locked to, and each way of not resolving keeps its own reason: `profile_field_unknown`, `profile_meaning_not_profile_data`, `profile_fact_missing`, `profile_fact_ambiguous`, `profile_fact_not_locked`, `profile_fact_expired`. Two facts claiming one meaning fill neither. Everything after the resolution is unchanged: the action package names the resolved CandidateFact ID, the value hash is exact, and import re-checks the fact through the material lock and the active snapshot. `canonical_id` is valid only on a fact field.
- The content hash and every fact hash must match the active user-registered CandidateSnapshot and material-locked ResumeVersion. The resolved fact must be locked, unexpired, and have a scalar value.
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

## The worker

`fill_worker.py` sits outside `fill_core` because it is the part `fill_core` does not trust.
It reads no database, no AnswerLibrary and no CandidateFact, consumes exactly one
already-verified private package, discovers no file path — an upload path can only come from
the package — and returns hashes and closed codes rather than claims.

Most of the boundary is structural rather than promised. The operations are fill, select,
check, uncheck and upload; there is no click, Enter, navigate, press, download or evaluate, so
Next, Continue and the final action are not refused by a rule that could be argued with —
they are not expressible. No JavaScript of any origin runs: control identity, visibility,
enablement, uniqueness and type agreement are all decided with selectors, and `page.locator`
searches the top frame only, so acting inside a frame is impossible rather than forbidden.
Every one of those checks runs immediately before the control is touched, because a page can
re-render, duplicate, detach or retype a control between observation and action.

Trust in the surface does not come from the address. `127.0.0.1` is a network location and any
local process can listen on one, so the package carries an attestation `fill_core` wrote from
a `replay_surfaces` record it holds: the exact origin, the renderer version, and the digest of
the page this run is expected to load. The worker hashes the **response body** — not
`page.content()`, which is the browser's normalised DOM and would never match — and touches
nothing if it differs. The nonce is deliberately not in the package; the worker has no use for
it and a package is a file on disk.

A package is not self-authorising, and it is not self-verifying either. An earlier attempt
signed a grant with an HMAC and wrote the key into the same file as the signature, which
authenticates nothing: an attacker never needed a real grant, only a secret of their own and a
package to sign with it. **A verifying secret cannot sit beside the object it verifies.**

So verification is not a document. `fill_core` issues an **execution grant** — a row in the
protected store naming one package digest, with an expiry — and the worker redeems it through
`execution_authority`, a loopback service with one endpoint, a per-run bearer token, and no
answer it can give without consulting the authority's own database. A package the authority
never exported has no row, and no amount of local file writing creates one.

Issuance itself is checked, because the first version signed whatever bytes the caller pointed
at after confirming only that the session existed. A grant is issued only when the digest is
one `export_page` recorded, the session, page and application identity match, the actions are
exactly the pending steps in order with their values and expected hashes, and the surface is
the attestation that is live now. An added action, an edited value, a changed hash or a
swapped surface each refuse.

The redemption token travels in a 0600 capability file, never in `argv`: process arguments are
visible in the process list to other users and to anything running `ps`. **The mode excludes
other Unix users; it does not exclude a hostile process running as this same user**, which can
read the file — closing that would need a different OS identity, a sandbox, or an inherited
descriptor, and no file permission substitutes for one. The token stops an unauthenticated
caller, and that is the whole of the claim.
Revocation reports which of four things happened — unknown, already revoked, already consumed,
or revoked — because returning success for a grant that never existed is an absence turned
into a fact.

Redemption is two phases, because everything that can still refuse a run happens after the
parameters are known. **Reserve** holds the grant briefly and returns them; the target's
counter baseline is read; only then does **consume** spend it. Consuming first burned the
grant on failures that had touched nothing — an unreadable counter left the user an error that
looked retryable and a grant that never could be. A reservation lapses on its own, so a worker
that dies holds nothing and the grant runs later.

Both phases are single conditional updates, and both clocks are conditions of the statement
rather than checks before it. Reading a row and then writing it let two authorities each see
"not held", each write, and each report success — and consuming while checking only the
reservation string let a hold taken at T0 be spent after its own window and after the grant's
had closed, two expiry guarantees that existed only in the column names. The winner is
whichever update matched a row; a `SELECT` afterwards only names the reason.

The capability file's authority URL is held to one exact shape —
`http://127.0.0.1:<port>/reserve` — with no hostname variants, userinfo, query, fragment or
other path. Without that check a misconfigured or hostile file would send the token, the grant
id and the package digest to any address on the network, and then act on whatever that address
called an authorisation.

Consumption is where single-use lives. It is an atomic update of that row, not a marker beside
a file path, so copying the package and re-running buys nothing: the second consumption
updates zero rows. The response carries the parameters the run may use — target, origin, renderer
version, page digest, and the oracle URL — and the worker uses those instead of the package's
own account of them. **The oracle is a capability of the attested surface**, never a
caller-supplied URL, because any service can return a constant zero.

The residual limit, stated rather than hidden: a worker trusts the authority it is pointed at,
so a caller who can already choose that address has already chosen everything. The property is
narrower and real — a genuine authority refuses a package it never issued, refuses it twice,
and refuses it after expiry or revocation.

Scoped guards go in before the first action and are removed by **destroying the browser
context**, not by unrouting a page that then keeps living — an earlier version unrouted and
closed the browser afterwards, leaving a short but real window in which the page was alive and
unguarded. The worker owns its context, so there is no moment at which the page exists without
the guard, and nothing has to be handed back.

**No static wait is a safety boundary.** There is a 150ms pause between actions so an
immediate side effect can be attributed to the action that caused it, and nothing rests on it:
a page need only call `setTimeout(…, 400)` to land after any number that could be chosen.
The replay ships two hazards that do exactly that, with a control run proving the timer really
fires and really reaches the server when nothing stops it. What stops it in a worker run is
that the context is destroyed unconditionally, and the target's counter is read *after* that
destruction. A violation that surfaces only during teardown cannot be attributed to an action,
so the envelope reports `side_effect_attribution: unproven` and `validate_result` refuses it —
an unattributable run is not importable rather than quietly recorded as clean.

The oracle baseline is read **before a browser exists**. A run that cannot read it is refused
there and then, rather than executed and rejected at import once the user's data is already on
the page and the grant is spent. **Exactly one document
load is allowed, to the exact attested URL, before any action runs.** Same-origin GET is not
safe by virtue of being same-origin: a form with `method="GET"` submits by navigating, and an
input handler can set `window.location`, so an earlier version filled one field, reached the
application endpoint, left the page, and still reported no final action. Any document request
after the first is aborted and named for what was seen — a POST document request is the form
being sent, a GET one is the page being left. Popups and downloads are recorded. Upload traffic
is allowed by exact URL: a file upload is a POST, so refusing every POST would refuse uploads
while calling itself submit protection.

**Execution stops at the first violation.** Continuing would act on a page that has already
done something it was not allowed to do, and reporting every field as `refused` would hide
which one caused it, so the remaining actions are marked `not_attempted`.

The package is consumed by a marker file beside it rather than by deletion, so a replay fails
on its second attempt instead of its second effect and the package survives for audit. Action
and result files are mode 0600. The browser is headed by default — a run the user cannot see
is a run they cannot stop — and headless only in tests.

## The workflow, proven end to end

`tests/test_fill_worker_end_to_end.py` carries one application from a private root that does
not exist yet to `waiting_for_submission_approval`, through every production path and no
shortcut: a real PDF and claims manifest, an approved ResumeVersion, a material lock, a worker
lease, a replay served over loopback, an execution grant reserved and consumed, a real
Chromium, a result import, a checkpoint per page, a form inventory and a pre-submit review.
Moving between the two pages is a person following a link — the worker has no verb for it.

**Both pages are observed from the live DOM.** `tests/fixtures/replay_observer.py` reads the
page in front of Chromium — identifiers, labels, control types, requiredness, options, the
final control — and produces the protocol observation. Handing `fill_core` a constant would
have proved planner → worker → import and nothing about whether the description matches the
form, which is the same gap that let an upload action's shape drift from what `_plan_upload`
emits. It runs no page script, reaches into no frame, and refuses to describe a page at all
when a control is hidden, disabled, duplicated or unmapped. **It is not an ATS adapter**: it
reads Jobloom's own replay, whose controls carry a stable identifier and a reviewed kind that
no employer page has.

Every expected value comes from somewhere other than the thing being checked: package digests
from the exported bytes, the final-action count from the server's own endpoint, states from
the database, the review digest recomputed from the summary, and the approved disposition of
every recorded label from `tests/fixtures/ats-semantic/FIELD-DISPOSITION-APPROVAL.json` — a
reviewed file naming the corpus bytes it describes, rather than a second Jobloom table, since
comparing two internal maps proves they agree and not that either is right.

The two pages are **four** of the Lever fixture's twenty-seven controls re-paginated, because
every upstream fixture puts all its controls on one step with the final action alone on the
next. It is not a two-page replay of the Lever form.

Alongside it, sixteen mandatory pauses each assert the same four things — no action ran,
nothing was verified or checkpointed, the target's counter never moved, and the state is the
right kind of waiting.

**Three production defects this found**, all invisible to tests that built their own packages
and observations. The worker treated an upload action's value as a path when `_plan_upload`
emits an object, so every real upload failed as `value_rejected_by_page`. The conflict-question
pattern did not match `Related to someone at this company?` — the reviewed corpus's own
wording — so a per-employer disclosure fell through to the answer path, and `referral.contact`
was missing entirely. And the upload branch handed the file to the form before checking its
digest, so a resume swapped between export and execution was already in the employer's file
input by the time anything noticed: no submission is needed for the wrong document to be
disclosed. Verification now happens entirely before `set_input_files`, against both the file's
own bytes and the action's expected digest.

**What the local proof is worth.** The replay's final-action counter is a real oracle: a test
clicks the control without the guard and the server's counter reaches one, which is what makes
every other zero in that file worth reading. The worker reports that count only when it could
read it, and `null` otherwise — a run that could not observe the counter has not shown it did
not move, and `validate_result` refuses a null so an unprovable run cannot be imported as a
safe one.

But it is still our own page. Nothing here is evidence that a live ATS can be filled, and each
production adapter still needs its own supervised live acceptance test. The eleven browser
acceptance tests skip themselves where Chromium is absent, which is right on a laptop and
wrong as a merge gate, so CI runs them with `JOBLOOM_REQUIRE_BROWSER=1`, which turns a skip
into a failure.

## Importing a result

`fill_core.import_result` is the only way a browser observation becomes a verified step. Its
inputs are narrow on purpose: where the result is, and which grant authorised it. Every
expectation it is checked against — the package digest, the action IDs and their order, each
expected hash, the surface, the application identity — is read here, because a caller that
could supply its own expectations could satisfy any of them. The digest in particular comes
from `execution_grants` and `exported_packages`, never from the envelope's own
`package_sha256`: comparing a document to a field inside itself is how the worker's package
digest bug survived a passing test.

Everything the plan rested on is re-read rather than remembered — the worker's lease, the
application state, the session and page, the standing authorization, the active
CandidateSnapshot, the material lock — and then the whole envelope is validated: protocol
version, session and page, package digest, action completeness and order, `verified` on every
action with an exact hash match, a proven `final_action_activations` of zero, and
`side_effect_attribution: complete`.

Each step's source is re-checked against the store as it is **now**, and for answers that
means **re-running the planning decision**, not re-checking the row that won it. `answer_issue`
on the original answer cannot see a newly added answer of equal specificity with a different
value, a question form that has come to mean two things, or an immigration answer scoped to
another application — all of which change what the system would choose, which is the thing the
page was filled from. So the import calls `match_answer` again and requires `auto_fill_ready`,
the same selected `answer_id`, and the same value and hash, then re-applies the
discovery-source restriction. Facts are re-read as locked facts of the active snapshot;
non-disclosure policies go through `policy_issue` and their reviewed vocabulary, so a policy
revoked, expired, moved out of scope or narrowed after planning writes no marker.

**Either the page's actions are all recorded or none are**, and that is a real transaction
now. Nothing writes until every check has passed; the batch then runs inside a `SAVEPOINT`
and rolls back to it on any exception, because a Python error does not roll SQLite back on
its own — a partial batch would otherwise sit in the connection until someone else's commit
made it permanent. `archive_core.record_field` grew a `commit` flag for the same reason: it
committed per field, so "all or none" had never actually been true.

There is **no bypass**. `complete_step`, its CLI command, and the
`checkpoint_page(require_verified_import=False)` switch were removed rather than left as a
legacy option: together they let a caller finish a page with a hash read out of the database
and seal it, which made the entire import path optional. Suites that predate the import path
use `tests/fixtures/completed_page.py`, which lives outside the shipped code and says so.

Import is idempotent and non-replayable, and idempotence does not skip identity: a repeat is
matched against the recorded import's own session, page and package before it is allowed to
report success, because returning `already_imported` for a page the caller named wrongly would
hand back a fact about the wrong application. The result file is kept for audit; `imported_results`
records its digest, the grant, the package digest and the time. The same result imports once
and then reports `already_imported` without duplicating a field, a marker or an event; a
different result presented for the same grant is refused as a conflict rather than overwriting
what was recorded.

**A hash mismatch is not the same kind of failure as a refusal.** A malformed envelope, a
wrong identity, a stale lease or a source that expired all mean the import should not happen,
and nothing is written. An observed hash that differs means the worker did act and the page now
holds something other than what was planned — a disagreement about the form in front of the
user — so after confirming that no step, field, marker or verified row was written, the import
records a value-free rejection and moves the session to `waiting_for_user_takeover`.

**The rejection and the handover are one transaction.** The rejected row, a `result_rejected`
event, the paused session and page, and the application's move to takeover succeed together or
none of them happens. Committing the row first meant a failing pause left a result marked
rejected while the session stayed active — and because replay then answers `already_rejected`,
the handover would never be attempted again.

**The insert is the decision point on both paths.** An `INSERT OR IGNORE` swallowed the unique
conflict on the grant, so a connection that lost a race carried on and paused an application
whose page another connection had already imported cleanly. A conflict now rolls the savepoint
back, re-reads the row that won, and answers from it — `already_imported`, `already_rejected`,
or a conflict for different bytes — and pauses nothing.

Transaction ownership is not a public boolean. `transition` and `release_lease` always commit;
`_transition_uncommitted` and `_release_lease_uncommitted` are for a caller that already holds
a savepoint. A `commit=False` parameter on the public functions let any caller get persistence
wrong and still receive a successful-looking return value.

Everything persisted is value-free: the event carries a page id, a hashed step id and stable
codes, and neither the expected nor the observed hash appears anywhere — they are properties of
the value, and two of them together say a great deal about it. Replaying the same rejected
result reports `already_rejected` without pausing or recording again.

A page may be checkpointed only on the strength of a verified import. Steps marked complete by
some other route do not qualify, and a failed or conflicting import leaves nothing to qualify
on.

Two things this exposed. The step applier keyed on `operation == "fill"`, so planning a
reviewed non-disclosure option as `select` skipped both branches and recorded a verified step
with neither a field nor a marker behind it; it now keys on the source. And `_perform`'s
`select` branch had been unreachable because every planner emitted `fill` — a reviewed option
on a `<select>` would have been typed into it. Radiogroups remain unaddressed: the group holds
the identity and the option lives on one of its inputs, so those pause as
`nondisclosure_control_unsupported` rather than acting on the wrong node.
