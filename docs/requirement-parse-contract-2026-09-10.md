# Why a sentence stopped being allowed to prove anything

2026-09-10. Review of `a56936a`, the third round on the disposition proposer.

## What the review found

Two families, five reproductions, all run against the shipped code rather than the tests.

**Scope lost at the split.** `branches_of` cut on `or` with no syntax tree, so a constraint
written once survived only in the branch it stood next to:

| requirement | branches it produced | consequence |
|---|---|---|
| `(Python or R) and SQL` | `[(Python]`, `[R), SQL]` | Python alone met it |
| `(Bachelor's or Master's degree) and 5 years of product management experience` | `[(Bachelor's]`, `[Master's degree), 5 years…]` | a bachelor's alone met it |
| `5+ years of production experience with Python or R` | `[5+ years … Python]`, `[R]` | one class assignment in R met it |
| `Experience building production models in Python or R` | `[… Python]`, `[R]` | same |

**Promotion inside one obligation.** `_concept_obligation` called `match_requirement_prose`,
which reports the strongest concept in a sentence. `Analysis communication skills` resolved
`direct` on a presentation while the analysis half rested on a skill-list mention. Taking the
weakest obligation afterwards cannot undo it — the aggregation happened one level down.

## The fix that was not made

Teaching `branches_of` about brackets, then durations, then leading activity verbs. Each patch
answers one sentence shape and relocates the wrong guess: what a modifier applies to is a
reading of English, and a regex that produces `meets` is asserting one without recording it.

## The contract instead

`parse_requirement` produces a reviewable artifact between the sentence and the proposal:
source text and its sha256, the parse version, `or`-branches of `and`-obligations with exact
character spans back into the sentence, every modifier whose scope nobody has attributed, and
a `parse_status`:

- `reviewed_complete` — one of two closed templates, with nowhere for a modifier to hide:
  a **bare degree level** (`Bachelor's degree`, `Master's degree or higher`), and a **flat list
  of credential alternatives** (`MD, PharmD, NP, PA, RN, MPH, or 5+ years in clinical health
  IT`). A list whose last alternative is a credential carrying a modifier — `MD, PharmD, or MPH
  in public health` — is *not* complete: whether the field governs all three is the question.
- `ambiguous` — a scope is genuinely undecided, and the artifact names which.
- `unreviewed` — split for reading, by rules nobody has reviewed as sound for that shape.

`meets_invariant_problems` is the single place stating what `meets` requires, and `_proposal`
raises if a `meets` is ever built while it reports anything: parse `reviewed_complete`, no
unattributed scope, every obligation in the branch directly evidenced, every concept inside
those obligations carrying fact ids of its own, and no unread span.

Inside an obligation, `concept_evidence` answers one concept on its own facts and the
obligation takes the weakest necessary one. `match_requirement_prose` is unchanged and still
used by routing, where reporting the strongest concept is the right answer to a different
question.

## Cost, measured on the real worksheet

57 unread requirements from the 2026-09-10 sheet, read against the active snapshot. Nothing was
written to `.jobloom` and no queue row or application state was touched.

| | v1 (hand-checked) | v3 (`a56936a`) | now |
|---|---:|---:|---:|
| `meets` | 14 | 2 | 1 |
| `partially_meets` | — | 11 | 15 |
| `does_not_meet` | 2 | 0 | 0 |
| unproposed | 38 | 41 | 38 |
| `not_a_requirement` | 3 | 3 | 3 |

The 14 the user checked by hand — at least 11 of them promotions — were v1's. The two rounds
since had already cut those to 2; this round is about what remains, and about the four
sentences the review reproduced, none of which was in either count.

Row by row against v3, the only movements are:

- one `meets` stays: `Education: Bachelor's degree or higher`, on the `bare_degree_level`
  template, resting on one education fact.
- one `meets` becomes `partially_meets`: `Clinical credential or equivalent depth: MD, PharmD,
  NP, PA, RN, MPH, or 5+ years in clinical health IT or real-world evidence`. The list itself
  is a template shape and the held MPH answers it, but the label before the colon is longer
  than the label prefix this module strips (34 characters), so the template does not see a
  list. The bound is arbitrary and this is the direction it errs in; the row still cites the
  MPH fact and is one glance to confirm.
- three unproposed become `partially_meets`, all of the form `Bachelor's degree in a
  <field list>, or equivalent practical experience`. v3 suppressed these by copying the field
  onto the bare-level branch — itself an unrecorded guess that the field distributes. They now
  report what is true: the held bachelor's answers that branch, and who the field applies to is
  named as unattributed.
- everything else is unchanged.

Nothing is proposed as `does_not_meet`, unchanged: no fact in this schema asserts the
education, employment or skills record is complete.
