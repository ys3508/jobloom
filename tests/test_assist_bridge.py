"""The browser assistant is a passenger, not a driver.

Everything here pins a boundary rather than a feature: the bridge only answers about a page
the user already has open, it refuses callers without the run's token, it binds nothing but
loopback, and it does not keep a copy of what the user browsed unless they say so.
"""

import importlib.util
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
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
EXTENSION = ROOT / "skills" / "jobloom" / "extension"

PAGE = {
    "url": "https://www.linkedin.com/jobs/view/4012345678",
    "title": "Clinical Research Data Analyst",
    "employer": "Example Health System",
    "location": "Boston, MA",
    "country": "US",
    "required_skills": ["R", "SAS", "Epic"],
    "text": ("Clinical Research Data Analyst at Example Health System. Manage clinical "
             "trial data, run statistical analysis in R and SAS, support investigators. "
             "Required: R, SAS, Epic."),
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

    def test_sponsorship_refusal_is_read_but_never_invented(self):
        self.assertEqual(SECTIONS.extract("We are unable to sponsor visas.")["sponsorship"],
                         "does_not_support")
        self.assertEqual(SECTIONS.extract(MGB_POSTING)["sponsorship"], "unknown")



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
                {"id": "f-r", "type": "skill", "value": "R", "status": "confirmed",
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
        for permission in ("tabs", "webNavigation", "webRequest", "cookies", "history",
                           "<all_urls>"):
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

    def test_the_reader_skips_the_sites_own_headings(self):
        # The posting pane also holds a feedback prompt and section labels; the first
        # heading is not the job title.
        panel = self.sources["panel.js"]
        self.assertIn("CHROME_HEADINGS", panel)
        self.assertIn("are these results", panel)
        self.assertIn("hiring", panel, "employer and location come from the document title")

    def test_the_reader_does_not_shadow_window_location(self):
        # A local named `location` shadows window.location for the whole function, and the
        # reader ends by reading location.href, so the injection threw and the panel could
        # only report that the page returned no posting.
        panel = self.sources["panel.js"]
        self.assertNotIn("const location =", panel)
        self.assertIn("window.location.href", panel)

    def test_reading_is_refused_before_the_grant(self):
        self.assertIn("page access not granted yet", self.sources["panel.js"])

    def test_it_cannot_reach_a_job_site_from_its_own_code(self):
        for name, source in self.sources.items():
            for host in ("linkedin.com/", "indeed.com/"):
                self.assertNotIn(f"fetch(\"https://{host}", source, name)
                self.assertNotIn(f"fetch('https://{host}", source, name)

    def test_it_never_navigates_paginates_or_clicks_for_the_user(self):
        forbidden = ("chrome.tabs.create", "chrome.tabs.update", "location.assign",
                     "location.replace", "window.open", ".click()", "form.submit",
                     "setInterval", "MutationObserver")
        for name, source in self.sources.items():
            for token in forbidden:
                self.assertNotIn(token, source, f"{name} must not contain {token}")

    def test_nothing_runs_on_a_job_site_until_the_user_presses_the_button(self):
        # No declared content script means no code of ours executes on a job page at all
        # until the button is pressed and activeTab is granted by that press.
        self.assertNotIn("content_scripts", self.manifest)
        panel = self.sources["panel.js"]
        self.assertIn("chrome.scripting.executeScript", panel)
        self.assertIn('$("read").addEventListener', panel)

    def test_injection_targets_the_active_tab_only(self):
        panel = self.sources["panel.js"]
        self.assertIn("active: true, currentWindow: true", panel)
        self.assertNotIn("allFrames: true", panel)
        self.assertNotIn("chrome.tabs.query({})", panel)


if __name__ == "__main__":
    unittest.main()
