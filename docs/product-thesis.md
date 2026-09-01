# Jobloom product thesis

Recorded 2026-08-31 after comparing Jobloom with career-ops, job-apply-plugin, JobNavigator,
JobSync, Resume Matcher, browser-use, Skyvern, AIHawk, ApplyPilot, interview-coach-skill,
Reactive Resume, VitaeContext, and other open-source career tools.

## The claim

Jobloom should become the most trustworthy evidence-grounded career positioning and application
system, not the tool that sends the most applications or has the longest feature list.

Its product promise is:

> Help a person decide who to present themselves as for a particular opportunity, assemble the
> strongest truthful case from their whole career, carry that same case consistently through
> resume, application, interview, and outcome, and make every material decision replayable.

That promise contains four different jobs:

1. understand the person's whole career rather than treating one resume as the boundary;
2. choose a target identity and evidence emphasis for a direction and a specific job;
3. execute application work without inventing, substituting, silently expiring, or overreaching;
4. learn from outcomes without corrupting the evidence or pretending correlation is causation.

## Why existing projects do not make Jobloom redundant

### Feature breadth is not the differentiator

career-ops already has a broader visible workflow: scanning, evaluation, level strategy, company
research, contact drafts, tailored PDFs, interview preparation, follow-ups, negotiation, and
outcome analysis. JobNavigator and JobSync have better user interfaces. Browser-use and Skyvern
have far broader browser capabilities. Resume Matcher has a more polished resume/JD workflow.

Jobloom cannot credibly claim to be better at those things today.

The missing property is that those capabilities usually terminate in model output, Markdown,
or browser behavior. Their correctness depends on a prompt, a reviewer noticing a mistake, or a
README promise. Jobloom's goal is to make correctness a property of the stored data and allowed
state transitions.

### “Never fabricate” has levels

There is a meaningful difference between:

1. a prompt saying not to fabricate;
2. a model being given a resume and asked to use only it;
3. a generated claim being checked against strings or numbers in that resume;
4. every claim naming confirmed evidence, its strength, source, scope, and immutable value;
5. downstream artifacts becoming invalid when that evidence changes.

Jobloom must operate at levels 4 and 5. A competitor reaching those levels should be treated as
a compatible evidence system, not dismissed through branding.

### “Human in the loop” also has levels

There is a difference between showing a generated form before Submit and making it impossible for
the execution protocol to contain a submission action. There is a difference between asking the
user to review an answer and querying a protected registry at pre-submit time to prove that the
answer remains fresh and scoped to this application.

Jobloom's user control must be enforced by capability boundaries, not only by instructions.

### A deterministic score alone is not enough

Jobloom has already measured a deterministic `ranking_score` that runs against evidence. A bad
rule does not become good because it is reproducible. Determinism supplies auditability; it does
not supply truth.

Therefore every consequential rule needs:

- a named decision it is allowed to affect;
- evidence that the rule correlates with that decision in the intended direction;
- an explicit unknown state;
- measured failure cases;
- downstream consumer analysis;
- a migration or rollback path.

“Rules first” means measurable rules first, not rules at any cost.

## The defensible moat

Jobloom's moat should be a shared trust substrate with seven properties.

### 1. Career evidence is the source of truth

The system models the career as evidence units, capabilities, metrics, domains, dates, sources,
and confidence. Resumes, LinkedIn, application answers, interview stories, and career directions
are views over that bank. None may independently invent a new career fact.

### 2. Positioning is explicit

Jobloom separates:

- what the person has actually done;
- which career direction they are pursuing;
- which professional identity should lead for this job;
- which evidence should be selected and emphasized;
- what is transferable but not direct;
- what is a real gap;
- whether filling that gap advances the desired career.

This answers the deeper question, “Facing this role, who should I present myself as?” without
turning presentation into fabrication.

### 3. Reuse is conditional and expiring

“Confirmed once” is not permanent permission. Facts and answers have scope, conditions,
expiration, invalidation triggers, and conflicts. Standing authorization is separate and cannot
revive stale content. Sensitive or legally consequential questions receive narrower reuse.

### 4. Execution is less powerful than reasoning

Models may propose classification, prose, or a plan. They do not get an unrestricted browser
and permission to turn their own proposal into an external action. The executor consumes a
bounded, hash-locked package, returns observations rather than claims, and cannot submit.

### 5. The record describes what physically happened

An application record stores or copies the actual approved artifacts and verified answer
references used at that moment. It distinguishes intent to apply, a user's statement that they
submitted, and positive backend submission evidence. Uncertain submission remains uncertain.

### 6. Learning does not rewrite history

Outcomes may change search allocation, surface evidence gaps, or suggest a strategy review. They
do not silently modify CandidateFacts, inflate claims, or prove causation. Every funnel rate has
a numerator and denominator; small samples are labeled insufficient.

### 7. Known defects are part of the product record

Jobloom records where its own assumptions failed, the evidence that exposed the failure, where
the failure is currently harmless or dangerous, and the measurement that would settle it. A
known liability is not closed by rewriting documentation or adding a test that misses the real
path. This is the precondition for honest replayability: a reviewer must know which outputs and
contracts are not yet trustworthy.

## What “better” must mean

Jobloom is better than a broader tool only if it wins on both trust and usefulness.

### Trust gates

Every production workflow must satisfy all applicable gates:

| Gate | Passing evidence |
| --- | --- |
| Claim provenance | Every factual claim resolves to approved CandidateFact/EvidenceUnit IDs and immutable values |
| Evidence honesty | Direct, related, transferable, mention-only, and unsupported cannot be silently promoted |
| Freshness | Expired, conflicted, invalidated, or out-of-scope facts/answers fail closed |
| User authority | Only the user can approve identity, directions, materials, sensitive reuse, and final review |
| Deterministic safety | State transitions, material selection, browser packages, verification, trust signals, and archives do not depend on an LLM |
| Privacy | Logs, telemetry, reports, fixtures, and support bundles contain no answer values or unnecessary personal data |
| Execution boundary | Fixture final-action activations remain zero; live fills use scoped submit-event and unexpected-navigation guards. The worker fills one page and never owns Enter, Next, Continue, or final submission. CAPTCHA, payment, identity documents, assessment, biometrics, and legal surprises pause |
| Physical integrity | Bound and archived files match approved hashes and permitted formats |
| Replayability | A reviewer can reproduce the material decision from preserved inputs, code/schema version, and hashes |
| Honest uncertainty | Parsing failure, unavailable evidence, and uncertain submission are never converted into negative or positive certainty |

### Usefulness gates

Trust without usefulness produces a correct system nobody can use. Jobloom must also demonstrate:

| Outcome | Required measure |
| --- | --- |
| Less repeated work | Median user minutes and model tokens per reviewed job/application decline with reuse |
| Better targeting | More reviewed opportunities reach a user-approved `precision` or `broad` decision for evidence-bearing reasons |
| Defensible materials | Unsupported-claim count is zero; user corrections and blocked claims are measured |
| Reliable filling | Per-ATS verified-field rate, pause reasons, incorrect-fill rate, and final-action activations (must remain zero) |
| Better interview preparation | Interview claims and stories resolve to evidence; gaps are visible before the interview |
| Strategy learning | Outcome reports preserve denominators and require user approval before direction changes |
| Operational usability | Onboarding completion, time-to-first-reviewed-job, recovery success, backup/restore, and upgrade success are measured |

The primary optimization metric remains **user time and model cost per qualified interview**, not
applications per day. Until sample sizes support that rate, the product reports the upstream
measures rather than inventing an interview probability.

### Measurement bootstrap

The outcome funnel is currently empty, so outcome-based usefulness claims are not available.
Jobloom must not optimize only the trust half merely because it is easier to test, and it must not
loosen submission gates to manufacture a denominator. Use this staged evidence ladder:

| Stage | Minimum evidence | What may be claimed |
| --- | --- | --- |
| 0 — local workflow | fixture runs and timed supervised tasks | setup time, steps, user corrections, verified-field rate, pause correctness, final-action activations |
| 1 — first supervised use | 5 user-confirmed application attempts, including abandoned flows | time per reviewed job/application, completion blockers, manual takeover rate; no interview-effect claim |
| 2 — operational sample | 20 confirmed submissions with explicit denominators | descriptive funnel and workflow-cost trends, still statistically insufficient |
| 3 — analysis threshold | 30 confirmed submissions | descriptive conversion comparisons with uncertainty; still no causal claim |

Reaching Stage 2 is a product objective, not permission to auto-submit. A calendar commitment must
be set by the product owner after the first five supervised attempts reveal the real workload.

## The feature strategy

Jobloom should not remain a narrow compliance backend. It should match or exceed competitors on
the useful surface while keeping the trust substrate underneath.

### Foundation

1. Career Evidence Bank with multi-source proposed-fact import and conflict review.
2. Career Direction and explicit positioning hypothesis.
3. Dynamic Resume with claim-level provenance, rendered review, ATS parseability, and drift.
4. JD Fit/Gap separated into Current Fit, Career Value, Real Gaps, Posting Trust, and User Cost.

### Application execution

1. First-form domain coverage before protocol freeze: contact/profile facts, manual employer-defined
   compensation brackets, certified conflict-registry derivation, materials, authorization, and
   voluntary-EEO value exclusion with optional exact-match non-disclosure policy.
2. Measured ATS adapters, currently Lever, Greenhouse, and Ashby before broader coverage.
3. One-page Fill-Only protocol, value-free observation, verified hashes, user-owned navigation,
   fixture final-action oracle, and scoped live submit/navigation guards.
4. Answer freshness, scoped reuse, pre-submit review, physical archive, and recovery.

### Career operations

1. Level strategy and cross-surface positioning consistency.
2. Compensation observations, salary floor, commute/relocation, and offer objects.
3. Company dossier and evidence-bearing posting trust.
4. Interview Story Bank and defensibility packet.
5. Gap-to-action paths: training, portfolio project, adjacent-role strategy, or honest non-fit.
6. Network matching and contact drafts without automatic outreach.
7. Follow-up, outcome classification, debrief, and approved strategy feedback.

### Product experience

1. Local review dashboard and guarded approval queue.
2. Source health, migration fixtures, doctor, backup/restore, and safe support reports.
3. Portable Career Evidence Packet and provider adapters for Codex, Claude, and other agents.
4. Local/cheap model options without moving deterministic gates into a model.

## Explicit non-goals

Jobloom should not add these merely to claim parity:

- autonomous or bulk submission;
- CAPTCHA solving, anti-bot evasion, fingerprint spoofing, or account farming;
- model-directed unrestricted browser execution;
- keyword stuffing or fabricated gap bridging;
- opaque fit, ghost-job, interview, or hiring probabilities;
- automatic fact merging from embeddings;
- silent cross-city/cross-board job merging;
- scraping logged-in consumer job platforms without an explicit product and policy decision;
- sending recruiter messages or changing strategy without user approval.
- storing or automatically filling voluntary EEO/self-identification values such as race,
  ethnicity, gender, disability, or veteran status; these controls always pause for the user and
  their values never enter Jobloom state.

## Competitive acceptance rule

For every capability competitors have and Jobloom lacks, choose one:

1. **build it on the trust substrate**;
2. **integrate it behind a narrow adapter**;
3. **measure that it does not matter to the target user**;
4. **reject it explicitly because it violates a non-goal**.
5. **defer it with a named trigger, owner, and evidence that will reopen the decision**.

“Not built” without one of those decisions is a product gap, not principled restraint.

For every claim that Jobloom is safer or more trustworthy, preserve a regression test, audit,
or reproducible artifact that proves it. A README promise without an enforcement point does not
count as differentiation.

## The shortest positioning statement

> Other tools ask a model to be careful. Jobloom makes carefulness a property of stored state:
> every claim, reused answer, and browser action must pass a gate that exists whether or not the
> model cooperates. It uses that substrate to decide who you should present yourself as and carry
> the same truthful case through application, interview, and outcome.

## The condition for continued existence

If Jobloom cannot make this workflow materially easier than manual work, or if it implements the
same prompt-driven behavior as broader tools without stronger evidence and execution guarantees,
it should not exist as a separate product.

Its existence is justified only by delivering both:

- competitor-level usefulness across the career workflow; and
- materially stronger, testable guarantees about truth, freshness, privacy, replayability, and
  user authority.
