# Candidate Profile

Three stores, three questions. The Candidate Profile holds *who I am*. The AnswerLibrary holds
*how I answer this employer's question*. An Application holds *what was used this time*. A
field on an employer's form reaches the profile by **meaning**, never by an internal id: an
observer reports `contact.email` and `resolve_canonical_fact` resolves it, against the active
CandidateSnapshot, to exactly one locked fact — or refuses with a stable reason.

`candidate_profile.py` owns the shape (`PROFILE_V1`), the read path
(`resolve_canonical_fact`), and the write path described here.

## The shape

Twenty-one fields in six groups, each labelled with where its demand comes from:

- `demand: corpus` — the reviewed recordings ask for it (14 fields).
- `demand: user` — it exists because a person needs it and no recording proves demand
  (7 fields: middle name, phone extension, street lines, postal code, region, country).
- `PROFILE_V1_DEFERRED` — recorded as unclear rather than admitted or dismissed
  (`profile.location_url`). It has no reviewed shape, so no round may propose it.
- `FORBIDDEN_MEANINGS` — never profile data whatever a form calls it: credentials and
  identity documents, EEO, compensation, referral contacts, and every AnswerLibrary meaning —
  all four immigration questions, both recorded sponsorship wordings, prior employment,
  residence and discovery source.

Two rules in the shape are worth restating because both are cheap to break:

- **`full_name` is not first plus last.** Name order is not universally western, and forms ask
  for the whole name *and* its parts. It is confirmed on its own.
- **A phone is kept as the user writes it.** `contact.phone_country` is a separate confirmed
  field. Nothing derives a country code, an E.164 form or a local number from the other.

## Rounds

A round is a named, pinned set of meanings. `required-v1` is derived from the two measured
properties on each field — the corpus asks for it, and every recorded control carrying it was
marked required — and today that is nine:

```text
contact.first_name  contact.last_name   contact.full_name
contact.email       contact.phone       contact.phone_country
contact.location    contact.location_city  profile.linkedin
```

Optional and `demand: user` fields are deliberately in no round. A profile round costs a new
CandidateSnapshot, and a field no recorded form requires does not earn one.

## The write path

```text
propose-profile  -> private worksheet (0600, inside the private root)
fill-profile     -> Jobloom asks each field; the answers go into that worksheet
confirm-profile  -> draft CandidateSnapshot + impact preview; nothing is activated
register-profile -> the user names the draft by its exact sha256; atomic switch
```

**Asking is the step, not editing a file.** `fill-profile` puts each field to the user in
turn, then writes the answers back into the worksheet it was given — only the three fields the
digest leaves editable, so a filled worksheet is still the one its proposal bound. It runs the
binding checks before the first question rather than after the last, refuses a pipe (an intake
reading from a script is how a scripted value gets filed as a person's own word), and writes
nothing until every field has been asked and the user says to write it.

It validates and it offers; it never rewrites. An address with no `@` is refused and asked
again. A country code typed as `1` gets `+1` **offered** — shown, and applied only if the user
says so — as does a profile link pasted without its scheme. Leading and trailing whitespace is
stripped, which is the absence of a change rather than one. Nothing else is touched, and
nothing is ever computed from another field: the no-derivation rule does not become negotiable
because the value arrived through a prompt instead of an editor.

**Proposing** reads a value out of a composite contact fact so the user checks rather than
retypes it, and proposes nothing for anything else. A fact that already carries a
`canonical_id` is not scanned — it states one meaning already and is not a composite. Two facts
offering one meaning propose nothing and say so: picking either would put a value in front of
the user with the authority of a proposal and no arbitration behind it.

**Two confirmations, not one.** `confirmed_by_user` says the value is right;
`autofill_allowed_by_user` says Jobloom may type it into an employer's form. `gate_status`
maps them:

| confirmed | autofill | result |
| --- | --- | --- |
| false | false | left out of the draft entirely |
| true | false | a `confirmed` fact — recorded, and the planner still refuses it |
| true | true | a `locked` fact once the snapshot is registered |
| false | true | refused |

Intake never grants `locked` on its own. It records the authorization the user gave; the fact
becomes locked when the snapshot carrying it is registered.

**The draft only adds.** Every fact in the active snapshot is copied and checked against its
recorded hash, so "this draft alters nothing" is verified rather than asserted. The composite
fact stays exactly as it is — answers name it in their `dependent_fact_ids`, and dropping it
would break an invalidation chain to tidy up a value. The facts split out of it record it in
`source.derived_from`. A meaning that already has a profile fact is refused rather than given a
second one.

**The preview says what activation would cost**, before anything is activated: how many
material locks are invalidated, which resume versions therefore need rebinding, which
applications are affected, how many pre-submit reviews are invalidated, and which answers go
stale. It is computed with the same predicates registration uses, so it cannot promise one
thing and the switch do another. Adding facts changes no existing fact, so no answer's
dependency moves — but every material lock bound to the old snapshot dies regardless.

**Registration is atomic.** The proposal is claimed and the draft marked inside the transaction
that `register_snapshot` commits, so a failed switch takes the bookkeeping with it: no consumed
proposal behind a snapshot that does not exist, and never half a profile. A proposal is
single-use and a draft is single-use. Both are bound to the snapshot they were prepared
against; if a snapshot is registered in between, the whole batch is refused rather than
confirmed against a profile the user never previewed.

## What this does not do

- It does not migrate the real profile. Running it against real data is a separate, user-owned
  step, and it is not free: the active resume versions are bound to the old snapshot hash, so
  every material lock behind them is invalidated. Carrying one across is a successor, not a
  rebind — see the migration section of `references/resume-versions.md`.
- It does not cover the optional or `demand: user` fields. They are reachable only when a round
  names them, and no round does.
- It does not put a profile fact on a real employer's form. `fill_core` now resolves a fact
  field by its `canonical_id` against the locked snapshot, so a registered profile is what the
  planner reads — but the only observer that produces such a field is the local semantic
  replay. Production ATS adapters remain unimplemented; see `references/known-liabilities.md`.
- It does not capture anything from a web page. Nothing here reads what a user typed into an
  employer's form; every value in a profile came from a worksheet the user filled in.
- It does not answer an employer's question. Work authorization and every sponsorship wording
  belong to the AnswerLibrary and to its mandatory pauses; the profile refuses them by name.
