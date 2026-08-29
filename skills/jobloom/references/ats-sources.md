# ATS public job-board APIs as a first-party job source

Decision recorded 2026-08-29. Implemented in `scripts/ats_sources.py`.

## Decision

**Read postings from the company's own ATS, not from a job board that re-published them.**

Most employers post through a hosted applicant tracking system, and several of those
systems publish an unauthenticated, first-party JSON endpoint per company board — built
to be read. A posting seen on an aggregator usually originated in one of them. Reading
the origin gives structured fields, a stable posting identifier, the employer's own
canonical URL, and no question about whose terms apply.

Jobloom does not need every job in the world. It needs the openings at the companies a
user's approved directions actually target — and that set of companies is enumerable, so
the set of endpoints is too.

## Supported systems

Each endpoint below was read live on 2026-08-29 and each adapter is written against the
payload that came back, not against recollection of the documentation.

| ATS | Endpoint | Detail call | Notes |
| --- | --- | --- | --- |
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | no | Body is HTML-escaped inside the JSON; carries `requisition_id` |
| Lever | `api.lever.co/v0/postings/{token}?mode=json` | no | Feed lists open postings only; does not name the company |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true` | no | Structured compensation components; does not name the company |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/{token}/postings` | yes | Paginated; the body lives on the per-posting detail call |

**Workday is deliberately absent.** Its tenant job feeds are a POST to an internal
endpoint that is not published for outside consumption, and an adapter for it would be a
scraper wearing an API's clothes. Workday employers stay a manual `ingest_job.py` URL.
How that slice gets covered is an open policy decision, not a missing adapter — see
[`docs/adr-workday-coverage.md`](../../../docs/adr-workday-coverage.md). Do not resolve it
by adding an adapter.
**Workable is absent** for a narrower reason: its per-account endpoint could not be
verified against a live board, and an unverified endpoint in a shipped adapter is worse
than no adapter.

## Tier 5 — sources read on the operator's own compliance judgement

Recorded 2026-08-29, resolving `docs/adr-workday-coverage.md`.

**Tier 5 — self-asserted.** Postings read through an endpoint the platform does not
document for outside consumption, such as a Workday tenant feed. The platform does not
permit public reading; inclusion in this tier is the operator accepting the compliance
judgement themselves, not the platform granting anything.

The difference from Tiers 0–4 is not data quality. It is who carries the compliance
judgement: for Tiers 0–4 that is the platform or a vendor, for Tier 5 it is the operator.
Tier 5 data is never described as a clean or platform-permitted source. Its authorization
is permanently `self_asserted` and must be stated wherever source trustworthiness is
described to a person.

### This does not relax Tier 0

Tier 0's definition is unchanged and still admits only sources the platform explicitly
publishes for public reading. **Workday does not become admissible to Tier 0 because Tier 5
now exists — it belongs in Tier 5 precisely because it cannot enter Tier 0.** Tier 5 is not
a loosening; it is an honest, isolated place for a source we know is not clean and choose to
read anyway. Any future source arguing "Workday got in, why not me" gets the same answer: it
goes in Tier 5 too, not Tier 0.

### `self_asserted` never becomes platform-permitted

Authorization is derived from the adapter, never from caller input, and travels **on the
card** — not looked up from the registry at submission time. The registry changes: a source
can be disabled, removed, or registered again under a different basis, while an archived
card still has to say six months later how it was read. This is the rule the resume archive
already follows — copy the bytes, never a pointer.

There is no path that raises a card's authorization. A source that genuinely gains platform
permission is registered again under the new basis, which is a new registry entry, not a
relabelled old one.

### Tier 5 may never shape a market profile

This is the deepest of the contamination paths and the least visible. `market_profile`
aggregates JobCards into profiles that feed `career_direction_core`, which proposes career
directions and scores their market and accessibility axes. A `self_asserted` posting that
reached a profile would shape *where the user looks for work* — its label sitting untouched
on a card nothing downstream reads. **Silent promotion is not only relabelling; it is also
being consumed by something that never checks the label.**

So `market_profile` refuses `self_asserted` **by name**, in `REFUSED_AUTHORIZATION_BASES`,
rather than merely omitting it from the accepted set. Defence by omission is not defence:
the next person who wants scraped postings to be more useful adds one word to
`AUTHORIZATION_BASES` and cannot tell a deliberate exclusion from an oversight. A named
refusal makes that edit fail a test that says which gate is being removed.

### What a Tier 5 registry row records beyond the others

`compliance_basis` — why the operator accepts reading this source — and `known_risks` —
when it stops being readable or acceptable. Registration is refused without both. The
compliance responsibility sits with the operator here, so the reasoning has to be written
down and traceable rather than implicit. Conversely a platform-permitted source refuses a
private rationale: the platform's own terms are the basis, and recording another beside them
would blur which one applies.

### Adding Tier 5 did not unify the authorization vocabularies

Two exist and still diverge. `ats_sources` records `public_job_board_api`, which is not a
member of `market_profile.AUTHORIZATION_BASES` (`user_supplied`, `official_api`,
`licensed_dataset`, `employer_feed`). Reconciling them is a separate contract change with
its own stored records, and is deliberately not attempted here.

## Registration comes before pulling

A board is pulled only after `add-source` records it in `<private-root>/ats-sources.json`
with the company, the ATS, the board token, who registered it, the endpoint template it
authorizes, the vendor's own documentation for that endpoint, and — unless registration
was forced with `--skip-verify` — the time the board was read and how many postings it
held. Nothing pulls an endpoint the registry does not name, so the full set of hosts
Jobloom touches is one file a user can read.

The board token is validated against a closed character class before it is put into a
URL. A token carrying a path or a host would move the request somewhere the registry
never authorized.

No adapter sends a credential, a cookie, or a stored session. Every request is an
anonymous GET the ATS publishes for anonymous GETs.

## A pulled card is an unreviewed card

A pull produces the same JobCard the rest of the pipeline already consumes, and it
produces it in the same state a scraped one arrives in:

- `requirements_reviewed` is `false` and `extraction.needs_user_review` is `true`.
- `sponsorship` is `unknown`. The scan quotes the sponsorship sentences verbatim into
  `sponsorship_statements`; it never decides the value.
- `required_skills`, `preferred_skills`, `responsibilities`, and `seniority` stay empty.
  An ATS board publishes prose, not a structured requirement list. Filling those fields
  from prose would be an inference presented as a fact the employer stated.
- `source` is `ats` and `ats` names the system, so a card's provenance survives into the
  job store.

Cards additionally carry `requisition_id` where the ATS exposes one, which gives
`application_core` a first-party key for duplicate detection rather than a URL match.

**Identity is the ATS posting, not the URL or the title.** A card's `job_id` derives from
`{ats}|{board_token}|{posting id}`, so a re-titled or re-hosted posting resolves to the
same job on the next pull, and the same posting on two different boards stays two jobs
for `application_core` to reconcile.

## Prose becomes structure, or the card routes on its title alone

`direction_core` never reads `description` — it is on the routing denylist — and an ATS
board publishes prose, not a structured requirement list. A card that carries its evidence
only in `description` is therefore routed on its title and nothing else.

Measured over 1,199 distinct openings from the registered boards, against the approved
portfolio and the real candidate profile: **8 reached review.** `outside_direction_title_scope`
fired on 1,185 of them. A pull-time title filter and no filter at all produce the same
result, because routing re-applies the same constraint either way.

So every pulled card is passed through `posting_sections.extract` — the same rules-only,
model-free extractor the browser path uses — which turns the parts a posting states
outright into `required_skills`, `preferred_skills`, `responsibilities`, and
`compensation_structure`.

Two rules govern it:

- **What the board stated wins.** The extractor reads prose, and prose is the weaker
  source; it may only fill a field the board left empty or unknown. A Lever posting that
  labels itself hybrid stays hybrid however the description reads.
- **Structuring proposes, it never reviews.** `requirements_reviewed` stays false and
  `extraction.needs_user_review` stays true. The verbatim requirement lines travel in
  `extraction.sections` so a reviewer sees what the employer actually wrote, because a
  recognised term is not the requirement.

### Reading is more generous than routing

`posting_sections` reads lines up to 1,000 characters; `direction_core.JOB_FIELD_SHAPES`
rejects a routed list item over 500 and raises `malformed job card field`. The gap is
closed at this boundary, by splitting a long item on sentence boundaries and truncating
only what still will not fit — never by reading less. Dropping a requirement for being long
is a worse failure than carrying a shortened one. `test_a_structured_card_satisfies_the_routing_contract`
routes a built card through `_validate_job_shape`, so the two caps cannot drift apart
silently.

## Country comes from a country field, not from a location string

A two-letter token is only read as a country when the board puts it in a field it labels
as the country — Lever's `country`, SmartRecruiters' `location.country`, Ashby's
`addressCountry`. In a free-text location it is not: "Philadelphia, PA" is not Panama and
"Chicago, IL" is not Israel, and a card carrying a country nothing checked is precisely
what a country filter must never be handed. Free text resolves through spelled-out names
instead — country names, and US state names, which is how these boards write a US
location. "Georgia" is deliberately unresolvable, being a state and a country at once.

One shape is an exception, because it is unambiguous in practice: the trailing segment of
a multi-part location. "Philadelphia, PA" is Pennsylvania, not Panama, and a Greenhouse
board writing "City, ST" is the common case. That reading is recorded — the card carries
`country_inferred_from_state_abbreviation` in `extraction.notes` — so it can be audited
rather than trusted. Its cost is that a board stating no country and writing "Berlin, DE"
would be read as Delaware; every board that labels its country outranks the rule, so in
practice only Greenhouse reaches it.

A location that resolves to nothing still yields `country: unknown`, which the evaluator
treats as an uncertainty to review rather than a failure — so **do not pass `--country` to
a pull.** Filtering on a field the board never stated is how a real opening disappears
silently; the direction's own country criteria apply at routing, where they belong.

## One opening posted per city is still several postings

A board listing one role in forty cities returns forty postings, each with its own
identifier, and each becomes its own card — the puller reports the board, it does not
reinterpret it. Duplicate detection belongs to `application_core`, which collapses them on
the description fingerprint, and giving the puller a second, divergent notion of identity
would be worse than the repetition.

What the puller owes is visibility: every pull reports `distinct_descriptions` and
`repeated_postings` alongside `kept`, so "kept 59" is never mistaken for fifty-nine
openings before the cards are even written.

## Filters narrow the fetch; they never decide fit

`pull` accepts only hard, checkable narrowing: posting status, title substrings, location
substrings, country, work arrangement, employment type, and a posted-since date. Every
rule reports how many postings it dropped, an explicit `--limit` reports what it cut, and
a posting with no date survives a date filter and is counted separately rather than being
discarded for a field the board left blank.

Relevance is not decided here. That belongs to the approved direction profile and
`direction_core.route_job`, which reads a reviewed card and never the raw posting. A
discovery filter that started scoring titles would be a second, unapproved routing path.

## Cost

Zero models. A pull is HTTP plus JSON parsing plus string normalization. Only
SmartRecruiters costs a request per posting, and those requests are made *after* the
filters run, so a narrow pull stays a small number of calls.

## When to revisit

1. A user's directions target a company whose ATS is not covered here and whose board
   publishes a verifiable public endpoint — add the adapter, with the payload read live.
2. A supported endpoint changes shape. The adapters are written against recorded payloads
   in `tests/test_ats_sources.py`; a shape change should break a test, not a pull.
3. Coverage from registered boards stops being the binding constraint on finding roles.
   That is the point at which a licensed aggregator feed earns its cost — and it would be
   registered the same way, as another named source with a recorded basis.
