# Career Direction Engine

The Career Direction Engine turns uploaded career material into explainable direction
proposals. It never treats a resume as the user's intent and never silently activates a search
direction.

## Two-stage proposal

`propose-material` accepts PDF, DOCX, TXT, or Markdown. It extracts a CandidateFact review
packet and scores the unverified facts against the controlled direction catalog. The result is
`mode: provisional`, `adoptable: false`, and always carries
`candidate_facts_require_confirmation`. It is useful immediately after upload, but it cannot be
registered or used for job routing.

After the user reviews the facts and registers a CandidateSnapshot, `propose-candidate` produces
a `mode: verified` proposal. Only `confirmed` and `locked` facts contribute. Every evidence
signal reports its fact IDs and preserved evidence strength; raw fact values are not copied into
the proposal.

## Scores and intent

- **Current Fit** is deterministic weighted evidence coverage. `direct`, `strongly_related`,
  `transferable`, and `mention_only` evidence contribute at decreasing strength. Missing core
  signals are returned as `core_gaps`. In V1 this is explicitly a provisional heuristic, not a
  decision-grade fit score. Unverified material is capped at `transferable` and can never receive
  high confidence. Verified summary text, unclassified resume claims, and skill lists are capped
  at `mention_only`; education, certifications, and experience headers are capped at
  `strongly_related`. Every cap is visible as declared versus effective strength.
- **Career Value** is computed only when the user supplies non-empty `career-goals` containing desired
  roles, industries, skills to build, avoided roles/industries, and the Current Fit/Career Value
  priority split. With no goals, Career Value is `null`; the engine never infers aspiration from
  resume wording. An untouched empty goal template is treated exactly like no goals. Goal terms
  match complete normalized controlled phrases only; token subsets such as `data`, `analyst`, or
  `research` do not match longer titles. Unresolved positive goals are surfaced for review.
- **Overall score** applies the user's declared priority split. Without goals it equals Current
  Fit. Scores rank proposals; they are not interview probabilities.
- **Confidence** describes evidence breadth, not certainty of getting a job.

The controlled catalog contains role archetypes, titles, industries, evidence signals, search
terms, warnings, and discovery terms. Extend the catalog with explicit terms and regression
tests. The shipped catalog covers Jobloom's initial healthcare, research, life-sciences, and data
analytics scope; it is an extensible taxonomy, not a claim to enumerate every occupation. When
no catalog direction has evidence or explicit goal alignment, the engine returns no direction
and `no_supported_direction_in_catalog` instead of forcing an irrelevant recommendation. Do not
use a model to invent titles or promote evidence.

## Commands

Generate a provisional proposal and the mandatory fact-review packet:

```bash
python3 scripts/career_direction_core.py propose-material \
  --proposal-id upload-v1 --material resume.pdf \
  --output proposal.json --fact-review-output fact-review.json
```

Generate an adoptable proposal after CandidateSnapshot review:

```bash
python3 scripts/career_direction_core.py propose-candidate \
  --proposal-id verified-v1 --candidate candidate.json \
  --db .jobloom/jobloom.db --goals career-goals.json --output proposal.json
```

The proposal includes complete, hash-locked SearchDirection profiles and a suggested portfolio.
Verified generation requires the exact active user-registered CandidateSnapshot and verifies its
physical snapshot hash.
The suggestion is not approval. The user reviews it and provides a selection whose integer
weights total 100:

```json
{
  "portfolio_id": "portfolio-v1",
  "name": "Reviewed career directions",
  "allocations": [
    {"archetype_id": "research-clinical-data", "weight_percent": 80},
    {"archetype_id": "biostatistics", "weight_percent": 20}
  ]
}
```

Materialize the exact reviewed profiles and portfolio:

```bash
python3 scripts/career_direction_core.py materialize-selection \
  --proposal proposal.json --selection selection.json --actor user \
  --proposal-sha256 <reviewed-hash> --output-dir reviewed-directions
```

Materialization does not register or approve anything. Use `direction_core.py register-direction`,
`register-portfolio`, and `approve-portfolio --actor user` afterward. This preserves the existing
immutable approval boundary.

## Privacy and failure behavior

- Uploaded files, review packets, proposals, and materialized profiles belong under `.jobloom/`
  or another private ignored directory.
- The default catalog and goal template contain no candidate data and are safe to track.
- PDF extraction prefers `pdftotext` and falls back to the declared `pypdf` dependency. Encrypted or image-only PDFs
  fail with a clear request for an unlocked or OCR/text-based source.
- A malformed catalog, goal file, proposal hash, selection, or weight total fails closed.
- Provisional proposals can never be materialized.
