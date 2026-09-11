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

## The sponsorship triage page

`GET /triage`, served from `assets/triage.html`, over `GET /api/sponsorship/queue` and
`POST /api/sponsorship/posting`. Both read a built queue file and a pull directory; neither
opens the database.

**Why it exists.** Over the 2026-09-10 queue, all 112 openings carry `sponsorship: unknown`,
and the candidate's `sponsorship_future` is true, so `evaluate_job` sends every one of them to
`sponsorship_requires_review`. For 28 of them the posting said something and nobody has read
it. Those 28 are the page.

**It chooses nothing.** No keyword scan, no suggested verdict, no pre-selected control. The
extractor stopped short of a verdict on purpose and this page does not finish the job on its
behalf: it shows the employer's sentence with enough of the posting either side to place it,
the structured status as it stands, a way into the full description, and three choices.

The three controls are inert in this version. Annotation and persistence are the next one, and
a control that looked like it saved would cost someone an afternoon of triage. What the writing
version must carry is already in the payload: `job_card_sha256` and a `statement_sha256` per
sentence, so a verdict binds to the card and the wording it was read from and does not survive
either being edited — the rule `direction_core` already applies to a routing record.

`unclear` is not a weaker `supports`; only `does_not_support` reaches `evaluate_job`'s hard
filter; and `supports` is evidence about one posting, never about the employer.

## Boundaries

Kept in the service, not trusted to the page:

| Boundary | How |
| --- | --- |
| Nothing on the network reaches it | Binds `127.0.0.1` only |
| Another page cannot call it | A session token, generated per run, never written to disk; it arrives in the URL this process opens, so nobody types or pastes one |
| A website cannot post to it by guessing the port | A request whose `Origin` is present and is not this server's own is refused |
| The page cannot fetch anything | `Content-Security-Policy: default-src 'none'; connect-src 'self'` |
| No value is logged | The request log is off; unexpected errors return a bare code, because a message may carry a path or a value |
| The page holds nothing | No storage of any kind; values live in the window while it is open and go to the private worksheet |
| A document under review can be read and nothing else | `frame-src blob:` only, the PDF fetched with the token and shown from a blob; served `inline`, `no-store`, and only for a version an open migration is carrying |
| A cover letter is not left behind | A bound cover letter approved against the old snapshot stops the carry by name, before the application moves |

The token is in the URL the process opens, and the page removes it from the address bar on
load, so it stays out of history and out of anything a user might copy to somebody.

## What the window trades away

**The exact-hash approval moves off the person.** At the terminal, registering means naming a
draft by its 64-character digest, which is how the approval binds to one specific set of facts
rather than to whatever is pending. The window keeps the binding — the button carries the hash
of the draft whose impact is on the screen — and drops the part where a person could check that
hash against the one they were shown. That is the trade a window makes for not asking anyone to
compare 64 characters. It is recorded here rather than glossed, and it is the reason the page
is served from a process the user started rather than from anywhere else.

## Running it

```bash
python3 skills/jobloom/scripts/jobloom_app.py --db .jobloom/jobloom.db
```

`--private-root` and `--store` default beside the database. `--no-browser` prints the URL
instead of opening it. `--port` is chosen by the OS unless given.
