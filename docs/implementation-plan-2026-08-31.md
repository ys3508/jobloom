# Jobloom implementation plan — executable backlog

Recorded 2026-08-31. This is the ordered implementation backlog for a long-running Claude
Code session. It turns the product priorities into small, reviewable commits. It does not
authorize submitting a real application, contacting anyone, reading an inbox, or storing
real candidate data in the repository.

## Outcome and operating rules

The next product milestone is a locally testable, end-to-end **Fill-Only** path:

```text
fixture form
  -> value-free observation
  -> Jobloom action package
  -> browser worker executes allowed actions
  -> worker returns hashes only
  -> fill_core verifies and checkpoints
  -> value-free pre-submit review
  -> stop before Submit
```

After that foundation is real, add the review dashboard, posting-trust signals, and interview
story bank. Network scanning and inbox classification stay out of tonight's critical path.

Every task below must obey these rules:

1. Read `AGENTS.md`, `skills/jobloom/SKILL.md`, the references named in the task, and the
   touched tests before editing.
2. Preserve all user-owned and unrelated changes. Run `git status --short` before and after
   every task. Never use `git reset --hard`, `git checkout --`, or delete `.jobloom/`.
3. Use Conventional Commits. Make exactly one focused commit per numbered task. Do not amend,
   squash, push, or open a pull request.
4. Never put candidate values, answer values, tokens, cookies, selectors containing secrets,
   complete job descriptions, or action-package contents in logs, test output, snapshots, or
   git-tracked fixtures.
5. No real job site, real employer form, logged-in session, email account, LinkedIn account,
   or external message is allowed in Tasks 0–7. Use only the local fixture server.
6. The worker must not contain a submit operation. Merely naming a button `submit` is not
   permission to click it. `stop_before_submit` must fail closed.
7. Prefer deterministic parsing and stable reason codes. No LLM is allowed in the browser
   execution, hash verification, posting-trust, or state-transition path.
8. A task is complete only when its acceptance tests pass and its documentation describes
   what is actually implemented. If blocked, do not make a partial commit: write the blocker
   and the exact failing command in the session report, then move only to an independent task.

## Tonight's execution order

Run Tasks 1, 0, 1A, 2, 3, 4, 5, 6, and 7 in that order. Tasks 8–9 are stretch work only after
the full test suite passes. Task 10 was removed because the audit already exists in commits
`e94533c`, `73e30f2`, and `8783fb0`. Tasks 11–13 are the next-phase backlog and must not be
started tonight unless explicitly requested.

Estimated order, not a time promise:

| Gate | Tasks | Result |
| --- | --- | --- |
| A | 1, 0, 1A, 2 | safe materials, measured decision, first-form policy, and protocol |
| B | 3–5 | browser protocol, fixture, and worker |
| C | 6–7 | end-to-end Fill-Only proof and hardened extension |
| D | 8–9 | review UI foundation and posting trust |
| Later | 11–13 | stories, network, and inbox outcomes |

---

## Task 0 — establish the baseline and write the decision record

**Goal:** make the changed product direction explicit and capture a reproducible baseline
before implementation.

Read:

- `README.md`
- `ROADMAP.md`
- `skills/jobloom/references/known-liabilities.md`
- `skills/jobloom/references/browser-assist.md`
- `skills/jobloom/references/fill-only-execution.md`
- `docs/adr-workday-coverage.md`

Implement:

1. Include this implementation plan and its `ROADMAP.md` link in the Task 0 commit; they are
   intentionally left as the product owner's reviewable working-tree changes before execution.
2. Add `docs/adr-fill-only-browser-worker.md` with status `Accepted`, date, context, decision,
   alternatives, safety boundary, rollout stages, and rollback condition.
3. State that this 2026-08-31 decision supersedes only the earlier choice to keep applying
   manually; it does not relax Fill-Only or submission rules.
4. Record the measured 2026-08-31 review-queue distribution: Lever 71/105, Greenhouse 19/105,
   Ashby 14/105, SmartRecruiters 1/105, and Workday 0/105. Preserve how the denominator and
   domain grouping were computed so the measurement can be repeated without committing private
   job data.
5. Define the production adapter order as Lever, Greenhouse, then Ashby. SmartRecruiters remains
   measured but deferred. Workday and a generic fallback are explicitly out of this milestone;
   Workday measured 0% of the current queue and remains deliberately absent from Tier 0.
6. Define the first executable surface as a local semantic replay generated from reviewed,
   value-free fixtures. Each production adapter still requires a supervised live acceptance
   test before enablement; a semantic replay is not evidence that current ATS DOM works.
7. Correct any status text that implies an end-to-end worker already exists. Do not remove the
   known liability yet; add a link to the ADR and define the evidence required to close it.
8. Record baseline output in the commit message body or session report, not in a generated
   repository file.
9. Freeze these v1 bounds in the ADR: one current page per worker run; every Next, Continue, and
   final action belongs to the user; employer-defined compensation brackets are manual; EEO values
   are excluded while an exact-match non-disclosure policy is permitted; conflict reuse requires
   approved employer identity plus a fresh complete-registry certification. Distinguish the local
   fixture final-action oracle from scoped submit/navigation guards on a supervised live page.

Verify:

```bash
python3 -m unittest discover -s tests
git diff --check
```

Acceptance:

- Documentation distinguishes backend readiness from runnable browser execution.
- The ADR explicitly says no real-site test and no submission is authorized.
- Existing tests pass unchanged.

Commit: `docs: accept staged fill-only browser worker`

---

## Task 1 — enforce PDF-only application materials

**Goal:** close the known path where a DOCX or renamed DOCX can be bound, locked, and uploaded
as an application resume.

Read:

- the `Nothing stops a non-PDF resume` entry in `known-liabilities.md`
- `skills/jobloom/scripts/resume_core.py`
- `skills/jobloom/scripts/cover_letter_core.py`
- `skills/jobloom/scripts/fill_core.py`
- `tests/pdf_fixture.py`
- every test returned by `rg 'resume\.txt|\.docx|snapshot_path' tests`

Implement:

1. Add one shared material-format validator. Do not create three drifting implementations.
2. Validate semantic kind, `.pdf` suffix, and leading PDF bytes. A renamed ZIP/DOCX must fail.
3. Enforce it at resume binding, material locking, and upload planning.
4. Apply the corresponding rule to bound cover letters if the submission path requires PDF.
   Keep `master_source` registration formats unchanged; only application-bound artifacts are
   PDF-only.
5. Replace text fixtures mechanically with the minimal PDF helper. Preserve each test's real
   assertion.
6. Update the liability with closure evidence only after the complete gate lands.

Required tests:

- valid PDF binds, locks, and plans an upload;
- `.pdf` containing DOCX/ZIP bytes is rejected;
- valid PDF bytes under `.docx` are rejected;
- direction `.docx` is rejected before material lock;
- `master_source` DOCX registration remains allowed but cannot be selected for submission;
- cover-letter behavior is explicit and tested;
- physical file replacement after approval still fails hash validation.

Verify:

```bash
python3 -m unittest discover -s tests
python3 skills/jobloom/scripts/artifact_integrity_audit.py --help
git diff --check
```

Commit: `fix: require pdf application materials`

---

## Task 1A — close first-form domain policy gaps

**Goal:** decide which protected Jobloom authority may answer every field class in the reviewed
Lever semantic fixture before freezing the worker protocol vocabulary.

Read:

- `docs/lever-first-form-readiness.md`
- `skills/jobloom/scripts/answer_library.py`
- `skills/jobloom/scripts/fill_core.py`
- `skills/jobloom/scripts/archive_core.py`
- `skills/jobloom/references/answers-and-authorization.md`

Implement only the minimum policy/core changes required for safe replay:

1. Add explicit field dispositions for fact, answer, material, always-manual, and unsupported.
2. Exclude voluntary race/ethnicity, gender, disability, and veteran values and hashes from every
   Jobloom store and artifact. A separate user-approved non-disclosure policy may select only an
   exact reviewed “decline/prefer not to answer” option; ambiguity pauses. Persist only a value-free
   handling marker for the intentional archive blind spot.
3. Keep employer-conflict question families separate. Derive an application answer only from a
   user-approved employer entity plus a fresh, explicitly complete conflict registry; registry
   absence without that certification is unknown. `normalized_employer` may suggest but cannot
   establish identity. Otherwise keep the field manual.
4. Make employer-defined `compensation.total_range` radiogroups always-manual in v1. Defer the
   optional numeric target-salary stance; when built, its number belongs in protected storage and
   logs/packages carry only a reference/hash. Salary floor never resolves an application answer.
5. Map broad sponsorship fixture kinds to ambiguity/pause unless one of the four exact canonical
   meanings was user-reviewed for this application.
6. Treat discovery source as application-specific or conditional user-confirmed data; never infer
   it from the URL or collector.

Required tests are the pre-protocol tests in `lever-first-form-readiness.md`, plus proof that
no voluntary EEO value or expected hash reaches SQLite, stdout, events, packages, or archives.

Verify:

```bash
python3 -m unittest tests.test_answer_library tests.test_fill_core tests.test_pre_submit_core
python3 -m unittest discover -s tests
git diff --check
```

Commit: `feat: define first-form answer boundaries`

---

## Task 2 — define a versioned browser-worker protocol

**Goal:** create a narrow protocol between `fill_core` and a future browser worker before any
browser code is allowed to touch a form.

Read:

- `skills/jobloom/references/fill-only-execution.md`
- `skills/jobloom/assets/form-page-observation.template.json`
- `skills/jobloom/assets/fill-session.template.json`
- `skills/jobloom/scripts/fill_core.py`
- `tests/test_fill_core.py`
- `tests/test_fill_core_cli.py`

Implement:

1. Add tracked, value-free JSON Schemas for:
   - worker request envelope;
   - action-package metadata envelope;
   - worker result envelope.
2. Keep the private action package itself under `.jobloom/`; never add a value-bearing example.
3. Add `protocol_version`, `session_id`, `page_id`, `package_sha256`, expiration, allowed origin,
   ordered action IDs, `stop_before_submit: true`, and `submission_action: null`.
4. Result entries may contain action ID, outcome code, observed hash, control type, and bounded
   error code. They must not contain observed values or page text.
5. Add deterministic protocol validation to `fill_core` or a small shared module.
6. Unknown versions, expired packages, wrong session/page, changed package bytes, extra action
   IDs, duplicate results, missing results, unexpected origin, or any submission action must
   fail closed before recording a verified field.

Required tests:

- round-trip valid envelope;
- tampered package hash;
- replay against another session/page;
- expired package;
- unknown protocol version;
- extra, duplicate, missing, and reordered results;
- leaked `value`, `text`, `cookie`, `token`, or file path fields rejected from result envelope;
- submit-like operation rejected regardless of selector or label.

Verify:

```bash
python3 -m unittest tests.test_fill_core tests.test_fill_core_cli
python3 -m unittest discover -s tests
git diff --check
```

Commit: `feat: define fill worker protocol`

---

## Task 3 — adopt reviewed semantic fixtures and generate local replay forms

**Goal:** reuse the value-free Lever, Greenhouse, and Ashby semantic corpus from
`neonwatty/job-apply-plugin`, then generate deterministic local browser targets without touching
a real employer or carrying real personal data.

Upstream facts that must remain explicit:

- source: `https://github.com/neonwatty/job-apply-plugin`;
- license: MIT, copyright Jeremy Watt;
- reviewed upstream commit: `081a5d9d793da29111e2d5331767021718f1d8b5`;
- upstream paths: `qa/fixtures/{lever-application-2026-08-v1,greenhouse-single-page-2026-08-v1,ashby-application-2026-08-v1}`;
- these are generic semantic replay models derived from private recordings, not captured DOM,
  screenshots, selectors, current live-site acceptance evidence, or permission to access a site.

Implement under `tests/fixtures/ats-form/` or another clearly test-only path:

1. Vendor only the three `fixture.json`, `provenance.json`, and `approval.json` sets needed for
   Lever, Greenhouse, and Ashby. Preserve bytes where practical, verify their published SHA-256,
   and include an attribution/NOTICE file with source URL, upstream commit, license, copied paths,
   and local modifications. Do not copy the upstream worker, recorder, private QA process, DOM,
   documentation, or unrelated fixtures.
2. Add a strict local allowlist for `kind`, `role`, requiredness, choices, lifecycle, and
   provenance. Map each semantic `kind` explicitly to a CandidateFact family, AnswerLibrary
   canonical question family, material, voluntary/sensitive pause, or unsupported pause. Never
   collapse Jobloom's four immigration questions into upstream's broader sponsorship names.
3. Generate accessible local HTML controls from the reviewed semantic fixtures. The renderer is
   Jobloom-owned code and must use roles/labels for observation while giving every generated
   control a stable test identity. This tests upstream field combinations, not a claim that live
   ATS selectors match.
4. Add a loopback-only HTTP server. Multi-page fixtures require an explicit test/user navigation
   step: the worker fills only the current page and never activates Next, Continue, or a final
   action. The final control handler records whether it was ever activated.
5. Add Jobloom-owned safety variants for disabled, hidden, duplicated, or DOM-replaced controls;
   an unknown question; standard attestation; selector/role ambiguity; unexpected origin;
   CAPTCHA; payment; identity document; assessment; and biometric/video requests.
6. Use only synthetic values. No real employer wording, applicant value, source URL, screenshot,
   DOM capture, cookie, token, or raw recording may enter the repository.
7. Add a test-only observation endpoint or DOM state helper that proves exact field state and
   whether the final action was activated.
8. Ensure no fixture accepts a non-PDF upload.

Required tests:

- server binds only to `127.0.0.1`;
- vendored bytes, fixture hashes, approval hashes, provenance, upstream commit, and MIT notice
  are checked by test;
- every upstream semantic kind has an explicit Jobloom mapping or fail-closed disposition;
- Lever, Greenhouse, and Ashby fixtures each render and reach review locally;
- fixture starts and stops cleanly with a dynamically allocated port;
- each safety control is discoverable;
- submit activation counter begins at zero and can be read without activating it;
- fixture contains no external URLs or network requests.

Verify:

```bash
python3 -m unittest tests.test_ats_form_fixture
python3 -m unittest discover -s tests
git diff --check
```

Commit: `test: add attributed ats semantic replays`

---

## Task 4 — implement the minimal worker as a separate process

**Goal:** consume exactly one validated action package, act on exactly one local fixture page,
and return hashes only.

Architecture requirements:

1. Put the worker outside `fill_core.py`. `fill_core` remains the authority; the worker is an
   untrusted executor whose output must be verified.
2. Use Playwright with a visible browser by default. Headless mode is allowed only in tests.
3. Allow only `http://127.0.0.1:<ephemeral-port>` in this task. Reject every other host,
   including `localhost`, redirects, subframes, popups, downloads, and new tabs.
4. Support only deterministic operations required by the fixture: fill, select, check/uncheck,
   and approved PDF upload.
   Operate on one current page only; never click a button/link or synthesize Enter, Next, Continue,
   navigation, or final submission.
5. Never evaluate page-provided JavaScript strings, use arbitrary shell commands, infer answers,
   call a model, or discover a file path.
6. Reject controls that are hidden, disabled, ambiguous, duplicated, detached, changed since
   observation, or outside the top frame.
7. During each worker action, install scoped live guards that capture and block submit events and
   fail closed on unexpected navigation or final-application requests. Remove the guards before
   handing control back to the user. File upload traffic is separately expected and hash-bound;
   do not pretend that banning every POST is a valid submission detector.
8. Write the result envelope mode 0600 to a new file; do not overwrite it and do not print
   values or paths.

Required tests:

- every supported control is filled on the local fixture;
- observed text/file hashes match `fill_core` expectations;
- wrong selector, duplicated selector, and changed control fail closed;
- redirect, popup, iframe, non-loopback URL, and cross-origin navigation fail closed;
- Enter, Next, Continue, buttons, links, submit events, and unexpected navigation are never worker-owned;
- submit activation remains zero;
- logs and result JSON do not contain synthetic values or local upload paths;
- action and result files have restrictive permissions;
- a package cannot be executed twice.

Verify using the runtime already selected by the repository. If adding Node dependencies, pin
them and commit the lockfile. Then run:

```bash
python3 -m unittest tests.test_fill_worker
python3 -m unittest discover -s tests
git diff --check
```

Commit: `feat: add local fill-only browser worker`

---

## Task 5 — connect worker results to verification and checkpoints

**Goal:** exercise the existing backend half with real browser-observed hashes.

Implement:

1. Add a CLI command or narrow bridge that imports the result envelope into `fill_core`.
2. Revalidate lease, application state, material lock, authorization, candidate snapshot,
   protocol envelope, package hash, page identity, and result completeness at import time.
3. Record a field only after exact hash equality. Keep values out of events and stdout.
4. Make imports atomic and idempotent. A partial or invalid result records no successful steps.
5. Delete neither action nor result files automatically. Mark consumption in protected state so
   audit and safe manual cleanup remain possible.
6. Permit checkpoint only when all page actions verify.

Required tests:

- valid import verifies all actions and permits checkpoint;
- one mismatched hash verifies none of the batch if atomicity is the chosen contract;
- stale lease, changed answer, changed fact, revoked authorization, changed material, or resumed
  application invalidates the import;
- replay is rejected without duplicating ApplicationFields or events;
- stdout and database events remain value-free.

Verify:

```bash
python3 -m unittest tests.test_fill_core tests.test_fill_worker
python3 -m unittest discover -s tests
git diff --check
```

Commit: `feat: verify browser fill results`

---

## Task 6 — prove the complete local Fill-Only workflow

**Goal:** add one end-to-end test that reaches `waiting_for_submission_approval`, generates a
pre-submit review, and proves the form was never submitted.

Scenario:

1. Initialize a temporary private database and stores.
2. Register a synthetic candidate and confirmed scoped answers.
3. Register and approve a minimal real PDF fixture and claims manifest.
4. Create and material-lock an application.
5. Acquire a fill lease and start a session.
6. Observe fixture page 1, export, execute, import, verify, checkpoint.
7. Repeat for page 2.
8. Observe but do not act on the final submit control.
9. Complete the fill session and create the form inventory.
10. Generate the pre-submit review and verify it is value-free.
11. Assert application state is `waiting_for_submission_approval` and fixture submission count
    is exactly zero.

Also test mandatory pauses for unknown question, CAPTCHA, payment, identity document,
assessment, biometric/video, special legal term, cross-origin navigation, and incorrect autofill.

Verify:

```bash
python3 -m unittest tests.test_fill_worker_end_to_end
python3 -m unittest discover -s tests
git diff --check
```

Acceptance:

- The test uses a real browser engine, not mocked DOM calls.
- The action package is consumed at least once by code outside `fill_core`.
- No test or implementation can produce a submission action.
- `known-liabilities.md` closes the missing-worker liability narrowly: local fixture support is
  complete; production ATS adapters remain unimplemented and named.

Commit: `test: prove local fill-only workflow`

---

## Task 7 — integrate worker control into the existing extension

**Goal:** give the user an explicit, inspectable way to run one Fill-Only page while preserving
the current browser-assist boundary.

Read all of `skills/jobloom/extension/`, `assist_bridge.py`, and
`tests/test_assist_bridge.py` before changing anything.

Implement:

1. Keep posting reading and form filling as separate modes with separate buttons and status.
2. Require a user gesture for each page execution. Do not add polling, resident content scripts,
   automatic navigation, pagination, or automatic continuation.
3. Display only value-free information: application identity summary, number/control kinds,
   source kinds, risks, package expiration, and the explicit `Stops before Submit` guarantee.
4. The bridge may coordinate package and result file IDs, but must never return values or local
   paths to extension UI code.
5. Require same active tab, top frame, allowed form origin, live lease, and current package.
6. After execution, show verified/paused counts and stable reason codes. Do not claim an
   application was submitted.
7. Preserve the existing `Read this posting` behavior and its no-storage default.

Required tests:

- old browser-assist tests remain unchanged and pass;
- no automatic navigation, clicking, polling, or content script is introduced;
- one explicit user gesture executes one page only;
- bridge responses and extension storage contain no values or paths;
- submit control is rendered as a stop boundary and never acted upon;
- reload/retry cannot replay a consumed package.

Verify:

```bash
python3 -m unittest tests.test_assist_bridge tests.test_fill_worker_end_to_end
python3 -m unittest discover -s tests
git diff --check
```

Commit: `feat: expose one-page fill-only control`

---

## Task 8 — add a value-free review dashboard foundation

**Stretch goal. Do not start until Tasks 0–7 and the complete suite pass.**

Goal: add a local read-only dashboard before adding dashboard mutations.

First views:

- review queue ordered by evidence coverage, never raw `ranking_score`;
- saved, approved, filling, waiting-for-user, and waiting-for-approval applications;
- direction allocation status;
- resume/cover-letter version status and hashes shortened for display;
- answer/authorization freshness counts without answer values;
- source health;
- funnel numerators and denominators;
- known warning that samples below 30 submissions are insufficient.

Constraints:

- loopback only, per-run token, read-only routes;
- no candidate facts, answer values, archive contents, prompts, or job-description bodies;
- use existing core functions as the source of truth; do not duplicate business logic in the UI;
- display groups never merge cross-city jobs and always show independent opening counts;
- label `decision='applied'` as intent, not submission.

Tests must cover access control, output field allowlists, ranking order, redaction, and empty-state
behavior.

Commit: `feat: add value-free review dashboard`

---

## Task 9 — add posting-trust records without changing fit

**Stretch goal. Independent of Task 8 after the baseline passes.**

Goal: introduce a deterministic `PostingTrustRecord` that never changes eligibility, evidence
strength, direction routing, or match recommendation.

Initial signals:

- posting age and observation time;
- official ATS source vs aggregator;
- official URL unavailable while aggregator copy remains;
- canonical/apply domain mismatch;
- exact repost fingerprints already safely known;
- compensation present/absent, without treating absence as fraud;
- suspicious payment, identity-document, off-platform contact, or credential request;
- unknown when evidence is unavailable.

Do not implement:

- cross-city employer/title merging;
- text-similarity duplicate deletion;
- an opaque `ghost job percentage`;
- model-based trust scoring;
- automatic rejection from weak or missing signals.

Output should be `low_concern`, `review`, or `high_concern`, with stable evidence-bearing reason
codes and source timestamps. Test that fit output remains byte-for-byte unchanged when a trust
record is added.

Commit: `feat: add deterministic posting trust signals`

---

## Task 11 — build an evidence-backed interview story bank

**Next phase, not tonight.**

Define immutable `StoryEvidence` records that reference existing CandidateFact/EvidenceUnit IDs.
Support Situation, Task, Action, Result, Reflection as optional structured sections, but never
infer missing facts. Store user-approved story versions and mappings to question families. A
generated preparation packet must cite every factual sentence and mark gaps for user completion.
Interview usage and outcomes may suggest review; they must never silently rewrite a story.

Commit sequence should separate schema/core, CLI, report generation, and tests.

---

## Task 12 — add a review-only network scan

**Next phase, not tonight.**

Import a user-provided contacts CSV into a private, explicitly authorized store. Match normalized
company identities deterministically. Draft messages only after user selection; never send or
open profiles. Keep contact data out of model context unless the user authorizes one selected
contact. Store no scraped LinkedIn session or data.

---

## Task 13 — add privacy-preserving outcome classification

**Next phase, not tonight.**

Begin with user-imported `.eml` fixtures, not a live inbox. Filter headers locally, classify only
job-related candidates, store message/source hashes and outcome codes rather than bodies, and
require user confirmation before application-state changes. A live read-only connector requires
a separate ADR and explicit user authorization.

---

## Claude Code launch prompt

Paste the following prompt into Claude Code from the repository root:

```text
Open docs/implementation-plan-2026-08-31.md and execute Tasks 1, 0, 1A, 2, 3, 4, 5, 6, and 7 in
that exact order.

Treat every numbered task as a separate change: inspect the repository first, implement only
that task, run its focused tests and then the full unittest suite, review the diff, and create
the exact Conventional Commit named by the plan. Preserve unrelated and user-owned changes.
Never amend, squash, push, open a PR, touch a real job site, use real candidate data, submit a
form, send a message, or weaken any Jobloom freshness, evidence, authorization, material-lock,
pre-submit, or archive boundary.

After each task, append a concise session report in your response with commit hash, files
changed, focused/full test results, and unresolved risks. If a task is blocked, do not make a
partial commit. Record the exact blocker and continue only with a later task that the plan marks
independent. Stop after Task 7 even if time remains. Do not start stretch Tasks 8–9 without a
new instruction.
```

## Review handoff checklist

When Claude Code stops, provide the reviewer:

- `git status --short`;
- `git log --oneline --decorate -12`;
- one-line purpose for every new commit;
- focused and full test commands with pass/fail counts;
- dependency and lockfile changes;
- every schema or database migration;
- every new permission requested by the extension;
- a recursive search showing no submit action exists in the worker;
- a test artifact proving the local fixture submit counter stayed zero;
- known liabilities closed, narrowed, or added;
- any task skipped or blocked.

The reviewer should inspect commits individually, check value/redaction boundaries, attempt
protocol replay and package tampering, and rerun the end-to-end fixture before approving a real
ATS adapter.
