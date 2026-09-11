# The Jobloom app

Every component below this one is a command. That is the right shape for an engine and the
wrong shape for a product: someone looking for work should not meet a Python invocation, a
JSON file, a database path, or a SHA-256 to copy. This is the surface that replaces them.

`jobloom_app.py` is a local service that serves one page and answers it over loopback. It
decides nothing. Every rule it appears to apply — which fields are asked, what a valid email
is, what the two confirmations mean, what registering costs — lives in `candidate_profile` and
is re-checked there. The terminal path (`fill-profile`) and this window call the same
functions, so they cannot disagree; the terminal path stays for development, diagnosis and
tests, and is no longer the way in.

## What exists and what does not

**Exists:** the local service, the onboarding window, and one complete vertical slice —
welcome, nine questions, review, impact preview, registration, done. Bilingual, no external
resources, nothing stored in the browser.

Then the second half of the same sitting: **carrying the resumes the new profile left
behind.** Registering a profile invalidates the material locks bound to the old snapshot, so
the window goes on to list what was stranded, prepares a successor for the one the user picks,
shows the actual PDF, shows the claims revalidated against the new profile, takes the two
approvals separately, and binds and re-locks. Closing the window mid-carry loses nothing: an
in-flight migration is picked up where it was left, and a round already answered is not asked
again.

**Two approvals, two presses.** *I have looked at this PDF and approve it for the new profile*
is not *bind it to the application and re-lock*. Opening the document is neither: reading it is
a GET that advances nothing, and `materials_reviewed` is a field the approval refuses without.
A hash proves two files are the same file; it cannot prove the file is the one a person wants
sent to an employer.

**No path crosses the boundary, in either direction.** The page cannot say a version is
migratable, cannot name a candidate document, a resume or a manifest, and never receives one:
the service resolves all three from rows it has already verified, and the PDF is served from
the registry's own path after re-checking its hash. That endpoint serves only a document under
an open migration — it is not a way to read the resume store.

**Does not exist yet, and is not pretended to:**

- **Packaging.** The page opens in a window of the user's own browser (Chrome's app mode where
  it is there, a tab otherwise). A `.dmg`, an `.exe`, an icon, code signing, notarisation and
  an updater are a separate piece of work, and the choice of shell — Tauri, Electron, or a
  platform webview — has not been made. The arrangement here is deliberately the one such a
  shell wraps: the same local service, the same HTML, inside a frame.
- **A private data directory outside the repository.** The app still reads `.jobloom/` beside
  the database it is given. A shipped app puts user data in
  `~/Library/Application Support/Jobloom/` (macOS) or the platform equivalent, and moving the
  existing data there is a migration a user performs, not something an app does to them on
  first launch.
- **Most of what follows onboarding.** Resume import, the tracker, and the pre-submission
  review are still commands or still the browser panel. The exception is the read-only
  application-assist slice below.

## The application-assist slice

The second vertical: the applications already decided on, what `evaluate_job` knows would stop
one, and — from the form's own questions, pasted in — who may answer each of them.

**Which profile answers.** The one active, user-registered CandidateSnapshot, read from the
`snapshot_path` on its own row and re-verified before use: the file still hashing to
`file_sha256`, and the document still hashing to `content_sha256`. Not
`<private_root>/candidate.json` — that file is whatever was last written beside the database
and after a registration it can be a superseded profile, which is what it was here when the
first version of this slice read it. Profile meanings then resolve against the snapshot this
application's *material lock* is bound to, through the join `archive_core` already uses, so a
stale lock is visible as a reason instead of silently resolving against whatever is active.

**It writes nothing, and that is tested rather than asserted.** `answer_library.inspect_answer`
is the read-only sibling of `match_answer`: the same decision from the same extracted core,
with no audit event and no commit. The audited path is unchanged and still runs where a value
actually reaches a form. `tests/test_jobloom_app.py` classifies a page containing a real
answer hit and compares every table's row count, `total_changes`, and the database file's bytes
before and after.

**Six lanes, because three of them used to be one.** A locked profile field, an answer with a
live standing authorization, and an answer with none are different situations for the person
reading the screen; calling all three "confirmed already" told someone a form was handled when
what it meant was that a value existed somewhere. An answer waiting on an authorization is a
blocking lane.

  profile_ready · answer_ready · answer_needs_authorization · you_answer · manual_only ·
  narrative_gap

**Order of authority.** `field_policy` first and final — a legal, immigration, compensation,
EEO, conflict or referral question is `manual_only` however it is worded. Then the reviewed
meaning of the exact question, from `answer_library.canonical_meaning`: exact on the normalised
text and nothing else, because a resemblance score deciding that a question means
`work_authorized_now` is a machine concluding what an immigration field asks. The meaning is
then shown to `field_policy` too, so a form whose label trips nothing — "Which of these
describes you" — cannot carry `eeo.race` past the gate. Only an unmapped question reaches the
StoryBank hint.

**What the narrative hint offers.** An ordering of the user's own confirmed facts by shared
content words, the words that put each one there, and how many facts were looked at. It is
material to write from and claims nothing about sufficiency. It is deliberately not
`evidence_matcher.related_facts`, which requires every token of its input to appear in the fact
— correct for the requirement "SQL", impossible for a question containing the word "please",
and in the first version it returned an empty column that read as "you have no relevant
experience".

**The page may not name anything.** It sends questions and an application id. A candidate path,
a snapshot hash, a fact id, an answer id or an authorization decision arriving in a payload are
ignored, and every response is built from an explicit allowlist rather than from a database row.
Refusals are bare codes: a message could carry a path or a value.

**Input is paste only.** A screenshot would need local OCR, and an OCR slip would feed a wrong
question into a classification whose every reason code the user is meant to act on. Reading a
live Workday page belongs to the fill-only worker ADR, not here.

Endpoints: `GET /apply`, `GET /api/apply/queue`, `POST /api/apply/readiness`,
`POST /api/apply/split`, `POST /api/apply/classify`. The page is `assets/apply.html`, under the
same policy as the onboarding window: no storage, no external resource, the session token in
the header of every call.

**Not there yet.** Nothing records that an application was submitted, and no outcome is
written back — steps a person still does by hand. The window is a preflight and stops at the
checklist.

## Recording an application made by hand

The third vertical, and the first that writes. `POST /api/submission/state`, `/intend`,
`/confirm` and `/tracker`, reached from the end of the preflight.

**It records the user's word, because that is all there is.** The fill worker is fixture-only,
so the employer's form is filled in the employer's own tab and Jobloom sees none of it.
`saved_jobs` already models this in three rungs that are never collapsed: `decision='applied'`
is an intention pressed before the form opens, `submitted_confirmed_at` is the user saying
afterwards that they finished, and `application_core`'s `submitted` needs positive submission
evidence and a material lock.

**This slice climbs rungs 1 and 2 and cannot reach rung 3**, by construction: it never calls
`application_core.transition` and never writes `submission_evidence`. A reference the user
types — a confirmation number, an employer email, what their account shows — is stored beside
the confirmation on the saved job. The vocabulary is `application_core`'s own four types,
shared on purpose so there is no second taxonomy; the rung is which table the reference is in.
Letting a typed value into `submission_evidence` would let the second rung's evidence open the
third rung's gate, and a test asserts that afterwards the application still cannot be
transitioned to `submitted`.

**Two presses, not one.** The gap between the intention and the completion is the abandonment
rate — the number that makes a reply rate over intentions wrong — and one press would record
both at the same instant and erase it.

**No archive is made, and this task does not add one.** `archive_core.create_archive` requires
an archivable state, a `submitted_at`, and a `use_type='submitted'` resume usage; a hand-made
application has none of them. That is a property of the evidence, not a gap, so nothing here
fabricates one.

**Schema migration is not a read path.** `submission_record.state` and `pending` execute only
SELECT: they do not create, alter, insert, update or commit, and a database whose schema is not
ready is refused with a stable code rather than migrated underneath a caller who wanted to
look. `submission_record.initialize` is the one place that writes schema, called from
`serve()` alongside the other components.

**Rung 2 now has an application state of its own: `submitted_by_user_unverified`.** The first
version left the application at `ready_to_fill` and hid it from the window's list, which hid it
from one reader and no others — `acquire_next` selects exactly `ready_to_fill`, so a worker
could still have been handed an opening whose form was already submitted by hand. The state is
reachable only from `ready_to_fill` and `waiting_for_user_takeover`, only on the user actor,
and only with the reason code `manual_submission_confirmed_by_user`.

It is still not rung 3: no `submitted_at`, no submitted resume usage, no evidence row, and
`create_archive` still refuses. The tracker reads it as "confirmed after applying".

**The confirmation and the state change land together.** Both go through their uncommitted
cores inside one `BEGIN IMMEDIATE`, because either alone survives a crash as a half-truth — a
rung with no state change leaves the opening acquirable, a state change with no rung leaves an
application nobody can explain. Confirming twice keeps the first time and adds no second
transition.

**A reference is kept whole or refused.** It used to be silently truncated at 500 characters,
which stored something nobody confirmed. The limit is now explicit, measured in code points,
and exceeding it refuses the whole confirmation: nothing is written, not the timestamp, not the
reference, not the state. The refusal is a bare code (`reference_too_long`) and the value never
appears in an exception, a log line or an HTTP body.

**Rung 3 is read from history, not from the current state.** `applied_evidence` says
"tracked application" only when the application has both a `submitted_at` and an
`application_events` row moving to `submitted`. Reading the current state instead was wrong in
both directions: a `submitted_at` written around the engine counted, and a real submission
later withdrawn did not — withdrawing does not un-send an application, and dropping it would
have flattered every rate computed over that denominator. `answer_matched` is not a filling or
submission signal either and must not appear in any funnel: it records that an answer was
resolved, not that a field was filled.

**The tracker is rebuilt from state.** `build_worksheets` writes `applied.xlsx` and its CSV
with `worksheet_writer`, which is stdlib only — it exists because
`build_application_tracker.mjs` needs a package this repository cannot install. No process is
spawned, no dependency is required, and the three rung counts are returned separately rather
than added together. Paths are not returned to the page.

## The sponsorship triage page

`GET /triage`, served from `assets/triage.html`, over `GET /api/sponsorship/queue` and
`POST /api/sponsorship/posting`. Both read a built queue file and a pull directory; neither
opens the database.

**Why it exists.** Over the 2026-09-10 queue, all 112 openings carry `sponsorship: unknown`,
and the candidate's `sponsorship_future` is true, so `evaluate_job` sends every one of them to
`sponsorship_requires_review`. For 28 of them the posting said something and nobody has read
it. Those 28 are the page.

**It chooses nothing.** No suggested verdict, no pre-selected control, nothing ticked by
default. The extractor stopped short of a verdict on purpose and this page does not finish the
job on its behalf.

**One hint, for ordering only.** In clinical research a *sponsor* is the organisation running a
trial, and `ingest_job` matches the bare word, so in this corpus 11 of the 28 cards carry only
trial sentences and 1 carries both. Each card is labelled `employment_or_visa_signal`,
`mixed_signal` or `possible_trial_sponsor_only` and the page reads in that order. The upstream
markers are deliberately not narrowed: they over-recall, which costs reading time, and dropping
the bare word would risk a false negative — a real visa sentence never shown. So every card
stays, `sponsorship` stays `unknown`, the hint reaches no JobCard, routing decision or
eligibility result, and a `possible_trial_sponsor_only` card still offers all three verdicts.

**Two layers, because reading and deciding are different sizes.** Grouped on the hash of the
exact sentence — 52 occurrences over that queue collapse to 21 sentences, three of which cover
26 occurrences — so a person reads a sentence once. Nothing looser than exact: grouping by
similarity or by employer is how "Beghou said no once" becomes "Beghou never sponsors". A
group supports one *interpretation*; the decision stays per opening, which is why every member
carries its own `job_id`, `job_card_sha256` and context, why applying a reading to a set of
openings has to be an explicit act, and why no member is selected by default.

**Merging is display only.** Statements whose 220-character context windows would overlap are
shown as one block, because rendering two adjacent sentences separately makes the reader read
the same paragraph twice. A block's text is the posting's own contiguous run, never a
concatenation, and it carries the hashes of the statements inside it. **A block has no identity
of its own** — no hash is minted for it, and a verdict binds to the statements and the JobCard,
so a block gaining or losing a member changes what is displayed and not what was decided.

**The controls are inert in this version.** Annotation and persistence are the next one, and a
control that looked like it saved would cost someone an afternoon of triage. What the writing
version needs is already in the payload: `job_card_sha256`, a `statement_sha256` per sentence,
and the per-opening membership. Both invalidation rules belong there too — a changed JobCard
hash, or a changed set of statement hashes, drops that opening's verdict and no other's.

`unclear` is not a weaker `supports`; only `does_not_support` reaches `evaluate_job`'s hard
filter; `supports` clears the uncertainty for one opening and never becomes an employer rule.
