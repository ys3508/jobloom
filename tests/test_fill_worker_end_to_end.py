"""One application, start to stop, through every production path there is.

No test-only completion helper, no fixture-written state, no skipped stage: a fresh private
root, a real PDF, a real material lock, a real lease, a real replay served over loopback, a
real execution grant reserved and consumed, a real Chromium, a real result import, real
checkpoints, and a real pre-submit review. What it proves is that the whole thing reaches
`waiting_for_submission_approval` with the target's own final-action counter still at zero.

Every expected value is computed from something other than the thing being checked — package
digests from the exported bytes, the final-action count from the server's own endpoint, states
from the database, the review digest from the artifact — because an assertion that compares a
field to itself is how the worker's package digest bug survived a passing suite.

The two pages are a Jobloom arrangement of the reviewed controls. Upstream's fixtures put
every control on one step with the final action alone on the next, so a flow with two packages
needs a split; the controls are unchanged and only the pagination is ours.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"e2e_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


FILL = load_script("fill_core")
WORKER = load_script("fill_worker")
AUTHORITY = load_script("execution_authority")
POLICY = load_script("field_policy")
PROTOCOL = load_script("worker_protocol")
APPLICATIONS = load_script("application_core")
ANSWERS = load_script("answer_library")
RESUMES = load_script("resume_core")
COVERS = load_script("cover_letter_core")
ARCHIVE = load_script("archive_core")
PRE_SUBMIT = load_script("pre_submit_core")
CANDIDATES = load_script("candidate_core")
REPLAY = load_script("semantic_replay")

from tests.fixtures.ats_replay_server import ReplayServer
from tests.pdf_fixture import synthetic_pdf


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
class LocalFillOnlyWorkflow(unittest.TestCase):
    """A private root built from nothing, and one application carried through it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        self.now = datetime.now(timezone.utc)

        self.database_path = self.private / "jobloom.db"
        self.db = sqlite3.connect(self.database_path, check_same_thread=False)
        os.chmod(self.database_path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.db.close)
        for module in (APPLICATIONS, ANSWERS, RESUMES, COVERS, ARCHIVE, PRE_SUBMIT,
                       FILL, CANDIDATES):
            module.initialize(self.db)

        self.build_candidate()
        self.build_materials()
        self.build_application()
        self.build_answers()

    # ---- building the state, entirely through production calls ------------

    def build_candidate(self):
        facts = [
            {"id": "fact-name", "type": "identity", "value": "Verified Candidate",
             "status": "locked", "locked": True, "evidence_strength": "direct"},
            {"id": "fact-city", "type": "location", "value": "New York, NY",
             "status": "locked", "locked": True, "evidence_strength": "direct"},
        ]
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": False, "employer_action_required": False,
                "confirmed": True,
            },
            "search": {}, "facts": facts,
        }
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        self.candidate_path = self.private / "candidate.json"
        self.candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        self.manifest_path = self.private / "claims-manifest.json"
        self.manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-name", "claim_text": "Verified Candidate",
            "fact_ids": ["fact-name"], "evidence_strength": "direct",
            "exact_locked_value_preserved": True,
        }]}), encoding="utf-8")
        CANDIDATES.register_snapshot(self.db, self.private / "candidates",
                                     self.candidate_path, "user", self.now)

    def build_materials(self):
        source = self.private / "resume.pdf"
        source.write_bytes(synthetic_pdf(["Verified Candidate", "New York, NY"]))
        RESUMES.register_version(self.db, self.private / "resumes", source, "resume-1",
                                 "master_source", "general", at=self.now)
        RESUMES.approve_version(self.db, "resume-1", self.candidate_path,
                                self.manifest_path, "user", self.now)

    def build_application(self):
        APPLICATIONS.ingest_job(self.db, {
            "job_id": "job-1", "canonical_url": "https://example.invalid/job/1",
            "employer": "Example Corp", "title": "Backend Engineer",
            "location": "New York, NY", "status": "open", "country": "US",
            "employment_type": "full_time",
        }, at=self.now)
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

    def build_answers(self):
        ANSWERS.add_answer(self.db, {
            "answer_id": "answer-company", "canonical_id": "current_company",
            "canonical_meaning": "Current company", "answer": "Example Employer",
            "answer_type": "stable_fact", "source_type": "user_confirmed",
            "confirmation_status": "confirmed", "confirmed_at": self.now.isoformat(),
            "validity_class": "stable",
            "scope": {"country": "US", "application_id": "app-1"},
            "auto_fill_allowed": True, "auto_submit_allowed": False,
        })
        ANSWERS.add_question_form(self.db, "current_company", "Current company")
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-1", "confirmed_at": self.now.isoformat(),
            "expires_at": (self.now + timedelta(hours=2)).isoformat(),
            "scope": {"country": "US", "application_id": "app-1"},
        })

    # ---- helpers that only wrap production calls --------------------------

    def oracle(self, server):
        """Read the target's own counter, not anything the worker reported."""
        with urllib.request.urlopen(f"{server.origin}/__state", timeout=5) as response:
            return json.load(response)["final_action_activations"]

    def observation(self, page_id, index, path, server, fields, final=False,
                    predecessor=None, **extra):
        value = {
            "page_id": page_id, "page_index": index,
            "page_url": f"{server.origin}{path}",
            "fields": fields, "legal_items": [], "restricted_requests": [],
            "final_page": final,
        }
        if predecessor:
            value["predecessor_checkpoint_sha256"] = predecessor
        value.update(extra)
        return value

    def field(self, field_id, question, control="text", **extra):
        value = {"field_id": field_id, "question": question, "selector": f"#{field_id}",
                 "control": control, "required": False, "sensitivity": "normal"}
        value.update(extra)
        return value

    def run_page(self, server, page_id, index, path, fields, final=False,
                 predecessor=None, **extra):
        """observe -> export -> grant -> reserve/consume -> Chromium -> import -> checkpoint."""
        observed = FILL.observe_page(
            self.db, "session-1", "worker-1", self.candidate_path,
            self.observation(page_id, index, path, server, fields, final, predecessor,
                             **extra), self.now)
        self.assertNotEqual(observed.get("status"), "paused", observed.get("reasons"))
        package = self.private / f"{page_id}-actions.json"
        FILL.export_page(self.db, "session-1", "worker-1", page_id, package, self.now)
        # Independently computed, from the bytes on disk.
        package_digest = hashlib.sha256(package.read_bytes()).hexdigest()
        grant = FILL.issue_execution_grant(self.db, "session-1", page_id, package, self.now)
        result = self.private / f"{page_id}-result.json"
        with AUTHORITY.ExecutionAuthority(
            self.db, FILL.reserve_execution_grant,
            consume=FILL.consume_execution_grant, clock=lambda: self.now
        ) as authority:
            capability = authority.write_capability(self.private / f"{page_id}-cap.json")
            url, token = WORKER.read_capability(capability)
            WORKER.run(package, result, url, token, grant["grant_id"], headed=False,
                       at=self.now)
        imported = FILL.import_result(self.db, "session-1", page_id, "worker-1", result,
                                      grant["grant_id"], self.now)
        self.assertEqual(imported["status"], "verified")
        checkpoint = FILL.checkpoint_page(self.db, "session-1", "worker-1", page_id,
                                          f"checkpoint-{page_id}", self.now)
        return {"package": package, "package_digest": package_digest, "grant": grant,
                "result": result, "capability": capability, "checkpoint": checkpoint,
                "imported": imported}

    def start_session(self, server, path="/lever/split/0"):
        return FILL.start_session(
            self.db, session_id="session-1", application_id="app-1", worker_id="worker-1",
            form_url=f"{server.origin}{path}", observed_employer="Example Corp",
            observed_role="Backend Engineer", known_form=True, authorization_id="auth-1",
            authorization_context={"country": "US", "application_id": "app-1"},
            at=self.now)

    def page_one_fields(self):
        return [
            self.field("lever-0-0", "Resume", control="file", required=True,
                       upload_kind="resume"),
            self.field("lever-0-1", "Full name", required=True,
                       source_kind="fact", source_id="fact-name"),
            self.field("lever-0-5", "Current company", source_kind="answer"),
        ]

    def page_two_fields(self):
        return [
            self.field("lever-0-20", "City and state", required=True,
                       source_kind="fact", source_id="fact-city"),
            self.field("final-action", "Submit application", control="submit",
                       required=True),
        ]


    # ---- the whole workflow ------------------------------------------------

    def test_the_complete_local_fill_only_workflow(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.assertEqual(self.oracle(server), 0)
            self.start_session(server)

            first = self.run_page(server, "page-1", 0, "/lever/split/0",
                                  self.page_one_fields())
            # The page is left by a person following a link; the protocol has no way to.
            with urllib.request.urlopen(f"{server.origin}/lever/split/1", timeout=5) as page:
                self.assertIn(b"final-action", page.read())
            second = self.run_page(
                server, "page-2", 1, "/lever/split/1", self.page_two_fields(), final=True,
                predecessor=first["checkpoint"]["checkpoint_sha256"])

            FILL.finish_session(self.db, "session-1", "worker-1", "inventory-1", self.now)
            PRE_SUBMIT.create_review(
                self.db, "review-1", "inventory-1", "auth-1",
                {"country": "US", "application_id": "app-1"}, at=self.now)
            final_oracle = self.oracle(server)

        # ---- state, read from the database rather than from any return value
        self.assertEqual(self.db.execute(
            "SELECT state FROM applications WHERE application_id='app-1'"
        ).fetchone()["state"], "waiting_for_submission_approval")
        session = self.db.execute(
            "SELECT status FROM fill_sessions WHERE session_id='session-1'").fetchone()
        self.assertEqual(session["status"], "completed")
        pages = self.db.execute(
            "SELECT page_id, status, final_page FROM fill_pages ORDER BY page_index"
        ).fetchall()
        self.assertEqual([page["status"] for page in pages], ["completed", "completed"])
        self.assertEqual([bool(page["final_page"]) for page in pages], [False, True])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM fill_steps WHERE status!='completed'").fetchone()[0], 0)

        # ---- one grant and one verified import per page, nothing shared
        for page_id, artefacts in (("page-1", first), ("page-2", second)):
            with self.subTest(page=page_id):
                grants = self.db.execute(
                    "SELECT grant_id, package_sha256, consumed_at FROM execution_grants "
                    "WHERE page_id=?", (page_id,)).fetchall()
                self.assertEqual(len(grants), 1)
                self.assertIsNotNone(grants[0]["consumed_at"])
                # Expected digest computed from the exported bytes, not read back out of
                # the row it is checking.
                self.assertEqual(grants[0]["package_sha256"], artefacts["package_digest"])
                imports = self.db.execute(
                    "SELECT result_sha256, status, package_sha256 FROM imported_results "
                    "WHERE page_id=?", (page_id,)).fetchall()
                self.assertEqual(len(imports), 1)
                self.assertEqual(imports[0]["status"], "verified")
                self.assertEqual(
                    imports[0]["result_sha256"],
                    hashlib.sha256(artefacts["result"].read_bytes()).hexdigest())
                self.assertEqual(imports[0]["package_sha256"], artefacts["package_digest"])
        self.assertNotEqual(first["package_digest"], second["package_digest"])
        self.assertNotEqual(first["grant"]["grant_id"], second["grant"]["grant_id"])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(DISTINCT checkpoint_sha256) FROM fill_checkpoints").fetchone()[0], 2)

        # ---- the final action was never activated, per the target's own count
        self.assertEqual(final_oracle, 0)
        for artefacts in (first, second):
            envelope = json.loads(artefacts["result"].read_text(encoding="utf-8"))
            self.assertEqual(envelope["final_action_activations"], 0)
            self.assertEqual(envelope["side_effect_attribution"], "complete")
            package = json.loads(artefacts["package"].read_text(encoding="utf-8"))
            self.assertTrue(package["stop_before_submit"])
            self.assertIsNone(package["submission_action"])
            operations = {action["operation"] for action in package["actions"]}
            self.assertTrue(operations <= WORKER.SUPPORTED_OPERATIONS, operations)
        # The submit control was observed on the final page and is in no action.
        observed = json.loads(self.db.execute(
            "SELECT observation_json FROM fill_pages WHERE page_id='page-2'"
        ).fetchone()["observation_json"])
        self.assertIn("final-action",
                      [field["field_id"] for field in observed["fields"]])
        self.assertNotIn("final-action", json.dumps(
            json.loads(second["package"].read_text(encoding="utf-8"))["actions"]))

        # ---- nothing claims a submission happened
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM submission_evidence").fetchone()[0], 0)
        stored = self.db.execute(
            "SELECT summary_json, summary_sha256, status FROM pre_submit_reviews "
            "WHERE review_id='review-1'").fetchone()
        review = json.loads(stored["summary_json"])
        self.assertEqual(review["submission_policy"], "stop_before_submit")
        self.assertEqual(review["voluntary_disclosure_handling"],
                         {"status": "unknown", "markers": {}})
        self.assertEqual(review["checks"]["mandatory_pauses"], [])
        # Nothing in the review describes a submission having happened.
        self.assertNotIn("submitted", json.dumps(review).casefold())
        # Digest recomputed from the summary rather than read back beside itself.
        self.assertEqual(stored["summary_sha256"], PRE_SUBMIT.canonical_hash(review))

        # ---- value isolation across every artefact that leaves the store
        events = " ".join(str(part) for row in self.db.execute("SELECT * FROM fill_events")
                          for part in row)
        for leak in ("Verified Candidate", "Example Employer", "New York, NY",
                     server.nonce, str(self.private)):
            self.assertNotIn(leak, events, leak)
            self.assertNotIn(leak, json.dumps(review), leak)
            for artefacts in (first, second):
                self.assertNotIn(
                    leak, artefacts["result"].read_text(encoding="utf-8"), leak)

        # ---- artefacts are kept, and private
        self.assertEqual(os.stat(self.private).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.database_path).st_mode & 0o777, 0o600)
        for artefacts in (first, second):
            for name in ("package", "result", "capability"):
                path = artefacts[name]
                self.assertTrue(path.is_file(), name)
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600, name)


@unittest.skipUnless(PLAYWRIGHT, "playwright is not installed")
class MandatoryPauses(LocalFillOnlyWorkflow):
    """Every stop the first form can demand, each proving the same four things.

    No action ran, nothing was verified or checkpointed, the target's counter never moved,
    and the state is the right kind of waiting.
    """

    def assert_paused(self, server, fields, expect_reason, expect_state, **extra):
        before = self.oracle(server)
        result = FILL.observe_page(
            self.db, "session-1", "worker-1", self.candidate_path,
            self.observation("page-1", 0, "/lever/split/0", server, fields, **extra),
            self.now)
        self.assertEqual(result["status"], "paused", result)
        self.assertIn(expect_reason,
                      {reason.split(":", 1)[0] for reason in result["reasons"]},
                      result["reasons"])
        self.assertEqual(result["state"], expect_state)
        # No worker ran, so nothing can have been verified or sealed.
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM fill_steps WHERE status='completed'").fetchone()[0], 0)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM imported_results").fetchone()[0], 0)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM fill_checkpoints").fetchone()[0], 0)
        self.assertEqual(self.oracle(server), before)
        self.assertEqual(self.oracle(server), 0)
        self.assertEqual(self.db.execute(
            "SELECT state FROM applications WHERE application_id='app-1'"
        ).fetchone()["state"], expect_state)
        events = " ".join(str(part) for row in self.db.execute("SELECT * FROM fill_events")
                          for part in row)
        for leak in ("Verified Candidate", "Example Employer", "New York, NY"):
            self.assertNotIn(leak, events)
        return result

    def test_a_question_nobody_has_answered_waits_for_an_answer(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            self.assert_paused(
                server,
                [self.field("lever-0-5", "When can you start?", source_kind="answer")],
                "new_question", "waiting_for_user_answer")

    def test_every_restricted_request_hands_control_back(self):
        for restriction in ("captcha", "payment", "identity_document", "assessment",
                            "biometric", "video", "tax_document", "banking_document"):
            with self.subTest(restriction=restriction):
                self.setUp()
                with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
                    self.start_session(server)
                    self.assert_paused(
                        server, self.page_one_fields()[1:2], restriction,
                        "waiting_for_user_takeover",
                        restricted_requests=[restriction])

    def test_a_legal_term_nobody_reviewed_hands_control_back(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            self.assert_paused(server, self.page_one_fields()[1:2], "unknown_legal_item",
                               "waiting_for_user_takeover",
                               legal_items=["a term this system has never seen"])

    def test_a_page_on_another_origin_hands_control_back(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            before = self.oracle(server)
            result = FILL.observe_page(
                self.db, "session-1", "worker-1", self.candidate_path,
                {"page_id": "page-1", "page_index": 0,
                 "page_url": "http://127.0.0.1:9/elsewhere",
                 "fields": self.page_one_fields()[1:2], "legal_items": [],
                 "restricted_requests": [], "final_page": False}, self.now)
            self.assertEqual(result["status"], "paused")
            self.assertIn("unexpected_navigation", result["reasons"])
            self.assertEqual(result["state"], "waiting_for_user_takeover")
            self.assertEqual(self.oracle(server), before)

    def test_a_sensitive_document_field_cannot_be_declared_ordinary(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            self.assert_paused(
                server,
                [self.field("lever-0-5", "Tax identifier for payroll")],
                "sensitive_field_misclassified", "waiting_for_user_takeover")

    def test_an_employer_defined_compensation_band_is_the_users(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            self.assert_paused(
                server,
                [self.field("lever-0-5", "Expected total compensation")],
                "employer_defined_compensation_manual", "waiting_for_user_takeover")

    def test_an_employer_conflict_question_is_unknown_not_no(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            self.assert_paused(
                server,
                [self.field("lever-0-5", "Related to someone at this company?")],
                "employer_entity_not_approved", "waiting_for_user_takeover")

    def test_a_broad_sponsorship_question_is_ambiguous(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            self.assert_paused(
                server,
                [self.field("lever-0-5", "Will you require employment visa sponsorship?")],
                "sponsorship_meaning_ambiguous", "waiting_for_user_answer")

    def test_an_eeo_radiogroup_is_named_unsupported_rather_than_guessed_at(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            POLICY.register_policy(
                self.db, policy_id="policy-eeo_race", question_family="eeo_race",
                locale="en-US", option_tokens=["decline_to_answer"], confirmed_by="user",
                confirmed_at=self.now, scope={})
            self.start_session(server)
            self.assert_paused(
                server,
                [self.field("lever-0-24", "Race or ethnicity", control="radio",
                            options=[{"label": label,
                                      "value": POLICY.replay_option_value(label,
                                                                          server.nonce)}
                                     for label in ("Asian", "Decline to answer")])],
                "nondisclosure_control_unsupported", "waiting_for_user_takeover",
                locale="en-US")

    def test_an_eeo_select_needs_an_issued_surface_and_a_reviewed_option(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            POLICY.register_policy(
                self.db, policy_id="policy-eeo_gender", question_family="eeo_gender",
                locale="en-US", option_tokens=["decline_to_answer"], confirmed_by="user",
                confirmed_at=self.now, scope={})
            self.start_session(server)
            # A label a person reviewed, wired to a value the surface did not produce.
            self.assert_paused(
                server,
                [self.field("lever-0-23", "Gender", control="select",
                            options=[{"label": "Decline to answer", "value": "Asian"}])],
                "option_mapping_unverified", "waiting_for_user_takeover", locale="en-US")

    def test_an_unreadable_target_counter_stops_before_the_browser(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                              self.observation("page-1", 0, "/lever/split/0", server,
                                               self.page_one_fields()), self.now)
            package = self.private / "blind-actions.json"
            FILL.export_page(self.db, "session-1", "worker-1", "page-1", package, self.now)
            grant = FILL.issue_execution_grant(self.db, "session-1", "page-1", package,
                                               self.now)

            def blind(connection, grant_id, package_sha256, at):
                answer = FILL.reserve_execution_grant(connection, grant_id,
                                                      package_sha256, at)
                if answer.get("authorised"):
                    answer["oracle_url"] = answer["origin"] + "/__state-missing"
                return answer

            with AUTHORITY.ExecutionAuthority(
                self.db, blind, consume=FILL.consume_execution_grant,
                clock=lambda: self.now
            ) as authority:
                with self.assertRaises(WORKER.WorkerRefusal) as caught:
                    WORKER.run(package, self.private / "blind-result.json", authority.url,
                               authority.token, grant["grant_id"], headed=False,
                               at=self.now)
            self.assertEqual(str(caught.exception), "oracle_unavailable")
            self.assertEqual(self.oracle(server), 0)
            # Nothing typed, nothing imported, and the grant is still spendable.
            page = urllib.request.urlopen(f"{server.origin}/lever/split/0",
                                          timeout=5).read().decode()
            self.assertNotIn("Verified Candidate", page)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM imported_results").fetchone()[0], 0)

    def test_a_source_that_changes_after_the_run_refuses_the_import(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                              self.observation("page-1", 0, "/lever/split/0", server,
                                               self.page_one_fields()), self.now)
            package = self.private / "stale-actions.json"
            FILL.export_page(self.db, "session-1", "worker-1", "page-1", package, self.now)
            grant = FILL.issue_execution_grant(self.db, "session-1", "page-1", package,
                                               self.now)
            result = self.private / "stale-result.json"
            with AUTHORITY.ExecutionAuthority(
                self.db, FILL.reserve_execution_grant,
                consume=FILL.consume_execution_grant, clock=lambda: self.now
            ) as authority:
                WORKER.run(package, result, authority.url, authority.token,
                           grant["grant_id"], headed=False, at=self.now)
            # Revoked between the run and the import.
            self.db.execute("UPDATE answers SET status='revoked' "
                            "WHERE answer_id='answer-company'")
            with self.assertRaises(FILL.ImportRefused):
                FILL.import_result(self.db, "session-1", "page-1", "worker-1", result,
                                   grant["grant_id"], self.now)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM fill_steps WHERE status='completed'").fetchone()[0], 0)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM application_fields").fetchone()[0], 0)
            with self.assertRaises(ValueError):
                FILL.checkpoint_page(self.db, "session-1", "worker-1", "page-1", "cp-1",
                                     self.now)
            self.assertEqual(self.oracle(server), 0)


@unittest.skipUnless(PLAYWRIGHT, "playwright is not installed")
class WorkflowInvariants(LocalFillOnlyWorkflow):
    """Properties of the flow itself, rather than of one stage."""

    def test_every_recorded_label_classifies_the_way_the_mapping_says(self):
        """Patterns written from imagination miss the forms employers ship.

        The first conflict pattern did not match `Related to someone at this company?`,
        which is the corpus's own wording, and only running against the real labels found it.
        """
        misses = []
        for path in sorted((ROOT / "tests" / "fixtures" / "ats-semantic" / "upstream"
                            ).glob("*/fixture.json")):
            fixture = json.loads(path.read_text(encoding="utf-8"))
            for step in fixture["steps"]:
                for control in step["controls"]:
                    disposition = REPLAY.KIND_DISPOSITIONS[control["kind"]][0]
                    domain = POLICY.classify(control["kind"].split(".")[-1],
                                             control.get("label") or "")
                    manual = bool(domain) and domain[0] in POLICY.ALWAYS_MANUAL_DOMAINS
                    if (disposition == "always_manual") != manual:
                        misses.append((control["kind"], control.get("label")))
        self.assertEqual(misses, [])

    def test_the_worker_has_no_way_to_leave_a_page(self):
        # Not a rule that could be argued with: the vocabulary has no verb for it.
        self.assertEqual(WORKER.SUPPORTED_OPERATIONS,
                         {"fill", "select", "check", "uncheck", "upload"})
        for forbidden in ("click", "submit", "navigate", "press", "download", "evaluate"):
            self.assertNotIn(forbidden, WORKER.SUPPORTED_OPERATIONS)
            self.assertIn(forbidden, PROTOCOL.FORBIDDEN_OPERATIONS)

    def test_page_two_cannot_reuse_page_ones_package_grant_or_result(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            first = self.run_page(server, "page-1", 0, "/lever/split/0",
                                  self.page_one_fields())
            second_observation = self.observation(
                "page-2", 1, "/lever/split/1", server, self.page_two_fields(), final=True,
                predecessor=first["checkpoint"]["checkpoint_sha256"])
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                              second_observation, self.now)
            package = self.private / "page-2-actions.json"
            FILL.export_page(self.db, "session-1", "worker-1", "page-2", package, self.now)
            # Page one's grant does not authorise page two.
            with self.assertRaises(ValueError) as caught:
                FILL.import_result(self.db, "session-1", "page-2", "worker-1",
                                   first["result"], first["grant"]["grant_id"], self.now)
            # Page one's result was already imported under that grant, so the recorded
            # import is what refuses first — either way page two gets nothing from it.
            self.assertEqual(str(caught.exception), "import_belongs_to_another_page")
            # Nor does page one's package: issuance checks the recorded export.
            with self.assertRaisesRegex(ValueError, "was not exported by this authority"):
                FILL.issue_execution_grant(self.db, "session-1", "page-2",
                                           first["package"], self.now)

    def test_an_unfinished_page_stops_the_session_from_looking_complete(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            self.run_page(server, "page-1", 0, "/lever/split/0", self.page_one_fields())
            # A second page observed and left unfinished.
            FILL.observe_page(
                self.db, "session-1", "worker-1", self.candidate_path,
                self.observation("page-2", 1, "/lever/split/1", server,
                                 self.page_two_fields(), final=True,
                                 predecessor=self.db.execute(
                                     "SELECT checkpoint_sha256 FROM fill_checkpoints"
                                 ).fetchone()["checkpoint_sha256"]), self.now)
            with self.assertRaisesRegex(ValueError, "completed checkpoints"):
                FILL.finish_session(self.db, "session-1", "worker-1", "inventory-1",
                                    self.now)
            self.assertEqual(POLICY.handling_summary(self.db, "app-1"),
                             {"status": "unknown", "markers": {}})
            self.assertEqual(self.oracle(server), 0)

    def test_a_completed_form_still_says_unknown_about_disclosures_it_never_met(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            first = self.run_page(server, "page-1", 0, "/lever/split/0",
                                  self.page_one_fields())
            self.run_page(server, "page-2", 1, "/lever/split/1", self.page_two_fields(),
                          final=True,
                          predecessor=first["checkpoint"]["checkpoint_sha256"])
            FILL.finish_session(self.db, "session-1", "worker-1", "inventory-1", self.now)
            # A self-reported chain cannot establish absence, so nothing writes not_present.
            self.assertEqual(POLICY.handling_summary(self.db, "app-1"),
                             {"status": "unknown", "markers": {}})
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM nondisclosure_handling").fetchone()[0], 0)

    def test_a_rejected_import_does_not_satisfy_the_checkpoint_gate(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start_session(server)
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                              self.observation("page-1", 0, "/lever/split/0", server,
                                               self.page_one_fields()), self.now)
            package = self.private / "reject-actions.json"
            FILL.export_page(self.db, "session-1", "worker-1", "page-1", package, self.now)
            grant = FILL.issue_execution_grant(self.db, "session-1", "page-1", package,
                                               self.now)
            result = self.private / "reject-result.json"
            with AUTHORITY.ExecutionAuthority(
                self.db, FILL.reserve_execution_grant,
                consume=FILL.consume_execution_grant, clock=lambda: self.now
            ) as authority:
                WORKER.run(package, result, authority.url, authority.token,
                           grant["grant_id"], headed=False, at=self.now)
            envelope = json.loads(result.read_text(encoding="utf-8"))
            envelope["results"][0]["observed_sha256"] = "f" * 64
            broken = self.private / "reject-broken.json"
            broken.write_text(json.dumps(envelope), encoding="utf-8")
            outcome = FILL.import_result(self.db, "session-1", "page-1", "worker-1", broken,
                                         grant["grant_id"], self.now)
            self.assertEqual(outcome["status"], "rejected")
            self.assertEqual(self.db.execute(
                "SELECT status FROM imported_results").fetchone()["status"], "rejected")
            with self.assertRaises(ValueError):
                FILL.checkpoint_page(self.db, "session-1", "worker-1", "page-1", "cp-1",
                                     self.now)
            self.assertEqual(self.oracle(server), 0)


if __name__ == "__main__":
    unittest.main()
