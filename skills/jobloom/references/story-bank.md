# Story Bank

Spec: [`docs/jobloom-story-bank-spec.md`](../../../docs/jobloom-story-bank-spec.md).

**What exists today is build-order steps 1–3 of that spec** — the first usable vertical
slice: the registry, claim binding, approval, revocation, snapshot carry, reviewed competency
mappings, `story map`, and `answer draft` through the existing AnswerLibrary.
`story find-gaps`, retrieval drills, interview usage reporting, and `story improve` are
**not implemented**.

Nothing here fills a form or submits anything. `story_answers.propose` decides what may
answer one observed field and, where that is a story, writes a draft the user must approve;
approval authorizes reuse of that one answer and nothing else.

## What a Story is

A Story is what a person says out loud about something they did — in an interview, or in the
free-text box on an application form. It is **not** a source of truth. It is a versioned,
user-approved projection over CandidateFacts that were already confirmed.

The reference implementation this adapts (`noamseg/interview-coach-skill`, MIT) keeps its
storybank as markdown, where the number in the `Result` line is attached to nothing. That is
the gap this closes: here every factual assertion binds to EvidenceUnits, and the binding is
what makes the story usable at all.

## The narrative must be completely accounted for

A version's narrative is the four STAR fields plus the earned secret and reflection. Every
span of it must be either:

- a **claim** — a verbatim span bound to one or more EvidenceUnits, carrying an
  `evidence_class`; or
- a **framing span** — a verbatim span the user explicitly marked as narrative framing,
  which is never evidence and never counts as coverage.

Whatever neither covers comes back as `unbound_spans`. A version with any of them can be
drafted and read, and **cannot be approved or selected**.

The alternative was to ask a model which sentences make factual assertions. That puts a model
in front of the gate deciding whether a story may be used — the wrong end of the ladder, and
unfalsifiable besides. This asks the author instead: account for all of it.

`account_for()` matches spans verbatim and never overlaps them, so a sentence written twice is
covered once; the repeat comes back as residue. Residue that is only whitespace or punctuation
is not unbound material.

## Evidence classes

A claim's `evidence_class` is one of `mention_only`, `transferable`, `strongly_related`,
`direct`, or the separate `unsupported`.

- **A claim may weaken its evidence and never strengthen it.** `EVIDENCE_ORDER[class]` may not
  exceed the strongest source it cites. This is the rule `resume_core.validate_claims_manifest`
  already applies to a resume claim, applied here for the same reason: a sentence cannot be
  better evidenced than the evidence it cites, however it is worded.
- **`unsupported` may be drafted and never approved.** Writing down "this part has nothing
  behind it" is the point of the class; `approve_version` refuses it by name.

## A capability names the claims that evidence it

`primary_capability` and each `secondary_capabilities` entry is a **binding**, not a label:

```json
{"capability_id": "cap.survey-design", "claim_ids": ["c1", "c2"]}
```

The claim ids must exist in the same version, and a reviewed mapping
(`record_mapping`) carries its own `claim_ids` for the same reason.

**Why it is not a bare id.** The class a competency is retrieved at used to be the strongest
class anywhere in the version, so a capability supported only by `transferable` evidence was
retrieved as `strong` whenever some unrelated claim in the same story happened to be
`direct`. No rule was missing — the rule was reading the wrong rows. `bound_class()` computes the class
from the binding's own claims.

**What the binding does not scope.** Two different questions live here and collapsing them
was its own defect. *What evidences this capability* is binding-scoped and decides the
retrieval band. *What does this text assert* is text-scoped: the answer is the whole rendered
version, so its `evidence_refs` and `dependent_fact_ids` cover **every** claim in it. Scoping
those to the binding left an answer asserting things whose evidence was not recorded, and
whose loss would not have invalidated the answer because the fact was not among its
dependencies.

## Approval is of an exact content hash

`approve_version(connection, version_id, content_sha256)` requires the hash as an argument
rather than looking it up, so an approval cannot land on a version that changed between being
shown and being confirmed. Editing one character produces a new immutable version and the
previous approval does not follow it.

Capability mappings, domain tags, framing spans, and the earned secret live on the version
rather than beside it, because changing any of them changes what the user approved.

## A Story is bound to the snapshot it was approved against

When the active CandidateSnapshot changes, an approved Story becomes unselectable with reason
`candidate_snapshot_changed`. Approval does not carry silently.

- `stranded()` lists approved stories the active profile left behind, each with what moved.
- `restate(version_id)` re-resolves the bindings read-only. EvidenceUnit ids are derived from
  the snapshot, so the same fact has a different id under the new one; the **fact** is what
  persists and the reference is re-derived from it. Each claim comes back `unchanged`,
  `evidence_weakened` (with the class the new profile actually supports), or
  `evidence_missing`.
- `prepare_successor(version_id)` writes an unapproved successor with the re-derived
  references. A claim whose evidence got weaker is carried **at the weaker class**, never at
  the class it used to hold. A claim whose evidence is gone stops the carry: it must be
  re-evidenced or removed, not carried.

The successor arrives unapproved however little changed. An unchanged projection is still a
projection of a different profile, and the user is the one who says so — the same explicit
successor pattern `resume_migration` uses for a resume.

## Usage is counted from events

`use_count` and `last_used` are read from append-only `story_usage_events`, never stored as
columns. There is deliberately no counter for a caller to set and disagree with. Overuse
thresholds are presentation policy for a later step, not evidence truth.

## Confidentiality

`reusable` | `employer_confidential` | `application_confidential`, declared when the story is
first drafted, and a confidential story must name what it is confidential to. `selectable()`
returns the confidentiality alongside its answer so a caller cannot read one without the
other, and `map_stories()` enforces it as a hard AND restriction: `employer_confidential:A`
never surfaces for employer B, and — because absent context does not open a restriction — it
does not surface when no employer was resolved either. `application_confidential` requires
the exact application id.

**Identity is resolved, never supplied.** The only identity input is `application_id`;
`application_identity()` reads the employer from the `applications` → `jobs` rows and matches
on the normalized name. There is deliberately no `employer` parameter on `map_stories` or
`propose`. A caller able to name the employer could unlock any employer-confidential story by
naming the right one, which is not a restriction but a password written on the thing it
protects.

## Mapping a story to a competency

`map_stories(competencies, application_id=, advisory=)` is read-only and produces nothing
submittable. It runs in two layers, and the separation is the point.

**The deterministic layer** decides whether a version may be used at all and on what
evidence. A version must pass `selectable()` and the confidentiality gate; then
`bound_class()` — the strongest class among the claims *the matching binding names* — places
it:

| Fit | When |
|---|---|
| `strong` | the competency is the version's `primary_capability`, on `direct` or `strongly_related` evidence |
| `workable` | the competency is a `secondary_capability` on covering evidence, or a reviewed mapping says the version answers it |
| `transferable` | the same coverage, but resting on evidence classed `transferable` |
| `gap` | no story at any fit level |

`mention_only` is deliberately not coverage. `transferable` is the weakest band the spec
names, and a fact the profile merely mentions is not something to answer a question out of.

A competency nothing covers comes back as `gap`, never as the nearest thing — the nearest
thing is how a bridging claim gets invented.

`record_mapping(version_id, competency, relation, limitation)` stores a reviewed mapping
rather than inferring one per use, so what a story is retrievable under is something a person
decided once and can be shown. `answers_with_limitation` must say what the limitation is, and
the limitation travels with the retrieval result.

**The advisory layer** may reorder what the deterministic layer returned, and may do nothing
else. An advisory signal must carry `provenance` (`source: model`, the model, a timestamp)
and a score, or it is refused. It sorts only after fit and evidence class have tied, so a
model's opinion cannot lift a `workable` story above a `strong` one, cannot add or remove a
story, and cannot change a class. Without any advisory input the order is still total: fit,
evidence class, least-used, version id.

## Proposing an application answer

`story_answers.propose()` is the only path from a story to an employer's form, and almost all
of it is about what has to happen first. There is no second answer store and no second
sensitive-question taxonomy: the disposition comes from `field_policy`, the stop boundary from
`pre_submit_core.MANDATORY_PAUSES`, the question meaning and exact-reuse decision from
`answer_library`, and an approved draft lands in the AnswerLibrary like any other answer.

The gates run in order of consequence, not likelihood:

| # | Gate | Outcome |
|---|---|---|
| 1 | an item or control on the existing mandatory-pause list | `pause / stop_boundary` |
| 2 | `field_policy` disposition `always_manual` (EEO, compensation, sponsorship, employer conflict, referral contact) | `manual`, no draft |
| 3 | disposition `unsupported` / `material` / `fact` | `pause` or `manual`, no draft |
| 4 | AnswerLibrary exact match, fresh, scope-valid, independently authorized | `reuse`, **no model, no story touched** |
| 5 | an approved answer exists but cannot auto-fill | `review_existing_answer` — never a second text for one meaning |
| 6 | unknown or conflicting question form | `pause` |
| 7 | a domain rule fired but the meaning is supported (e.g. `discovery_source`) | `pause / sensitive_requires_exact_answer` — a story may not compose one |
| 8 | no reviewed competency for the canonical meaning | `pause / competency_not_mapped` |
| 9 | ordinary narrative question, stories retrieved | `choose` (≤3 options plus `none_of_these`), then `drafted` or `gap` |

**Which competency a question tests is a reviewed artifact.** `record_question_competency(
canonical_id, competency)` writes it to `question_form_competencies`; `propose` reads it from
there and takes no competency argument. A meaning with no reviewed competency pauses. A
caller naming the competency was naming which of the user's stories it wanted, one step in
front of the evidence gate, and a model naming it would do the same thing less visibly.

**The question form is locked onto the draft.** A draft records `question_form_sha256` — a
hash of every registered form for that question, not merely the meaning it resolved to — and
`approve_draft` recomputes it. A form later remapped, unverified, or joined by a second
canonical id refuses approval: the mapping that said what the question means is not the one
the draft was written under.

**The draft is assembled, not composed.** Every span of an approved version is already either
a bound claim or a reviewed framing span, so the draft is that version read back verbatim in
STAR order. Nothing rewrites it; a rewrite would be text generated outside the binding gate.

**Weak evidence is named inside the answer, in the class it actually is.** The drafted
`answer_text` ends with an evidence note when there is something to say, and it makes up to
two separate statements:

- the **competency's own footing**, when the retrieved binding is weaker than
  `strongly_related`: *this draws on `<class>` rather than direct evidence of `<competency>`*;
- the **rest of the text**, when any rendered claim is weaker than that: *some supporting
  detail rests on `<class>` evidence* — listing the distinct weak classes present, minus the
  binding's own if it was already stated.

Both use the real class name. One fixed competency-level sentence was wrong twice: saying
"transferable" about a `mention_only` claim promotes exactly the class the ladder puts
lowest, in the sentence written to prevent promotion; and where the competency's binding was
`direct` and some other sentence was weaker, it denied a direct footing the evidence had. A
weak claim sitting inside a binding whose *strongest* claim carried it is reported too,
because the binding's class is a maximum and can hide one.

(A binding resting only on `mention_only` never reaches a draft at all — `mention_only` is
not coverage, so the story is not retrieved.)

The note is in the text rather than beside it because a note stored next to the answer is not
read by whoever reuses the answer. It used to live in the draft's `bridge` column; only
`answer_text` reaches the library, so an answer approved on transferable evidence came back
on the next form reading exactly like one drawn from direct evidence — `transferable never
upgrades` intact as a rule and walked around by the reuse path. A draft carrying a note is
never `auto_fill_ready`, and `approve_draft` refuses `auto_fill_allowed=True` for it.

**A draft is not an answer.** It records the application, employer, canonical meaning, story
version, exact evidence refs, snapshot and a content hash. `approve_draft(draft_id,
content_sha256, scope=, validity_class=)` requires that exact hash, re-checks that the story
is still selectable, and writes one AnswerLibrary entry with `source_type: user_confirmed`,
`auto_submit_allowed: False`, and the facts behind the evidence as `dependent_fact_ids` — so
the invalidation the library already runs reaches a story-derived answer like any other.
Approval returns what it authorizes (`reuse_of_this_answer_only`) and what it does not
(submit, Next/Continue, another question meaning).

## Opening a database written by an earlier schema

`story_core._migrate` runs inside `initialize`, is idempotent, and is safe to re-run after a
crash. Each step is its own transaction, and **what remains to do is read from the data, not
from the schema**: a row whose `capability_bindings_json` is missing, empty or unparseable is
a row still to backfill, whatever columns exist. The earlier version decided from column
existence, so a crash between adding the column and filling it left every row at the `'{}'`
default and the next run skipped the backfill entirely — a database that looked migrated and
had lost its capabilities. `primary_capability` is never dropped, which is what makes the
repair always possible, even once `secondary_capabilities_json` is gone.

**Reading a stored binding is total.** `read_bindings()` accepts whatever is in the column
and returns a shape the rest of the module can rely on; an empty result means "no usable
binding", which the migration repairs and `selectable` reports as
`capability_binding_unreviewed`. It tolerated only *unparseable* JSON once, so a column
holding well-formed JSON of the wrong type — `[]`, `null`, `5`, `"text"` — raised inside
`initialize` and the database could not be opened at all. No escalation, but a worse failure
than the one being guarded: nothing can be done to a database that will not start.

Nothing is guessed at in the process. A `claim_ids` that is not a list of strings reads as
empty rather than as one claim id. Whatever the reader normalizes away is also rewritten —
compared on the canonical forms, so a shape nobody anticipated is repaired too — and a
binding that survives normalization is kept rather than flattened to the unreviewed form.

**Shape is not meaning.** `read_bindings` says whether the blob is the right shape;
`binding_problems()` says whether the ids inside refer to anything, and `selectable()` and
retrieval both run it:

| Reason | When |
|---|---|
| `capability_binding_unreviewed` | no usable binding, or a primary naming no claims |
| `capability_not_in_ontology` | a primary or secondary capability the ontology has no SKILL entry for — a retired id, a typo, or a DOMAIN tag put where a capability goes |
| `capability_binding_claim_missing` | a binding naming a claim this version does not contain |

Validation at write time cannot stand in for this. A database edited by hand, or written
half way, never went through write time — and opening one is a case this module explicitly
supports. A stored reviewed mapping is read with the same suspicion: one naming a claim the
version does not have produces no fit.

A consequence worth stating rather than discovering: **retiring a capability from the
ontology makes every story bound to it unselectable** until it is re-bound. That is the
intended direction. The ontology is the reviewed vocabulary, and a story retrievable under a
name the system no longer uses is a story nobody reviewed under that name. It adds
`capability_bindings_json`, drops the `secondary_capabilities_json` it replaces, adds
`confidential_employer_normalized` and backfills it through the same normalizer identity is
matched with, and adds `claim_ids_json` to reviewed mappings. `story_answers.initialize` adds
`question_form_sha256` to an existing drafts table.

**What it will not backfill.** An older version named a capability and no claims, so nothing
on disk says which claims evidenced it. Filling that in with "all of them" would recreate the
promotion this schema exists to prevent, silently, on rows nobody would look at again. So:

- the binding is carried with an empty claim list and `migrated_without_claims: true`;
- `selectable()` returns `capability_binding_unreviewed`, so the version is preserved,
  readable, and retrieved by nothing;
- a migrated reviewed mapping gets `claim_ids: []` and likewise produces no fit;
- a draft predating the question-form lock gets an empty digest, and `approve_draft` refuses
  it by name rather than comparing it against today's digest — today's answer to "what does
  this question mean" is not evidence about what it meant when the draft was written.

The way back is a person drafting a successor with real bindings and approving it. It is
**not** `prepare_successor`: carrying re-derives evidence references from the same facts and
cannot supply claim ids nobody recorded, so it refuses a migrated version by name and says
what to do instead. Nothing is lost — the story, its text, its claims and its usage history
are all still there.

## What is deliberately not here

- No model call anywhere in this module. Eligibility, binding validity, class comparison, and
  usage counts are deterministic.
- No `story improve`, and in particular none of the source's two fabrication prompts. When
  step 2 adds it, "what numbers could you attach, even rough ones" and "can you add a specific
  metric" are negative tests, not features.
- No sensitive-question handling, because nothing here answers a question yet. When step 3
  adds `answer draft`, it composes the existing question-form registry, `field_policy`
  dispositions, AnswerLibrary authorization and freshness, and the existing stop boundary — it
  does not invent a second taxonomy.

*Adapts MIT-licensed structure from `noamseg/interview-coach-skill`. No prose or code is
vendored.*
