# OSS review reconciliation

Date: 2026-08-31. Shared decision surface for Codex, Claude Code, and later review agents.

Inputs:

- `docs/github-open-source-landscape-2026-08-31.md` — broad Codex survey;
- `docs/oss-landscape-2026-08-31.md` — independent Claude Code survey with queue measurements;
- `docs/product-thesis.md` — competitive thesis;
- `skills/jobloom/references/known-liabilities.md` — measured local failures.

This records agreements, real disagreements, decisions, and open experiments. It does not
authorize importing code, accessing a real job site, or submitting an application.

## 1. Decisions

### A — dependency evidence and design value are different

The earlier “A-grade” list mixed two meanings: safe enough to reuse, and valuable enough to read.
A classification can remain valuable if its author disappears. A runtime dependency needs clear
license, active maintenance, external users who have found failures, a bounded interface,
upgrade/rollback cost, and a replacement path.

| Label | Meaning | Current examples |
| --- | --- | --- |
| Vendor candidate | Copy a bounded asset after license/provenance/hash review | job-apply-plugin semantic fixtures only |
| Adapter candidate | Evaluate a stable narrow module behind a Jobloom interface | none approved yet |
| Reference implementation | Read behavior/code, then implement locally | selected JobSync/career-ops patterns |
| Design/classification only | Extract schema, UX, taxonomy, or failures | resume-builder, VitaeContext, interview-coach-skill, JobNavigator |
| Negative reference | Turn risks into non-goals or regression tests | AIHawk, ApplyPilot, Skyvern execution, anti-bot paths |

Star and fork counts are risk signals, not quality scores. Low adoption does not invalidate an
idea, but it prevents calling the repository a proven dependency.

### B — browser-use does not enter the execution layer

Resolved in favor of direct Playwright:

- browser-use's differentiated value is model-directed browser operation;
- Jobloom prohibits an LLM from deciding execution actions or field values;
- after removing that value, Jobloom needs Playwright/CDP, not the extra agent framework;
- the smaller executor is easier to allowlist, replay, fuzz, audit, upgrade, and replace.

Permitted research use: compare control semantics, collect public failure categories, and derive
local fixture tests. Prohibited: runtime dependency, cloud browser, profile import, generic model
action planner, or generic task API.

```text
Jobloom core -> hash-locked action package -> minimal direct Playwright worker
             <- value-free result hashes  <-
```

### C — accept three MIT semantic fixture sets with constraints

Accepted candidate assets from `neonwatty/job-apply-plugin`:

- Lever application fixture/provenance/approval;
- Greenhouse single-page fixture/provenance/approval;
- Ashby application fixture/provenance/approval.

Conditions:

1. pin and record the reviewed upstream commit;
2. preserve MIT copyright and license notice;
3. verify fixture, approval, and provenance hashes in tests;
4. manually inspect every copied field and choice before promotion;
5. copy no raw recording, DOM, URL, employer identity, applicant value, screenshot, or token;
6. treat them as semantic field combinations, not current ATS DOM evidence;
7. render Jobloom-owned local interactive pages from them;
8. require supervised live acceptance before claiming a production adapter works;
9. map every `kind` to CandidateFact, AnswerLibrary, material, pause, or unsupported;
10. never collapse Jobloom's four authorization/sponsorship questions into a broad upstream kind.

### D — frame security-header bypass is prohibited

Jobloom will not strip or bypass `X-Frame-Options`, CSP `frame-ancestors`, or related browser
security headers to embed employer pages. If a page cannot be framed, the user views it in its
normal top-level tab. This is a non-goal, not an adapter gap.

### E — current ATS investment follows measured demand

| Surface | Openings | Share | Decision |
| --- | ---: | ---: | --- |
| Lever | 71 | 68% | first production adapter |
| Greenhouse | 19 | 18% | second |
| Ashby | 14 | 13% | third |
| SmartRecruiters | 1 | 1% | defer and remeasure |
| Workday | 0 | 0% | outside current worker milestone |
| LinkedIn/Indeed/Glassdoor | 0 | 0% | no collector/auto-apply investment |

This is a current portfolio decision, not a permanent global-market claim. Re-run it when
approved directions, source portfolio, target geography, or candidate needs change.

## 2. Consensus across both surveys

1. job-apply-plugin is the only immediate bounded reuse candidate.
2. career-ops is the strongest workflow comparison, not a deterministic-core dependency.
3. JobSync is useful for dashboard/onboarding information architecture.
4. Match percentages, keyword stuffing, generic LLM form answers, and auto-submit conflict with
   Jobloom's thesis.
5. AGPL/GPL code does not enter without an explicit project-wide license decision.
6. CAPTCHA solving, fingerprint spoofing, anti-bot evasion, unrestricted browser agents, and
   frame-header bypass are explicit non-goals.
7. Jobloom must close gaps in compensation, company research, interview stories, positioning
   consistency, ATS parseability, migration testing, and usable review surfaces.
8. “Never fabricates/submits” counts only when backed by enforcement and regression tests.

## 3. New integrated feature: sponsorship evidence bridge

The surveys independently surfaced the same domain from different sides: ATS forms ask
authorization questions; postings and employer history contain sponsorship signals; Jobloom
already separates four answer concepts that competitors collapse.

### Keep four object types separate

1. **Candidate facts/answers**: authorized now; sponsorship now; sponsorship future; employer
   action/H-1B transfer required.
2. **Posting statements**: explicit support; explicit refusal; restricted categories; no
   statement/extraction unknown.
3. **Employer historical evidence**: dated LCA/petition observations with source limitations;
   history is never proof of this role's policy.
4. **Application mappings**: exact or user-reviewed semantic question, one canonical concept,
   application scope, and per-application recheck.

### Required behavior

- explicit refusal may hard-fail only when the matching confirmed candidate need is true and the
  posting statement is reviewed;
- explicit support ranks above historical support;
- historical support may affect review priority but never answers an application question;
- absence of history is unknown, not refusal;
- broad fixture kinds require disambiguation/pause;
- no candidate immigration answer is inferred from employer data;
- every employer/posting signal carries source date and freshness policy.

### Proposed specification sequence

1. audit current `JobCard` fields and four AnswerLibrary canonical IDs;
2. define value-free `SponsorshipEvidence` and reason codes;
3. map explicit posting language deterministically while retaining stated lines;
4. define an optional dated employer-history importer and provenance;
5. update evaluation ordering without collapsing evidence types;
6. add fixture-kind ambiguity pauses;
7. expose separated evidence in job review and pre-submit summaries;
8. regression-test every prohibited substitution.

Write a separate accepted specification before production implementation.

## 4. Corrections that future agents must preserve

### Semantic replay is not live DOM

The checked-in upstream fixture is reduced to a generic semantic model. `role` helps the local
renderer and worker avoid CSS coupling; it does not prove that a current production ATS can be
located by role or that employer custom questions match the closed profile.

```text
semantic fixture -> field/lifecycle coverage
local interactive replay -> protocol and browser execution safety
sanitized DOM fixture -> locator behavior at one captured version
supervised live acceptance -> current real-site behavior for one variant
ongoing drift telemetry -> continued reliability
```

Each rung proves only its own claim.

### JobSentinel is a design source, not a reuse priority

Its multi-board discovery and deduplication do not match current investment, and Jobloom measured
that unsafe cross-city merge thresholds delete real jobs. Retain only posting-trust reason codes,
salary/offer concepts, restricted-source user control, and safe support-report ideas.

## 5. Next brainstorm questions

1. Should sanitized DOM fixtures ever be committed, or only semantic fixtures plus private live
   acceptance? What privacy/copyright evidence would be required?
2. What closes “Lever supported”: number of variants, dates, field coverage, failure rate, and
   custom-question behavior?
3. Where should dated employer sponsorship history live, and what freshness applies?
4. How should positioning consistency compare resume, LinkedIn, answers, and interview stories
   without storing a live LinkedIn profile?
5. Which ATS parseability checks are deterministic enough for material approval?
6. What is the smallest dashboard that exposes the trust substrate without duplicating rules?
7. How can usefulness be measured honestly before 30 confirmed submissions?
8. Which attractive low-adoption ideas deserve schemas/tests, and which are only README claims?
9. What dependency-admission checklist must every future integration pass?
10. What failure cases from competing tools should become Jobloom regressions?

## 5A. Product-thesis review resolutions

An independent review of `docs/product-thesis.md` produced six evidence-backed challenges. They
are resolved as follows:

1. **Empty usefulness funnel — accepted with refinement.** Outcome claims are unavailable at
   sample size zero, but local verified-field rate, task time, corrections, pauses, and final-
   action activation are measurable now. The thesis now has stages 0/5 attempts/20 submissions/
   30 submissions. No gate may be weakened to reach a denominator.
2. **Lever domain coverage — accepted.** Career evidence covers only a small part of a real form.
   Compensation, employer-specific conflicts, discovery source, protected voluntary disclosures,
   contact facts, materials, and authorization need separate authorities. See
   `docs/lever-first-form-readiness.md`.
3. **Submit is a result invariant — accepted and narrowed.** Package-level absence is necessary,
   not sufficient. Fixture counters are test oracles, not evidence about Lever. Live workers fill
   one page, never own Enter/Next/Continue, and use scoped submit-event and unexpected-navigation
   guards during execution before returning control to the user.
4. **Defer option — accepted.** Competitive capabilities may be deferred only with a named
   trigger, owner, and reopening evidence.
5. **Positioning sentence targeted the wrong competitor — accepted.** The thesis now contrasts
   model cooperation with persisted enforcement, not speed with truth.
6. **Known-liability practice is a moat — accepted.** It is now the seventh trust-substrate
   property and part of honest replayability.

Local code evidence for item 2: AnswerLibrary already permits `company` and `application_id`
scope, but matching uses exact equality and voluntary disclosures can currently be stored and
auto-filled. `normalized_employer` is a dedup/search key, not a stable legal entity ID. Conflict
reuse therefore requires an approved employer entity plus a fresh, explicitly complete registry;
missing means unknown otherwise. EEO demographic values require total exclusion, while a separate
exact-match non-disclosure policy may reduce repeated work without storing those values.

## 6. Request to the next agent

Challenge the decisions above using repository evidence or local measurements, not preference.
Produce one bounded artifact:

- dependency-admission checklist;
- sponsorship schema and prohibited-substitution matrix;
- ATS support evidence ladder and acceptance thresholds;
- positioning-consistency model;
- deterministic ATS parseability checks;
- usefulness metrics below 30 submissions.

Do not implement production code until the artifact is accepted.
