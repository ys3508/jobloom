# Answer-library coverage, measured 2026-09-02

What the answer library can answer, measured twice: against an empty library, and
with the reviewed question forms applied and still no answers written. Rendered and
compared whole by `tests/test_answer_library_coverage.py`, so it cannot drift.

**This measures a fresh in-memory library, not the private one.** Nothing here reads
`.jobloom/` or the authorization living in it. The context is rebuilt in the shape of
the current one — `{country: US, application_id:
app-mgb-rq4077023}`, authorization `auth-mgb-rq4077023` — so
`auto_fill_ready` is measured with a standing authorization present rather than with
none. It is not a reading of live state.

Applying the forms to the private library is `answer_library.py
import-question-forms`, a command a person runs, and what that library holds
afterwards is a separate value-free check against the library itself.

**Value-free by construction.** Each row carries source fixture, kind, recorded
employer wording, reviewed disposition, canonical meaning, reason code and
`auto_fill_ready`. No answer, no candidate value, no local path, no token, no database
row.

## What was measured

- 45 recorded controls, sharing 35 distinct wordings. The same
  question reaches the library from three vendors, and measuring by wording would drop
  that.
- 10 reviewed question forms covering 10 canonical meanings.
- Answers written at any point: **0**.

## Result

`new_question` — nothing has ever mapped this wording to a meaning; the planner pauses.
`no_applicable_answer` — the meaning is understood and the user has not answered it yet.
The second state is the whole of what this phase can reach without a private value.

### `ashby-application-2026-08-v1`

| kind | recorded wording | disposition | canonical | baseline | after forms | auto_fill_ready |
| --- | --- | --- | --- | --- | --- | --- |
| `contact.email` | Email address | `fact` | `contact.email` | `new_question` | `no_applicable_answer` | false |
| `contact.full_name` | Full name | `fact` | — | `new_question` | `new_question` | false |
| `resume.file` | Resume | `material` | — | `new_question` | `new_question` | false |

### `greenhouse-single-page-2026-08-v1`

| kind | recorded wording | disposition | canonical | baseline | after forms | auto_fill_ready |
| --- | --- | --- | --- | --- | --- | --- |
| `authorization.sponsorship_select` | Will you require employment visa sponsorship? | `always_manual` | — | `new_question` | `new_question` | false |
| `contact.email` | Email address | `fact` | `contact.email` | `new_question` | `no_applicable_answer` | false |
| `contact.first_name` | First name | `fact` | — | `new_question` | `new_question` | false |
| `contact.last_name` | Last name | `fact` | — | `new_question` | `new_question` | false |
| `contact.location_city` | City | `fact` | — | `new_question` | `new_question` | false |
| `contact.phone` | Phone number | `fact` | `contact.phone` | `new_question` | `no_applicable_answer` | false |
| `contact.phone_country` | Phone country | `fact` | — | `new_question` | `new_question` | false |
| `contact.preferred_name` | Preferred first name | `fact` | — | `new_question` | `new_question` | false |
| `cover_letter.file` | Cover letter | `material` | — | `new_question` | `new_question` | false |
| `employment.prior_affiliate` | Have you previously worked for this company or an affiliate? | `answer` | `prior_employment_at_an_affiliate` | `new_question` | `no_applicable_answer` | false |
| `profile.linkedin` | LinkedIn profile | `fact` | `profile.linkedin` | `new_question` | `no_applicable_answer` | false |
| `profile.website` | Website | `fact` | — | `new_question` | `new_question` | false |
| `referral.contact` | Employee referral contact | `always_manual` | — | `new_question` | `new_question` | false |
| `resume.file` | Resume | `material` | — | `new_question` | `new_question` | false |
| `source.discovery` | How did you hear about this opportunity? | `answer` | `discovery_source` | `new_question` | `no_applicable_answer` | false |

### `lever-application-2026-08-v1`

| kind | recorded wording | disposition | canonical | baseline | after forms | auto_fill_ready |
| --- | --- | --- | --- | --- | --- | --- |
| `authorization.green_card` | Permanent resident? | `answer` | `permanent_residence_status` | `new_question` | `no_applicable_answer` | false |
| `authorization.sponsorship_status` | Will you require employment visa sponsorship? | `always_manual` | — | `new_question` | `new_question` | false |
| `authorization.us_citizen` | United States citizen? | `answer` | `citizenship_status` | `new_question` | `no_applicable_answer` | false |
| `authorization.work_authorized` | Authorized to work in the United States? | `answer` | `work_authorized_now` | `new_question` | `no_applicable_answer` | false |
| `compensation.target_salary` | Target salary | `always_manual` | — | `new_question` | `new_question` | false |
| `compensation.total_range` | Expected total compensation | `always_manual` | — | `new_question` | `new_question` | false |
| `conflict.customer_partner_reseller` | Worked for a customer, partner, or reseller? | `always_manual` | — | `new_question` | `new_question` | false |
| `conflict.related_person` | Related to someone at this company? | `always_manual` | — | `new_question` | `new_question` | false |
| `contact.email` | Email address | `fact` | `contact.email` | `new_question` | `no_applicable_answer` | false |
| `contact.full_name` | Full name | `fact` | — | `new_question` | `new_question` | false |
| `contact.location` | Current location | `fact` | — | `new_question` | `new_question` | false |
| `contact.phone` | Phone number | `fact` | `contact.phone` | `new_question` | `no_applicable_answer` | false |
| `eeo.disability` | Disability status | `always_manual` | — | `new_question` | `new_question` | false |
| `eeo.gender` | Gender | `always_manual` | — | `new_question` | `new_question` | false |
| `eeo.race` | Race or ethnicity | `always_manual` | — | `new_question` | `new_question` | false |
| `eeo.veteran` | Veteran status | `always_manual` | — | `new_question` | `new_question` | false |
| `employment.current_company` | Current company | `fact` | — | `new_question` | `new_question` | false |
| `employment.prior_company` | Previously worked for this company? | `answer` | `prior_employment_at_this_company` | `new_question` | `no_applicable_answer` | false |
| `location.city_state` | City and state | `fact` | — | `new_question` | `new_question` | false |
| `location.us_resident` | Live in the United States? | `answer` | `current_country_of_residence` | `new_question` | `no_applicable_answer` | false |
| `profile.github` | GitHub profile | `fact` | — | `new_question` | `new_question` | false |
| `profile.linkedin` | LinkedIn profile | `fact` | `profile.linkedin` | `new_question` | `no_applicable_answer` | false |
| `profile.location_url` | Current location profile | `fact` | — | `new_question` | `new_question` | false |
| `profile.portfolio` | Portfolio | `fact` | — | `new_question` | `new_question` | false |
| `profile.website` | Website | `fact` | — | `new_question` | `new_question` | false |
| `resume.file` | Resume | `material` | — | `new_question` | `new_question` | false |
| `source.discovery_radio` | How did you hear about this opportunity? | `answer` | `discovery_source` | `new_question` | `no_applicable_answer` | false |

## What changed, and what did not

- 15 of 45 controls moved from `new_question` to `no_applicable_answer`.
- 30 still read `new_question`. That is not a backlog: most are reviewed as
  `always_manual` or as facts, and pausing on them is the correct outcome.
- Nothing became fillable. No answer exists.

## What this does not show

- It does not show that anything has been applied to the private library. That is a
  separate, explicit command.
- It does not show that any question can be **answered**. That needs values the user
  confirms one at a time, and those live only in `.jobloom/`.
- It does not show that a real ATS page presents this wording. The corpus is a reviewed
  semantic model of real recordings, not current employer DOM.
- It does not show that a production observer can find these fields. There is none.

