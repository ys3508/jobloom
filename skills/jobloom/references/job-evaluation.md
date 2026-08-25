# Job evaluation

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

## Recommendation

- `precision`: hard filters pass, core responsibilities have direct evidence, and user value is high.
- `broad`: hard filters pass and the approved direction resume adequately covers supported requirements.
- `review`: uncertainty, evidence ambiguity, or a user choice is required.
- `skip`: verified hard-filter failure or clearly insufficient mandatory evidence.

Use user-facing match labels rather than numeric interview probabilities.

Use `application_core.py` for persistent deduplication. Treat canonical URL, employer plus requisition ID, and same-employer/title description fingerprint matches as definite duplicates. Treat normalized employer/title/location alone as a possible duplicate requiring review. See `application-state.md` for the state contract.
