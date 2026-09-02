# Answer-library coverage, measured 2026-09-02

What the answer library can answer, measured before anything was confirmed into it and
again with the reviewed question forms applied and still no answers written. Regenerated
and checked by `tests/test_answer_library_coverage.py`, so it cannot drift into a story.

**Value-free by construction.** Canonical ID, recorded employer wording, reviewed
disposition and reason code. No answer, no candidate value, no private path.

## What was measured

- 35 distinct employer questions, taken from the 45 reviewed controls in the vendored corpus.
- 10 question forms in `skills/jobloom/assets/question-forms.json`, covering 10 canonical meanings.
- Answers written at any point in this measurement: **0**.

## Result

`new_question` means nothing has ever mapped this wording to a meaning.
`no_applicable_answer` means the meaning is understood and the user has not answered it yet.
That second state is the whole of what this phase can reach without a private value.

| Canonical meaning | Recorded employer wording | Before | After forms |
| --- | --- | --- | --- |
| `citizenship_status` | United States citizen? | `new_question` | `no_applicable_answer` |
| `contact.email` | Email address | `new_question` | `no_applicable_answer` |
| `contact.phone` | Phone number | `new_question` | `no_applicable_answer` |
| `current_country_of_residence` | Live in the United States? | `new_question` | `no_applicable_answer` |
| `discovery_source` | How did you hear about this opportunity? | `new_question` | `no_applicable_answer` |
| `permanent_residence_status` | Permanent resident? | `new_question` | `no_applicable_answer` |
| `prior_employment_at_an_affiliate` | Have you previously worked for this company or an affiliate? | `new_question` | `no_applicable_answer` |
| `prior_employment_at_this_company` | Previously worked for this company? | `new_question` | `no_applicable_answer` |
| `profile.linkedin` | LinkedIn profile | `new_question` | `no_applicable_answer` |
| `work_authorized_now` | Authorized to work in the United States? | `new_question` | `no_applicable_answer` |
| — | City | `new_question` | `new_question` |
| — | City and state | `new_question` | `new_question` |
| — | Cover letter | `new_question` | `new_question` |
| — | Current company | `new_question` | `new_question` |
| — | Current location | `new_question` | `new_question` |
| — | Current location profile | `new_question` | `new_question` |
| — | Disability status | `new_question` | `new_question` |
| — | Employee referral contact | `new_question` | `new_question` |
| — | Expected total compensation | `new_question` | `new_question` |
| — | First name | `new_question` | `new_question` |
| — | Full name | `new_question` | `new_question` |
| — | Gender | `new_question` | `new_question` |
| — | GitHub profile | `new_question` | `new_question` |
| — | Last name | `new_question` | `new_question` |
| — | Phone country | `new_question` | `new_question` |
| — | Portfolio | `new_question` | `new_question` |
| — | Preferred first name | `new_question` | `new_question` |
| — | Race or ethnicity | `new_question` | `new_question` |
| — | Related to someone at this company? | `new_question` | `new_question` |
| — | Resume | `new_question` | `new_question` |
| — | Target salary | `new_question` | `new_question` |
| — | Veteran status | `new_question` | `new_question` |
| — | Website | `new_question` | `new_question` |
| — | Will you require employment visa sponsorship? | `new_question` | `new_question` |
| — | Worked for a customer, partner, or reseller? | `new_question` | `new_question` |

## What this does not show

- It does not show that any question can be **answered**: that needs values the user
  confirms one at a time, and those live only in `.jobloom/`.
- It does not show that a real ATS page presents this wording. The corpus is a reviewed
  semantic model of real recordings, not current employer DOM.
- It does not show that a production observer can find these fields. There is none.
- The 25 rows still reading `new_question` are not a backlog. Most are reviewed as
  `always_manual` or as facts, and pausing on them is the correct outcome.

