# Story Bank

Spec: [`docs/jobloom-story-bank-spec.md`](../../../docs/jobloom-story-bank-spec.md).

**What exists today is build-order step 1 of that spec**: the registry, claim binding,
approval, revocation, and snapshot carry. `story map`, `answer draft`, `story find-gaps`,
retrieval drills, and interview usage reporting are **not implemented**. Nothing here can
answer an employer's question yet; a Story reaches an application only through the
AnswerLibrary, and that path is step 3.

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
other. **Enforcement at retrieval belongs to step 2** and does not exist yet; today this is
recorded and surfaced, not applied.

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
