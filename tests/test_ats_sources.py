import copy
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ats_sources_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


ATS = load_script("ats_sources")
APPLICATIONS = load_script("application_core")
DIRECTIONS = load_script("direction_core")
AT = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)

GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 8126988,
            "title": "Biostatistician II",
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/8126988",
            "location": {"name": "Boston, MA"},
            "offices": [{"name": "United States of America"}],
            "departments": [{"name": "Biometrics"}],
            "requisition_id": "REQ-42",
            "company_name": "Acme Bio",
            "first_published": "2026-08-24T13:52:04-04:00",
            "updated_at": "2026-08-26T17:36:49-04:00",
            # Greenhouse returns the body HTML-escaped inside the JSON string.
            "content": "&lt;p&gt;Run mixed models &amp;amp; SAS.&lt;/p&gt;",
        },
        {
            "id": 900,
            "title": "Closed Role",
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/900",
            "location": {"name": "Remote - US"},
            "offices": [],
            "departments": [],
            "company_name": "Acme Bio",
            "first_published": "2020-01-01T00:00:00+00:00",
            "application_deadline": "2020-02-01T00:00:00+00:00",
            "content": "&lt;p&gt;Old&lt;/p&gt;",
        },
    ]
}

LEVER_PAYLOAD = [
    {
        "id": "abc-123",
        "text": "Clinical Data Manager",
        "country": "US",
        "workplaceType": "hybrid",
        "createdAt": 1755000000000,
        "categories": {"commitment": "Regular Full Time (Salary)", "department": "Clinical",
                       "location": "Arlington, TX", "team": "Data"},
        "hostedUrl": "https://jobs.lever.co/acme/abc-123",
        "applyUrl": "https://jobs.lever.co/acme/abc-123/apply",
        "description": "<div>Own the study database.</div>",
        "lists": [{"text": "Qualifications", "content": "<li>EDC experience</li>"}],
        "additional": "<div>We do not sponsor visas for this role.</div>",
    }
]

ASHBY_PAYLOAD = {
    "jobs": [
        {
            "id": "ash-1",
            "title": "Statistical Programmer",
            "location": "Remote - European Union",
            "department": "Biometrics",
            "team": "Programming",
            "employmentType": "FullTime",
            "isListed": True,
            "isRemote": True,
            "publishedAt": "2026-03-04T14:29:08.532+00:00",
            "jobUrl": "https://jobs.ashbyhq.com/acme/ash-1",
            "applyUrl": "https://jobs.ashbyhq.com/acme/ash-1/application",
            "descriptionPlain": "Write SDTM and ADaM.",
            "address": {"postalAddress": {"addressCountry": "European Union"}},
            "compensation": {
                "compensationTierSummary": "€110K – €185K • Offers Equity",
                "compensationTiers": [{"components": [
                    {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "EUR",
                     "minValue": 110000, "maxValue": 185000, "summary": "€110K – €185K"},
                    {"compensationType": "EquityPercentage", "interval": "NONE", "currencyCode": None,
                     "minValue": None, "maxValue": None, "summary": "Offers Equity"},
                ]}],
            },
        },
        {
            "id": "ash-2",
            "title": "Data Scientist",
            "location": "New York, United States",
            "isListed": False,
            "employmentType": "FullTime",
            "publishedAt": "2026-05-01T00:00:00+00:00",
            "jobUrl": "https://jobs.ashbyhq.com/acme/ash-2",
            "descriptionPlain": "Analytics.",
            "address": {"postalAddress": {"addressCountry": "United States"}},
            "compensation": {"compensationTiers": [
                {"components": [{"compensationType": "Salary", "interval": "1 YEAR",
                                 "currencyCode": "USD", "minValue": 150000, "maxValue": 180000,
                                 "summary": "$150K – $180K"}]},
                {"components": [{"compensationType": "Salary", "interval": "1 YEAR",
                                 "currencyCode": "USD", "minValue": 120000, "maxValue": 150000,
                                 "summary": "$120K – $150K"}]},
            ]},
        },
    ]
}

SMARTRECRUITERS_LIST = {
    "offset": 0, "limit": 100, "totalFound": 1,
    "content": [{
        "id": "744000143115219",
        "name": "Senior Epidemiologist",
        "refNumber": "REF2010Z",
        "releasedDate": "2026-08-12T14:04:56.128Z",
        "company": {"name": "Acme Health"},
        "location": {"city": "Warsaw", "region": "MZ", "country": "pl",
                     "remote": True, "fullLocation": "Warsaw, MZ, Poland"},
        "department": {"label": "Research"},
        "function": {"label": "Science"},
        "typeOfEmployment": {"label": "Full-time"},
        "visibility": "PUBLIC",
    }],
}
SMARTRECRUITERS_DETAIL = {
    "id": "744000143115219", "active": True,
    "postingUrl": "https://jobs.smartrecruiters.com/acme/744000143115219-senior-epidemiologist",
    "applyUrl": "https://jobs.smartrecruiters.com/acme/744000143115219?oga=true",
    "jobAd": {"sections": {
        "companyDescription": {"title": "Company Description", "text": "<p>We are Acme.</p>"},
        "jobDescription": {"title": "Job Description", "text": "<p>Design cohort studies.</p>"},
        "qualifications": {"title": "Qualifications", "text": "<p>PhD preferred.</p>"},
    }},
}


def recorded_fetch(mapping, calls=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        for fragment, payload in mapping.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise ATS.SourceError(f"unmapped url: {url}")
    return fetch


GREENHOUSE_SOURCE = {"company": "Acme Bio", "ats": "greenhouse", "board_token": "acme"}
LEVER_SOURCE = {"company": "Acme Bio", "ats": "lever", "board_token": "acme"}
ASHBY_SOURCE = {"company": "Acme Bio", "ats": "ashby", "board_token": "acme"}
SMARTRECRUITERS_SOURCE = {"company": "Acme Health", "ats": "smartrecruiters", "board_token": "acme"}


def pull(source, mapping, **kwargs):
    kwargs.setdefault("at", AT)
    kwargs.setdefault("sleep", lambda _: None)
    return ATS.pull_source(source, fetch=recorded_fetch(mapping), **kwargs)


class NormalizationTests(unittest.TestCase):
    def test_country_alias_and_iso_code(self):
        self.assertEqual(ATS.normalize_country_code("United States of America"), "US")
        self.assertEqual(ATS.normalize_country_code("pl"), "PL")

    def test_unrecognized_region_is_unknown_not_uppercased(self):
        # A card claiming "EUROPEAN UNION" as a country would pass a country filter it was
        # never actually checked against.
        self.assertEqual(ATS.normalize_country_code("European Union"), "unknown")
        self.assertEqual(ATS.normalize_country_code(""), "unknown")

    def test_country_read_from_trailing_segment(self):
        self.assertEqual(ATS.resolve_country(None, "Boston, MA, United States"), "US")
        self.assertEqual(ATS.resolve_country(None, "Eastern Timezone"), "unknown")

    def test_first_resolvable_candidate_wins(self):
        self.assertEqual(ATS.resolve_country(None, "Eastern Timezone", "Canada"), "CA")

    def test_labelled_country_field_wins_over_the_location(self):
        self.assertEqual(ATS.resolve_country("pl", "Warsaw"), "PL")
        self.assertEqual(ATS.resolve_country("US", "Somewhere odd"), "US")

    def test_a_two_letter_token_is_never_a_country_in_free_text(self):
        # "PA" is Pennsylvania here, not Panama — but only in the shape that means a state.
        self.assertEqual(ATS.resolve_country_basis(None, "Philadelphia, PA"),
                         ("US", "us_state_abbreviation"))
        self.assertEqual(ATS.resolve_country_basis(None, "Chicago, IL"),
                         ("US", "us_state_abbreviation"))

    def test_a_bare_two_letter_location_stays_unknown(self):
        # Nothing says whether a lone "DE" is Delaware or Germany, so nothing is claimed.
        self.assertEqual(ATS.resolve_country_basis(None, "DE"), ("unknown", None))
        self.assertEqual(ATS.resolve_country_basis(None, "NY office"), ("unknown", None))

    def test_the_labelled_country_field_outranks_a_state_reading(self):
        # This is what keeps "Berlin, DE" from being read as Delaware on the boards that
        # state their country; only a board that states none reaches the state rule.
        self.assertEqual(ATS.resolve_country_basis("DE", "Berlin, DE"),
                         ("DE", "board_country_field"))

    def test_how_the_country_was_reached_is_recorded(self):
        self.assertEqual(ATS.resolve_country_basis(None, "Charlottesville, Virginia"),
                         ("US", "location_name"))
        self.assertEqual(ATS.resolve_country_basis("pl", "Warsaw"), ("PL", "board_country_field"))

    def test_spelled_out_us_states_resolve(self):
        # This is how the boards actually write a US location.
        self.assertEqual(ATS.resolve_country(None, "Charlottesville, Virginia"), "US")
        self.assertEqual(ATS.resolve_country(None, "Santa Fe, New Mexico"), "US")

    def test_georgia_stays_ambiguous(self):
        # A state and a country share the name, and a location string does not say which.
        self.assertEqual(ATS.resolve_country(None, "Atlanta, Georgia"), "unknown")

    def test_arrangement_from_any_signal(self):
        self.assertEqual(ATS.normalize_arrangement("", "Remote - EU"), "remote")
        self.assertEqual(ATS.normalize_arrangement("hybrid", "Austin, TX"), "hybrid")
        self.assertEqual(ATS.normalize_arrangement("", "Austin, TX"), "unknown")

    def test_markup_blocks_stay_on_separate_lines(self):
        # Bullets carry no terminal punctuation, so a space-join would fuse a whole
        # requirements section into one segment the sponsorship scan cannot narrow.
        body = ATS.clean_markup("<p>Duties</p><ul><li>run models</li><li>no sponsorship</li></ul>")
        self.assertEqual(body.split("\n"), ["Duties", "run models", "no sponsorship"])

    def test_timestamp_accepts_epoch_millis_and_iso(self):
        self.assertEqual(ATS.normalize_timestamp(1755000000000)[:10], "2025-08-12")
        self.assertEqual(ATS.normalize_timestamp("2026-08-12T14:04:56.128Z")[:10], "2026-08-12")
        self.assertIsNone(ATS.normalize_timestamp("not a date"))
        self.assertIsNone(ATS.normalize_timestamp(None))


class GreenhouseAdapterTests(unittest.TestCase):
    def test_card_fields(self):
        result = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD})
        card = result["cards"][0]
        self.assertEqual(card["title"], "Biostatistician II")
        self.assertEqual(card["employer"], "Acme Bio")
        self.assertEqual(card["country"], "US")
        self.assertEqual(card["location"], "Boston, MA")
        self.assertEqual(card["requisition_id"], "REQ-42")
        self.assertEqual(card["ats"], "greenhouse")
        self.assertEqual(card["source"], "ats")
        self.assertEqual(card["extraction"]["ats"]["department"], "Biometrics")

    def test_escaped_markup_is_unescaped_before_it_is_parsed(self):
        result = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD})
        self.assertEqual(result["cards"][0]["description"], "Run mixed models & SAS.")

    def test_past_deadline_is_closed_and_dropped_by_default(self):
        result = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD})
        self.assertEqual(result["report"]["dropped"], {"status_not_open": 1})
        closed = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD},
                      filters={"include_closed": True})
        self.assertEqual([card["status"] for card in closed["cards"]], ["open", "closed"])

    def test_non_object_payload_is_a_source_error(self):
        with self.assertRaises(ATS.SourceError):
            pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": ["not", "an", "object"]})


class LeverAdapterTests(unittest.TestCase):
    def test_card_fields(self):
        card = pull(LEVER_SOURCE, {"api.lever.co": LEVER_PAYLOAD})["cards"][0]
        self.assertEqual(card["title"], "Clinical Data Manager")
        self.assertEqual(card["employer"], "Acme Bio")  # Lever does not name the company
        self.assertEqual(card["country"], "US")
        self.assertEqual(card["work_arrangement"], "hybrid")
        self.assertEqual(card["employment_type"], "full_time")
        self.assertEqual(card["extraction"]["ats"]["apply_url"],
                         "https://jobs.lever.co/acme/abc-123/apply")

    def test_description_keeps_the_requirement_lists(self):
        card = pull(LEVER_SOURCE, {"api.lever.co": LEVER_PAYLOAD})["cards"][0]
        self.assertIn("Own the study database.", card["description"])
        self.assertIn("EDC experience", card["description"])
        self.assertIn("Qualifications", card["description"])

    def test_sponsorship_segments_are_captured_verbatim(self):
        card = pull(LEVER_SOURCE, {"api.lever.co": LEVER_PAYLOAD})["cards"][0]
        self.assertEqual(card["sponsorship_statements"],
                         ["We do not sponsor visas for this role."])
        self.assertEqual(card["sponsorship"], "unknown")  # the scan never decides the value


class AshbyAdapterTests(unittest.TestCase):
    def test_single_band_becomes_structured_salary(self):
        card = pull(ASHBY_SOURCE, {"api.ashbyhq.com": ASHBY_PAYLOAD})["cards"][0]
        self.assertEqual(card["salary"], {"currency": "EUR", "min": 110000, "max": 185000,
                                          "unit": "YEAR"})
        self.assertEqual(card["compensation_structure"], ["Offers Equity"])
        self.assertEqual(card["work_arrangement"], "remote")
        self.assertEqual(card["country"], "unknown")  # "European Union" is not a country

    def test_conflicting_bands_leave_salary_null_and_record_the_reason(self):
        result = pull(ASHBY_SOURCE, {"api.ashbyhq.com": ASHBY_PAYLOAD},
                      filters={"include_closed": True})
        card = next(item for item in result["cards"] if item["title"] == "Data Scientist")
        self.assertIsNone(card["salary"])
        self.assertIn("multiple_compensation_tiers", card["extraction"]["notes"])
        self.assertEqual(card["status"], "closed")  # isListed false

    def test_unlisted_posting_is_dropped_by_default(self):
        result = pull(ASHBY_SOURCE, {"api.ashbyhq.com": ASHBY_PAYLOAD})
        self.assertEqual(result["report"]["dropped"], {"status_not_open": 1})


class SmartRecruitersAdapterTests(unittest.TestCase):
    def test_detail_is_fetched_and_sections_are_ordered(self):
        calls = []
        result = ATS.pull_source(
            SMARTRECRUITERS_SOURCE,
            fetch=recorded_fetch({"/postings/744": SMARTRECRUITERS_DETAIL,
                                  "/postings?": SMARTRECRUITERS_LIST}, calls),
            at=AT, sleep=lambda _: None)
        card = result["cards"][0]
        self.assertEqual(card["title"], "Senior Epidemiologist")
        self.assertEqual(card["employer"], "Acme Health")
        self.assertEqual(card["country"], "PL")
        self.assertEqual(card["work_arrangement"], "remote")
        self.assertEqual(card["requisition_id"], "REF2010Z")
        self.assertEqual(card["canonical_url"], SMARTRECRUITERS_DETAIL["postingUrl"])
        self.assertLess(card["description"].index("Design cohort studies"),
                        card["description"].index("We are Acme"))
        self.assertEqual(len([url for url in calls if "/postings/744" in url]), 1)

    def test_details_are_only_fetched_for_postings_that_survive_the_filters(self):
        calls = []
        ATS.pull_source(
            SMARTRECRUITERS_SOURCE,
            fetch=recorded_fetch({"/postings/744": SMARTRECRUITERS_DETAIL,
                                  "/postings?": SMARTRECRUITERS_LIST}, calls),
            filters={"countries": ["US"]}, at=AT, sleep=lambda _: None)
        self.assertEqual([url for url in calls if "/postings/744" in url], [])

    def test_paging_stops_at_total_found(self):
        calls = []
        ATS.pull_source(
            SMARTRECRUITERS_SOURCE,
            fetch=recorded_fetch({"/postings/744": SMARTRECRUITERS_DETAIL,
                                  "/postings?": SMARTRECRUITERS_LIST}, calls),
            at=AT, sleep=lambda _: None)
        self.assertEqual(len([url for url in calls if "/postings?" in url]), 1)


class FilterTests(unittest.TestCase):
    def summaries(self):
        return [
            {"title": "Biostatistician", "location": "Boston, MA", "country": "US",
             "work_arrangement": "onsite", "employment_type": "full_time", "status": "open",
             "posted_at": "2026-08-20T00:00:00+00:00", "external_id": "1"},
            {"title": "Sales Director", "location": "Remote - US", "country": "US",
             "work_arrangement": "remote", "employment_type": "full_time", "status": "open",
             "posted_at": None, "external_id": "2"},
            {"title": "Data Scientist", "location": "Berlin", "country": "DE",
             "work_arrangement": "hybrid", "employment_type": "contract", "status": "open",
             "posted_at": "2026-01-01T00:00:00+00:00", "external_id": "3"},
        ]

    def kept_titles(self, filters):
        active = ATS.normalize_filters(filters)
        kept, report = ATS.apply_filters(self.summaries(), active)
        return [self.summaries()[index]["title"] for index in kept], report

    def test_each_rule_reports_what_it_dropped(self):
        titles, report = self.kept_titles({"countries": ["US"]})
        self.assertEqual(titles, ["Biostatistician", "Sales Director"])
        self.assertEqual(report["dropped"], {"country": 1})

    def test_title_include_and_exclude(self):
        titles, _ = self.kept_titles({"title_contains": ["data", "biostat"],
                                      "title_excludes": ["sales"]})
        self.assertEqual(titles, ["Biostatistician", "Data Scientist"])

    def test_arrangement_location_and_employment_rules(self):
        self.assertEqual(self.kept_titles({"work_arrangements": ["remote"]})[0], ["Sales Director"])
        self.assertEqual(self.kept_titles({"location_contains": ["berlin"]})[0], ["Data Scientist"])
        self.assertEqual(self.kept_titles({"employment_types": ["contract"]})[0], ["Data Scientist"])

    def test_undated_posting_survives_a_date_filter_and_is_counted(self):
        titles, report = self.kept_titles({"posted_since": "2026-08-01"})
        self.assertEqual(titles, ["Biostatistician", "Sales Director"])
        self.assertEqual(report["undated_kept"], 1)
        self.assertEqual(report["dropped"], {"posted_since": 1})

    def test_unsupported_filter_is_rejected_rather_than_ignored(self):
        with self.assertRaises(ValueError):
            ATS.normalize_filters({"seniority": ["senior"]})

    def test_limit_is_reported_never_silent(self):
        result = pull(LEVER_SOURCE, {"api.lever.co": LEVER_PAYLOAD * 3}, limit=1)
        self.assertTrue(result["report"]["limit_truncated"])
        self.assertEqual(result["report"]["dropped_by_limit"], 2)
        self.assertEqual(len(result["cards"]), 1)


class RepetitionTests(unittest.TestCase):
    def test_one_role_posted_per_city_is_reported_not_hidden(self):
        # A board listing one opening in three cities returns three postings; a report that
        # says only "kept 3" reads as three openings.
        payload = json.loads(json.dumps(GREENHOUSE_PAYLOAD))
        payload["jobs"] = [{**payload["jobs"][0], "id": 1, "location": {"name": "Omaha, Nebraska"}},
                           {**payload["jobs"][0], "id": 2, "location": {"name": "Eugene, Oregon"}},
                           {**payload["jobs"][0], "id": 3, "location": {"name": "El Paso, Texas"}}]
        report = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": payload})["report"]
        self.assertEqual(report["kept"], 3)
        self.assertEqual(report["cards_built"], 3)
        self.assertEqual(report["distinct_descriptions"], 1)
        self.assertEqual(report["repeated_postings"], 2)

    def test_distinct_postings_report_no_repetition(self):
        report = pull(ASHBY_SOURCE, {"api.ashbyhq.com": ASHBY_PAYLOAD},
                      filters={"include_closed": True})["report"]
        self.assertEqual(report["distinct_descriptions"], 2)
        self.assertEqual(report["repeated_postings"], 0)


class CardTests(unittest.TestCase):
    def test_identity_is_the_ats_posting_not_the_title_or_url(self):
        first = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD})["cards"][0]
        renamed = json.loads(json.dumps(GREENHOUSE_PAYLOAD))
        renamed["jobs"][0]["title"] = "Biostatistician III"
        renamed["jobs"][0]["absolute_url"] = "https://job-boards.greenhouse.io/acme/jobs/8126988?src=x"
        second = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": renamed})["cards"][0]
        self.assertEqual(first["job_id"], second["job_id"])

    def test_the_same_posting_on_two_boards_is_two_jobs(self):
        card = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD})["cards"][0]
        other = pull({**GREENHOUSE_SOURCE, "board_token": "other"},
                     {"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD})["cards"][0]
        self.assertNotEqual(card["job_id"], other["job_id"])

    def test_a_pulled_card_is_never_pre_reviewed(self):
        # Structuring proposes; it never reviews. Whatever it fills, the card still has to
        # be read by a person before anything is judged against it.
        for source, mapping in ((GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD}),
                                (LEVER_SOURCE, {"api.lever.co": LEVER_PAYLOAD}),
                                (ASHBY_SOURCE, {"api.ashbyhq.com": ASHBY_PAYLOAD})):
            for card in pull(source, mapping)["cards"]:
                self.assertFalse(card["requirements_reviewed"])
                self.assertTrue(card["extraction"]["needs_user_review"])
                self.assertEqual(card["seniority"], "unknown")

    def test_card_carries_every_field_the_evaluator_requires(self):
        required = ["job_id", "canonical_url", "employer", "title", "country", "work_arrangement",
                    "employment_type", "status", "sponsorship", "required_skills",
                    "requirements_reviewed"]
        card = pull(ASHBY_SOURCE, {"api.ashbyhq.com": ASHBY_PAYLOAD})["cards"][0]
        for field in required:
            self.assertIn(field, card)

    def test_description_is_capped_and_the_cap_is_recorded(self):
        payload = json.loads(json.dumps(LEVER_PAYLOAD))
        payload[0]["description"] = "<div>" + ("word " * 60000) + "</div>"
        card = pull(LEVER_SOURCE, {"api.lever.co": payload})["cards"][0]
        self.assertEqual(len(card["description"]), ATS.MAX_DESCRIPTION_CHARS)
        self.assertIn("description_truncated", card["extraction"]["notes"])

    def test_posting_without_an_identifier_is_reported_not_dropped_silently(self):
        payload = json.loads(json.dumps(LEVER_PAYLOAD))
        payload[0]["id"] = ""
        result = pull(LEVER_SOURCE, {"api.lever.co": payload})
        self.assertEqual(result["cards"], [])
        self.assertEqual(result["report"]["errors"], 1)
        self.assertIn("identifier", result["errors"][0]["error"])


class StructuringTests(unittest.TestCase):
    """`direction_core` never reads `description`; a card whose evidence lives only in
    prose is routed on its title alone. These cover the step that fixes that."""

    def card(self, payload=None):
        return pull(LEVER_SOURCE, {"api.lever.co": payload or LEVER_PAYLOAD})["cards"][0]

    def test_prose_becomes_the_fields_routing_is_allowed_to_read(self):
        payload = json.loads(json.dumps(LEVER_PAYLOAD))
        payload[0]["lists"].append({"text": "Responsibilities",
                                    "content": "<li>Own the study database build</li>"})
        card = pull(LEVER_SOURCE, {"api.lever.co": payload})["cards"][0]
        self.assertEqual(card["required_skills"], ["EDC"])
        # A section runs until the next heading, so it may pick up trailing unheaded prose.
        # What matters here is that the stated responsibility reached a routable field.
        self.assertIn("Own the study database build", card["responsibilities"])
        self.assertIn("sections", card["extraction"])

    def test_the_verbatim_requirement_lines_travel_for_review(self):
        # The recognised term is not the requirement the employer wrote, so the line itself
        # has to survive somewhere a reviewer will see it.
        card = self.card()
        self.assertIn("EDC experience", card["extraction"]["sections"]["required_skills_stated"])

    def test_a_board_stated_field_outranks_the_prose(self):
        # The extractor reads prose; prose is the weaker source and may only fill a gap.
        payload = json.loads(json.dumps(LEVER_PAYLOAD))
        payload[0]["description"] = "<div>This is a fully remote contract position.</div>"
        card = pull(LEVER_SOURCE, {"api.lever.co": payload})["cards"][0]
        self.assertEqual(card["work_arrangement"], "hybrid")   # categories.location said so
        self.assertEqual(card["employment_type"], "full_time")  # categories.commitment did

    def test_prose_fills_only_what_the_board_left_unknown(self):
        payload = json.loads(json.dumps(GREENHOUSE_PAYLOAD))
        payload["jobs"] = [payload["jobs"][0]]
        payload["jobs"][0]["content"] = "&lt;p&gt;This is a full-time hybrid role.&lt;/p&gt;"
        card = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": payload})["cards"][0]
        self.assertEqual(card["employment_type"], "full_time")  # Greenhouse states neither
        self.assertEqual(card["work_arrangement"], "hybrid")

    def test_a_structured_card_satisfies_the_routing_contract(self):
        # The per-field caps and the split-never-drop rule live with the extractor and are
        # covered in `tests/test_posting_sections.py`. What this asserts is that a card
        # coming out of a *pull* still survives `direction_core`.
        long_line = "Design and validate clinical study databases in SAS and SQL " * 15
        payload = json.loads(json.dumps(LEVER_PAYLOAD))
        # Every structured field at once: their caps differ, and compensation is the
        # tightest of them at 300 characters and 20 items.
        payload[0]["lists"] = [{"text": heading,
                                "content": "".join(f"<li>{long_line}</li>" for _ in range(80))}
                               for heading in ("Qualifications", "Responsibilities",
                                               "Compensation", "Preferred")]
        card = pull(LEVER_SOURCE, {"api.lever.co": payload})["cards"][0]
        DIRECTIONS._validate_job_shape(card)  # raises if any cap is exceeded

    def test_a_posting_that_will_not_parse_is_noted_not_fatal(self):
        def boom(*_args, **_kwargs):
            raise RuntimeError("unparseable")
        card = ATS.build_card(LEVER_SOURCE, {"external_id": "x", "title": "Analyst",
                                             "description": "text", "canonical_url": "https://e/1"},
                              endpoint="https://e", at=AT, extract=boom)
        self.assertIn("section_extraction_failed", card["extraction"]["notes"])
        self.assertEqual(card["required_skills"], [])

    def test_an_inferred_country_is_labelled_on_the_card(self):
        payload = json.loads(json.dumps(GREENHOUSE_PAYLOAD))
        payload["jobs"] = [{**payload["jobs"][0], "offices": [],
                            "location": {"name": "Philadelphia, PA"}}]
        card = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": payload})["cards"][0]
        self.assertEqual(card["country"], "US")
        self.assertIn("country_inferred_from_state_abbreviation", card["extraction"]["notes"])


SELF_ASSERTED_ADAPTER = {
    "fetch": lambda token, get: ([], []),
    "summary": lambda posting: {},
    "detail_url": None,
    "card": lambda source, posting, detail: {},
    "board_url": lambda token: f"https://{token}.example/careers",
    "endpoint_template": "https://{board_token}.example/undocumented",
    "docs": None,
    "authorization": "self_asserted",
}


class AuthorizationTierTests(unittest.TestCase):
    """Tier 5 exists so a source known not to be platform-permitted has an honest, isolated
    place instead of being quietly washed into Tier 0."""

    def setUp(self):
        self.adapters = copy.copy(ATS.ADAPTERS)
        ATS.ADAPTERS["demo_scraped"] = SELF_ASSERTED_ADAPTER
        self.addCleanup(lambda: (ATS.ADAPTERS.clear(), ATS.ADAPTERS.update(self.adapters)))
        self.registry = ATS.empty_registry()

    def test_every_shipped_adapter_declares_its_authorization(self):
        # A new adapter must state which tier it is in; inheriting Tier 0 by omission is how
        # a scraper ends up described as platform-permitted.
        for name, adapter in self.adapters.items():
            self.assertIn(adapter.get("authorization"), ATS.AUTHORIZATIONS, name)

    def test_the_shipped_adapters_are_all_platform_permitted(self):
        for name, adapter in self.adapters.items():
            self.assertIn(adapter["authorization"], ATS.PLATFORM_PERMITTED, name)

    def test_a_self_asserted_source_must_record_the_operator_s_reasoning(self):
        # The operator carries this compliance judgement, so it cannot stay implicit.
        with self.assertRaises(ValueError) as caught:
            ATS.add_source(self.registry, "Acme", "demo_scraped", "acme", "sissi", at=AT)
        self.assertIn("compliance_basis", str(caught.exception))
        with self.assertRaises(ValueError):
            ATS.add_source(self.registry, "Acme", "demo_scraped", "acme", "sissi",
                           compliance_basis="reviewed with counsel", at=AT)

    def test_a_registered_self_asserted_source_carries_tier_and_reasoning(self):
        source = ATS.add_source(self.registry, "Acme", "demo_scraped", "acme", "sissi",
                                compliance_basis="reviewed with counsel 2026-08",
                                known_risks="breaks if the tenant endpoint changes", at=AT)
        authorization = source["authorization"]
        self.assertEqual(authorization["basis"], ATS.SELF_ASSERTED)
        self.assertEqual(authorization["tier"], 5)
        self.assertFalse(authorization["platform_permitted"])
        self.assertEqual(authorization["operator_justification"]["known_risks"],
                         "breaks if the tenant endpoint changes")

    def test_a_platform_permitted_source_takes_no_private_rationale(self):
        # The platform's own terms are the basis; recording a private one beside them would
        # blur which of the two actually applies.
        with self.assertRaises(ValueError):
            ATS.add_source(self.registry, "Acme Bio", "greenhouse", "acme", "sissi",
                           compliance_basis="we think it is fine", at=AT)

    def test_a_card_carries_its_authorization_rather_than_a_registry_lookup(self):
        # The registry changes — a source can be disabled, removed, or re-registered under a
        # different basis — and an archived card still has to say how it was read.
        card = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD})["cards"][0]
        self.assertEqual(card["authorization"], ATS.PUBLIC_JOB_BOARD_API)
        self.assertEqual(card["source_tier"], 0)
        self.assertTrue(card["platform_permitted"])

    def test_a_registered_source_keeps_the_basis_it_was_registered_under(self):
        # The invariant this tier exists for. Editing one line of ADAPTERS must not relabel
        # cards from sources already registered against it: that is a silent upgrade of
        # exactly the kind re-registration exists to prevent. Reading the adapter at card
        # time made the invariant a comment rather than a behaviour.
        source = ATS.add_source(self.registry, "Acme", "demo_scraped", "acme", "sissi",
                                compliance_basis="reviewed with counsel",
                                known_risks="tenant endpoint may change", at=AT)
        ATS.ADAPTERS["demo_scraped"] = {**SELF_ASSERTED_ADAPTER,
                                        "authorization": ATS.PUBLIC_JOB_BOARD_API}
        card = ATS.build_card(source, {"external_id": "1", "title": "Analyst",
                                       "description": "text", "canonical_url": "https://e/1"},
                              endpoint="https://e", at=AT, extract=lambda *a, **k: {})
        self.assertEqual(card["authorization"], ATS.SELF_ASSERTED)
        self.assertEqual(card["source_tier"], 5)
        self.assertFalse(card["platform_permitted"])

    def test_a_pull_carries_the_registered_basis_onto_every_card(self):
        # The identity handed to `build_card` used to drop the registry row's authorization,
        # so the card had nothing to read but the adapter.
        ATS.add_source(self.registry, "Acme Bio", "greenhouse", "acme", "sissi", at=AT)
        source = ATS.find_source(self.registry, "greenhouse", "acme")
        cards = ATS.pull_source(source, fetch=recorded_fetch(
            {"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD}), at=AT, sleep=lambda _: None)["cards"]
        self.assertTrue(cards)
        for card in cards:
            self.assertEqual(card["authorization"],
                             source["authorization"]["basis"])

    def test_an_unregistered_source_may_only_claim_what_its_adapter_claims(self):
        # A probe or a test has no registry row; the adapter's own basis is the most it can
        # honestly say, and it can never be more than that.
        card = ATS.build_card({"company": "Acme", "ats": "demo_scraped", "board_token": "acme"},
                              {"external_id": "1", "title": "Analyst", "description": "text",
                               "canonical_url": "https://e/1"},
                              endpoint="https://e", at=AT, extract=lambda *a, **k: {})
        self.assertEqual(card["authorization"], ATS.SELF_ASSERTED)

    def test_authorization_comes_from_the_adapter_not_from_caller_input(self):
        # There is no path that raises a card's authorization. A source that genuinely gains
        # platform permission is registered again under the new basis.
        fields = {"external_id": "1", "title": "Analyst", "description": "text",
                  "canonical_url": "https://e/1", "authorization": ATS.PUBLIC_JOB_BOARD_API,
                  "platform_permitted": True, "source_tier": 0}
        card = ATS.build_card({"company": "Acme", "ats": "demo_scraped", "board_token": "acme"},
                              fields, endpoint="https://e", at=AT, extract=lambda *a, **k: {})
        self.assertEqual(card["authorization"], ATS.SELF_ASSERTED)
        self.assertEqual(card["source_tier"], 5)
        self.assertFalse(card["platform_permitted"])


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.path = ATS.registry_path(self.root)
        self.registry = ATS.empty_registry()

    def test_add_records_the_authorization_basis(self):
        source = ATS.add_source(self.registry, "Acme Bio", "greenhouse", "acme", "sissi", at=AT)
        self.assertEqual(source["authorization"]["basis"], "public_job_board_api")
        self.assertFalse(source["authorization"]["credentials_used"])
        self.assertIn("boards-api.greenhouse.io", source["authorization"]["endpoint_template"])
        self.assertEqual(source["registered_by"], "sissi")
        self.assertTrue(source["enabled"])

    def test_verification_is_recorded_when_the_board_was_read(self):
        source = ATS.add_source(self.registry, "Acme Bio", "greenhouse", "acme", "sissi",
                                verification={"verified_at": AT.isoformat(), "postings": 2}, at=AT)
        self.assertEqual(source["authorization"]["verified_posting_count"], 2)

    def test_duplicate_registration_is_refused(self):
        ATS.add_source(self.registry, "Acme Bio", "greenhouse", "acme", "sissi", at=AT)
        with self.assertRaises(ValueError):
            ATS.add_source(self.registry, "Acme Bio Again", "greenhouse", "acme", "sissi", at=AT)

    def test_unsupported_ats_is_refused(self):
        with self.assertRaises(ValueError):
            ATS.add_source(self.registry, "Acme", "workday", "acme", "sissi", at=AT)

    def test_board_token_may_not_carry_a_url(self):
        # The token is interpolated into the endpoint; a path or host in it would move the
        # pull to somewhere the registry never recorded.
        for token in ("acme/../evil", "http://evil.example", "acme?x=1", "acme acme", ""):
            with self.assertRaises(ValueError):
                ATS.add_source(self.registry, "Acme", "greenhouse", token, "sissi", at=AT)

    def test_actor_is_required(self):
        with self.assertRaises(ValueError):
            ATS.add_source(self.registry, "Acme", "greenhouse", "acme", "  ", at=AT)

    def test_enable_disable_and_remove(self):
        ATS.add_source(self.registry, "Acme Bio", "lever", "acme", "sissi", at=AT)
        ATS.set_source_enabled(self.registry, "lever", "acme", False)
        self.assertEqual(ATS.list_sources(self.registry, include_disabled=False), [])
        ATS.set_source_enabled(self.registry, "lever", "acme", True)
        self.assertEqual(len(ATS.list_sources(self.registry, include_disabled=False)), 1)
        ATS.remove_source(self.registry, "lever", "acme")
        self.assertEqual(self.registry["sources"], [])
        with self.assertRaises(ValueError):
            ATS.remove_source(self.registry, "lever", "acme")

    def test_list_filters_by_company(self):
        ATS.add_source(self.registry, "Acme Bio", "lever", "acme", "sissi", at=AT)
        ATS.add_source(self.registry, "Other Co", "ashby", "other", "sissi", at=AT)
        self.assertEqual([item["company"] for item in
                          ATS.list_sources(self.registry, company="acme bio")], ["Acme Bio"])

    def test_registry_round_trips_and_stays_private(self):
        ATS.add_source(self.registry, "Acme Bio", "ashby", "acme", "sissi", at=AT)
        ATS.save_registry(self.path, self.registry)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")
        self.assertEqual(ATS.load_registry(self.path), self.registry)

    def test_missing_registry_reads_as_empty(self):
        self.assertEqual(ATS.load_registry(self.root / "absent.json"), ATS.empty_registry())

    def test_malformed_registry_is_refused(self):
        self.path.write_text('["not a registry"]', encoding="utf-8")
        with self.assertRaises(ValueError):
            ATS.load_registry(self.path)


class ProbeTests(unittest.TestCase):
    def test_probe_reports_counts_without_registering(self):
        result = ATS.probe("greenhouse", "acme",
                           fetch=recorded_fetch({"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD}),
                           at=AT)
        self.assertTrue(result["resolved"])
        self.assertEqual(result["postings"], 2)
        self.assertEqual(result["open_postings"], 1)
        self.assertIn("Biostatistician II", result["sample_titles"])

    def test_probe_token_reports_which_ats_hosts_the_board(self):
        result = ATS.probe_token("acme",
                                 fetch=recorded_fetch({"api.lever.co": LEVER_PAYLOAD}), at=AT)
        self.assertEqual(result["resolved"], ["lever"])
        failed = next(item for item in result["results"] if item["ats"] == "ashby")
        self.assertIn("unmapped", failed["error"])

    def test_an_empty_board_is_not_reported_as_found(self):
        # Some of these endpoints answer 200 with an empty list for a slug that was never
        # registered; treating that as a hit would send a pull at the wrong company.
        result = ATS.probe_token("acme", fetch=recorded_fetch({
            "api.lever.co": LEVER_PAYLOAD,
            "api.smartrecruiters.com": {"content": [], "totalFound": 0}}), at=AT)
        self.assertEqual(result["resolved"], ["lever"])
        self.assertEqual(result["answered_empty"], ["smartrecruiters"])


class PullSourcesTests(unittest.TestCase):
    def test_one_unreachable_board_does_not_lose_the_others(self):
        fetch = recorded_fetch({"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD,
                                "api.lever.co": ATS.SourceError("HTTP 404")})
        result = ATS.pull_sources([GREENHOUSE_SOURCE, LEVER_SOURCE], fetch=fetch, at=AT,
                                  sleep=lambda _: None)
        self.assertEqual(result["failed_sources"], 1)
        self.assertEqual(result["cards_built"], 1)
        failed = next(item for item in result["results"] if item.get("failed"))
        self.assertEqual(failed["ats"], "lever")
        self.assertIn("404", failed["error"])


class WriteCardsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output = Path(self.temp_dir.name) / "cards"

    def test_states_report_what_actually_changed(self):
        cards = pull(LEVER_SOURCE, {"api.lever.co": LEVER_PAYLOAD})["cards"]
        first = ATS.write_cards(cards, self.output)
        self.assertEqual([item["state"] for item in first], ["created"])
        self.assertEqual(oct(os.stat(first[0]["path"]).st_mode & 0o777), "0o600")
        self.assertEqual([item["state"] for item in ATS.write_cards(cards, self.output)],
                         ["unchanged"])
        changed = json.loads(json.dumps(LEVER_PAYLOAD))
        changed[0]["description"] = "<div>Rewritten posting.</div>"
        updated = pull(LEVER_SOURCE, {"api.lever.co": changed})["cards"]
        self.assertEqual([item["state"] for item in ATS.write_cards(updated, self.output)],
                         ["updated"])
        stored = json.loads((self.output / f"{cards[0]['job_id']}.json").read_text())
        self.assertIn("Rewritten posting.", stored["description"])


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        APPLICATIONS.initialize(self.db)
        self.addCleanup(self.db.close)

    def test_cards_enter_the_job_store_with_their_source_recorded(self):
        cards = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD})["cards"]
        result = ATS.ingest_cards(self.db, cards, at=AT)
        self.assertEqual(result["counts"], {"inserted": 1})
        row = self.db.execute("SELECT source, ats, requisition_id FROM jobs").fetchone()
        self.assertEqual((row["source"], row["ats"], row["requisition_id"]),
                         ("ats", "greenhouse", "REQ-42"))

    def test_pulling_the_same_board_twice_does_not_create_a_second_job(self):
        cards = pull(GREENHOUSE_SOURCE, {"boards-api.greenhouse.io": GREENHOUSE_PAYLOAD})["cards"]
        ATS.ingest_cards(self.db, cards, at=AT)
        second = ATS.ingest_cards(self.db, cards, at=AT)
        self.assertEqual(second["counts"], {"duplicate": 1})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
