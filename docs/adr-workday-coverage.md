# ADR: How to cover the Workday-hosted slice of the target market

Status: **Decided 2026-08-29 — option 3, a new Tier 5. See the resolution below.**
Recorded 2026-08-29. Audience: the product owner. Nothing here is a to-do.

---

**This is not a to-do.** Nobody can "do" it. It is a judgment call between two legitimate
options, and it must not be resolved by an engineer picking whichever is easier to build.

### Context

Workday was deliberately kept out of Tier 0. Its tenant feed is a POST to an endpoint that
is not published for outside consumption, so it does not meet the Tier 0 definition — the
platform explicitly permits public reading. Putting it in Tier 0 would launder a scraper
into the clean layer, and the entire value of Tier 0 is that its boundary is honest.

### Why this is not marginal

Removing Workday from Tier 0 made a fact visible rather than creating a problem: the
densest part of this market has no first-party clean source. The largest employers across
the target directions — IQVIA, Parexel, Medpace, Regeneron, Verily, Moderna — are all
Workday-hosted. In a 31-company probe, Workday alone was half the adapter gap, and after it
the tail was roughly one company per vendor. The adapter question therefore collapses into
this single policy question rather than into a roadmap of eight to ten adapters.

### The two legitimate options

1. **Buy the slice through Tier 1.** A compliant jobs-aggregator API carries the legal
   responsibility and already covers these employers. Cost: recurring vendor spend. This is
   why Tier 1 is a primary source in this market rather than a fallback.
2. **Explicitly redefine the Tier 0 boundary** to admit Workday tenant feeds, with the cost
   written down plainly: what "explicitly permits public reading" now means, and why the
   POST-to-an-unpublished-endpoint case is being accepted.

### What is not acceptable

Letting Workday slip into Tier 0 quietly — adding a Workday adapter without either paying
for the coverage through Tier 1 or openly restating the boundary. The honest boundary is
what made this gap visible in the first place. Erasing it silently turns a policy decision
back into a code change.

### Until this is decided

The Workday-hosted slice is a known, intentional hole, not a bug. Do not "fix" it by adding
an adapter.

Recorded in `skills/jobloom/references/ats-sources.md`.

---

## Resolution (2026-08-29)

Neither of the two options as originally framed. A third was chosen: **a new Tier 5 for
sources read on the operator's own compliance judgement.**

Workday coverage is obtained, and the Tier 0 boundary does not move by a millimetre. Dirty
data wears its own label rather than a clean one. Tier 5 is defined in
`skills/jobloom/references/ats-sources.md`; the framework, its provenance rules, and its
tests landed before any Workday adapter, so what gets reviewed first is the isolation
boundary rather than a specific source.

The measure that makes this hold is the one absent from the first draft of the spec: a
`self_asserted` source may never shape a market profile, and `market_profile` refuses it by
name rather than by omission. Market profiles decide which career directions are proposed;
data admitted there would contaminate the user's positioning itself, with its label intact
on a card nothing downstream reads.

The Workday adapter is a separate change, registered as the first Tier 5 entry.
