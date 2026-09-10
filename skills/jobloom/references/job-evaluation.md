# Job evaluation

## Requirement tiers

`requirement_tiers.py` reads what the employer said about each requirement's *weight* —
`must_have`, `preferred`, or `unknown` — and `review_queue` reports and sorts on it. Audit
and measurements: [`docs/requirement-tier-audit-2026-09-09.md`](../../../docs/requirement-tier-audit-2026-09-09.md).

Three rules, each derived from the 112-posting queue before the code was written:

1. **The line beats the heading.** 77 lines say "preferred" under a Required heading and
   effectively one says the reverse.
2. **Two weights in one sentence is `unknown`**, unless a semicolon separates them cleanly —
   then it is two requirements, not one ambiguous one.
3. **A heading decides only when it is explicit.** `Requirements` and `Minimum
   Qualifications` do; `Qualifications`, `About You` and `Knowledge, Skills, and Abilities`
   do not, and calling those must-have is an upgrade nobody wrote.

`unknown` is never resolved quietly in either direction. It is not a weaker `preferred`; it
is the tier for a requirement whose weight the posting did not state, and it is reported as
its own column.

**A must-have is covered only by `direct` evidence.** Transferable and mention-only evidence
is reported in an `adjacent` column and never counted as covering a mandatory requirement —
the whole reason for a must-have column is that adjacent experience does not fill it.

Ordering is direction weight, then **whether any must-have gap is known** (none first), then
unique must-have coverage, then the number of known gaps, then the older evidence keys. The
asymmetry is deliberate: a known gap is a strong negative, while "no gap" is a weak positive
because most requirement text is not parsed at all. Coverage is counted in **unique**
requirements — a posting naming one tool in three paragraphs is not three requirements met. Every classification carries its reason code, the cue that fired, and the
character offset of the requirement in the posting, so a misclassification can be looked at.

**Read the coverage numbers with the distillation gap in mind:** 566 of 652 must-have lines
in the current queue yield no controlled term, so coverage describes a minority of what the
postings state. The audit says so in more detail.

## Sequence

1. Normalize employer, title, location, work arrangement, compensation, employment type, posting date, source, canonical URL, application URL, ATS, and requisition ID.
2. Deduplicate by canonical URL, employer plus requisition ID, normalized employer/title/location, description fingerprint, and application history.
3. Apply deterministic hard filters.
4. Match requirements to candidate evidence.
5. Apply soft preferences only after eligibility passes.
6. Create a compact job card and recommend an action.

## Hard filters

Normally skip when any verified condition holds:

- incompatible work authorization or explicit refusal of required sponsorship
- unmet citizenship or security-clearance restriction
- incompatible location or work arrangement
- known salary maximum below the user's floor
- wrong employment type
- missing mandatory degree, license, or certification
- clearly incompatible seniority
- closed posting, duplicate application, or excluded employer
- fraudulent, unpaid, commission-only, or non-job listing

Use `uncertain` and require review when a safety-critical condition is missing or conflicting. Company sponsorship history is only a weak signal; it cannot override the current posting.

## Evidence matching

Match required skills and responsibilities to evidence IDs, not raw resume keyword presence. Preserve the strength of every match. Missing preferred skills lower ranking; missing truly mandatory skills create a main gap and normally lead to review or skip depending on the posting.

Only a user-reviewed JobCard with passing eligibility and a `broad` or `precision` recommendation may enter the deterministic resume-adaptation planning flow in `search-directions.md`. A deadline, high-value flag, or requested keyword cannot bypass eligibility, evidence strength, or approved direction scope.

## Recommendation

- `precision`: hard filters pass, core responsibilities have direct evidence, and user value is high.
- `broad`: hard filters pass and the approved direction resume adequately covers supported requirements.
- `review`: uncertainty, evidence ambiguity, or a user choice is required.
- `skip`: verified hard-filter failure or clearly insufficient mandatory evidence.

Use user-facing match labels rather than numeric interview probabilities.

Use `application_core.py` for persistent deduplication. Treat canonical URL, employer plus requisition ID, and same-employer/title description fingerprint matches as definite duplicates. Treat normalized employer/title/location alone as a possible duplicate requiring review. See `application-state.md` for the state contract.
