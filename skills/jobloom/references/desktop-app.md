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
- **Everything past onboarding.** Resume import, the job queue, the tracker, answers, and the
  pre-submission review are still commands or still the browser panel.

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
