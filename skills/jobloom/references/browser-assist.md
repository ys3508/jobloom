# Browser assist

Covers the platforms that have no open interface and forbid collection. It works because
the subject of every sentence is the user: they browse, they open a posting, they click
apply. The assistant reads what is already on their screen and answers questions about it.

## What makes this different from collection

| | Browser assist | Background collection (out of scope, §5.3) |
|---|---|---|
| Who opens the page | The user, with their own hands | The program, in batches |
| Whose session | The user's, already signed in | Simulated or automated |
| Reach | The one page the user is looking at | Systematic traversal |
| Platform controls | Untouched; ordinary browsing | Rate limits and bot checks worked around |

The test is a single sentence: **the extension never does something the user is not
already doing.** A posting they have not opened is never touched. Nothing is discovered,
traversed, or fetched.

That sentence also settles what the panel *should* do. Clicking a different posting in the
list is the user moving, so the panel follows: it re-reads when the posting under it
changes, scoped to the tab they are on and only while the panel is open. Following someone
is not the same as going somewhere they are not. Asking them to press a button to confirm
an intent they just expressed by clicking is friction, so there is no button.

Following needs `webNavigation`. These sites change the open posting with
`history.pushState`, and a same-document navigation is not reported by `tabs.onUpdated` —
only `onHistoryStateUpdated` sees it. The permission is held for that one event, filtered
by host to the two job sites, then to the active tab and the top frame. Banning it on the
name alone is what left the panel unable to notice the user had moved.

## Parts

- `scripts/assist_bridge.py` — a loopback HTTP server that answers from the local
  registries. Started by the user, holds a token printed once per run.
- `extension/` — Manifest V3 extension: a service worker that opens the panel on a toolbar
  click, and a side panel that reads the open posting and renders the judgement. The panel
  also carries the one-page fill control described below, which runs nothing itself.

There is **no declared content script**. Nothing from this extension executes on a job site
until the user presses *Read this posting*, at which point the reader is injected into that
one tab for that one reading.

Page access is an **optional host permission**, not `activeTab`. `activeTab` is granted by
clicking the toolbar action, which a click inside the side panel is not, and LinkedIn
revokes it again on every in-app navigation — so it does not survive the way this panel is
used. The honest alternative is to ask: the user grants access to `www.linkedin.com` and
`*.indeed.com` once, in Chrome's own dialog, and can revoke it in `chrome://extensions`.

This is genuinely broader than `activeTab`, and worth stating rather than glossing: the
extension is permitted to read those two hosts whenever it runs. What keeps that from
becoming collection is unchanged — no content script, so it only runs when the button is
pressed; no navigation or polling, asserted by test; and the bridge stores nothing unless
`--allow-store` is on. A resident script would have been the
easier build, but it would also mean our code running on every job page the user visits,
which is not what "only acts when the user asks" should mean.

## The second mode: extension-controlled separate guarded worker

Reading a posting and filling a form are two modes with two buttons, two status lines and
two promises. They share a panel and nothing else — in particular, the automatic re-read that
follows the user between postings cannot start a fill, which is asserted by running the
shipped panel rather than by reading it.

What the fill mode does **not** do is drive the tab in front of the user. It cannot: the
extension holds no host permission for any employer form, `worker_protocol` accepts a
loopback origin only, and verifying an upload means hashing a file the extension is never
given a path to. So the run happens somewhere else and the panel says so, in the panel:

> Runs one page in a separate guarded Jobloom browser window.
> Your current tab will not be changed.
> Stops before Submit.

The panel presses a button and holds an opaque execution id. It sends `{application_id}` to
prepare and `{execution_id}` to execute, and those field sets are closed — an origin, a
target, a tab id or a path in the body is a refusal, not an ignored extra, because a field
that is quietly dropped today is a field someone wires up tomorrow. Nothing the panel says
about the user's tab is read as authorization material; the panel does not look at the tab
at all, since what it could see would not be evidence about the window the run happens in.

Everything the run is authorised to touch comes from the execution authority and from the
bridge's own protected state, re-verified at prepare and again at execute: the live lease,
the current package, its expiry, that it has not been consumed, and that the session, page
and application identity have not moved. Three layers refuse a double press — the panel
disables its button, the bridge moves a run out of `prepared` under a lock, and the authority
consumes the grant exactly once. Only the last is a safety boundary; the other two exist so
the user is told rather than left reading identical refusals.

Consuming a grant once is not the same as there being one grant, and the difference is where
a second bridge lives. `--port` starts another instance on the same database, with its own
lock and its own memory of what is prepared, so both could read "nothing live" and both could
issue. The invariant is therefore written where both can see it: a partial unique index on
`execution_grants(session_id, page_id)` over every row that has not been revoked. A file lock
orders the queue and the process lock orders one bridge, but neither is the guarantee — with
both removed, the index alone still leaves a page with one authority.

The predicate says nothing about `consumed_at`, and that is the load-bearing part. The worker
spends its grant *before* it opens a browser, so a rule that excluded consumed grants would
free the page's slot while the run was still starting: a second bridge asking in that window
would find nothing live, be issued its own grant, and fill the page again — and the `running`
flag that would have stopped it is process-local, so it belongs to the first bridge and the
second cannot see it. A consumed grant therefore keeps the slot for good. A run that spends
its grant and then fails leaves its steps pending and its page unauthorisable, which is
deliberate: retrying goes through a new reviewed page or session, never through a second
grant over the same pending steps. A database written before this rule can already hold two
unrevoked grants for one page; the index then refuses to be created and the bridge refuses to
prepare rather than continuing without the guarantee.

**Permissions did not change for this.** The manifest is the same five permissions, the same
single host permission for the bridge, and the same two optional job-site hosts, and a test
asserts it. One thing is worth stating rather than glossing: the bridge token is kept in
`chrome.storage.local`, which is existing browser-assist behaviour and is left as it is. What
does not join it is anything to do with a run — the execution id lives in memory for as long
as the panel is open and no longer, because a capability that outlives the window it was
granted in is a capability nobody is watching.

**This is the local semantic replay only.** Production ATS adapters remain unimplemented and
named. A green run here is not evidence that any live employer form can be filled.

## Boundaries, and where each is enforced

Boundaries live in code, not in a promise, and the ones the extension could otherwise talk
itself out of are enforced on the bridge side:

| Rule | Enforced by |
|---|---|
| Loopback only | `assist_bridge.LOOPBACK`; the server binds nothing else |
| Callers must hold this run's token | `Handler.do_POST`, generated per run by `secrets` |
| A page cannot declare its own card reviewed | `build_card` forces `requirements_reviewed: false` |
| Reading stores nothing | `/positioning` never writes; `/store` is a separate endpoint |
| Browsing does not accumulate a job database | `--allow-store` is off by default |
| Job-site access is optional and revocable | `optional_host_permissions`; the user grants it in Chrome's dialog and can revoke it in `chrome://extensions` |
| Access is scoped to two hosts over https | asserted by `test_page_access_is_optional_scoped_and_granted_by_the_user` |
| Nothing runs unless the panel is open | no `content_scripts`; every read starts from the panel |
| Following is limited to the two job sites | `webNavigation` listeners carry a `hostSuffix` filter |
| The listener never fires for another tab or a subframe | `active?.id !== details.tabId`, `frameId !== 0` |
| An unchanged posting is not re-read | `onlyIfChanged` against the last posting id |
| The extension cannot call a job site | `host_permissions` is the bridge alone |
| No navigation, pagination, clicking, polling | asserted absent from the shipped sources by `tests/test_assist_bridge.py` |
| No automatic submission | the whole product; the assistant stops where filling stops |
| Confirming a submission needs storing enabled | `_confirm_submitted` is behind `--allow-store`, like `/save` |
| A confirmation is the user's word, never an observation | `saved_jobs` reports it as `confirmed after applying`, never as `submitted` |

## What the buttons record, and when

The two apply buttons are pressed *before* the employer's form is open, so what they
record is a decision, not an application. A Workday flow abandoned at the account wall
would otherwise sit in the record as `applied` forever with nothing to correct it, and a
reply rate computed over those rows is deflated by exactly the abandonment rate.

So finishing has its own button, offered only after a decision to apply and pressed after
the form is actually sent:

| Rung | Written when | Claim |
|---|---|---|
| `decision='applied'` | the apply button is pressed, before the form | the user intends to apply |
| `submitted_confirmed_at` | the *I submitted it* button, after the form — or an outcome only the employer could have sent | the user says it was finished |
| `application_core`'s `submitted` | never, while no browser worker exists | positive submission evidence |

Anything counting real applications uses the bottom two. Using the top one counts
intentions. The gap between the first two is reported as `stated_not_confirmed` rather
than divided away, because nothing here measures how many decisions were abandoned.

That last table row is a test, not a convention: `test_it_never_navigates_paginates_or_clicks_for_the_user`
fails if `chrome.tabs.create`, `location.assign`, `.click()`, `setInterval` or
`MutationObserver` ever appear in the extension.

## Running it

```bash
./skills/jobloom/scripts/start-assist.sh
```

**Start it once and leave it running.** The token lives in `.jobloom/assist-token` at 0600
and is reused across restarts, so the panel is configured one time rather than after every
start; `--rotate-token` replaces it if it has been shown to anyone. Restarting is only
needed when this code changes, which is a development concern and not something a user of
the tool should ever do.

Load `skills/jobloom/extension/` through `chrome://extensions` → Developer mode → Load
unpacked. Open the side panel from the toolbar, grant page access once, paste the token
once. After that, opening the panel on a job page reads it, and clicking a different
posting re-reads it. The boundary is not "no listeners" but "nowhere the user is not": the
listener is scoped to the active tab, does nothing when the posting has not changed, and
there is no polling and no reading of a tab the user did not open. When the site
swaps the posting under the panel, it re-reads on its own.

To have it running whenever the machine is on, wrap `start-assist.sh` in a macOS
LaunchAgent or the equivalent; nothing in the bridge needs a terminal.

Add `--allow-store` only when you intend to keep a job you have reviewed. Without it the
assistant reads and forgets, which is the difference between help and collection.

## What the panel answers

A keyword counter tells you how many of a posting's terms your resume contains and offers
to raise the number. That advice is the same whichever term is missing, which is why it
rewards padding: it cannot tell work you did but left off a page from work you never did.

The panel keeps four cases apart, because each asks for a different move:

| | Case | What it means | The move |
|---|---|---|---|
| 🟢 | `hidden_strength` | Confirmed in your facts, absent from the resume this direction uses | Add it. It is your own work |
| 🟡 | `evidence_gap` | On the resume, but with no figure or outcome | Strengthen it |
| 🔵 | `transferable` | Adjacent work, not the same thing | Say it as adjacent. It never becomes direct |
| 🔴 | `real_gap` | Nothing in your facts supports it | Leave it out. A stretch is honest, an invention is not |

The split needs one thing a resume-only tool cannot have: **which of the user's facts the
approved resume actually carries**, read from its claims manifest. Without that set,
`hidden_strength` and `real_gap` are indistinguishable — both look like a missing keyword.

`transferable` is decided before anything else can promote it, so the ordering itself
carries the rule that transferable evidence never becomes direct experience.

## What the verdict is, and is not

Three separate things were collapsed into one call, and each collapse produced a wrong
answer:

- **A page that gave up no requirements is not a job to skip.** Saying "probably skip"
  there passes a parsing failure off as a judgement about the user. It now says it could
  not read the posting.
- **A direction that does not accept the posting is not a reason not to apply.** Whether
  the user can do the job is a question about their evidence; whether it sits inside a
  registered direction is a question about how they are budgeting applications. Letting the
  second answer the first turned a genomics role they match well into "skip" because its
  title was not on a list. It now reads as worth a look, and says the direction may want
  widening.
- **A capability's name is not a requirement.** `Statistical programming` is this
  ontology's label, not something a posting asked for. Emitting it as a requirement and
  then failing to find those two words in the user's facts invented a gap — which is how
  someone whose whole career is R came up short of a skill R is. Matched capabilities are
  kept for routing under `required_skills_capabilities`; only terms the posting actually
  names are judged.

## Finding the posting on a search page

LinkedIn's search view holds the result list and the open posting in one tree, and puts the
search — not the job — in the document title. Neither a selector list nor the document
title identifies the posting there.

What does identify it is the URL: `currentJobId` names the job the user has open. The
posting is the part of the page that links to it, so the reader finds
`a[href*="/jobs/view/<currentJobId>"]` and walks up to the container that holds the
description. That works without knowing any of the site's class names, which is what makes
it survive a redesign. The selector list stays as a fallback, and reading the whole page
stays as the last resort, with the panel saying which happened.

## From page text to a JobCard

The page arrives as prose, so `posting_sections.py` turns it into fields by rule:

1. **Sections.** A controlled set of headings — *Required*, *Preferred*, *Responsibilities*,
   *Compensation* and their common variants — opens a section, and the lines beneath belong
   to it until the next heading. A second set (*EEO*, *About us*, *Physical requirements*)
   closes one without opening another.
2. **Distillation.** A requirement is written as a sentence, but evidence resolves per
   capability, so a twenty-word line would match nothing and read as a gap the candidate
   does not have. Each line is reduced to the terms it names, from two controlled sources:
   `TOOL_TERMS`, and the capability patterns already in the ontology.
3. **What nothing recognised is reported.** Lines that yield no term are listed under
   `extraction.unrecognised_requirements`. A requirement nobody parsed is not the same as a
   requirement nobody has, and the card says which it was.
4. **The stated lines are kept** in `required_skills_stated` beside the distilled terms, so
   the reduction can always be checked against what the posting actually said.

Two ordering rules exist because postings say both things at once: *hybrid* is tested
before *remote*, since a hybrid posting almost always carries the label "Remote Type"
directly above the word Hybrid; and a stated minimum and maximum are read as a range before
a bare figure is.

## What is not verified

The container selectors in the injected reader have not been checked against the live sites
and will drift when either redesigns. The failure mode is deliberately soft: if no container
matches, the script falls back to the page's own visible text and the panel says the pane
was not recognised. Reading whole-page text rather than parsing per-field selectors is the
reason a redesign degrades the reading instead of breaking the extension.

## What this does not do

It does not find jobs. Discovery on these platforms would mean traversal, which is the
line this design exists to stay on the right side of. Jobs arrive through the channels in
`market-sources.json` and through the user's own browsing; the assistant makes the second
of those cheaper, not automatic.
