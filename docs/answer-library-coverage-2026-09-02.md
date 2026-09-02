# Answer-library coverage, measured 2026-09-02

What the answer library can answer today, measured before anything was written to it.
Regenerated and checked by `tests/test_answer_library_coverage.py`, so it cannot drift
into a story.

**Value-free by construction.** Canonical meaning, recorded employer wording and reason
code. No answer, no candidate value, no private path.

## What was measured

- 35 distinct employer questions, from the 45 reviewed controls in the vendored corpus.
- Question forms in the library: **0**.
- Answers in the library: **0**.

## Result

Every question is `new_question`: nothing has ever mapped this wording to a meaning, so
the fill planner pauses on all of them. This is the state one application has been in
since 2026-08-28.

| Reviewed disposition | Recorded employer wording | Reason |
| --- | --- | --- |
| `citizenship_status` | United States citizen? | `new_question` |
| `contact.email` | Email address | `new_question` |
| `contact.first_name` | First name | `new_question` |
| `contact.full_name` | Full name | `new_question` |
| `contact.last_name` | Last name | `new_question` |
| `contact.location` | Current location | `new_question` |
| `contact.location_city` | City | `new_question` |
| `contact.location_city` | City and state | `new_question` |
| `contact.phone` | Phone number | `new_question` |
| `contact.phone_country` | Phone country | `new_question` |
| `contact.preferred_name` | Preferred first name | `new_question` |
| `cover_letter` | Cover letter | `new_question` |
| `current_country_of_residence` | Live in the United States? | `new_question` |
| `discovery_source` | How did you hear about this opportunity? | `new_question` |
| `employer_defined_compensation_manual` | Expected total compensation | `new_question` |
| `employer_defined_compensation_manual` | Target salary | `new_question` |
| `employer_entity_not_approved` | Related to someone at this company? | `new_question` |
| `employer_entity_not_approved` | Worked for a customer, partner, or reseller? | `new_question` |
| `employment.current_company` | Current company | `new_question` |
| `permanent_residence_status` | Permanent resident? | `new_question` |
| `prior_employment_at_an_affiliate` | Have you previously worked for this company or an affiliate? | `new_question` |
| `prior_employment_at_this_company` | Previously worked for this company? | `new_question` |
| `profile.github` | GitHub profile | `new_question` |
| `profile.linkedin` | LinkedIn profile | `new_question` |
| `profile.location_url` | Current location profile | `new_question` |
| `profile.portfolio` | Portfolio | `new_question` |
| `profile.website` | Website | `new_question` |
| `referral_contact_requires_user` | Employee referral contact | `new_question` |
| `resume` | Resume | `new_question` |
| `sponsorship_meaning_ambiguous` | Will you require employment visa sponsorship? | `new_question` |
| `voluntary_eeo` | Disability status | `new_question` |
| `voluntary_eeo` | Gender | `new_question` |
| `voluntary_eeo` | Race or ethnicity | `new_question` |
| `voluntary_eeo` | Veteran status | `new_question` |
| `work_authorized_now` | Authorized to work in the United States? | `new_question` |

## What this does not show

- It does not show which of these *should* be answerable. Most are reviewed as
  `always_manual` or as facts, and pausing on them is the correct outcome.
- It does not show that a real ATS page presents this wording. The corpus is a reviewed
  semantic model of real recordings, not current employer DOM.
- It does not show that a production observer can find these fields. There is none.

