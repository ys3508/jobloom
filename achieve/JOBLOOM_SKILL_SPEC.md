# Jobloom Skill Design and Answer Library Specification

## Document Status

- Product name: Jobloom
- Document type: Skill design, functional specification, and answer library specification
- Primary audience: Product designers, AI-agent developers, reviewers, safety reviewers, and users configuring Jobloom
- Status: Draft for review
- Language: English

## 1. Executive Summary

Jobloom is a truth-constrained, token-efficient job-search and application skill designed to reduce repetitive application work while improving the conversion rate from applications to interviews.

Jobloom is not primarily a mass-submission bot. Its purpose is to:

1. Build a verified source of truth from the user's complete resume and confirmed answers.
2. Create reusable, user-approved resumes for distinct job-search directions.
3. Discover, normalize, deduplicate, filter, and prioritize relevant jobs.
4. Separate high-touch precision applications from efficient broad applications.
5. Reuse previously confirmed application answers when their meaning, scope, and validity match.
6. Fill job applications accurately and stop only when new, ambiguous, conflicting, expired, or specially restricted information is encountered.
7. Minimize model usage through deterministic rules, caching, compact context, and tiered reasoning.
8. Track outcomes and improve the user's application strategy based on interview conversion rather than raw application volume.

Jobloom's north-star objective is:

> Minimize the total user time, model usage, and number of applications required to produce qualified interviews, without inventing candidate facts or silently making unsupported commitments.

## 2. Product Principles

### 2.1 Truth Before Persuasion

Jobloom must never invent or silently alter:

- Employers
- Employment dates
- Job titles
- Education
- Degrees
- Certifications
- Licenses
- Security clearances
- Projects
- Technical experience
- Management experience
- Metrics or achievements
- Work authorization
- Immigration status
- Criminal-history answers
- Compensation history

The skill may improve organization, emphasis, clarity, and relevance, but every material claim must be supported by the candidate fact library.

### 2.2 Confirm Once, Reuse Safely

The user should not have to answer the same question for every application.

When the user confirms an answer, Jobloom may reuse it if:

- The new question has the same verified meaning.
- The answer's scope applies to the current country, role, company, and employment type.
- The answer has not expired.
- No candidate fact or newer answer conflicts with it.
- The user has authorized automatic reuse for that answer category.

Jobloom must ask again when the question is new, ambiguous, materially different, conditional, conflicting, expired, or excluded from automatic reuse.

### 2.3 Rules Before Models

Jobloom must use the least expensive reliable method:

1. Deterministic rule
2. Exact historical match
3. Cached result
4. Low-cost semantic classification
5. High-capability reasoning
6. User clarification

A model must not be used for work that can be completed reliably through rules or stored decisions.

### 2.4 Human Control Is Configurable

The user controls:

- Which answers may be reused
- Which answers require confirmation
- Whether final submission is allowed
- Which sites may be automated
- Which companies and roles are excluded
- Daily and weekly application limits
- Precision versus broad-application limits
- Token or usage budgets
- Resume versions authorized for use
- Conditions that require Jobloom to pause

### 2.5 Optimize for Interviews, Not Application Count

Jobloom must distinguish successful form submission from successful job-search outcomes. Application volume is a throughput metric, not the primary success metric.

## 3. Scope

### 3.1 In Scope

- Candidate fact extraction and verification
- Master resume ingestion
- Direction-specific resume creation
- Resume version management
- Job preference configuration
- Job discovery or URL ingestion
- Job normalization and deduplication
- Hard filtering and preference-based ranking
- Sponsorship and work-authorization filtering
- Job-card creation
- Precision, broad, and skip recommendations
- Resume selection and limited tailoring
- Application answer library
- Form filling
- Configurable submission approval
- Duplicate-application prevention
- Submission evidence
- Application tracking
- Outcome and conversion analysis
- Token and context optimization

### 3.2 Out of Scope by Default

- Fabricating qualifications
- Bypassing hard eligibility requirements
- Solving or bypassing CAPTCHAs
- Taking video or audio assessments
- Completing personality, cognitive, or technical assessments without the user
- Uploading identity documents without explicit, case-specific authorization
- Making payments
- Providing tax or banking information
- Accepting unknown arbitration, non-compete, intellectual-property, or legal agreements
- Automatically messaging recruiters or references without authorization
- Creating public profiles outside the intended job-application flow

## 4. Skill Activation and Operating Modes

### 4.1 Activation Triggers

Jobloom should activate when the user asks to:

- Build or update a job-search profile
- Turn a master resume into a role-specific resume
- Search for suitable jobs
- Filter jobs by sponsorship, location, salary, or keywords
- Decide which jobs deserve precision applications
- Fill or prepare job applications
- Reuse prior application answers
- Track applications or interview conversion
- Reduce the model cost of job applications

### 4.2 Operating Modes

#### Advisory Mode

Jobloom analyzes and recommends but does not modify files, open application forms, or submit anything.

#### Preparation Mode

Jobloom creates job cards, resumes, cover letters, and draft answers but does not fill forms.

#### Fill-Only Mode

Jobloom fills approved answers and uploads approved documents, then stops before final submission.

#### Approval-Queue Mode

The user approves a queue of jobs. Jobloom fills each application and follows the configured submission policy.

#### Conditional Auto-Submit Mode

Jobloom may submit only when all configured conditions are met and no mandatory-pause condition is present.

The recommended default is Fill-Only Mode.

## 5. Candidate Fact Library

### 5.1 Master Resume

The user provides a complete and accurate master resume containing all relevant:

- Education
- Employment
- Internships
- Projects
- Skills
- Certifications
- Publications
- Patents
- Awards
- Volunteer experience
- Languages
- Quantified achievements

The master resume is a source document, not necessarily the document submitted to employers.

### 5.2 Fact Types

Jobloom should extract and store:

- Identity facts
- Contact facts
- Education facts
- Employment facts
- Project facts
- Skill facts
- Achievement facts
- Leadership facts
- Industry facts
- Certification and license facts
- Work-authorization facts
- Location and availability facts
- Compensation preferences

### 5.3 Evidence Links

Every material capability should be traceable to evidence. A skill claim should identify:

- Where it was used
- In which role or project
- At what depth
- For how long, when known
- What outcome it supported
- Whether the evidence is direct, related, transferable, or absent

### 5.4 Evidence Strength

Jobloom should use these evidence categories:

- Direct evidence: The candidate demonstrably performed the requested work.
- Strongly related evidence: The candidate performed highly similar work.
- Transferable evidence: The candidate has adjacent experience but not the exact requested experience.
- Mention only: The term appears but lacks meaningful supporting detail.
- No evidence: No reliable support exists.

Jobloom must not convert transferable evidence into direct experience.

### 5.5 Locked Facts

After user confirmation, the following should be locked by default:

- Employer and institution names
- Employment and education dates
- Degree names
- Formal job titles
- Certifications and licenses
- Project identity
- Quantified metrics
- Immigration and work-authorization facts
- Security clearance

Locked facts may be reformatted but not substantively changed without explicit user approval.

### 5.6 Conflict Detection

Jobloom must detect conflicts between:

- Resume versions
- Candidate facts
- Answer-library entries
- Job-application responses
- Work-history dates
- Years-of-experience calculations
- Sponsorship answers
- Relocation rules
- Salary rules

Conflicts require resolution before affected applications continue.

## 6. Resume System

### 6.0 Weighted Search Portfolio

Jobloom supports one active user-approved SearchPortfolio containing multiple distinct SearchDirections. Each allocation binds an exact direction-profile hash to a positive integer percentage, and all percentages must total 100.

The portfolio is approved once as a complete strategy. Its member directions remain separate routing and resume boundaries so consulting, clinical-data, pharma, biostatistics, and stretch roles do not collapse into one generic resume. A new approved portfolio supersedes the prior portfolio; it never mutates an approved allocation in place.

### 6.1 Direction Resumes

Jobloom creates reusable resumes for distinct job-search directions, such as:

- Backend engineering
- Full-stack engineering
- Data engineering
- Data analysis
- Machine learning
- Product management
- Program management

Each direction may have its own:

- Target titles
- Summary
- Skill emphasis
- Experience order
- Project selection
- Geography
- Compensation preference
- Filters
- Precision-application threshold

### 6.2 Permitted Transformations

Jobloom may:

- Reorder truthful content
- Emphasize relevant evidence
- Compress lower-value material
- Improve clarity and grammar
- Use job-relevant terminology supported by facts
- Improve applicant-tracking-system readability
- Select the most relevant projects and achievements

Jobloom may not:

- Add unsupported skills
- Add invented achievements
- Change dates
- Inflate seniority
- Invent management responsibility
- Invent direct experience from adjacent experience
- Alter work-authorization facts

### 6.3 Review and Approval

A direction resume must be approved before use. The review should show:

- The proposed resume
- Material changes from the master resume
- Removed or compressed content
- Reordered content
- Claims requiring attention
- Evidence supporting emphasized claims

### 6.4 Resume Selection Levels

#### Direct Reuse

Use the approved direction resume without model-generated changes.

#### Lightweight Adaptation

Add or emphasize supported keywords already present in the fact library, adjust ordering, and make limited edits.

#### Precision Adaptation

Perform a deeper but still fact-constrained adjustment for a high-value job.

### 6.5 Versioning

Each resume version should record:

- Version identifier
- Parent version
- Direction
- Creation date
- Approval date
- Approval status
- Jobs where it was used
- Responses and interviews associated with it
- Whether it remains authorized

## 7. Search Profile and Job Preferences

### 7.1 Search Criteria

The user may configure:

- Target titles and synonyms
- Industries
- Target and excluded companies
- Countries and cities
- Remote, hybrid, and onsite preferences
- Commuting radius
- Salary floor and target range
- Employment type
- Seniority
- Years-of-experience range
- Travel limits
- Company size
- Preferred technologies
- Positive keywords
- Negative keywords
- Sponsorship requirements
- Citizenship restrictions
- Security-clearance restrictions
- Recency requirements

### 7.2 Hard Filters

Hard-filter failures normally cause a job to be skipped:

- Incompatible work authorization
- Explicit refusal of required sponsorship
- Citizenship requirement not met
- Required security clearance not held
- Incompatible location or work arrangement
- Compensation below the user's floor
- Wrong employment type
- Required degree, license, or certification not held
- Clearly incompatible seniority
- Closed or expired posting
- Duplicate application
- Excluded employer
- Fraudulent, unpaid, commission-only, or non-job listing

### 7.3 Soft Preferences

Soft preferences affect ordering rather than eligibility:

- Industry preference
- Company size
- Technology preference
- Remote preference
- Commute
- Brand or reputation
- Growth potential
- Team type
- Salary transparency
- Target-company status
- Referral potential
- Posting age

## 8. Sponsorship and Work-Authorization Model

### 8.1 Required Separation

Jobloom must treat these as independent questions:

1. Is the candidate currently authorized to work in the country?
2. Does the candidate require sponsorship now?
3. Will the candidate require sponsorship in the future?
4. Does the candidate require an H-1B transfer or another specific employer action?

Answers to these questions must never be substituted for one another.

### 8.2 Sponsorship Evidence States

A job or company may be classified as:

- Explicitly supports the required sponsorship
- Explicitly does not support it
- Current posting is silent, but credible historical support exists
- Unknown
- Conflicting evidence

Historical sponsorship is a weak signal, not proof of eligibility for the current role.

### 8.3 Validity and Expiration

Work-authorization entries should include:

- Country
- Status type
- Start date, when relevant
- Expiration date, when relevant
- Current sponsorship requirement
- Future sponsorship requirement
- Transfer requirement
- Last user confirmation
- Reconfirmation trigger

Changing any underlying status should invalidate dependent answers.

## 9. Job Ingestion, Normalization, and Deduplication

### 9.1 Sources

Jobloom may accept jobs from:

- User-provided URLs
- Employer career sites
- Applicant-tracking systems
- Job boards
- Saved searches
- Imported lists
- User-authorized notifications

### 9.2 Normalization

Normalize:

- Employer name
- Job title
- Location
- Work arrangement
- Compensation
- Employment type
- Posting date
- Source
- Original URL
- Application URL
- Applicant-tracking-system type

### 9.3 Deduplication

Detect duplicates using:

- Canonical URL
- Employer and requisition identifier
- Normalized title and location
- Description similarity
- Cross-board matching
- Existing application history

Prefer the employer's original career page when available.

## 10. Job Cards

Each job should be summarized into a compact reusable card containing:

- Employer
- Title
- Location
- Work arrangement
- Compensation
- Employment type
- Seniority
- Required experience
- Required skills
- Preferred skills
- Core responsibilities
- Education requirements
- Sponsorship evidence
- Citizenship or clearance restrictions
- Posting date
- Source and application URL
- Hard-filter result
- Evidence match
- Main strength
- Main gap
- Main uncertainty
- Recommended resume
- Recommended action: precision, broad, user review, or skip

The full job description should be reloaded only when necessary.

## 11. Job Evaluation

### 11.1 Evaluation Sequence

1. Apply deterministic hard filters.
2. Match requirements to candidate evidence.
3. Evaluate soft preferences.
4. Determine the application category.
5. Identify uncertainties requiring user input.

### 11.2 User-Facing Categories

- Strong match
- Worth applying
- Borderline
- Not recommended

Internally, Jobloom may maintain ranking weights, but it must not present them as a scientifically precise probability of interview success.

### 11.3 Application Categories

#### Precision Application

Recommended when hard filters pass, core responsibilities have direct evidence, and the role has high user value.

#### Broad Application

Recommended when hard filters pass and the approved direction resume is sufficiently relevant, but deep customization has limited expected value.

#### User Review

Used when eligibility, sponsorship, compensation, or role fit is uncertain.

#### Skip

Used when hard filters fail or expected value is too low.

### 11.4 Queue Ordering

Ordering applies only within the set of jobs that pass hard filters and have sufficient evidence. Neither a deadline nor posting freshness may cause an ineligible job to be applied to.

Within that eligible set, order by:

1. Approaching deadline first. A closing date is a hard constraint: a missed deadline is irreversible, so jobs that will close if not applied to today are ordered ahead of everything else.
2. Earliest opening next. Among jobs with no urgent deadline, order from earliest posting to latest, because early submission improves the probability of being seen (rolling review) even though it does not by itself raise the interview rate.

Jobloom records discovery-to-submission latency for each application (the interval from posting or opening to submission) so the early-application assumption can be validated against response rate in section 19, rather than assumed. The operational target is to compress discovery-to-submission latency, not merely to process a fresh-looking backlog.

## 12. Precision and Broad Application Workflows

### 12.1 Broad Workflow

- Use deterministic filters.
- Use the compact job card.
- Use the approved direction resume.
- Perform a supported-keyword coverage check.
- Avoid a cover letter unless required or specifically valuable.
- Reuse approved answers.
- Invoke a model only for ambiguity or required open-text responses.

### 12.2 Precision Workflow

- Read the full job description.
- Analyze the role's core problems.
- Select the candidate's strongest evidence.
- Perform limited resume adaptation.
- Research the employer only when useful.
- Draft targeted required responses.
- Generate a cover letter only when expected value justifies it.
- Identify referral or follow-up opportunities when authorized.
- Prepare a concise screening-call brief.

## 13. Answer Library

### 13.1 Purpose

The answer library stores user-confirmed answers and decision rules so repeated application questions can be completed accurately with minimal user interruption and minimal model usage.

### 13.2 Answer Entry Requirements

Each answer entry should contain:

- Canonical question identifier
- Canonical meaning
- Answer value
- Answer type
- Source
- User-confirmation status
- Confirmation timestamp
- Effective date
- Expiration date or review interval
- Applicable country
- Applicable jurisdiction
- Applicable company, if restricted
- Applicable role or employment type, if restricted
- Preconditions
- Exclusions
- Whether automatic filling is allowed
- Whether automatic submission is allowed
- Sensitivity level
- Reconfirmation triggers
- Superseded entry, if any
- Notes for ambiguity handling

### 13.3 Answer Types

- Stable fact
- Time-sensitive fact
- Conditional preference
- Company-specific answer
- Role-specific answer
- Application-specific answer
- Voluntary disclosure preference
- Legal commitment
- Open-text template
- Derived answer

### 13.4 Sources

Valid sources include:

- User explicitly confirmed
- Verified candidate fact
- Approved resume
- User-defined rule
- Derived deterministically from verified data

Model inference alone is not a valid source for a factual answer.

### 13.5 Matching Levels

#### Exact Match

The question and context match a stored form. Reuse without a model.

#### Verified Semantic Equivalent

Wording differs, but the meaning is reliably equivalent. Reuse if scope and validity match.

The four sponsorship and work-authorization questions in section 8.1 have mutually independent canonical meanings. Semantic-equivalent reuse must never occur across them: an answer to one is never matched to another, no matter how similar the wording.

#### Conditional Match

The answer depends on job, company, location, compensation, or employment type. Evaluate conditions first.

#### Ambiguous Match

The question resembles a stored entry but may differ materially. Ask the user.

#### New Question

Ask the user, then offer to save the answer.

#### Conflict

Stop and surface the conflicting entries.

### 13.6 User Save Options

After answering a new question, the user may choose:

- Use once
- Save globally
- Save for a country or jurisdiction
- Save for a company
- Save for a role family
- Save as a conditional rule
- Always ask

### 13.7 Answer Validity Classes

#### Stable

Review only when related facts change.

Examples may include legal name, completed degree, or adulthood status.

#### Periodically Reviewed

Review on a configured interval.

Examples may include address, availability, salary preference, and relocation preference.

#### Event-Driven

Invalidate when a specific event occurs.

Examples include immigration-status changes, a new employer, relocation, or a compensation-policy update.

#### Per-Application

Never reuse beyond the current application unless the user explicitly creates a broader rule.

### 13.8 Mandatory Distinctions

Jobloom must not conflate:

- Authorized to work now versus sponsorship required now
- Sponsorship required now versus in the future
- Sponsorship versus H-1B transfer
- Willing to relocate versus currently located in the area
- Desired salary versus minimum acceptable salary
- Base salary versus total compensation
- Direct experience versus related experience
- Disability status versus preference not to disclose
- Veteran status versus preference not to disclose
- Prior employment by the company versus prior application to the company

### 13.9 Voluntary Demographic Answers

EEO, disability, veteran, gender, race, and similar voluntary responses must be represented as user preferences, not universal defaults.

The user may choose:

- A specific answer
- Prefer not to answer
- Always ask
- Reuse for a defined jurisdiction

Voluntary demographic answers default to not eligible for auto-submission unless the user explicitly authorizes reuse for a defined jurisdiction.

### 13.10 Legal Commitments

Jobloom must distinguish factual responses from new legal commitments.

Examples requiring special treatment include:

- Arbitration agreements
- Non-compete obligations
- Intellectual-property assignments
- Background-check authorizations
- Electronic signatures outside a standard application attestation
- Statements with materially new contractual terms

These should default to user review unless the user has provided explicit, sufficiently specific authorization.

### 13.11 Standing Authorization

To avoid repetitive confirmations, Jobloom supports a standing authorization in which the user periodically confirms that:

- The candidate profile remains accurate.
- Work-authorization and sponsorship facts remain current.
- Salary, location, and relocation rules remain current.
- Approved answers may be used for the approved job queue.
- Standard application attestations may be handled subject to the freshness gate in section 14.6.

Standing authorization must have a clear scope and expiration.

#### 13.11.1 Reconfirmation Interval

Standing authorization is reconfirmed on a fixed interval, defaulting to once every two weeks, rather than at the start of every application session. The user may shorten this interval but not disable it. A lapsed interval revokes standing authorization until the user reconfirms.

#### 13.11.2 Two Independent Channels

Answer freshness is governed by two independent channels. Both must be satisfied for an answer to be usable under standing authorization.

- Channel A — Authorization currency: the standing authorization interval (13.11.1) has not lapsed. This governs whether the user is currently permitted to have answers filled and attestations handled on their behalf.
- Channel B — Per-answer expiration: each answer's own expiration and event-driven invalidation (sections 8.3 and 13.7) still hold. This governs whether a specific answer is still fresh.

Channel A must never relax Channel B. A current standing authorization does not extend, override, or reset any individual answer's expiration. An answer that is expired, stale, or conflicting is not usable even while standing authorization is current, and it returns to the user regardless of the interval.

Work-authorization and immigration answers bind their expiration to the underlying real-world date (for example a visa expiration), never to the reconfirmation interval, and are re-verified on every application in which they appear.

#### 13.11.3 User-Initiated Change Declaration

The user may declare a change at any time (for example a visa change, relocation, or a new salary floor). On such a declaration, Jobloom immediately invalidates all dependent answers per section 13.7 and requires reconfirmation before they are reused.

User-initiated declaration is an accelerator for invalidation, not the only path to it. Jobloom must not rely on the user remembering to declare a change; Channel B expirations and event triggers run independently so that an undeclared change is still caught by the answer's own expiration.

## 14. Form Filling and Submission

### 14.1 Form-Filling Requirements

Jobloom may fill:

- Contact information
- Address
- Employment history
- Education
- Skills
- Work authorization
- Sponsorship
- Compensation
- Relocation
- Availability
- Voluntary demographic responses
- File uploads
- Approved open-text responses

### 14.2 Pre-Submission Check

Before submission, verify:

- Correct employer and role
- Correct candidate identity
- Correct contact information
- Correct location
- Correct work-authorization answers
- Correct sponsorship answers
- Correct compensation and relocation answers
- Correct resume version
- Correct cover letter
- All required fields completed
- No unconfirmed new answers
- No fact conflicts
- No duplicate application

### 14.3 Mandatory Pause Conditions

Pause when:

- A new or ambiguous question appears.
- A stored answer is expired.
- Candidate facts conflict.
- The page conflicts with the job card.
- A new legal agreement appears.
- Payment is requested.
- Identity, tax, or banking documents are requested.
- Camera, microphone, video, audio, or biometric activity is requested.
- An assessment or examination is required.
- An unapproved file is requested.
- Submission status is uncertain.
- The page appears unsafe or unrelated to a normal job application.

### 14.4 Submission Policies

- Never submit
- Always stop before submit
- Submit approved jobs after final summary
- Submit only known forms with known answers
- Submit jobs in an explicitly approved queue

The user must be able to revoke submission authorization at any time.

### 14.5 Submission Evidence

Record:

- Employer and role
- Submission time
- Application URL
- Resume version
- Cover-letter version
- Material answers
- Confirmation identifier
- Success evidence
- Submission policy used
- Any unresolved uncertainty

### 14.6 Attestation Freshness Gate

A standard application attestation (for example "I certify that all information is true and accurate") is not a switch toggled by submission policy. Whether it may be auto-checked is a function of the freshness of every field the attestation covers in that specific application.

Jobloom may auto-check a standard attestation under standing authorization only when both of the following hold:

- Standing authorization is current (Channel A, section 13.11.2).
- Every field appearing in this application is either a locked fact or an active answer, and no field is stale, expired, unknown, or conflicting (Channel B, section 13.11.2).

If any single field is stale, expired, unknown, or conflicting, the attestation returns to the user and this application is not auto-submitted. This gate cannot be overridden by any submission policy.

The attestation is therefore neither permanently blocked nor permanently allowed. When every covered field is fresh it is a routine action; when any covered field is stale it becomes a legal-misrepresentation risk and must return to the user. This is the reason a lapsed visa or an outdated salary floor must halt auto-submission even when standing authorization is current.

New legal commitments (arbitration, non-compete, intellectual-property assignment, or any signature outside a standard application attestation) are governed by section 13.10 and always pause, independent of this gate.

### 14.7 Local Application Archive

Jobloom preserves a local, human-readable record of every submission so the user can trace back exactly what was sent. The archive is a file-system and spreadsheet artifact, not a model task: writing it must not invoke a model, and archived contents must never be injected into a model context.

#### 14.7.1 Folder Structure

```
Jobloom/
  applications.xlsx
  /applications/
    /<date>_<employer>_<role>/
      resume_used.pdf
      cover_letter_used.pdf
      answers_snapshot.json
      confirmation.(png|txt)
      job_card.json
```

#### 14.7.2 Master Spreadsheet

`applications.xlsx` holds one row per submission, generated deterministically from backend state (never hand-edited as a data source). Columns draw from submission evidence (14.5) and application state (section 18): submission time, employer, role, location, work arrangement, source and applicant-tracking system, resume version, cover-letter used, precision or broad, current status, confirmation identifier, follow-up date, and model usage. The same rows feed the conversion funnel (section 19), so the user-facing sheet and the analytics data share one source.

#### 14.7.3 Snapshots, Not Pointers

Archived resume and cover-letter files are physical copies of the exact version submitted at that moment, not references to a version identifier. Because direction resumes keep evolving, a stored identifier would later resolve to a changed document; only a physical copy lets the user trace back what was actually sent.

#### 14.7.4 Redaction

`answers_snapshot.json` follows the log-redaction rules (17.4). Identity-document numbers, tax identifiers, and dates of birth are never written to a snapshot. Addresses and similar sensitive fields, when stored, reside in the local protected store (17.1). Archiving counts as logging for redaction purposes.

#### 14.7.5 Retention

- Metadata (the spreadsheet row: time, employer, role, status, confirmation identifier, resume version) is retained permanently as the trace-back index; it is a few kilobytes per row.
- Original PDF snapshots are retained in full by default. For an individual user the total volume is small (on the order of one to two megabytes per submission), so the default is to keep everything rather than compress, since any compression or transcription weakens the value of the copy as evidence.
- Tiered cleanup is an optional user setting, not a forced policy: for example, PDF snapshots for applications that are rejected or have had no response beyond twelve months may be archived or deleted while their metadata is retained.
- The user may delete archived material by employer, date range, or status, with metadata optionally retained, consistent with data deletion (17.7).

## 15. Failure Handling and Recovery

Classify failures such as:

- Closed job
- Invalid URL
- Login failure
- CAPTCHA
- Unsupported form
- Upload failure
- New question
- Eligibility failure
- Location failure
- Compensation failure
- Network failure
- Website failure
- Uncertain submission
- Safety restriction
- User pause

Jobloom should preserve progress, record the failed step, avoid restarting completed work, and prevent accidental duplicate submission.

## 16. Token and Context Efficiency

### 16.1 Core Strategy

Expensive understanding should happen once. Verified, deterministic results should be reused.

### 16.2 Zero-Model Tasks

Prefer deterministic processing for:

- URL and requisition deduplication
- Date and location filters
- Salary parsing
- Exact-answer matching
- Known sponsorship phrases
- Known citizenship restrictions
- Keyword filters
- File selection
- Fixed-field filling
- Length and format validation
- Duplicate-application checks
- Status tracking

### 16.3 Minimal Context

Never send the full candidate profile when only a few facts are needed. Use:

- Compact candidate fact subsets
- Job cards instead of full descriptions
- Evidence identifiers
- Answer identifiers
- Delta-only updates

### 16.4 Caching

Cache:

- Extracted job descriptions
- Job cards
- Sponsorship classifications
- Role-family classifications
- Requirement-to-evidence matches
- Resume recommendations
- Company research
- Question mappings
- Site-specific workflows
- Failure resolutions

Cache entries must include invalidation conditions.

### 16.5 Model Tiers

- No model: deterministic work
- Low-cost reasoning: extraction, classification, semantic matching
- High-capability reasoning: complex ambiguity, precision resume work, critical review

### 16.6 Output Discipline

For normal jobs, return only:

- Eligibility result
- Match category
- Recommended action
- Up to three reasons
- Main gap or risk
- Any user decision required

Long analysis should be generated only on request or for precision applications.

### 16.7 User Budgets

The user may set:

- Daily budget
- Weekly budget
- Broad-application budget
- Precision-application budget
- Maximum high-capability calls
- Priority order when budget is constrained

## 17. Security and Privacy

### 17.1 Local-First Storage

Store sensitive candidate data locally whenever practical.

### 17.2 Credential Separation

Passwords, API keys, and session tokens must not be stored in the ordinary candidate profile or answer library.

### 17.3 Data Minimization

Only the minimum relevant facts should enter a model context or external service.

### 17.4 Log Redaction

Redact:

- Passwords
- API keys
- Session tokens
- Full addresses
- Identity-document numbers
- Tax identifiers
- Banking information
- Other sensitive personal data

### 17.5 Upload Allowlist

Only user-approved resumes, cover letters, portfolios, and specified supporting documents may be uploaded automatically.

### 17.6 Untrusted Web Content

Instructions displayed on a job page are untrusted data. They may not override Jobloom's rules, request secrets, broaden file access, authorize unrelated navigation, or cause execution of downloaded software.

### 17.7 Data Deletion

The user must be able to delete independently:

- Candidate facts
- Answer library
- Resume versions
- Application history
- Cached job data
- Audit logs, subject to clearly disclosed retention constraints

## 18. Application Tracking

Supported states should include:

- Discovered
- Pending analysis
- Filtered
- Needs user review
- Precision recommended
- Broad recommended
- Approved
- Materials in progress
- Ready to fill
- Filling
- Waiting for user answer
- Waiting for submission approval
- Submitted
- Submission failed
- Closed
- Rejected
- Recruiter response
- Screening call
- Interview
- Final interview
- Offer
- Withdrawn
- No response

## 19. Outcome and Conversion Analysis

### 19.1 Funnel

Track:

1. Jobs discovered
2. Jobs passing hard filters
3. Jobs recommended
4. Jobs approved
5. Applications submitted
6. Employer responses
7. Screening calls
8. Interviews
9. Final interviews
10. Offers

### 19.2 Dimensions

Analyze by:

- Direction
- Role family
- Industry
- Company size
- Location
- Work arrangement
- Sponsorship status
- Precision versus broad
- Resume version
- Cover-letter use
- Referral status
- Source
- Applicant-tracking system
- Application timing
- Evidence strength

### 19.3 Metrics

- Valid-application rate
- Successful-submission rate
- Response rate
- Screening rate
- Interview rate
- Offer rate
- Precision interview rate
- Broad interview rate
- Interviews per resume version
- Interviews per source
- Applications per interview
- Model usage per interview
- User time per interview

### 19.4 Statistical Caution

With small samples, Jobloom should report trends rather than claim causal conclusions. Strategy changes should require sufficient evidence and user approval.

## 20. Auditability and Explainability

Jobloom should be able to answer:

- Why was this job recommended or skipped?
- Which hard filters passed or failed?
- Which candidate facts support the match?
- Which resume was used?
- What changed in the resume?
- Where did each application answer come from?
- Was the answer exact, conditional, or semantically matched?
- Which actions used a model?
- Why did Jobloom pause?
- Under which authorization was the job submitted?
- How much model usage was consumed?

## 21. Minimum Viable Product

The recommended MVP includes:

1. Master resume ingestion
2. Candidate fact extraction
3. User confirmation and fact locking
4. One weighted SearchPortfolio containing one or more independent job-search directions
5. One approved direction resume
6. Work-authorization, sponsorship, location, salary, and relocation profile
7. Answer library with exact and reviewed semantic reuse
8. Job URL ingestion or limited search import
9. Deduplication
10. Hard filters
11. Compact job cards
12. Supported-keyword coverage checks
13. Fill-only browser workflow
14. Mandatory pre-submission summary
15. Duplicate-application prevention
16. Submission evidence
17. Local application archive with master spreadsheet and per-application resume and cover-letter snapshots
18. Basic outcome tracking
19. Basic model-usage tracking

Deferred features:

- Fully unattended submission
- CAPTCHA solving
- Multiple simultaneous directions
- Automated recruiter outreach
- Referral discovery
- Deep company research by default
- Automated strategy changes
- Complex experimentation
- Large-scale parallel browsers
- Automatic acceptance of special agreements

## 22. Acceptance Criteria

Jobloom is ready for MVP use when:

- No resume claim can be introduced without evidence or user approval.
- Locked facts remain unchanged across generated resumes.
- The four sponsorship and work-authorization questions remain distinct.
- Exact stored answers can be reused without a model call.
- Ambiguous or conflicting questions reliably pause.
- A standard attestation is auto-checked only when every covered field is fresh, and any single stale, expired, unknown, or conflicting field returns it to the user (14.6).
- A current standing authorization never overrides an individual answer's expiration (13.11.2).
- The user can review the answer source before submission.
- Duplicate applications are prevented.
- The correct resume version is recorded for every application.
- Submission evidence is preserved.
- The system can explain why a job was filtered or recommended.
- Repeated applications become cheaper in model usage over time.

## 23. Evaluation Plan

### 23.1 Truthfulness Tests

- Attempt to introduce unsupported skills.
- Attempt to change employment dates.
- Attempt to inflate seniority.
- Attempt to convert transferable experience into direct experience.
- Verify that each attempt is blocked or surfaced.

### 23.2 Answer-Matching Tests

- Exact wording match
- Paraphrased equivalent
- Similar but materially different question
- Conditional relocation question
- Current versus future sponsorship
- Expired answer
- Conflicting answer
- Jurisdiction mismatch

### 23.3 Form Tests

- Known single-page form
- Known multi-page form
- New question
- File upload
- Incorrect autofill correction
- Closed job
- Uncertain submission
- Duplicate application
- Restricted legal agreement
- Attestation with every field fresh (auto-check permitted)
- Attestation with one stale field (returns to user despite current standing authorization)
- Undeclared visa change caught by per-answer expiration while standing authorization is current

### 23.4 Token-Efficiency Tests

- Repeated job processing uses cached cards.
- Repeated questions use exact matching.
- Broad applications do not reload the master resume.
- Precision applications load only relevant facts.
- Long output is not generated for ordinary filtering.

### 23.5 Outcome Tests

- Track submission success.
- Track recruiter responses.
- Track interviews by resume version and application category.
- Report small-sample uncertainty.

## 24. Open Questions for Review

Reviewers should examine:

1. Which answer categories should never permit automatic submission?
2. Is the two-week default reconfirmation interval (13.11.1) appropriate across jurisdictions, and how should standing authorization be revoked?
3. What constitutes reliable semantic equivalence?
4. Which sponsorship phrases can be handled deterministically?
5. How should site terms and account-risk policies affect supported workflows?
6. Which data may be sent to external models?
7. What cache invalidation events are mandatory?
8. How should the system measure saved user time?
9. When does precision adaptation provide measurable value?
10. What evidence threshold should be required for a skill claim?
11. How should cross-jurisdiction legal and voluntary questions be handled?
12. How can onboarding be reduced without weakening verification?

## 25. Final Product Definition

Jobloom is a reusable job-application skill that combines a verified candidate fact library, approved direction resumes, a scoped and expiring answer library, job filtering, application preparation, controlled form filling, outcome tracking, and token-efficient reasoning.

Its defining behavior is:

> Reuse confirmed and still-valid facts automatically; pause for new facts, ambiguity, conflicts, expired information, restricted actions, and materially new commitments.

Its defining business value is:

> Help users spend less time and fewer model resources on repetitive applications while increasing the number of qualified interviews produced by each application.
