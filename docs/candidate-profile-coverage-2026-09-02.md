# Candidate-profile coverage, measured 2026-09-02

What a Candidate Profile would have to hold, and what the active snapshot holds today.
Measured before designing the shape, because the reviewed field set has to be decided by
what the corpus asks for and what the profile actually contains — not by a list written from
imagination.

**Value-free by construction.** Fact ids, types, statuses, part counts and canonical target
names. No fact value, no candidate detail, no local path. Counts drawn from the private
library are recorded here once; the corpus half is reproducible and is checked by
`tests/test_candidate_profile_coverage.py`.

## What the recorded corpus asks a profile for

15 canonical meanings across the three vendored fixtures carry
`disposition: fact` — that is, the corpus expects them to come from stable profile data
rather than from an answer the user gives per employer:

- `contact.email`
- `contact.first_name`
- `contact.full_name`
- `contact.last_name`
- `contact.location`
- `contact.location_city`
- `contact.phone`
- `contact.phone_country`
- `contact.preferred_name`
- `employment.current_company`
- `profile.github`
- `profile.linkedin`
- `profile.location_url`
- `profile.portfolio`
- `profile.website`

A further 5 meanings are `always_manual` and must never enter a profile:

- `employer_defined_compensation_manual`
- `employer_entity_not_approved`
- `referral_contact_requires_user`
- `sponsorship_meaning_ambiguous`
- `voluntary_eeo`

## What the active snapshot holds

- Facts in the active snapshot: **78**, of which **4** are of a type a
  profile would draw on (`identity`, `contact`, `location`).
- Of those, **1** hold more than one meaning in a single string.
- Of those, **2** are `locked`; the rest are `confirmed` only.

## Coverage

| Canonical meaning | State today |
| --- | --- |
| `contact.email` | held inside a composite fact — not separately addressable |
| `contact.first_name` | no fact resolves to this meaning |
| `contact.full_name` | no fact resolves to this meaning |
| `contact.last_name` | no fact resolves to this meaning |
| `contact.location` | no fact resolves to this meaning |
| `contact.location_city` | no fact resolves to this meaning |
| `contact.phone` | held inside a composite fact — not separately addressable |
| `contact.phone_country` | no fact resolves to this meaning |
| `contact.preferred_name` | no fact resolves to this meaning |
| `employment.current_company` | no fact resolves to this meaning |
| `profile.github` | no fact resolves to this meaning |
| `profile.linkedin` | held inside a composite fact — not separately addressable |
| `profile.location_url` | no fact resolves to this meaning |
| `profile.portfolio` | no fact resolves to this meaning |
| `profile.website` | no fact resolves to this meaning |

**Nothing is directly usable.** Not one of the fifteen resolves today to a single fact the
planner could fill from.

## Three findings that shape the design

**1. There is no production mapping from a canonical meaning to a fact.** `fill_core` reads
`source_id` off the observation and looks it up — so something upstream has to know that, say,
`contact.full_name` means a particular fact id. The only such mapping in the repository is
`FACT_IDS` in `tests/fixtures/replay_observer.py`: a **test fixture**, three entries long.
A profile therefore needs its own canonical-id-to-fact binding; splitting values alone would
not make them reachable.

**2. Facts a profile would serve are not locked.** `fill_core` refuses any fact that is not
both `status = 'locked'` and `locked`, with `candidate_fact_not_locked`. The composite contact
fact and the other contact-typed fact are `confirmed` only. So splitting the composite is
necessary and not sufficient: the facts it produces have to be locked before the planner can
use them, and locking is part of snapshot approval rather than something intake can grant
itself.

**3. Two pairs look like drift rather than data.** The snapshot holds two `identity` facts of
identical length and two `contact` facts, one composite and one short single value whose
length and provenance suggest a location rather than a contact detail. Whether these are
duplicates, supersessions the snapshot kept, or a mistyped fact is a question for the user;
the measurement records that they exist without guessing which.

## What this does not show

- It does not say which fields the profile should have. The corpus proves demand for fifteen;
  addresses appear in none of the three fixtures, so a postal address would be a requirement
  from the user rather than from the recorded evidence.
- It does not show that any value is correct. Nothing here read a value.
- It does not show what a live employer form asks. The corpus is a reviewed semantic model of
  real recordings, not current ATS DOM.

