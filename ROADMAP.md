# Jobloom roadmap

Recorded 2026-08-28 from the product owner. **Nothing here is implemented yet.** This file
records the intended direction so later work can be judged against it; it is not a
specification and no module below should be built before the one above it works.

## Framing

Jobloom is not an AI resume tailoring tool. It is an **evidence-grounded career positioning
engine**.

The user's real question is not *"how do I word this bullet better?"* It is:

> I have done all of these things. Facing this particular job, who should I present myself as?

A tool that only rewrites keywords against a pasted JD answers the shallow question. The
deep question requires understanding the whole career first, then deciding — per target —
what to select, what to emphasize, what to leave out, and what must never be invented.

## Target pipeline

```text
ALL CAREER HISTORY
        ↓
Extract verified evidence
        ↓
Identify career goal
        ↓
Define target direction
        ↓
Rank experiences by relevance
        ↓
Select one page of evidence
        ↓
Rewrite without inventing facts
        ↓
Identify hidden strengths
        ↓
Identify true skill gaps
        ↓
Generate direction resume
        ↓
Input a specific JD
        ↓
Re-rank evidence
        ↓
Tailor 2–4 bullets + summary + skills
        ↓
Score: Current Fit / Career Value / Skill Gaps
        ↓
APPLY / STRETCH / SKIP
```

Layered, this is:

```text
Career Evidence Bank → Career Direction → Resume Variant → Specific JD → Tailored Resume
```

Not:

```text
Upload Resume → Paste JD → Rewrite keywords
```

## Build order

Build **Evidence Bank → Career Direction → Dynamic Resume → JD Fit/Gap** first, in that
order. Do not add auto-apply, cover letters, interview questions, or LinkedIn features until
those four layers are right — once they are, every peripheral feature shares the same Career
Evidence Bank instead of re-deriving the candidate from a resume file.

Modules 1–3 are the V1/V2 priorities.

---

## 1. Career Evidence Bank — build the real experience library first

**The problem this comes from.** Working from a one-page resume made INNSCI look weak. The
three-page all-experience resume showed it actually carried 2 focus groups, 3 pharmaceutical
products, a 17% sales increase, presentations to audiences up to 100, plus Shanghai Jiao Tong
public-health research, lab experience, and more statistical and visualization evidence.

**The rule that follows:** the current resume is not the boundary of what the user has done.
Never treat it as the source of truth.

Model the career as:

```text
Experience → Evidence → Skill → Metric → Domain → Confidence
```

```text
AstraZeneca
├── Clinical trial database
│   ├── 400+ trials
│   ├── PICO
│   ├── ClinicalTrials.gov / PubMed
│   └── SAS / SPSS / Excel
│
INNSCI
├── Focus groups: 2
├── Products analyzed: 3
├── Presentation: audience up to 100
└── Business impact: 17% sales increase
```

Then a JD asking for `stakeholder presentation` resolves to INNSCI evidence, and one asking
for `clinical trial data` resolves to AstraZeneca — mechanically, not by re-reading a resume.
This bank becomes Jobloom's bottom-level source of truth.

### Unified implementation; the real skill layer is still missing

Found on the first real posting (2026-08-28, MGB Computational Research Associate). The same
requirement list, measured against the same fact library, gets two different answers:

| Requirement | `direction_core` coverage | `evaluate_job._match_skill` |
|---|---|---|
| `R` | covered | `strength: none` |
| `Python` | covered | `strength: none` |

- `direction_core` pools every token from every fact and asks whether all of the requirement's
  tokens appear anywhere in that pool. Too loose: a token from an unrelated fact counts.
- `evaluate_job._match_skill` requires a fact's whole `value` string, or one of its `keywords`,
  to equal the requirement exactly. Too strict: a fact whose value is
  `Programming: R, SAS, SQL, SPSS, Python` never matches `Python`, so the evaluator reported
  `main_gap: R` for a candidate whose entire career is built on R.
- Neither stems: `statistics` does not match `statistical`, and the fact library is full of
  `statistical`.

This is finding 2 of the original code review — one rule, two implementations, already drifted —
reappearing in the filtering layer. It is also why module 1 matters: the fix is not a better
string comparison but a real skill layer with curated terms and aliases, so a requirement
resolves to evidence instead of to a substring.

Resolved 2026-08-28 by introducing one shared evidence resolver used by both routing and job
evaluation. It matches normalized requirement tokens within a single fact (never across the
global fact pool), preserves evidence strength and fact IDs, prefers exact terms when evidence
strength ties, and uses only explicit curated aliases such as `statistics` / `statistical`.
Fuzzy matching remains intentionally disallowed. Add new aliases only with regression tests.

This closes the contradictory implementations, not module 1 itself. Remaining limitations are
explicit: connective words such as `and` still participate in matching, compound capabilities
need a designed decomposition policy, and fact strength is only as trustworthy as its source
annotation. In particular, a course certificate must not silently stand in for demonstrated
on-the-job Python use merely because its fact was labeled `direct`.

**Capability foundation implemented 2026-08-28.** A versioned, globally shared
`Capability` / `FunctionNode` ontology now covers the initial three core directions, with strict
schema validation and a golden fact for every evidence pattern. `pattern_matcher.py` adds
ordered token-run matching, controlled English inflection (`focus group` → `focus groups`),
explicit variants, CJK substring matching, and fail-closed semantic-anchor rules. This is the
foundation of module 1 rather than its completion at that commit; the following implementation
milestone records when EvidenceUnit extraction and V2 reconciliation began consuming it.

**Career Direction Engine V2 non-market path implemented 2026-08-28.** Confirmed facts now
flow through EvidenceUnits, guarded quantity signals, typed capability relations, bottom-up
FunctionNode hypotheses, independent direction axes, deterministic title resolution, convergence,
hash-locked proposals, and the existing user-only materialization gate. Structured JobCards can
be aggregated into fail-closed market profiles; no external collector is enabled without an
authorized source, so unavailable market axes remain null by design.

## 2. One resume is not enough — Master → Variant → Tailored

The output of a real session is not a resume. It is:

```text
Master career evidence
        ↓
Resume A — healthcare / clinical / research data
        ↓
a tailored version for one specific JD
```

A variant must also re-rank internally per JD:

| JD type | Emphasis |
|---|---|
| Public health / epidemiology | Shanghai Jiao Tong ↑, INNSCI ↓ |
| Clinical / pharma | AstraZeneca ↑, INNSCI ↑, Shanghai Jiao Tong ↓ |
| Healthcare AI | Pittsburgh quantitative methods ↑, SQL/Python/AI evidence ↑ |

**Cardinality — implemented 2026-08-28.** A portfolio's weights say what share of
*applications* go to each direction. A resume variant has its own, different split: one
Resume A covers several directions at once, with its own internal ranking of them. These are
two separate allocations over the same directions and they do not have to agree. A
**ResumeVariant** is now its own object bound to a set of directions with its own weights;
see `references/resume-versions.md`. What remains from this module is the *dynamic* half:
re-ranking a variant's contents per JD.

## 3. Evidence Guardrail — every rewritten line knows where its evidence came from

The recurring questions in a real session were: *can this be written? is the 17% real? was
that a formal presentation or a meeting update? Epic was never used — it cannot be written.
REDCap was never used — it cannot be written.*

So every generated bullet carries a record:

```text
Generated:      "Led 2 focus groups and analyzed market data..."
Evidence:       INNSCI old resume bullet #3
Confidence:     HIGH
Transformation: Rephrased only
New factual claims: NONE
```

An attempt to generate `Experienced with Epic and REDCap` must return:

```text
BLOCKED — No supporting evidence
```

Surface per claim: `✓ Verified` / `△ Inferred` / `✕ Unsupported`. This is the feature that
separates Jobloom from an ATS keyword optimizer.

## 4. Job fit is a gap taxonomy, not a percentage

`Clinical Data Analyst — 72% match` tells the user nothing. Report instead:

```text
Evidence match:        R ✓  SAS ✓  statistical modeling ✓  data QC ✓
                       clinical research ✓  database management ✓
Transferable/partial:  SQL △  Python △
Real gaps:             Epic ✕  REDCap ✕  Tableau ✕
Hard blocker?          Epic required → potentially yes
```

The distinction that matters:

| Category | Meaning | Action |
|---|---|---|
| Hidden strength | Evidence exists, resume never showed it | Surface it |
| Resume gap | Wording problem, e.g. `data visualization` | Rewrite |
| Evidence gap | Listed but weakly demonstrated, e.g. `SQL` | Deepen or downgrade |
| Actual skill gap | Genuinely never done, e.g. `REDCap` | Never add |

Some things the user can do and the resume failed to say. Some things they genuinely cannot
do. Jobloom must separate the two automatically.

## 5. Career Direction Engine, not a job matcher

The goal is not the highest match score. It is: *get in somewhere now, and be positioned for
healthcare data + AI + H-1B in two years.* That needs two scores:

- **Current Fit** — can I get this job now?
- **Career Value** — does this job move me toward where I want to go?

```text
Research Data Coordinator        Current Fit 88   Career Value 72
Healthcare Data Analyst (SQL)    Current Fit 67   Career Value 91
Bioinformatics Analyst           Current Fit 83   Career Value 41
Healthcare AI Analyst            Current Fit 53   Career Value 96
```

Which resolves to a recommendation: `Apply Now` / `Apply as Stretch` / `Good Bridge Role` /
`High Match but Wrong Direction` / `Skip`.

**Direction proposal implemented 2026-08-28.** Uploaded PDF/DOCX/TXT/Markdown material now
produces provisional, non-adoptable direction recommendations plus a fact-review packet. A
confirmed CandidateSnapshot produces hash-locked, evidence-explained direction profiles with
provisional evidence-coverage heuristic; explicit non-empty user career goals add Career Value and a user-selected weighting can be
materialized into the existing SearchDirection/SearchPortfolio approval flow. This implements
direction discovery and approval boundaries, not decision-grade direction ranking. Per-job
bridge-role labels, typed evidence graphs, market capacity, and narrative coherence still belong
to later layers.

## 6. Resume Space Optimizer — decide who earns a line

A full history (Pittsburgh, DOHMH, Columbia, AstraZeneca, Shanghai Jiao Tong, INNSCI, Animal
Medical Center, LabBridge, Climate Club, projects, publication) does not fit on one page.
The question is not what to add — it is the **marginal value of each line for this JD**.

For a Research Data Analyst role: the DOHMH `reports + presentation + decision support`
bullet is high value; the publication is medium; Animal Medical Center is low. So:

```text
+ Add DOHMH communication bullet
- Remove Publication if space needed
- Do not restore Animal Medical Center

1-page budget: 47 / 48 lines
```

Adding a line must state what to remove for it. This is the fix for AI resume tools that only
ever append.

---

## Where today's implementation stands against this

Recorded so the gap is explicit, not to imply the current design is wrong.

| Roadmap module | Closest thing that exists | What is missing |
|---|---|---|
| 1. Career Evidence Bank | `candidate_facts` registry, `extract_candidate_facts.py` | Facts are flat and locked for safety; there is no Experience → Evidence → Skill → Metric → Domain → Confidence structure, and no JD-term → evidence resolution |
| 2. Master → Variant → Tailored | `resume_versions` with `master_source` / `direction` kinds and three source modes | Variants are static once approved; nothing re-ranks a variant's contents per JD |
| 3. Evidence Guardrail | claims manifest + `BaselinePlan` (fact IDs, reason codes, no promotion of evidence strength) | Governs *approval*, not composition: no per-bullet evidence/confidence/transformation record, and no generation-time BLOCKED |
| 4. Gap taxonomy | `evaluate_job.py` returns eligibility, a match category, ≤3 reasons | No hidden-strength / resume-gap / evidence-gap / skill-gap split |
| 5. Career Direction Engine | automatic material/candidate proposals + `SearchDirection` + weighted portfolio + routing | Direction-level Current Fit/Career Value is implemented; per-job bridge-role recommendations remain missing |
| 6. Space Optimizer | one-page gate on `rendered_page_count` | Enforces the limit, does not help allocate it — no marginal value, no line budget |
