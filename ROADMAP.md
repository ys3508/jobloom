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
| 5. Career Direction Engine | `SearchDirection` + weighted portfolio + routing | One axis only: there is no Career Value score and no bridge-role recommendation |
| 6. Space Optimizer | one-page gate on `rendered_page_count` | Enforces the limit, does not help allocate it — no marginal value, no line budget |
