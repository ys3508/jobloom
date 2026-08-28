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

## Parts

- `scripts/assist_bridge.py` — a loopback HTTP server that answers from the local
  registries. Started by the user, holds a token printed once per run.
- `extension/` — Manifest V3 extension: a service worker that opens the panel on a toolbar
  click, and a side panel that reads the open posting and renders the judgement.

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
| Nothing runs before the user asks | no `content_scripts`; the reader is injected on the button press |
| The extension cannot call a job site | `host_permissions` is the bridge alone |
| No navigation, pagination, clicking, polling | asserted absent from the shipped sources by `tests/test_assist_bridge.py` |
| No automatic submission | the whole product; the assistant stops where filling stops |

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
once. After that, opening the panel on a job page reads it: opening the panel *is* the
request, and asking for a second press for the same intent is friction, not a boundary.
The boundary is that nothing else triggers a read — there is no navigation hook, no
polling, and no reading of a tab the user did not open. **Re-read this posting** is there
for when the site swaps the posting under the panel without a page load.

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
