"""The browser assistant is a passenger, not a driver.

Everything here pins a boundary rather than a feature: the bridge only answers about a page
the user already has open, it refuses callers without the run's token, it binds nothing but
loopback, and it does not keep a copy of what the user browsed unless they say so.
"""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "jobloom" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(f"bridge_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


BRIDGE = load("assist_bridge")
SECTIONS = load("posting_sections")
RESUMES = load("resume_core")
EVIDENCE = load("evidence_matcher")
EXTENSION = ROOT / "skills" / "jobloom" / "extension"

from tests.fixtures.ats_replay_server import ReplayServer  # noqa: E402
from tests.fixtures.replay_observer import observe as observe_replay  # noqa: E402
from tests.pdf_fixture import synthetic_pdf  # noqa: E402

PAGE = {
    "url": "https://www.linkedin.com/jobs/view/4012345678",
    "title": "Clinical Research Data Analyst",
    "employer": "Example Health System",
    "location": "Boston, MA",
    "country": "US",
    "required_skills": ["R", "SAS", "Epic"],
    "text": (
        # A whole posting, not a corner of one. The previous fixture was 150 characters
        # holding a single requirements block — the exact shape the read-completeness check
        # now refuses to judge, so asserting a confident verdict on it locked in the bug.
        "Clinical Research Data Analyst at Example Health System.\n\n"
        "About the job\n"
        "The Clinical Research Informatics group supports investigator-initiated trials "
        "across the health system. We build and maintain the data infrastructure that "
        "study teams rely on, and we work directly with principal investigators from "
        "protocol design through publication.\n\n"
        "Responsibilities\n"
        "- Build and maintain study databases for investigator-initiated clinical trials\n"
        "- Perform data quality control, validation, and reconciliation across study visits\n"
        "- Produce analysis datasets and summary tables for study teams\n"
        "- Extract and reconcile clinical data from Epic reporting environments\n"
        "- Support statistical analysis plans and contribute to manuscript preparation\n"
        "- Present data summaries to investigators and coordinate with study coordinators\n\n"
        "Required\n"
        "- Manage clinical trial data using R and SAS\n"
        "- Experience with Epic reporting\n"
        "- Bachelor's degree in a quantitative field and two years of related experience\n"
        "- Strong written and verbal communication skills\n\n"
        "Preferred\n"
        "- Experience with REDCap or a comparable electronic data capture system\n"
    ),
}


class CardBuildingTests(unittest.TestCase):
    def test_a_card_from_a_page_is_never_pre_reviewed(self):
        card = BRIDGE.build_card({**PAGE, "requirements_reviewed": True})
        self.assertFalse(card["requirements_reviewed"],
                         "a page cannot declare its own card reviewed")

    def test_an_empty_or_oversized_page_is_refused(self):
        with self.assertRaises(BRIDGE.BridgeError) as empty:
            BRIDGE.build_card({**PAGE, "text": "   "})
        self.assertEqual(empty.exception.code, "page_text_empty")
        with self.assertRaises(BRIDGE.BridgeError) as big:
            BRIDGE.build_card({**PAGE, "text": "x" * (BRIDGE.MAX_PAGE_TEXT + 1)})
        self.assertEqual(big.exception.code, "page_text_too_large")

    def test_an_insecure_page_url_is_refused(self):
        with self.assertRaises(BRIDGE.BridgeError) as error:
            BRIDGE.build_card({**PAGE, "url": "http://www.linkedin.com/jobs/view/1"})
        self.assertEqual(error.exception.code, "page_url_not_https")

    def test_structured_fields_come_from_the_page_the_user_sees(self):
        card = BRIDGE.build_card(PAGE)
        self.assertEqual(card["title"], "Clinical Research Data Analyst")
        self.assertEqual(card["required_skills"], ["R", "SAS", "Epic"])



MGB_POSTING = """Computational Research Associate
Massachusetts General Hospital

Key Responsibilities
- Conducting gene-environment interaction analyses in biobank datasets

Required
- Bachelor's or master's degree in genetic epidemiology, statistics, or a related field
- Strong programming skills in R and/or Python and comfort with Linux/shell scripting
- Excellent written and verbal communication skills

Preferred
- Experience with biomedical cloud computing environments (e.g., Terra, DNAnexus)

Compensation
Salary beginning at a minimum of $55,000 per year and a maximum of $60,000 per year.

Remote Type
Hybrid

EEO Statement
An Equal Opportunity Employer."""

BU_GENOMICS_POSTING = """About the job
In a Highly Collaborative Environment, The Successful Applicant Will
- Implement computational analysis strategies for next generation sequencing (NGS), DNA-genome, transcriptomic, epigenomic, and proteomics data.
- Direct the development and maintenance of genome-wide association and genomic analysis pipelines.
- Collaborate in interpretation of data and preparation of manuscripts, grants, and presentations.
- Prepare tables and figures for manuscripts that report analysis results.
Required Skills
Strong programming and analytic skills in R and Python or Perl, strong scientific writing skills, and three years relevant work or graduate education experience.
Biological/Statistical knowledge preferred but not required.
Equal Opportunity Employer"""

UMASS_POSTING = """About the job
Major Responsibilities
Present research findings to various constituencies.
Create databases to sort, evaluate, and present information.
Required Qualifications
Bachelors degree in Business Administration, quantitative science, a related degree, or equivalent experience
1 year of experience in a related field
Strong research, writing, and analysis skills
Demonstrated experience in computer-based skills, including database creation, search engines and Microsoft Office applications
Ability to conduct research, summarize and present key information
Demonstrated organizational, analytical, and interpersonal skills.
Our lab uses multimodal MRI and neuromodulation approaches to understand risk factors.
Applicants with experience processing and analyzing neuroimaging data are preferred.
The University is an equal opportunity employer."""


class PostingSectionTests(unittest.TestCase):
    """A posting states its sections under headings; the extractor reads, it does not guess."""

    def test_sections_are_read_from_their_headings(self):
        sections = SECTIONS.split_sections(MGB_POSTING)
        self.assertEqual(len(sections["required_skills"]), 3)
        self.assertEqual(len(sections["preferred_skills"]), 1)
        self.assertTrue(sections["responsibilities"])

    def test_a_closing_heading_ends_a_section(self):
        sections = SECTIONS.split_sections(MGB_POSTING)
        joined = " ".join(sections["compensation_structure"])
        self.assertNotIn("Equal Opportunity", joined)

    def test_hybrid_wins_over_the_word_remote_in_a_label(self):
        # "Remote Type / Hybrid" must not read as a remote role.
        self.assertEqual(SECTIONS.extract(MGB_POSTING)["work_arrangement"], "hybrid")

    def test_a_minimum_and_maximum_are_read_as_a_range(self):
        salary = SECTIONS.extract(MGB_POSTING)["salary"]
        self.assertEqual((salary["minimum"], salary["maximum"]), (55000, 60000))

    def test_a_bare_small_number_is_not_a_salary(self):
        self.assertIsNone(SECTIONS.extract("Coffee costs $6 a day.").get("salary"))

    def test_a_capability_name_is_never_offered_as_a_requirement(self):  # noqa: D401
        # "Statistical programming" is this ontology's name for a capability, not something
        # the posting asked for. Judging the user against it invents a requirement and then
        # fails them on it — which is how someone who has R comes up short of a skill R is.
        card = SECTIONS.extract(MGB_POSTING)
        for term in card["required_skills"] + card.get("preferred_skills", []):
            self.assertNotEqual(term, "Statistical programming")
            self.assertNotEqual(term, "Data workflow automation")

    def test_a_matched_capability_is_kept_for_routing_not_for_judging(self):
        distilled = SECTIONS.distill_terms(
            ["Strong programming skills in R and/or Python and comfort with Linux/shell scripting"])
        self.assertIn("R", distilled["terms"])
        self.assertTrue(all(term.startswith("cap.") for term in distilled["capabilities"]))
        self.assertFalse(any(term.startswith("cap.") for term in distilled["terms"]))

    def test_requirement_sentences_are_distilled_into_matchable_terms(self):
        # A twenty-word sentence resolves to no evidence, so the line is reduced to the
        # controlled terms it actually names.
        card = SECTIONS.extract(MGB_POSTING)
        self.assertIn("R", card["required_skills"])
        self.assertIn("Python", card["required_skills"])
        self.assertIn("Linux", card["required_skills"])
        self.assertIn("Terra", card["preferred_skills"])

    def test_the_stated_lines_are_kept_beside_the_distilled_terms(self):
        card = SECTIONS.extract(MGB_POSTING)
        self.assertEqual(len(card["required_skills_stated"]), 3)

    def test_a_requirement_nothing_recognised_is_reported_not_dropped(self):
        card = SECTIONS.extract(MGB_POSTING)
        unrecognised = card["extraction"]["unrecognised_requirements"]["required_skills"]
        self.assertTrue(any("degree" in line for line in unrecognised))

    def test_unheaded_job_description_prose_is_a_reading_fallback(self):
        text = ("Research Associate, Framingham Heart Study\n"
                "The role supports longitudinal cardiovascular research.\n"
                "This position requires a bachelor's degree and two years of experience.\n"
                "Strong programming skills in R and excellent communication skills are required.\n")
        card = SECTIONS.extract(text)
        self.assertEqual(card["extraction"]["strategy"], "job_description_fallback")
        self.assertEqual(len(card["required_skills_stated"]), 2)
        self.assertIn("R", card["required_skills"])

    def test_bu_successful_applicant_section_is_read_as_full_responsibilities(self):
        card = SECTIONS.extract(BU_GENOMICS_POSTING)
        self.assertEqual(card["extraction"]["read_status"], "complete")
        self.assertGreaterEqual(len(card["responsibilities"]), 4)
        self.assertIn("genome-wide association", " ".join(card["responsibilities"]))
        self.assertIn("transcriptomic", " ".join(card["responsibilities"]))
        self.assertTrue(any("preferred but not required" in line
                            for line in card["preferred_skills_stated"]))
        self.assertFalse(any("preferred but not required" in line
                             for line in card["required_skills_stated"]))

    def test_one_requirements_line_is_partial_not_a_successful_read(self):
        card = SECTIONS.extract(
            "Required Skills\nStrong programming and analytic skills in R and Python or Perl.")
        self.assertEqual(card["extraction"]["read_status"], "partial")

    def test_umass_prose_is_not_all_misread_as_required(self):
        card = SECTIONS.extract(UMASS_POSTING)
        self.assertEqual(len(card["required_skills_stated"]), 6)
        self.assertEqual(len(card["preferred_skills_stated"]), 1)
        self.assertNotIn("risk factors", " ".join(card["required_skills_stated"]))
        self.assertTrue(card["responsibilities"])

    def test_requirement_prose_queries_confirmed_facts(self):
        facts = [
            {"id": "research", "type": "experience_claim", "status": "confirmed",
             "evidence_strength": "direct", "value": "Conducted research and statistical analysis"},
            {"id": "writing", "type": "experience_claim", "status": "confirmed",
             "evidence_strength": "direct", "value": "Drafted a manuscript and published reports"},
        ]
        match = EVIDENCE.match_requirement_prose(
            "Strong research, writing, and analysis skills", facts)
        self.assertTrue(match["recognized"])
        self.assertEqual(match["strength"], "direct")
        self.assertEqual(match["fact_ids"], ["research", "writing"])

    def test_navigation_chrome_is_not_mistaken_for_fallback_requirements(self):
        card = SECTIONS.extract("Jobs\nBoston, MA\n99+ results\n" * 30)
        self.assertNotIn("required_skills_stated", card)

    def test_every_requirement_without_an_explicit_term_stays_visible_for_review(self):
        card = SECTIONS.extract(MGB_POSTING)
        unassessed = card["extraction"]["unassessed_requirements"]
        self.assertTrue(any("degree" in line for line in unassessed["required_skills"]))
        lines = card["extraction"]["requirement_lines"]["required_skills"]
        self.assertEqual(len(lines), len(card["required_skills_stated"]))
        self.assertTrue(any(item["recognized_terms"] for item in lines))

    def test_sponsorship_refusal_is_read_but_never_invented(self):
        self.assertEqual(SECTIONS.extract("We are unable to sponsor visas.")["sponsorship"],
                         "does_not_support")
        self.assertEqual(SECTIONS.extract(MGB_POSTING)["sponsorship"], "unknown")



class TokenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_the_token_survives_a_restart(self):
        # Rotating on every start meant re-pasting into the panel each time, which is
        # friction with no security to show for it.
        first = BRIDGE.load_or_create_token(self.root)
        self.assertEqual(first, BRIDGE.load_or_create_token(self.root))

    def test_rotation_is_available_when_the_token_has_been_seen(self):
        first = BRIDGE.load_or_create_token(self.root)
        self.assertNotEqual(first, BRIDGE.load_or_create_token(self.root, rotate=True))

    def test_the_token_file_is_not_world_readable(self):
        BRIDGE.load_or_create_token(self.root)
        mode = (self.root / BRIDGE.TOKEN_FILENAME).stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


class StartupTests(unittest.TestCase):
    """Starting twice is common; a stack trace is never the right answer to it."""

    def test_health_reports_the_code_the_bridge_is_running(self):
        self.assertRegex(BRIDGE.source_fingerprint(), r"^[0-9a-f]{12}$")

    def test_the_fingerprint_follows_the_files_behaviour_depends_on(self):
        self.assertIn("assist_bridge.py", BRIDGE.VERSIONED_SOURCES)
        self.assertIn("posting_sections.py", BRIDGE.VERSIONED_SOURCES)


class ServerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        candidate = {
            "schema_version": "0.2.0", "profile_id": "c1",
            "work_authorization": {"country": "US", "authorized_now": True,
                                   "sponsorship_now": False, "sponsorship_future": False,
                                   "employer_action_required": False, "confirmed": True},
            "search": {}, "facts": [
                {"id": "f-r", "type": "experience_claim", "value": "Programmed analyses in R and SAS", "status": "confirmed",
                 "locked": False, "evidence_strength": "direct", "keywords": ["R", "SAS"]}],
        }
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        self.candidate_path = root / "candidate.json"
        self.candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        self.db_path = root / "jobloom.db"
        connection = sqlite3.connect(str(self.db_path))
        for name in ("application_core", "direction_core", "resume_core"):
            load(name).initialize(connection)
        connection.close()
        self.server = BRIDGE.serve(db_path=self.db_path, candidate_path=self.candidate_path,
                                   port=0, allow_store=False, token="token-under-test")
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path, payload, token="token-under-test"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Jobloom-Token": token})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_save_answers_the_request(self):
        # It did not. `/save` produced its result and never sent it, so the browser waited
        # on a response that never came — and the tests missed it because they exercised
        # `saved_jobs.save` directly and never the endpoint. Storing is off in this fixture,
        # so the refusal is what comes back; what matters is that something does.
        status, body = self.post("/save", {"job_card": {
            "canonical_url": "https://jobs.example.com/1", "title": "Analyst",
            "employer": "Acme"}})
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "storing_disabled")

    def test_confirming_a_submission_answers_and_needs_storing_enabled(self):
        # Confirming writes, so it lives behind the same gate as saving: browsing must not
        # be able to accumulate a record of applications any more than of jobs.
        status, body = self.post("/confirm-submitted",
                                 {"job_url": "https://jobs.example.com/1"})
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "storing_disabled")

    def test_confirming_refuses_a_payload_without_a_job(self):
        status, body = self.post("/confirm-submitted", {})
        self.assertIn(status, {400, 403})
        self.assertTrue(body["error"])

    def test_save_refuses_a_payload_without_a_job_card(self):
        status, body = self.post("/save", {"actor": "user"})
        self.assertIn(status, {400, 403})
        self.assertTrue(body["error"])

    def test_health_lets_a_second_start_tell_stale_from_current(self):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/health")
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.load(response)
        self.assertEqual(body["source_fingerprint"], BRIDGE.source_fingerprint())

    def test_the_server_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], BRIDGE.LOOPBACK)

    def test_a_caller_without_the_run_token_is_refused(self):
        status, body = self.post("/positioning", PAGE, token="guessed")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "bad_token")

    def test_storing_is_off_unless_the_user_enabled_it(self):
        status, body = self.post("/store", {"job_card": {"requirements_reviewed": True}})
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "storing_disabled")

    def test_reading_a_posting_stores_nothing(self):
        status, _ = self.post("/positioning", PAGE)
        self.assertEqual(status, 200)
        connection = sqlite3.connect(str(self.db_path))
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
        finally:
            connection.close()

    def test_a_requirement_is_sorted_into_the_move_it_calls_for(self):
        _, body = self.post("/positioning", PAGE)
        self.assertEqual(sorted(body["classified"]), sorted(BRIDGE.CLASSES))
        seen = [item for items in body["classified"].values() for item in items]
        self.assertEqual(len(seen), len(body["evidence"]["matches"]),
                         "every stated requirement lands in exactly one class")

    def test_evidence_the_resume_does_not_carry_is_a_hidden_strength_not_a_gap(self):
        # The fixture resume carries no claims manifest, so nothing is on the resume and
        # supported requirements must read as work to add, never as work not done.
        _, body = self.post("/positioning", PAGE)
        for item in body["classified"]["hidden_strength"]:
            self.assertTrue(item["evidence"])
            self.assertFalse(any(e["on_resume"] for e in item["evidence"]))
        for item in body["classified"]["real_gap"]:
            self.assertEqual(item["evidence"], [],
                             "a real gap has no supporting evidence at all")

    def test_transferable_evidence_never_lands_in_a_direct_class(self):
        facts = [{"id": "f-near", "type": "experience_claim", "value": "SQL",
                  "status": "confirmed", "locked": False,
                  "evidence_strength": "transferable", "keywords": []}]
        match = {"requirement": "SQL", "strength": "transferable", "fact_ids": ["f-near"]}
        result = BRIDGE.classify_requirement(match, {"f-near": facts[0]}, set(), preferred=False)
        self.assertEqual(result["class"], "transferable")

    def test_course_and_skill_list_do_not_become_confirmed_work(self):
        facts = [
            {"id": "course", "type": "education", "value": "Python Data Structure",
             "status": "confirmed", "evidence_strength": "direct", "keywords": ["Python"]},
            {"id": "skills", "type": "skill", "value": "Programming: R, SAS, Python",
             "status": "confirmed", "evidence_strength": "direct", "keywords": ["Python"]},
        ]
        match = BRIDGE.evaluate_job.match_requirement("Python", facts)
        result = BRIDGE.classify_requirement(
            match, {fact["id"]: fact for fact in facts}, set(), preferred=False)
        self.assertEqual(result["class"], "transferable")
        self.assertNotEqual(result["class"], "hidden_strength")

    def test_a_page_that_gave_up_no_requirements_is_not_a_verdict(self):
        # Saying "skip" here would pass off a parsing failure as a judgement about the user.
        _, body = self.post("/positioning", {**PAGE, "required_skills": [],
                                             "text": "Jobs\nBoston, MA\n99+ results\n" * 30})
        self.assertEqual(body["verdict"]["call"], "unreadable")
        self.assertEqual(body["lead_with"], [])
        self.assertIn("nothing was read", body["notice"])

    def test_unheaded_job_description_body_does_not_become_a_read_failure(self):
        prose = ("Research Associate, Framingham Heart Study\n"
                 "This position requires two years of research experience.\n"
                 "Strong programming skills in R and excellent communication skills are required.\n")
        _, body = self.post("/positioning", {**PAGE, "text": prose,
                                             "required_skills": None})
        self.assertNotEqual(body["verdict"]["call"], "unreadable")
        self.assertTrue(body["stated_requirements"])

    def test_unassessed_required_lines_are_not_claimed_as_met(self):
        unknown = """Research Analyst
Responsibilities
- Support the research team with project work
Required
- Must demonstrate exceptional judgment in ambiguous settings
"""
        _, body = self.post("/positioning", {**PAGE, "text": unknown,
                                             "required_skills": None})
        self.assertTrue(body["unassessed_requirements"])
        self.assertEqual(body["verdict"]["call"], "review")
        self.assertEqual(body["verdict"]["unassessed"],
                         len(body["unassessed_requirements"]))
        self.assertEqual(body["verdict"]["lines_read"], len(body["stated_requirements"]))
        self.assertTrue(any(not item["recognized_terms"]
                            for item in body["stated_requirements"]))
        # The wording changed when the verdict stopped refusing to judge on any unassessed
        # line at all; what must not change is that the reason says these were not assessed
        # rather than passing them off as met.
        self.assertIn("checked against your evidence", body["verdict"]["because"])

    def test_a_direction_that_does_not_accept_it_is_not_a_reason_to_skip(self):
        # Whether the user can do the job is about their evidence. Whether it sits in a
        # registered direction is about how they budget applications. The second must not
        # answer the first.
        page = {**PAGE, "title": "Computational Scientist I",
                "text": "Computational Scientist I\n\nRequired\n- Programming in R and SAS\n"}
        _, body = self.post("/positioning", page)
        directions = body["directions"]
        if directions and all(d["decision"] == "fail" for d in directions):
            self.assertNotEqual(body["verdict"]["call"], "review")
            self.assertIsNone(body["verdict"]["direction"])
        else:
            self.assertNotIn("outside the directions", body["verdict"]["because"])

    def test_empty_fact_store_is_explicit_not_worth_a_look(self):
        candidate = json.loads(self.candidate_path.read_text(encoding="utf-8"))
        candidate["facts"] = []
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        self.candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        _, body = self.post("/positioning", PAGE)
        self.assertEqual(body["verdict"]["call"], "evidence_unavailable")
        self.assertNotEqual(body["verdict"]["call"], "review")

    def test_the_reading_answers_the_three_questions_a_user_has(self):
        _, body = self.post("/positioning", PAGE)
        self.assertIn(body["verdict"]["call"],
                      {"apply", "review", "stretch", "skip", "unreadable"})
        self.assertTrue(body["verdict"]["because"])
        for item in body["lead_with"]:
            self.assertTrue(item["evidence"], "a lead must name the work that supports it")
        for gap in body["gaps"]:
            self.assertIn(gap["obligation"], {"required", "preferred"})

    def test_a_lead_quotes_the_users_own_fact_not_the_page(self):
        # Every line the panel offers to lead with must be one of the user's own confirmed
        # facts, identified by its fact id, not a sentence lifted off the posting.
        candidate = json.loads(self.candidate_path.read_text(encoding="utf-8"))
        by_id = {fact["id"]: str(fact["value"]) for fact in candidate["facts"]}
        _, body = self.post("/positioning", PAGE)
        self.assertTrue(body["lead_with"], "the fixture candidate should cover something")
        for item in body["lead_with"]:
            for evidence in item["evidence"]:
                self.assertIn(evidence["fact_id"], by_id)
                self.assertEqual(evidence["text"], by_id[evidence["fact_id"]][:180])

    def test_the_reading_says_it_is_a_draft_and_was_not_stored(self):
        _, body = self.post("/positioning", PAGE)
        self.assertIn("nothing was stored", body["notice"])
        self.assertFalse(body["job_card"]["requirements_reviewed"])

    def test_an_unknown_endpoint_is_not_a_silent_success(self):
        status, body = self.post("/apply", PAGE)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "unknown_endpoint")


class ExtensionBoundaryTests(unittest.TestCase):
    """The shipped extension must not contain the capabilities it promises not to use."""

    def setUp(self):
        self.manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        self.sources = {name: (EXTENSION / name).read_text(encoding="utf-8")
                        for name in ("background.js", "panel.js")}

    def test_it_never_asks_for_browsing_wide_permissions(self):
        # webNavigation is held deliberately: it is the only event that reports a
        # same-document navigation, which is how these sites change the open posting.
        for permission in ("tabs", "webRequest", "cookies", "history", "<all_urls>"):
            self.assertNotIn(permission, self.manifest["permissions"])

    def test_page_access_is_optional_scoped_and_granted_by_the_user(self):
        # Job-site access is not held at install time. It is an optional grant the user
        # makes in Chrome's own dialog and can revoke, and it names two hosts, not the web.
        self.assertEqual(self.manifest["host_permissions"], ["http://127.0.0.1:8787/*"])
        optional = self.manifest["optional_host_permissions"]
        self.assertEqual(sorted(optional),
                         ["https://*.indeed.com/*", "https://www.linkedin.com/*"])
        for origin in optional:
            self.assertNotIn("*://", origin, "job-site access must be https only")
        panel = self.sources["panel.js"]
        self.assertIn("chrome.permissions.request", panel)
        self.assertIn("chrome.permissions.contains", panel)

    def test_the_title_comes_from_the_link_to_the_open_posting(self):
        # Guessing at headings and excluding the site's own by name never finishes: two
        # attempts read the posting as "Are these results helpful?" and "See how you
        # compare to others who clicked apply". The page names the job in the link to it.
        panel = self.sources["panel.js"]
        self.assertIn("function readVisiblePosting", panel)
        self.assertIn('link?.closest("h1, h2, h3")', panel)
        self.assertNotIn("CHROME_HEADINGS", panel, "no blocklist of the site's own headings")

    def test_linkedin_random_classes_do_not_hide_the_current_job_metadata(self):
        panel = self.sources["panel.js"]
        self.assertIn("linkedInMatch", panel)
        self.assertIn("headerText", panel)
        self.assertIn("work_arrangement: workArrangement", panel)

    def test_nothing_to_act_on_does_not_get_a_screen(self):
        panel = self.sources["panel.js"]
        self.assertIn('["covered", "coveredTitle", "ok", "coveredAdvice", true]',
                      panel)

    def test_all_requirement_detail_is_one_optional_drawer(self):
        html = (EXTENSION / "panel.html").read_text(encoding="utf-8")
        self.assertIn('<details id="classes-drawer">', html)
        self.assertIn('data-i18n="drawer"', html)

    def test_the_complete_collapsed_description_is_available_to_the_reader(self):
        panel = self.sources["panel.js"]
        self.assertIn("current.textContent", panel)
        self.assertIn("complete.length > visible.length", panel)
        self.assertIn("usedFullBodyFallback", panel)
        self.assertIn('matched += "+full_body"', panel)

    def test_a_new_posting_waits_for_the_detail_pane_to_match_its_id(self):
        panel = self.sources["panel.js"]
        self.assertIn("requestAnimationFrame", panel)
        self.assertIn("frames < 300", panel)
        self.assertIn("if (!reading.aligned)", panel)
        self.assertIn('found = climb(current, "current_job_with_apply")', panel)
        self.assertIn("alignedLink && description && bodyChanged", panel)
        self.assertIn("bodySignature !== previousPosting.bodySignature", panel)
        self.assertIn("args: [lastPostingSnapshot]", panel)
        self.assertIn("lastPostingKey = key", panel)
        self.assertGreater(panel.index("lastPostingKey = key"), panel.index("render(body, page)"))

    def test_a_new_title_cannot_be_paired_with_the_previous_posting_body(self):
        panel = self.sources["panel.js"]
        self.assertIn("currentId !== previousPosting.postingId", panel)
        self.assertIn("body_changed", panel)
        self.assertIn("description did not reach the current job", panel)
        self.assertGreater(panel.index("lastPostingSnapshot = { postingId: page.postingId"),
                           panel.index("render(body, page)"))

    def test_following_errors_are_visible_and_old_reads_cannot_overwrite_new_ones(self):
        panel = self.sources["panel.js"]
        self.assertIn("generation !== readGeneration", panel)
        self.assertIn("正在等待新岗位正文", panel)
        self.assertNotIn('if (!onlyIfChanged) $("status").textContent', panel)

    def test_page_diagnostics_are_visible_without_a_console(self):
        html = (EXTENSION / "panel.html").read_text(encoding="utf-8")
        self.assertIn('id="page-diagnostics"', html)
        self.assertIn('id="diagnostics"', html)

    def test_the_main_verdict_does_not_dump_internal_counts(self):
        panel = self.sources["panel.js"]
        for piece in ("recognized terms supported", "required gaps", "requirements met",
                      "JD line read", "0/0"):
            self.assertNotIn(piece, panel)
        self.assertIn('$("why").textContent', panel)
        self.assertNotIn("result.verdict.because", panel)

    def test_unassessed_posting_lines_stay_inside_the_optional_drawer(self):
        panel = self.sources["panel.js"]
        html = (EXTENSION / "panel.html").read_text(encoding="utf-8")
        self.assertIn('id="unassessed"', html)
        self.assertIn('id="stated-requirements"', html)
        self.assertIn("需要你确认", panel)
        self.assertIn("岗位原文要求", panel)
        self.assertIn("不计为满足或缺失", panel)

    def test_internal_wording_does_not_reach_the_user(self):
        panel = self.sources["panel.js"]
        self.assertIn("还没写具体成果/数字", panel)
        self.assertIn("对照了这些技能", panel)
        self.assertIn('review: "Worth a look"', panel)
        self.assertNotIn("Routing detail", panel)
        self.assertNotIn("direction.decision", panel)
        self.assertNotIn("direction.direction_id", panel)
        self.assertIn("REASON_TEXT[reason]", panel)
        self.assertIn("HUMAN_ARRANGEMENT", panel)
        self.assertIn('value !== "unknown"', panel, "unset fields are not shown as unknown")

    def test_main_view_is_job_verdict_reason_actions_then_one_drawer(self):
        html = (EXTENSION / "panel.html").read_text(encoding="utf-8")
        ordered = ['id="job-heading"', 'id="verdict"', 'id="why"', 'id="actions"',
                   'id="classes-drawer"']
        positions = [html.index(token) for token in ordered]
        self.assertEqual(positions, sorted(positions))
        # The third action is "save for later", not "skip": skipping a job means moving to
        # the next one, so a button meaning "do not apply" would be pressed by nobody.
        for key in ("tailorApply", "applyAsIs", "saveLater", "drawer"):
            self.assertIn(f'data-i18n="{key}"', html)
        self.assertNotIn('data-i18n="skip"', html)

    def test_unreadable_state_is_one_human_sentence_without_zero_counts(self):
        panel = self.sources["panel.js"]
        self.assertIn("这个岗位页面的格式我暂时读不了，换成打开岗位详情页再试。", panel)
        self.assertIn('$("actions").hidden = unavailable', panel)

    def test_language_defaults_to_english_and_persists_the_user_choice(self):
        panel = self.sources["panel.js"]
        html = (EXTENSION / "panel.html").read_text(encoding="utf-8")
        self.assertIn('language: "en"', panel)
        self.assertIn('chrome.storage.local.set({ language: state.language })', panel)
        self.assertIn('id="language"', html)
        for wording in ("Worth applying", "Worth a look", "Not a fit",
                        "Tailor & apply", "Apply as-is", "You've done this — not on your resume"):
            self.assertIn(wording, panel)

    def test_the_panel_answers_rather_than_dumps(self):
        panel = self.sources["panel.js"]
        self.assertIn("verdict", panel)
        # The four classes must stay apart in the view as well as in the engine: merging
        # them is what turns a positioning tool into a keyword counter.
        for key in ("hidden_strength", "evidence_gap", "transferable", "real_gap"):
            self.assertIn(key, panel)

    def test_the_panel_needs_no_button_to_stay_current(self):
        # Opening the panel is the request, and clicking a different posting is the next
        # one. A button asking the user to repeat an intent they already expressed is
        # friction, so there is none.
        panel = self.sources["panel.js"]
        html = (EXTENSION / "panel.html").read_text(encoding="utf-8")
        self.assertNotIn('id="read"', html)

    def test_reading_is_refused_before_the_grant(self):
        # Every read path, including the one that follows the user to a new posting, goes
        # through this guard.
        self.assertIn("if (!state.token || !(await hasPageAccess())) return;",
                      self.sources["panel.js"])

    def test_it_cannot_reach_a_job_site_from_its_own_code(self):
        for name, source in self.sources.items():
            for host in ("linkedin.com/", "indeed.com/"):
                self.assertNotIn(f"fetch(\"https://{host}", source, name)
                self.assertNotIn(f"fetch('https://{host}", source, name)

    def test_it_never_navigates_paginates_or_clicks_for_the_user(self):
        # The boundary is about not going where the user is not. Sending them somewhere,
        # opening something, pressing something on their behalf, or waking up on a timer
        # all cross it.
        forbidden = ("chrome.tabs.create", "chrome.tabs.update", "location.assign",
                     "location.replace", "window.open", ".click()", "form.submit",
                     "setInterval", "setTimeout", "MutationObserver")
        for name, source in self.sources.items():
            for token in forbidden:
                self.assertNotIn(token, source, f"{name} must not contain {token}")

    def test_following_the_user_uses_the_event_that_actually_fires(self):
        # LinkedIn swaps postings with pushState. tabs.onUpdated does not report a
        # same-document navigation, so the panel never heard the user move.
        panel = self.sources["panel.js"]
        self.assertIn("chrome.webNavigation.onHistoryStateUpdated.addListener", panel)
        self.assertIn("webNavigation", self.manifest["permissions"])

    def test_following_the_user_is_scoped_to_the_tab_and_sites_involved(self):
        panel = self.sources["panel.js"]
        self.assertIn('hostSuffix: "linkedin.com"', panel)
        self.assertIn('hostSuffix: "indeed.com"', panel)
        self.assertIn("if (active?.id !== details.tabId) return;", panel)
        self.assertIn("details.frameId !== 0", panel, "top frame only")
        self.assertIn("onlyIfChanged", panel, "an unchanged posting must not be re-read")

    def test_nothing_runs_on_a_job_site_unless_the_panel_is_open(self):
        # No declared content script, so no code of ours executes on a job page on its own.
        # Every read starts from the panel, which only exists while the user has it open.
        self.assertNotIn("content_scripts", self.manifest)
        panel = self.sources["panel.js"]
        self.assertIn("chrome.scripting.executeScript", panel)
        for watcher in ("chrome.tabs.onUpdated", "chrome.webNavigation"):
            self.assertNotIn(watcher, self.sources["background.js"],
                             "the always-running worker must watch nothing")

    def test_injection_targets_the_active_tab_only(self):
        panel = self.sources["panel.js"]
        self.assertIn("active: true, currentWindow: true", panel)
        self.assertNotIn("allFrames: true", panel)
        self.assertNotIn("chrome.tabs.query({})", panel)


if __name__ == "__main__":
    unittest.main()


# ---- extension-controlled separate guarded worker ----------------------------------------

FILL = load("fill_core")
APPLICATIONS = load("application_core")
ANSWERS = load("answer_library")
CANDIDATES = load("candidate_core")
COVERS = load("cover_letter_core")
ARCHIVE = load("archive_core")
PRE_SUBMIT = load("pre_submit_core")


def _browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).exists()
    except Exception:  # noqa: BLE001
        return False


PLAYWRIGHT = _browser_available()

if os.environ.get("JOBLOOM_REQUIRE_BROWSER") and not PLAYWRIGHT:
    raise RuntimeError(
        "JOBLOOM_REQUIRE_BROWSER is set but no Chromium is available: "
        "run `python -m playwright install chromium`")


@unittest.skipUnless(PLAYWRIGHT, "playwright is not installed")
class FillControlTests(unittest.TestCase):
    """The panel presses a button; the bridge and the authority decide everything else.

    The point of this class is not that a page gets filled — `test_fill_worker_end_to_end`
    already proves that. It is that the control surface adds no authority: the extension
    names an application and receives an opaque id, and every target, origin, path, value,
    capability and grant stays on this side of the boundary.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private = Path(self.temp.name) / "private"
        self.private.mkdir(mode=0o700)
        self.now = datetime.now(timezone.utc)
        self.db_path = self.private / "jobloom.db"
        self.db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.db.close)
        for module in (APPLICATIONS, ANSWERS, RESUMES, COVERS, ARCHIVE, PRE_SUBMIT,
                       FILL, CANDIDATES):
            module.initialize(self.db)
        self._build_candidate()
        self._build_materials()
        self._build_application()
        self._build_answers()
        self.server = BRIDGE.serve(db_path=self.db_path, candidate_path=self.candidate_path,
                                   port=0, allow_store=False, token="token-under-test",
                                   fill_headed=False)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    # ---- state, built entirely through production calls -----------------------

    def _build_candidate(self):
        facts = [
            {"id": "fact-name", "type": "identity", "value": "Verified Candidate",
             "status": "locked", "locked": True, "evidence_strength": "direct"},
            {"id": "fact-city", "type": "location", "value": "New York, NY",
             "status": "locked", "locked": True, "evidence_strength": "direct"},
            {"id": "fact-company", "type": "employment", "value": "Example Employer",
             "status": "locked", "locked": True, "evidence_strength": "direct"},
        ]
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": False, "employer_action_required": False,
                "confirmed": True},
            "search": {}, "facts": facts}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        self.candidate_path = self.private / "candidate.json"
        self.candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        self.manifest_path = self.private / "claims-manifest.json"
        self.manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-name", "claim_text": "Verified Candidate",
            "fact_ids": ["fact-name"], "evidence_strength": "direct",
            "exact_locked_value_preserved": True}]}), encoding="utf-8")
        CANDIDATES.register_snapshot(self.db, self.private / "candidates",
                                     self.candidate_path, "user", self.now)

    def _build_materials(self):
        source = self.private / "resume.pdf"
        source.write_bytes(synthetic_pdf(["Verified Candidate", "New York, NY"]))
        RESUMES.register_version(self.db, self.private / "resumes", source, "resume-1",
                                 "master_source", "general", at=self.now)
        RESUMES.approve_version(self.db, "resume-1", self.candidate_path,
                                self.manifest_path, "user", self.now)

    def _build_application(self):
        APPLICATIONS.ingest_job(self.db, {
            "job_id": "job-1", "canonical_url": "https://example.invalid/job/1",
            "employer": "Example Corp", "title": "Backend Engineer",
            "location": "New York, NY", "status": "open", "country": "US",
            "employment_type": "full_time"}, at=self.now)
        APPLICATIONS.create_application(self.db, "app-1", "job-1", category="precision",
                                        at=self.now)
        for state, actor, reason in (
            ("pending_analysis", "system", "analysis"),
            ("precision_recommended", "system", "match"),
            ("approved", "user", "approved"),
            ("materials_in_progress", "system", "materials"),
        ):
            APPLICATIONS.transition(self.db, "app-1", state, actor, reason, at=self.now)
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=self.now)
        RESUMES.lock_materials(self.db, "app-1", "user", "lock-1", at=self.now)
        APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "ready",
                                at=self.now)
        APPLICATIONS.acquire_next(self.db, "worker-1", at=self.now)

    def _build_answers(self):
        ANSWERS.add_answer(self.db, {
            "answer_id": "answer-company", "canonical_id": "current_company",
            "canonical_meaning": "Current company", "answer": "Example Employer",
            "answer_type": "stable_fact", "source_type": "user_confirmed",
            "confirmation_status": "confirmed", "confirmed_at": self.now.isoformat(),
            "validity_class": "stable",
            "scope": {"country": "US", "application_id": "app-1"},
            "auto_fill_allowed": True, "auto_submit_allowed": False})
        ANSWERS.add_question_form(self.db, "current_company", "Current company")
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-1", "confirmed_at": self.now.isoformat(),
            "expires_at": (self.now + timedelta(hours=2)).isoformat(),
            "scope": {"country": "US", "application_id": "app-1"}})

    # ---- helpers --------------------------------------------------------------

    def observed_session(self, server, path="/lever/split/0"):
        """A session with one observed page, produced the way Task 6 produces one."""
        from playwright.sync_api import sync_playwright
        FILL.start_session(
            self.db, session_id="session-1", application_id="app-1", worker_id="worker-1",
            form_url=f"{server.origin}{path}", observed_employer="Example Corp",
            observed_role="Backend Engineer", known_form=True, authorization_id="auth-1",
            authorization_context={"country": "US", "application_id": "app-1"},
            at=self.now)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_context().new_page()
                page.goto(f"{server.origin}{path}", wait_until="domcontentloaded")
                observation = observe_replay(page, "page-1", 0, f"{server.origin}{path}")
            finally:
                browser.close()
        observed = FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                                     observation, self.now)
        self.assertNotEqual(observed.get("status"), "paused", observed.get("reasons"))

    def post(self, path, payload, token="token-under-test"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Jobloom-Token": token})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def oracle(self, server):
        with urllib.request.urlopen(f"{server.origin}/__state", timeout=5) as response:
            return json.load(response)["final_action_activations"]

    # ---- the run itself -------------------------------------------------------

    def test_prepare_then_run_executes_exactly_one_page(self):
        # Two requests and two gestures, named as such: the Run press is the one that
        # executes, and the Prepare press before it exists so there is a summary to read.
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            status, prepared = self.post("/fill/prepare", {"application_id": "app-1"})
            self.assertEqual(status, 200, prepared)
            self.assertEqual(prepared["status"], "prepared")
            self.assertTrue(prepared["execution_id"])
            status, done = self.post("/fill/execute",
                                     {"execution_id": prepared["execution_id"]})
            self.assertEqual(status, 200, done)
            self.assertEqual(done["status"], "verified")
            self.assertEqual(done["verified"], prepared["actions"]["count"])
            self.assertTrue(done["package_consumed"])
            # One page. The session is still active and the second page was never opened.
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM fill_pages").fetchone()[0], 1)
            # The target's own counter, which is the only party that could honestly say a
            # form was sent.
            self.assertEqual(self.oracle(server), 0)

    def test_the_prepared_summary_carries_no_value_path_or_target(self):
        """Built by naming fields, so this is a check on the naming.

        The forbidden strings are the ones actually in play: the candidate's locked name and
        city, the private root every artefact lives under, and the replay's own origin. A
        summary that leaked any of them would hand the panel something the boundary exists
        to keep from it.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            _, prepared = self.post("/fill/prepare", {"application_id": "app-1"})
            _, done = self.post("/fill/execute",
                                {"execution_id": prepared["execution_id"]})
            grant = self.db.execute(
                "SELECT grant_id, secret FROM execution_grants").fetchone()
            forbidden = [needle for needle in [
                "Verified Candidate", "New York, NY", "Example Employer",
                str(self.private), server.origin, "127.0.0.1", "lever",
                ".pdf", ".json", "actions.json", "result.json", "capability",
                "resume-1", "auth-1", "session-1", "page-1", "worker-1",
                grant["grant_id"], grant["secret"], "token-under-test",
            ] if needle]
            self.assertTrue(grant["grant_id"])
            for body, label in ((prepared, "prepare"), (done, "execute")):
                rendered = json.dumps(body, ensure_ascii=False)
                for needle in forbidden:
                    with self.subTest(response=label, needle=needle):
                        self.assertNotIn(needle, rendered)

    def test_the_panel_cannot_name_a_target_origin_or_tab(self):
        """A closed field set, so an extra field is a refusal rather than an ignored extra.

        The failure this prevents is not a caller today but a caller tomorrow: a field that
        is quietly dropped reads, to the next person, like a field that is merely unused.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            for extra in ({"application_id": "app-1", "tab_id": "17"},
                          {"application_id": "app-1", "origin": "http://127.0.0.1:1"},
                          {"application_id": "app-1", "target": "/lever/split/0"},
                          {"application_id": "app-1", "package_path": "/tmp/actions.json"},
                          {}, {"application_id": ""}, {"application_id": 17}):
                with self.subTest(payload=sorted(extra)):
                    status, body = self.post("/fill/prepare", extra)
                    self.assertEqual(status, 400)
                    self.assertEqual(body["error"], "fill_request_invalid")
            _, prepared = self.post("/fill/prepare", {"application_id": "app-1"})
            status, body = self.post("/fill/execute", {
                "execution_id": prepared["execution_id"],
                "origin": "http://127.0.0.1:1"})
            self.assertEqual(status, 400)
            self.assertEqual(body["error"], "fill_request_invalid")

    def test_a_second_press_cannot_replay_a_consumed_package(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            _, prepared = self.post("/fill/prepare", {"application_id": "app-1"})
            execution_id = prepared["execution_id"]
            status, _ = self.post("/fill/execute", {"execution_id": execution_id})
            self.assertEqual(status, 200)
            # The same id again: refused by the bridge's own state machine, and refused
            # without a second answer that could read as a second success.
            status, body = self.post("/fill/execute", {"execution_id": execution_id})
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "execution_already_started")
            self.assertEqual(self.oracle(server), 0)

    def test_a_fresh_prepare_over_a_spent_grant_still_cannot_run_twice(self):
        """The layer that actually holds when the first two are wrong.

        Refusing a repeated execution id is bookkeeping. What makes a replay impossible is
        that the grant is consumed once in the authority's own state, so a caller who asks
        for a brand-new preparation still meets a page with nothing left to do.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            _, first = self.post("/fill/prepare", {"application_id": "app-1"})
            self.post("/fill/execute", {"execution_id": first["execution_id"]})
            status, body = self.post("/fill/prepare", {"application_id": "app-1"})
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "fill_page_has_no_pending_steps")
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM execution_grants WHERE consumed_at IS NOT NULL"
            ).fetchone()[0], 1)
            self.assertEqual(self.oracle(server), 0)

    def test_a_lease_that_lapsed_between_the_two_presses_stops_the_run(self):
        """Re-verified at execute, not carried over from prepare.

        Prepare and execute are two user gestures with a person in between them, so every
        condition that could change has to be asked again — before a browser exists, not
        after the page is holding the user's data.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            _, prepared = self.post("/fill/prepare", {"application_id": "app-1"})
            self.db.execute("UPDATE applications SET worker_id='someone-else' "
                            "WHERE application_id='app-1'")
            self.db.commit()
            status, body = self.post("/fill/execute",
                                     {"execution_id": prepared["execution_id"]})
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "fill_lease_not_held")
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM execution_grants WHERE consumed_at IS NOT NULL"
            ).fetchone()[0], 0)
            self.assertEqual(self.oracle(server), 0)

    def test_an_unprepared_or_unknown_execution_is_refused(self):
        status, body = self.post("/fill/execute", {"execution_id": "0" * 32})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "execution_unknown")
        status, body = self.post("/fill/prepare", {"application_id": "app-does-not-exist"})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "fill_session_not_active")

    def test_the_fill_routes_need_the_run_token_like_every_other_write(self):
        status, body = self.post("/fill/prepare", {"application_id": "app-1"},
                                 token="guessed")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "bad_token")

    def test_the_worker_window_is_headed_unless_a_test_says_otherwise(self):
        # The separate window is only a safeguard while the user can see it.
        server = BRIDGE.serve(db_path=self.db_path, candidate_path=self.candidate_path,
                              port=0, allow_store=False, token="t")
        try:
            self.assertTrue(server.RequestHandlerClass.fill_headed)
        finally:
            server.server_close()

    # ---- one authority per page ----------------------------------------------

    def live_grants(self):
        return [row["grant_id"] for row in self.db.execute(
            "SELECT grant_id FROM execution_grants "
            "WHERE consumed_at IS NULL AND revoked_at IS NULL")]

    def test_preparing_twice_before_running_yields_one_executable_authority(self):
        """The bug this closes: two presses of Prepare, then two runs.

        De-duplicating on `execution_id` made every preparation its own execution with its
        own grant, and each grant was single-use and entirely valid. `prepare, prepare,
        execute(A), execute(B)` would have filled the page twice. The earlier test only
        prepared again *after* a run, by which point the steps were completed and any second
        attempt was refused for an unrelated reason — so it passed while the hole was open.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            status, first = self.post("/fill/prepare", {"application_id": "app-1"})
            self.assertEqual(status, 200, first)
            status, second = self.post("/fill/prepare", {"application_id": "app-1"})
            self.assertEqual(status, 200, second)
            # Idempotent: the page has not moved, so neither has the answer.
            self.assertEqual(second["execution_id"], first["execution_id"])
            self.assertEqual(len(self.live_grants()), 1)
            status, done = self.post("/fill/execute",
                                     {"execution_id": first["execution_id"]})
            self.assertEqual(status, 200, done)
            status, refused = self.post("/fill/execute",
                                        {"execution_id": second["execution_id"]})
            self.assertEqual(status, 409)
            self.assertEqual(refused["error"], "execution_already_started")
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM execution_grants WHERE consumed_at IS NOT NULL"
            ).fetchone()[0], 1)
            self.assertEqual(self.oracle(server), 0)

    def test_concurrent_prepares_produce_one_execution_and_one_run(self):
        """Serial de-duplication is easy; this is the version that races.

        Every request is issued at once, and then every execution id that came back is run
        at once. What has to hold at the end is not that the requests were tidy but that the
        page was filled once: one consumed grant, nothing else live, and the target's own
        counter still at zero.
        """
        import concurrent.futures

        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                prepared = list(pool.map(
                    lambda _: self.post("/fill/prepare", {"application_id": "app-1"}),
                    range(4)))
            ok = [body for status, body in prepared if status == 200]
            self.assertTrue(ok)
            self.assertEqual(len({body["execution_id"] for body in ok}), 1)
            self.assertEqual(len(self.live_grants()), 1)
            ids = {body["execution_id"] for body in ok}
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(
                    lambda execution_id: self.post("/fill/execute",
                                                   {"execution_id": execution_id}),
                    list(ids) * 4))
            self.assertEqual(sum(1 for status, _ in results if status == 200), 1)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM execution_grants WHERE consumed_at IS NOT NULL"
            ).fetchone()[0], 1)
            self.assertEqual(self.oracle(server), 0)

    def test_a_grant_this_process_never_saw_is_revoked_before_a_new_one_exists(self):
        """The invariant is read from the database, not from this process's memory.

        A bridge restarted between the two presses would remember no prepared run while the
        grant it issued was still valid for the rest of its fifteen minutes. Withdrawing
        from the table rather than from a dictionary is what makes the guarantee outlive
        the process.
        """
        import fill_core

        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            stray_package = self.private / "stray-actions.json"
            fill_core.export_page(self.db, "session-1", "worker-1", "page-1",
                                  stray_package, self.now)
            stray = fill_core.issue_execution_grant(self.db, "session-1", "page-1",
                                                    stray_package, self.now)
            self.assertIn(stray["grant_id"], self.live_grants())
            status, prepared = self.post("/fill/prepare", {"application_id": "app-1"})
            self.assertEqual(status, 200, prepared)
            self.assertEqual(len(self.live_grants()), 1)
            self.assertNotIn(stray["grant_id"], self.live_grants())
            self.assertEqual(self.db.execute(
                "SELECT revoked_at FROM execution_grants WHERE grant_id=?",
                (stray["grant_id"],)).fetchone()["revoked_at"] is not None, True)

    def test_a_prepare_during_a_run_is_refused_rather_than_authorised(self):
        import fill_core

        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            _, prepared = self.post("/fill/prepare", {"application_id": "app-1"})
            # Held in `running` the way `_fill_execute` holds it, without the browser.
            control = self.server.RequestHandlerClass.fill_control
            run = control.claim(prepared["execution_id"], datetime.now(timezone.utc))
            try:
                status, body = self.post("/fill/prepare", {"application_id": "app-1"})
                self.assertEqual(status, 409)
                self.assertEqual(body["error"], "fill_execution_in_progress")
                self.assertEqual(len(self.live_grants()), 1)
            finally:
                control.finish(run, "prepared", None)
            self.assertEqual(self.oracle(server), 0)

    # ---- failure responses are part of the API -------------------------------

    def assert_value_free_failure(self, status, body, server, codes):
        self.assertIn(status, {409, 500})
        self.assertEqual(set(body), {"error"}, body)
        self.assertIn(body["error"], codes)
        rendered = json.dumps(body, ensure_ascii=False)
        for needle in (str(self.private), "fill-runs", "actions.json", "capability",
                       "result.json", server.origin, "127.0.0.1", "Verified Candidate",
                       "New York, NY", "token-under-test", "Errno", "Traceback",
                       "session-1", "page-1", "grant-"):
            self.assertNotIn(needle, rendered)

    def test_a_failure_anywhere_in_preparation_says_only_a_code(self):
        """`str(error)` is where the private root lives.

        `mkdir`, `chmod`, the export and the grant all name the path they failed on, and the
        generic handler used to forward two hundred characters of it. A value-free success
        response beside a talkative failure response is not a value-free API.
        """
        import fill_core

        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            leak = f"[Errno 13] Permission denied: '{self.private}/fill-runs/actions.json'"
            injections = {
                "directory": (Path, "mkdir", OSError(leak)),
                "export": (fill_core, "export_page", ValueError(leak)),
                "grant": (fill_core, "issue_execution_grant", RuntimeError(leak)),
                "revocation": (fill_core, "revoke_execution_grant", RuntimeError(leak)),
            }
            for label, (target, name, error) in injections.items():
                with self.subTest(failure=label):
                    if label == "revocation":
                        # Revocation only runs when the page already carries an authority,
                        # so give it one that this process did not issue.
                        stray = self.private / f"stray-{label}.json"
                        fill_core.export_page(self.db, "session-1", "worker-1", "page-1",
                                              stray, self.now)
                        fill_core.issue_execution_grant(self.db, "session-1", "page-1",
                                                        stray, self.now)
                        self.assertTrue(self.live_grants())
                    with unittest.mock.patch.object(target, name, side_effect=error):
                        status, body = self.post("/fill/prepare",
                                                 {"application_id": "app-1"})
                    self.assert_value_free_failure(
                        status, body, server,
                        {"fill_preparation_failed", "fill_page_not_exportable",
                         "bridge_failure"})
            self.assertEqual(self.oracle(server), 0)

    def test_a_failure_anywhere_in_execution_says_only_a_code(self):
        import execution_authority
        import fill_core
        import fill_worker

        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            leak = (f"target http://127.0.0.1:9/lever refused; package "
                    f"{self.private}/fill-runs/actions.json held 'Verified Candidate'")
            injections = {
                "authority": (execution_authority, "ExecutionAuthority",
                              RuntimeError(leak)),
                "worker": (fill_worker, "run", RuntimeError(leak)),
                "import": (fill_core, "import_result", ValueError(leak)),
            }
            for label, (target, name, error) in injections.items():
                with self.subTest(failure=label):
                    _, prepared = self.post("/fill/prepare", {"application_id": "app-1"})
                    with unittest.mock.patch.object(target, name, side_effect=error):
                        status, body = self.post(
                            "/fill/execute", {"execution_id": prepared["execution_id"]})
                    self.assert_value_free_failure(
                        status, body, server,
                        {"fill_execution_failed", "bridge_failure"})
            self.assertEqual(self.oracle(server), 0)

    def test_the_bridge_writes_no_diagnostic_of_its_own(self):
        """Nothing is logged, so nothing logged can carry a path.

        The handler's `log_message` is a no-op and the fill routes add no logging of their
        own. If that ever changes, whatever replaces it has to be value-free too, and this
        is where that will be noticed.
        """
        source = (ROOT / "skills" / "jobloom" / "scripts" / "assist_bridge.py").read_text(
            encoding="utf-8")
        parts = source.split("# ---- one-page Fill-Only control")
        # The state machine above `Handler`, and the routes inside it. The rest of the
        # module is not in scope: `/positioning` and friends keep their `detail`, which is
        # existing behaviour on paths that never touch a package or a file.
        blocks = {"state machine": parts[1].split("class Handler(")[0],
                  "routes": parts[2].split("\ndef serve(")[0]}
        for label, block in blocks.items():
            code = "\n".join(line.split("#")[0] for line in block.splitlines())
            for forbidden in ("print(", "logging.", "logger.", "sys.stderr", "sys.stdout",
                              "str(error)", "traceback"):
                with self.subTest(block=label, forbidden=forbidden):
                    self.assertNotIn(forbidden, code)
        self.assertIn("_fill_prepare", blocks["routes"])
        self.assertIn("_fill_execute", blocks["routes"])

    # ---- two bridges, one database -------------------------------------------

    def second_bridge(self):
        """Another bridge on another port over the same database, as `--port` allows.

        Genuinely separate: its own `FillControl`, its own lock, its own idea of what is
        prepared. Sharing a `FillControl` by accident would make this test agree with
        itself and prove nothing.
        """
        server = BRIDGE.serve(db_path=self.db_path, candidate_path=self.candidate_path,
                              port=0, allow_store=False, token="token-under-test",
                              fill_headed=False)
        self.assertIsNot(server.RequestHandlerClass.fill_control,
                         self.server.RequestHandlerClass.fill_control)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def post_to(self, port, path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "X-Jobloom-Token": "token-under-test"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_two_bridges_on_one_database_cannot_both_authorise_a_page(self):
        """The process lock is not the invariant; the database is.

        `FillControl` is per-process and the CLI takes `--port`, so two bridges each hold
        their own lock and their own memory. Both could read "no live grants" and both could
        issue one, leaving a page with two authorities that were each single-use and each
        valid. What has to hold is not that the second request is tidy but that the page is
        filled once.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            other = self.second_bridge()
            status_a, first = self.post("/fill/prepare", {"application_id": "app-1"})
            self.assertEqual(status_a, 200, first)
            status_b, second = self.post_to(other, "/fill/prepare",
                                            {"application_id": "app-1"})
            # Whichever way the second bridge answers, the page carries one authority.
            self.assertIn(status_b, {200, 409})
            self.assertLessEqual(len(self.live_grants()), 1)
            attempts = [(status_a, first)]
            if status_b == 200:
                attempts.append((status_b, second))
            ran = 0
            for _, body in attempts:
                port = self.port if body is first else other
                status, _ = self.post_to(port, "/fill/execute",
                                         {"execution_id": body["execution_id"]})
                ran += status == 200
            self.assertEqual(ran, 1)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM execution_grants WHERE consumed_at IS NOT NULL"
            ).fetchone()[0], 1)
            self.assertEqual(self.oracle(server), 0)

    def test_two_bridges_racing_to_prepare_still_leave_one_authority(self):
        import concurrent.futures

        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            other = self.second_bridge()
            ports = [self.port, other] * 3
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(ports)) as pool:
                prepared = list(pool.map(
                    lambda port: (port, *self.post_to(port, "/fill/prepare",
                                                      {"application_id": "app-1"})),
                    ports))
            self.assertLessEqual(len(self.live_grants()), 1)
            offers = [(port, body) for port, status, body in prepared if status == 200]
            self.assertTrue(offers)
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(offers)) as pool:
                results = list(pool.map(
                    lambda offer: self.post_to(offer[0], "/fill/execute",
                                               {"execution_id": offer[1]["execution_id"]}),
                    offers))
            self.assertEqual(sum(1 for status, _ in results if status == 200), 1)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM execution_grants WHERE consumed_at IS NOT NULL"
            ).fetchone()[0], 1)
            self.assertEqual(self.oracle(server), 0)

    def test_the_database_refuses_a_second_live_grant_without_help_from_any_lock(self):
        """The constraint on its own, with both locks taken out of the picture.

        The file lock orders the queue and `FillControl` orders this process; neither is the
        guarantee. This holds revocation back so the insert meets a live grant directly, and
        the database is the only thing left to refuse it.
        """
        import fill_core

        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            status, first = self.post("/fill/prepare", {"application_id": "app-1"})
            self.assertEqual(status, 200, first)
            self.assertEqual(len(self.live_grants()), 1)
            other = self.second_bridge()
            with unittest.mock.patch.object(fill_core, "revoke_execution_grant",
                                            return_value={"revoked": False}):
                status, body = self.post_to(other, "/fill/prepare",
                                            {"application_id": "app-1"})
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "fill_page_already_authorised")
            self.assertEqual(set(body), {"error"})
            self.assertEqual(len(self.live_grants()), 1)
            self.assertEqual(self.oracle(server), 0)

    def test_the_authority_index_exists_and_ignores_whether_a_grant_was_spent(self):
        """The predicate must not mention `consumed_at`, and that is the whole point.

        Excluding consumed grants looks tighter and is much weaker: the grant is spent
        before a browser exists, so the slot would come free while the run was still
        starting.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            self.post("/fill/prepare", {"application_id": "app-1"})
        row = self.db.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='execution_grant_one_authority_per_page'").fetchone()
        self.assertIsNotNone(row, "the invariant is not in the database")
        self.assertIn("revoked_at IS NULL", row["sql"])
        self.assertNotIn("consumed_at", row["sql"])

    # ---- the window between spending a grant and touching a page -------------

    def test_a_second_bridge_is_refused_while_the_first_run_is_still_starting(self):
        """The grant is spent before Chromium exists, and the slot must not come free.

        `fill_worker` consumes the grant and only then launches a browser. With a predicate
        that excluded consumed grants, a second bridge asking in that window found nothing
        live, was issued its own grant, and filled the same page again — and the `running`
        flag that would have stopped it is process-local, so it belonged to the first bridge
        and the second one could not see it.

        This blocks inside `consume_execution_grant`, which puts the second bridge's request
        exactly in that window rather than near it.
        """
        import fill_core

        spent = threading.Event()
        release = threading.Event()
        real_consume = fill_core.consume_execution_grant

        def blocking_consume(*args, **kwargs):
            outcome = real_consume(*args, **kwargs)
            spent.set()
            # Held here, after the grant is consumed and before the browser opens.
            self.assertTrue(release.wait(timeout=60), "the test never released the worker")
            return outcome

        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            other = self.second_bridge()
            _, prepared = self.post("/fill/prepare", {"application_id": "app-1"})
            first: dict = {}
            def run_first():
                first["result"] = self.post(
                    "/fill/execute", {"execution_id": prepared["execution_id"]})
            with unittest.mock.patch.object(fill_core, "consume_execution_grant",
                                            blocking_consume):
                worker = threading.Thread(target=run_first, daemon=True)
                worker.start()
                try:
                    self.assertTrue(spent.wait(timeout=60), "the grant was never consumed")
                    self.assertEqual(self.db.execute(
                        "SELECT COUNT(*) FROM execution_grants "
                        "WHERE consumed_at IS NOT NULL").fetchone()[0], 1)
                    # The window. Nothing has been typed into anything yet.
                    status, body = self.post_to(other, "/fill/prepare",
                                                {"application_id": "app-1"})
                    self.assertEqual(status, 409)
                    self.assertEqual(body["error"], "fill_page_already_authorised")
                    self.assertEqual(set(body), {"error"})
                finally:
                    release.set()
                worker.join(timeout=120)
            self.assertEqual(first["result"][0], 200, first["result"])
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM execution_grants WHERE consumed_at IS NOT NULL"
            ).fetchone()[0], 1)
            self.assertEqual(self.oracle(server), 0)

    def test_a_spent_grant_whose_run_failed_cannot_be_reminted(self):
        """Steps still pending is not a licence to authorise them again.

        The import is what marks a step done, so a run that spends its grant and then fails
        to import leaves the page looking exactly as it did before — pending steps, ready to
        prepare. Under the old predicate that page could be authorised again, which is a
        consumed package coming back through a new grant. Retrying belongs to a new reviewed
        page or session.
        """
        import fill_core

        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            _, prepared = self.post("/fill/prepare", {"application_id": "app-1"})
            with unittest.mock.patch.object(fill_core, "import_result",
                                            side_effect=ValueError("import refused")):
                status, _ = self.post("/fill/execute",
                                      {"execution_id": prepared["execution_id"]})
            self.assertEqual(status, 409)
            # The grant is spent and the steps never moved: the shape the window leaves.
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM execution_grants WHERE consumed_at IS NOT NULL"
            ).fetchone()[0], 1)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM fill_steps WHERE status='pending'").fetchone()[0], 3)
            status, body = self.post("/fill/prepare", {"application_id": "app-1"})
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "fill_page_already_authorised")
            other = self.second_bridge()
            status, body = self.post_to(other, "/fill/prepare", {"application_id": "app-1"})
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "fill_page_already_authorised")
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM execution_grants WHERE consumed_at IS NOT NULL"
            ).fetchone()[0], 1)
            self.assertEqual(self.oracle(server), 0)

    def test_a_database_that_already_breaks_the_rule_refuses_to_prepare(self):
        """History does not disappear because the rule arrived.

        A database written before this constraint can hold two unrevoked grants for one
        page — that is the bug, recorded. Creating the index then fails, and the honest
        answer is to stop rather than to carry on with the guarantee quietly absent, or to
        revoke rows until the past fits.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.observed_session(server)
            for suffix in ("a", "b"):
                self.db.execute(
                    "INSERT INTO execution_grants (grant_id, session_id, page_id, "
                    "package_sha256, secret, issued_at, expires_at, consumed_at, revoked_at) "
                    "VALUES (?, 'session-1', 'page-1', 'd', '', ?, ?, ?, NULL)",
                    (f"legacy-{suffix}", self.now.isoformat(), self.now.isoformat(),
                     self.now.isoformat()))
            self.db.commit()
            status, body = self.post("/fill/prepare", {"application_id": "app-1"})
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "fill_authority_index_unavailable")
            self.assertEqual(set(body), {"error"})
            self.assertEqual(self.oracle(server), 0)
