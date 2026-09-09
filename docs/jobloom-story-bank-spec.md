# Jobloom Story Bank — Executable Functional Spec

**Status:** draft v0.2 · **Build target:** yes (this is the #1 feature from the two prior analyses)
**Grounded in:** file-by-file read of three cloned repos (see Provenance).
**Not a new task on the roadmap** — this is the spec; gate before building.

---

## 0. Provenance (what this is actually built from)

Three repos cloned and read file-by-file on 2026-09-08. All three are **MIT-licensed**, so structure and prose can be adapted with attribution.

| Repo | Commit-time path read | What Jobloom takes |
|---|---|---|
| `noamseg/interview-coach-skill` | `SKILL.md`, `references/commands/{stories,apply}.md`, `references/storybank-guide.md`, `references/story-mapping-engine.md`, `references/evidence-sourcing.md` | Story record schema, fit-scoring engine, gap classification, apply-with-user-choice, overuse/freshness tracking |
| `github/awesome-copilot` | `skills/technical-job-search/SKILL.md` | must-have / nice-to-have split (a *sibling* feature, referenced here only where it feeds mapping) |
| `MadsLorentzen/ai-job-search` | `tools/job_key.py`, `.claude/commands/apply.md`, `.agents/skills/*` | canonical dedup key pattern (sibling feature); apply's evidence-gap check |

### Three corrections to the earlier written analyses, now verified against source

1. **`interview-coach`'s `apply` is better-guarded than the second analysis feared.** It already says *"Never fabricate an experience. If the storybank and resume don't support an answer, flag it and ask,"* runs an explicit gap-check before drafting, and presents 2–3 candidate stories for the user to choose rather than auto-selecting. The AnswerLibrary shape is largely *already there in spirit*.
2. **`ai-job-search` is not the source of Jobloom's 94-board coverage.** It ships **6 portal skills** (`freehire`, `jobbank`, `jobdanmark`, `jobindex`, `jobnet`, `linkedin`) — five of them Danish. The portable asset is the **uniform search/detail contract + `job_key.py`**, not a board list. The 94-board figure describes Jobloom's own current ATS registry/pull and must not be attributed to this repository.
3. **The real, verified gaps** interview-coach leaves for Jobloom to close are narrow and specific — not "the whole storybank":
   - **No provenance binding.** Story `Impact` is free text in a markdown file; nothing ties a claimed number to an EvidenceUnit.
   - **Two fabrication invitations** (verbatim): `stories.md` improve step says *"What numbers could you attach to this? Even rough ones."*; `storybank-guide.md` enhancement says *"Can you add a specific metric?"*
   - **No sensitive-question hard-stop.** `apply` classifies questions as Behavioral / Process / Tools / Why-us / Other. There is **no Legal/Immigration/Work-authorization/Salary category and no halt.** Neither this repo nor `ai-job-search`'s apply has one.
   - **No scope/expiry** on the reusable answer library.

This spec keeps the compatible parts, closes those four gaps, and adds the versioning, approval, snapshot-invalidation, and confidentiality boundaries needed to make them executable inside Jobloom. It does not blanket-adopt the remainder of any source repository.

---

## 1. The gap it fills

At the baseline recorded for this spec, Jobloom's AnswerLibrary has **10 approved question types and 0 stored answers**. The product hypothesis is that repeated free-text questions, rather than résumé upload, are a material source of per-application delay. Do not encode the current counts or the earlier ~20-minute estimate as product constants: instrument the first five supervised applications and record question count, drafting time, review time, and reuse savings. The Story Bank is a projection of the evidence bank into **reusable, approved, provenance-bound interview material** and a source from which Jobloom may propose application-answer drafts. It is not itself an AnswerLibrary and may never make a draft reusable without the AnswerLibrary's existing review, scope, expiry, authorization, and invalidation gates. It acts on many applications and every interview, which is why it is the leading hypothesis for reducing time-to-interview.

---

## 2. Data model

A Story is **not** a new source of truth. It is a versioned, user-approved *projection* over already-confirmed CandidateFacts/EvidenceUnits, plus narrative framing that is never treated as evidence. **Every factual assertion**, not only a number, must resolve to one or more EvidenceUnits. A model may propose framing and tags, but neither becomes authoritative until the user reviews the exact StoryVersion.

```text
Story
├── id                     S### (stable)
├── status                 draft | approved | revoked
├── current_version_id     immutable StoryVersion reference
├── versions[]             immutable content-addressed StoryVersions
│     ├── id
│     ├── content_sha256
│     ├── candidate_snapshot_sha256
│     ├── authored_by      user | model_assisted
│     ├── approved_by      user | null
│     ├── approved_at      timestamp | null
│     ├── title            memorable label; never evidence
│     ├── star             { situation, task, action, result }
│     ├── primary_capability       reviewed canonical capability ID + binding
│     ├── secondary_capabilities[] reviewed canonical capability IDs + bindings
│     ├── domains[]         reviewed tags from Jobloom's canonical taxonomy
│     ├── earned_secret    optional reflection; factual clauses split into claims[]
│     ├── claims[]         every factual assertion MUST bind:
│     │     ├── claim_id
│     │     ├── text       exact text covered by this binding
│     │     ├── evidence_refs[]  one or more EvidenceUnit IDs
│     │     └── evidence_class   direct | strongly_related | transferable |
│     │                              mention_only | unsupported
│     └── reflection       optional user-approved interpretation; never evidence
├── applicability          { employers[], directions[], competencies[], question_types[] }
├── confidentiality        reusable | employer_confidential | application_confidential
└── invalidation_state     derived, never caller-supplied

StoryUsageEvent (separate append-only records keyed to StoryVersion)
└── { story_version_id, application_id?, interview_id?, round_id?,
      question_type, used_at, outcome_ref? }
```

`use_count` and `last_used` are derived from append-only usage events; they are not mutable counters supplied by a caller. Capability mappings, domain tags, and earned-secret text live on StoryVersion because changing any of them changes what the user approved.

**Hard invariant:** a StoryVersion is selectable only when it is user-approved and **every factual assertion** has at least one currently valid `evidence_ref`, the referenced CandidateFacts belong to the StoryVersion's registered snapshot, and no binding is `unsupported`. Unsupported or unbound prose may remain only in the draft/review surface; it is never included in coverage, answer drafting, interview packs, or autofill. This is the invariant that turns interview-coach's markdown storybank into a Jobloom-safe evidence projection.

An active CandidateSnapshot change does not silently carry Story approval forward. Jobloom re-resolves every binding against the new snapshot and requires a successor StoryVersion plus user approval when any exact value, evidence class, or rendered claim changes. An unchanged, hash-equivalent projection may use the same explicit carry/review pattern as resumes.

---

## 3. The five guardrails (mapped to Jobloom's principles)

| Guardrail | Rule | Jobloom principle |
|---|---|---|
| **G1 Provenance-or-draft** | Every factual assertion requires one or more EvidenceUnit bindings. Unbound or unsupported prose exists only as a draft review item and never reaches coverage, an answer, or an interview pack. | 1️⃣ only says what you said |
| **G2 Transferable never upgrades** | A claim's effective `evidence_class` is derived from its approved evidence relation. The Story layer may preserve or weaken that class, never strengthen it; any changed binding or class requires a successor StoryVersion and new approval. Rendering transferable evidence as direct is a test failure. | 1️⃣ transferable ↛ direct |
| **G3 Applicability is not authorization** | Story applicability helps retrieval; AnswerLibrary scope/expiry governs answer reuse. Employer/application confidentiality is an independent hard restriction. | 2️⃣ confirm once, reuse ≠ permanent |
| **G4 Authorization ≠ freshness** | Standing authorization and answer freshness are checked independently. Story approval cannot revive an expired answer or invalidated fact. | 3️⃣ authorization never relaxes freshness |
| **G5 Rules before model** | Eligibility, evidence validity, exact-answer reuse, scope, freshness, confidentiality, hard stops, and usage counts are deterministic. A model may propose mappings or prose only after those gates, and its output remains a draft until user approval. | 4️⃣ cheaper the more you use it |

---

## 4. Commands

Adapted from interview-coach's `stories` + `apply`, with Jobloom guardrails inserted.

### `story add`
Guided discovery (keep interview-coach's reflective prompts — they surface real stories, not rehearsed ones), then STAR capture, then a **claim segmentation and binding pass**: split every factual assertion, including qualitative responsibility, employer, role, action, result, date, number, tool, and outcome, and bind it to one or more EvidenceUnits. Unbound material remains in a draft review queue and makes the StoryVersion unselectable. Emit an immutable draft StoryVersion, show its complete rendered text and bindings, and require the user to approve that exact content hash. **Do not** ask "what numbers could you attach, even rough ones" — that prompt is removed (§6).

### `story improve`
Diagnose by coaching score (1–2 missing material / 3 missing proof / 4 missing differentiation), adapted from the source. This score is advisory model output, not a deterministic fact and never controls eligibility by itself. **Difference:** "missing proof" may be closed by locating an existing EvidenceUnit or by asking the user whether a real omitted fact should enter CandidateProfile intake. A newly supplied fact must complete proposal → user review → registered CandidateSnapshot before a successor StoryVersion can use it. If that does not happen, the output remains a **gap**, not a metric to add.

### `story find-gaps`
Cross-reference target directions × storybank skill coverage. Classify each gap using Jobloom's four-way gap taxonomy (hidden strength / résumé problem / weak evidence / true gap) — the interview-coach Critical/Important/Nice ranking becomes the *priority axis on top of* that taxonomy, not a replacement.

### `story map [direction|company]`
Run the fit-scoring engine (§5). Output the Strong/Workable/Transferable/Gap matrix with bridging guidance. This is read-only; produces no submittable artifact.

### `answer draft --application <application_id>`

This command proposes drafts **through** the existing AnswerLibrary and field-policy contracts; it does not create a second answer store.

1. Resolve each observed question through the existing exact/reviewed-semantic question-form registry. The page supplies neither a canonical meaning nor its own safety classification. Unknown or ambiguous forms pause.
2. Apply the existing field disposition and workflow control in §8. `always_manual`, `unsupported`, and hard-stop controls produce no draft. A sensitive but supported canonical meaning may use only an exact, fresh, scope-valid approved answer.
3. For a safe known question, check AnswerLibrary for a scope-matching, unexpired approved answer (exact match, no model). Reuse it only if standing authorization is independently current.
4. If no reusable answer exists and the question is narrative-safe, retrieve up to three eligible StoryVersions with structured reasons and evidence classes; **wait for user choice** and allow “none of these.”
5. Draft only from the chosen StoryVersion's bound claims. Transferable evidence produces an explicitly adjacent bridge and is never `auto_fill_ready` on first generation. Unsupported or unbound material is unavailable, not merely softened.
6. Save a **draft answer version** with `application_id`, employer identity, canonical question meaning, question-form version, exact evidence refs, candidate snapshot, and content hash. Show the complete answer and proposed scope/expiry to the user.
7. Only a separate user approval turns that exact draft into an AnswerLibrary entry. Approval never grants submission, never clicks Next/Continue, and never answers another form whose meaning did not exact/reviewed-semantically match.

---

## 5. Story mapping: deterministic eligibility, advisory ranking

| Fit | Definition | Jobloom change |
|---|---|---|
| **Strong** | An approved direct/strongly-related capability binding covers the tested competency and the StoryVersion has no invalid bindings. | Remove `strength ≥4`, domain alignment, and earned-secret relevance from the eligibility gate; those are advisory ranking signals. |
| **Workable** | A valid secondary-capability binding covers the competency directly/strongly-related, or an approved mapping says the story can answer it with a visible limitation. | Store the reviewed mapping; do not infer it anew on every use. |
| **Transferable** *(was "Stretch")* | Coverage depends on evidence explicitly classified `transferable`. | Hard-label it. It cannot count as direct coverage or become a first-use auto-fill answer; the user must review the adjacent bridge. |
| **Gap** | No story at any fit level. | Route to gap-handling; **never** fabricate a bridging claim. |

The deterministic layer answers only whether a StoryVersion is eligible and what evidence class supports it. A second advisory layer may rank eligible stories by user-reviewed competency mapping, coaching score, direction alignment, recency, and variety. Model-produced relevance, domain alignment, “earned secret relevance,” or story quality must carry provenance (`model`, model/version, timestamp) and confidence, and must never change `evidence_class` or eligibility.

Keep conflict resolution and freshness downgrade deterministic. Treat 3+/5+ overuse thresholds as configurable presentation policy, not evidence truth; compute them from usage events. A user may deliberately reuse the best story.

---

## 6. Fabrication blocklist (the two verbatim vectors, stripped)

These two source lines are **removed** in the Jobloom adaptation and become **negative tests**:

- ❌ `"What numbers could you attach to this? Even rough ones."`  → replaced by: *"Which EvidenceUnit backs this number? If none exists, we mark it a gap."*
- ❌ `"Can you add a specific metric?"` (enhancement)  → replaced by: *"Is there a confirmed metric in your evidence for this? If not, this stays qualitative."*

Other `story improve` techniques (tightening structure, surfacing stakes, extracting an earned secret, reordering) are allowed only when the resulting text passes full claim segmentation, evidence binding, and exact-version approval. No source operation receives blanket approval merely because it is described as editing or coaching.

---

## 7. Scope & expiry (what neither source has)

Story applicability and AnswerLibrary reuse scope are separate concepts:

```text
story.applicability = { employers: [...], directions: [...],
                        competencies: [...], question_types: [...] }
story.confidentiality = reusable | employer_confidential | application_confidential

answer.scope = the existing AnswerLibrary scope object
answer.expiry = the existing AnswerLibrary expiry/invalidation contract
```

- Applicability controls retrieval order, not permission. Empty means “no positive retrieval hint,” not “globally authorized.”
- Confidentiality is a hard AND restriction. `employer_confidential:A` never surfaces for employer B even if a direction or question type overlaps. `application_confidential` requires the exact application ID.
- Candidate fact validity is inherited through evidence bindings; do not duplicate arbitrary fact expiry rules on Story rows.
- Answer freshness remains governed by AnswerLibrary. Stable, periodic, and event-driven entries use its existing invalidation machinery.

**Authorization ≠ freshness** (Jobloom principle 3️⃣): a standing "my answers are accurate, use approved ones" grant does **not** revive an expired answer. Expiry is an independent channel; the drafter checks *both* before reuse.

---

## 8. Sensitive-question handling (compose existing contracts; do not invent a second taxonomy)

Neither `interview-coach/apply` nor `ai-job-search/apply` has this. Jobloom does **not** implement it as a keyword list or a new set of field-policy dispositions inside Story Bank. The implementation must compose the existing canonical question-form registry, `field_policy` dispositions (`fact`, `answer`, `material`, `always_manual`, `unsupported`), fill controls, AnswerLibrary authorization/freshness checks, and the EEO non-disclosure policy. Unknown or ambiguous meaning pauses rather than being guessed by a model.

| Existing contract / outcome | Examples | Story Bank behavior |
|---|---|---|
| `answer` + exact reviewed question form | the distinct work-authorization / sponsorship / visa-transfer meanings already pinned in AnswerLibrary | Reuse only an exact/reviewed-semantic, fresh, scope-valid, user-approved AnswerLibrary entry. Otherwise pause for answer intake; never infer one meaning from another. Story material cannot answer these fields. |
| `always_manual` | employer-defined salary brackets, salary history/expectation/current compensation, conflict questions without a complete approved registry | Produce no model draft. User handles the field; any future reusable answer follows the dedicated protected-data policy. |
| existing stop/control boundary | arbitration, non-compete, IP assignment, background-check consent, identity/tax/banking documents, payment, assessment, biometric/video request, CAPTCHA, nonstandard signature | Stop the page workflow and hand control to the user. Do not store the user's action as a reusable answer merely because they completed it. Story Bank must not weaken an existing stop into `always_manual` or `answer`. |
| existing EEO non-disclosure policy | voluntary race/ethnicity, gender, disability, veteran values | Store no protected value or expected hash. Only an independently reviewed exact-match non-disclosure policy may select a decline option. Story Bank receives no value and adds no behavior. |
| `standard_attestation` fill control | ordinary truthfulness declaration already covered by pre-submit review | Use the existing attestation policy and pre-submit gate; Story Bank adds no authority. |
| `unsupported` or unknown/ambiguous form | anything not covered by the pinned registry | Pause with bounded identifiers. Do not classify or draft with a model. |

The exact question may be shown in the live review UI, but logs, events, worker envelopes, and support artifacts keep only the bounded value-free identifiers already permitted by Jobloom.

---

## 9. Acceptance tests (executable)

Write these as the gate on the build. Each maps to a guardrail.

```text
T1  [G1]  A story with any unbound factual assertion, including a qualitative
          responsibility with no number → selectable=false; it never appears in
          a drafted answer or interview pack.
T2  [G2]  A Transferable mapping stays structured as transferable through retrieval,
          drafting, review, and persistence; it never increments direct coverage and
          the first generated answer is not auto_fill_ready.
T3  [G3]  An employer-confidential story for A never surfaces for B even when
          directions and question_types overlap; an application-confidential story
          requires the exact application ID.
T4  [G4/3️⃣] Set an answer expired; apply a standing authorization grant → answer
          stays expired (authorization does not revive it).
T5  [event]  Flip visa_status → every answer with invalidated_by ⊇ {visa_status}
          transitions to stale in one pass.
T6  [§6]  Run `story improve` on a coaching-score-3 story with no evidence → output is a
          gap prompt; assert it never emits the string "rough" or "attach a number".
T7  [§8]  Feed a form with salary expectation, one exact immigration meaning, an
          unknown question, voluntary EEO, and arbitration → each reaches the correct
          existing policy outcome; no model draft or protected value is written. Safe
          narrative questions on the same observed page may still produce drafts.
T8  [G5]  AnswerLibrary exact-match reuse path executes with zero model calls
          (assert on the call log).
T9  [approval] A model-generated answer draft is not reusable until the user approves
          its exact content hash, scope, and expiry; approval does not authorize submit.
T10 [snapshot] Change or revoke a bound CandidateFact → the old StoryVersion becomes
          unselectable; standing authorization cannot revive it.
T11 [version] Editing one character creates a new immutable StoryVersion; the prior
          approval does not transfer to the changed content.
T12 [privacy] Story, transcript, and answer values never appear in logs, worker
          envelopes, test snapshots, model-usage records, or support artifacts.
```

T1, T2, T7, T9, and T10 are the tests that make this *Jobloom's* Story Bank rather than a copy of interview-coach.

---

## 10. Build order

1. Immutable Story/StoryVersion registry, full factual-claim binding, user approval, revocation, and CandidateSnapshot invalidation/carry.
2. Deterministic eligibility + reviewed competency mappings (`story map`); keep advisory model ranking separate.
3. `answer draft --application` integrated with the existing question-form registry, field policy, AnswerLibrary approval, scope/expiry, and submission boundary.
4. `story find-gaps` folded into Jobloom's four-way taxonomy.
5. Interview usage events, retrieval drills, outcome links, and configurable overuse presentation.

Ship 1–3 as the first usable vertical slice. Shipping an answer drafter before StoryVersion approval, mapping, and invalidation would create a fast path around the evidence system.

---

*Attribution: adapts MIT-licensed material from noamseg/interview-coach-skill, github/awesome-copilot, and MadsLorentzen/ai-job-search. Verify each repo's LICENSE and pin commits before vendoring any prose or code.*
