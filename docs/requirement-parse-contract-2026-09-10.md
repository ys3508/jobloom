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

---

# Second round: five findings on `350dd4f`

The contract above shipped with five holes, all found by review of the built code.

## 1. `reviewed_complete` was granted by a regex

A template match establishes that a machine recognised a shape. Whether a person looked at the
sentence is a different fact, and the status name asserted the second while establishing only
the first — the same substitution this repository refuses everywhere else.

Two statuses now, and they are different claims:

- `closed_template` — recognised by rule, **nobody reviewed this line**. Still allowed to
  conclude, because the shape has nowhere for a modifier to hide; the rendered sheet says so
  in those words.
- `reviewed_complete` — a person approved *this* parse. It comes only from a review registry
  entry keyed by the sha256 of the sentence, carrying the distiller version, the sha256 of the
  tree they saw, and who approved it and when. A mismatch on any of those does not carry over,
  and says why. No registry file means no reviewed parses, which is the honest default.

`python3 disposition_proposals.py --parse "<requirement>"` prints the artifact a reviewer
approves, with both hashes and every span.

## 2. The provenance was written and never read

`source_sha256` and `parse_version` were recorded and no check consulted them. The invariant
now recomputes the source hash and the tree hash, rejects a parse from another distiller
version, and re-slices every obligation span against the source text — a parse whose spans no
longer cut the sentence they claim to cannot license anything (`source_hash_mismatch`,
`ast_hash_mismatch`, `parse_version_mismatch`, `span_does_not_match_source`,
`missing_provenance`).

## 3. Both closed templates could still produce a wrong `meets`

The degree template read the span with `[A-Za-z]+` and judged completeness on the words that
scan returned, so everything it could not tokenise was invisible:

| requirement | held | before | now |
|---|---|---|---|
| `Bachelor's degree, 5+` | a bachelor's | **meets** — the 5 was unreadable | `ambiguous` |
| `Bachelor's degree 学历` | a bachelor's | **meets** | `unreviewed` |

Completeness is now checked against every character in the span: only whitespace and the
punctuation that writes a degree name may sit between the pieces the template read. A comma
fails it too, which is right — `MPH, MS, or MA` is a list of alternatives, and the credential
list template is the one that reads those. (Before, the degree template swallowed it into one
obligation and only a coincidence — `STATE_SHAPED` eating `, MS` and `, MA` as state codes —
kept it from concluding.)

## 4. `_proposal()` believed the caller

The assertion checked the `problems` list it was handed, so any caller passing `problems=[]`
alongside `meets` granted itself the licence the invariant exists to withhold. For a `meets`
the list is now recomputed inside `_proposal` from the parse and the obligations being
reported; what the caller passed is ignored.

## 5. Professional credentials were answered out of `held_degrees()`

A university awards a degree; a board issues a licence. Reading both out of the education
record broke in both directions:

- **Over.** `Bachelor of Science – Nursing; Philadelphia PA May 2018` made the profile hold a
  physician assistant licence, and `MD, DO, or PA` **met** on it. (With a comma before the
  state it did not, so the bug was punctuation-deep.)
- **Under.** A licence the profile genuinely holds, recorded as a `certification` fact, could
  not answer a licence requirement at all.

Academic credentials now come from education facts, licences from certification facts, and
neither answers for the other. A missing licence names the record that would carry it: "the
recorded certification facts do not name it, and nothing asserts the certification record is
complete."

## Effect on the worksheet

Unchanged where it matters: 1 `meets` of 57, `Education: Bachelor's degree or higher`, now
labelled `closed_template` — recognised by rule, reviewed by nobody.
