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

Design stage — no implementation yet. The recommended default mode is **Fill-Only**: fill everything, stop before submit.

Full specification: [`achieve/JOBLOOM_SKILL_SPEC.md`](achieve/JOBLOOM_SKILL_SPEC.md)

> First README. Evolves with the spec.
