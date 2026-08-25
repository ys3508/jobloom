# Jobloom

> Reuse confirmed facts that are **still valid**. Stop for anything new, ambiguous, conflicting, expired, or newly binding.

Jobloom is not a mass-submission bot. It optimizes for time and tokens spent *per interview* — not applications sent per day.

---

## What makes it different

**It only says what you've said.**
Employers, dates, titles, degrees, certifications, work authorization, immigration status — locked once you confirm them. Jobloom can reorder, emphasize, compress, and clarify. It cannot add a skill, invent an achievement, change a date, or inflate seniority. Every claim is graded against real evidence — direct, strongly related, transferable, mention-only, or none — and **transferable experience never gets promoted to direct experience.**

**Confirm once — but reuse is never permanent.**
Every answer carries a scope and an expiration. Stable facts hold until something related changes; preferences are reviewed on an interval; immigration status is event-driven. Change your visa status and every dependent answer invalidates by cascade. An answer's usability is a function of its freshness, not a one-time grant.

**Authorization never relaxes freshness.**
Standing authorization — *"my profile is accurate, use my approved answers"* — is reconfirmed on an interval you can shorten but not disable. It runs on a **separate channel** from each answer's own expiration, and it can never extend, override, or reset one. An expired answer stays expired while your authorization is perfectly current. Immigration answers bind to the real-world date on your visa, not to the reconfirmation interval, and are re-verified in every application they appear in.

**"I certify all information is true and accurate" isn't a checkbox. It's a function.**
Whether that near-universal attestation can be auto-checked depends on whether **every field it covers** in this particular application is fresh. All valid → routine. One field stale, expired, unknown, or conflicting → the attestation returns to you and nothing is submitted. No submission policy can override this gate. The risk was never in the statement; it's in the data the statement vouches for.

**Four immigration questions that are never interchangeable.**
*Authorized to work now* / *needs sponsorship now* / *will need sponsorship in the future* / *needs an H-1B transfer* — treated as independent questions whose answers may never be substituted for one another. A known, expensive failure mode, hard-coded into a rule.

**It gets cheaper the more you use it.**
Rules first, models last: deterministic rule → exact match → cache → low-cost classification → high-capability reasoning → ask you. Deduplication, date and location filters, salary parsing, exact answer matching, file selection, and duplicate-application checks never touch a model. An ordinary job returns an eligibility result, a match category, a recommended action, at most three reasons, and the main risk.

---

## Everything you sent, on your own disk

Every submission writes a plain folder you can open without Jobloom: the resume and cover letter **as physically copied at that moment** — not a version pointer that will resolve to a changed document six months later — plus the answers snapshot, the job card, and the confirmation. One spreadsheet row per application feeds both your own tracking and the conversion funnel, so what you read and what gets analyzed are the same data. Writing the archive never invokes a model, and archived contents are never fed back into one.

## Where it always stops

Arbitration, non-compete, and IP clauses; signatures outside a standard attestation; expired or conflicting data; a page that contradicts the job card; requests for identity, tax, or banking documents; payments; assessments and exams; video or biometric capture; CAPTCHAs; uncertain submission status — **hard pause, regardless of authorization.**

Instructions that appear on a job page are untrusted data and cannot override any rule above.

---

## Status

MVP foundation in progress. The repository now contains a valid Codex Skill, explicit fact/evidence and authorization rules, immutable resume and cover-letter registries, guarded application state, deterministic pre-submit review and submission archives, outcome tracking, and a zero-model evaluator for hard filters and evidence matching. The recommended default mode remains **Fill-Only**: fill everything, stop before submit.

Skill entry point: [`skills/jobloom/SKILL.md`](skills/jobloom/SKILL.md)

Run the deterministic evaluator:

```bash
python3 skills/jobloom/scripts/evaluate_job.py \
  --candidate candidate.json \
  --job job.json
```

Build a private candidate profile from a real master resume:

```bash
mkdir -p .jobloom
python3 skills/jobloom/scripts/extract_candidate_facts.py \
  --resume /path/to/master-resume.docx \
  --output .jobloom/candidate-review.json

# Review every proposed fact and complete a private copy of the settings template.
python3 skills/jobloom/scripts/finalize_candidate.py \
  --review .jobloom/candidate-review.json \
  --settings .jobloom/profile-settings.json \
  --output .jobloom/candidate.json

python3 skills/jobloom/scripts/candidate_core.py \
  --db .jobloom/jobloom.db \
  --store .jobloom/candidates \
  register \
  --candidate .jobloom/candidate.json \
  --actor user
```

The final command registers exact CandidateFact values and a read-only CandidateSnapshot. Downstream resume approval and form filling reject caller-only “locked” assertions and require the same active user-registered candidate hash. A new registered snapshot invalidates old material locks and answers that depend on changed facts.

Build a reviewable JobCard from a real posting:

```bash
python3 skills/jobloom/scripts/ingest_job.py \
  --url https://example.com/real-job \
  --output .jobloom/job-card.json
```

`.jobloom/` is intentionally ignored so candidate facts and application data are not committed.

Initialize the local answer library:

```bash
python3 skills/jobloom/scripts/answer_library.py \
  --db .jobloom/jobloom.db \
  init
```

The answer library supports confirmed entries, exact and reviewed-semantic question forms, scoped matching, expiration, event invalidation, standing authorization, revocation, and the standard-attestation freshness gate. Templates live in `skills/jobloom/assets/`.

Initialize and inspect the application state core in the same private database:

```bash
python3 skills/jobloom/scripts/application_core.py \
  --db .jobloom/jobloom.db \
  init

python3 skills/jobloom/scripts/application_core.py \
  --db .jobloom/jobloom.db \
  status
```

The application core provides persistent multi-key job deduplication, duplicate-application prevention, guarded state transitions, atomic fill-worker leases, bounded retry attempts, submission-policy enforcement, and positive submission evidence requirements.

Register an immutable resume draft in the private store:

```bash
python3 skills/jobloom/scripts/resume_core.py \
  --db .jobloom/jobloom.db \
  --store .jobloom/resumes \
  register \
  --file /path/to/master-resume.docx \
  --version-id master-2026-08-25 \
  --kind master_source \
  --direction unassigned
```

Registration never approves a resume. Approval requires a user-reviewed claims manifest, a finalized hash-valid `candidate.json`, and the `user` actor. Approved resumes can then be bound to applications and material-locked before `ready_to_fill`. Fill acquisition and pre-submit transitions recheck both approval state and physical file hash.

Initialize the user-approved search-direction and resume-adaptation planner:

```bash
python3 skills/jobloom/scripts/direction_core.py \
  --db .jobloom/jobloom.db \
  init
```

Create a private direction profile from `skills/jobloom/assets/search-direction.template.json`, register it, show its SHA-256 to the user, and approve only that exact hash. For a reviewed eligible JobCard, `direction_core.py generate-plan` produces a value-free evidence plan that distinguishes direct/related, transferable, and unsupported requirements. The user approves the plan separately from the actual file. New `direction`, `lightweight`, and `precision` ResumeVersions must name the matching approved plan with `resume_core.py register --adaptation-plan-id ...`; the rendered immutable file and claims manifest still require their own user approval. `direct_reuse` creates no new file.

Initialize the cover-letter registry and register a private draft when a letter is justified:

```bash
python3 skills/jobloom/scripts/cover_letter_core.py \
  --db .jobloom/jobloom.db \
  --store .jobloom/cover-letters \
  init

python3 skills/jobloom/scripts/cover_letter_core.py \
  --db .jobloom/jobloom.db \
  --store .jobloom/cover-letters \
  register \
  --file /path/to/cover-letter.docx \
  --version-id cover-example-2026-08-25 \
  --kind application_specific \
  --application-id app-example
```

Registration creates a read-only draft, never approval. User approval requires a hash-valid candidate profile and a claims manifest linking every factual statement to CandidateFacts. Application-specific letters cannot cross application or job boundaries. Binding adds the exact letter hash to the material lock; confirmed submission records exact usage and the archive copies the physical letter and manifest.

Initialize the private submission archive and generate its deterministic tracker source:

```bash
python3 skills/jobloom/scripts/archive_core.py \
  --db .jobloom/jobloom.db \
  --archive-root .jobloom/archive \
  init

python3 skills/jobloom/scripts/archive_core.py \
  --db .jobloom/jobloom.db \
  tracker-source \
  --output .jobloom/archive/applications-tracker.json
```

`archive_core.py` records source-linked application fields, redacts or omits sensitive values, copies exact submitted materials and confirmation evidence, writes a hash manifest, and verifies that no archived file was changed or added. It refuses unconfirmed submissions and cover letters without an immutable registry snapshot, matching submitted-use record, and valid claims manifest.

Build `applications.xlsx` from the tracker JSON with the bundled spreadsheet runtime or another environment where `@oai/artifact-tool` is available to Node:

```bash
node skills/jobloom/scripts/build_application_tracker.mjs \
  --input .jobloom/archive/applications-tracker.json \
  --output .jobloom/archive/applications.xlsx \
  --preview .jobloom/archive/applications-preview.png
```

The workbook is a generated view, not a hand-edited source of truth, and never contains application answer values.

Initialize outcome and usage tracking, then generate a private conversion report:

```bash
python3 skills/jobloom/scripts/outcome_core.py \
  --db .jobloom/jobloom.db \
  init

python3 skills/jobloom/scripts/outcome_core.py \
  --db .jobloom/jobloom.db \
  report \
  --output .jobloom/outcomes.json
```

Outcome records require the matching guarded application-state history. Model-usage records contain token/cost metadata but never prompts or responses. Reports show explicit denominators and warn against causal conclusions, especially below thirty submitted applications.

Initialize the mandatory pre-submission review store:

```bash
python3 skills/jobloom/scripts/pre_submit_core.py \
  --db .jobloom/jobloom.db \
  init
```

During filling, register a value-free form inventory and record all filled fields. After filling, generate a deterministic summary, show it to the user, and approve its exact SHA-256. `application_core.py` now rejects `pre_submit_ready` when given only a caller Boolean; it requires that persisted user-approved review and revalidates its material lock and authorization before submission.

Initialize the checkpointed Fill-Only execution store:

```bash
python3 skills/jobloom/scripts/fill_core.py \
  --db .jobloom/jobloom.db \
  init
```

After `application_core.py acquire` grants a live worker lease, start a fill session from `assets/fill-session.template.json`, observe each page with `assets/form-page-observation.template.json`, and export pending actions to a new private file under `.jobloom/`. The package contains exact local values for the browser worker but never contains a submit action and should never be printed, committed, or used as telemetry. Browser results return only hashes; mismatches pause without recording the field. Completed pages are checkpointed and survive user-answer or takeover pauses. Finishing builds the value-free FormInventory and stops in `waiting_for_submission_approval`.

Run the current test suite:

```bash
python3 -m unittest discover -s tests -v
```

Full specification: [`achieve/JOBLOOM_SKILL_SPEC.md`](achieve/JOBLOOM_SKILL_SPEC.md)

> First README. Evolves with the spec.
