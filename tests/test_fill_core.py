import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"fill_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


FILL = load_script("fill_core")
CANDIDATES = load_script("candidate_core")
APPLICATIONS = load_script("application_core")
ANSWERS = load_script("answer_library")
RESUMES = load_script("resume_core")
COVERS = load_script("cover_letter_core")
ARCHIVE = load_script("archive_core")
PRE_SUBMIT = load_script("pre_submit_core")
from tests.pdf_fixture import synthetic_pdf

AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class FillCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        APPLICATIONS.initialize(self.db)
        ANSWERS.initialize(self.db)
        RESUMES.initialize(self.db)
        COVERS.initialize(self.db)
        ARCHIVE.initialize(self.db)
        PRE_SUBMIT.initialize(self.db)
        FILL.initialize(self.db)
        CANDIDATES.initialize(self.db)
        self.addCleanup(self.db.close)
        self.candidate_path, self.manifest_path = self.make_candidate()
        CANDIDATES.register_snapshot(
            self.db, self.root / "candidates", self.candidate_path, "user", AT
        )
        self.prepare_application()

    def make_candidate(self):
        facts = [
            {"id": "fact-name", "type": "identity", "value": "Verified Candidate",
             "status": "locked", "locked": True, "evidence_strength": "direct"},
            {"id": "fact-unlocked", "type": "location", "value": "New York",
             "status": "confirmed", "locked": False, "evidence_strength": "direct"},
        ]
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": False, "employer_action_required": False, "confirmed": True,
            },
            "search": {}, "facts": facts,
        }
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        candidate_path = self.root / "candidate.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        manifest_path = self.root / "claims.json"
        manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-name", "claim_text": "Verified Candidate", "fact_ids": ["fact-name"],
            "evidence_strength": "direct", "exact_locked_value_preserved": True,
        }]}), encoding="utf-8")
        return candidate_path, manifest_path

    def prepare_application(self):
        source = self.root / "resume.pdf"
        source.write_bytes(synthetic_pdf(["Verified Candidate"]))
        RESUMES.register_version(
            self.db, self.root / "resumes", source, "resume-1", "master_source", "general", at=AT
        )
        RESUMES.approve_version(
            self.db, "resume-1", self.candidate_path, self.manifest_path, "user", AT
        )
        card = {
            "job_id": "job-1", "canonical_url": "https://apply.example.com/jobs/1",
            "employer": "Example Corp", "title": "Backend Engineer", "location": "New York, NY",
            "country": "US", "employment_type": "full_time", "status": "open",
        }
        APPLICATIONS.ingest_job(self.db, card, at=AT)
        APPLICATIONS.create_application(self.db, "app-1", "job-1", "precision", "approved_queue", AT)
        APPLICATIONS.transition(self.db, "app-1", "pending_analysis", "system", "analysis", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "precision_recommended", "system", "match", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "approved", "user", "approved", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "materials_in_progress", "system", "materials", at=AT)
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        RESUMES.lock_materials(self.db, "app-1", lock_id="lock-1", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "ready", at=AT)
        APPLICATIONS.acquire_next(self.db, "worker-1", at=AT)
        self.add_answer(
            "answer-auth", "work_authorized_now", "Are you authorized to work?", True
        )
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-1", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=7)).isoformat(),
            "scope": {"country": "US", "application_id": "app-1"},
        })

    def add_answer(self, answer_id, canonical_id, question, value, scope=None):
        ANSWERS.add_answer(self.db, {
            "answer_id": answer_id, "canonical_id": canonical_id,
            "canonical_meaning": question, "answer": value, "answer_type": "stable_fact",
            "source_type": "user_confirmed", "confirmation_status": "confirmed",
            "confirmed_at": AT.isoformat(), "validity_class": "stable",
            "scope": scope if scope is not None else {"country": "US", "application_id": "app-1"},
            "auto_fill_allowed": True, "auto_submit_allowed": True,
        })
        ANSWERS.add_question_form(self.db, canonical_id, question)

    def start(self, **updates):
        value = {
            "session_id": "session-1", "application_id": "app-1", "worker_id": "worker-1",
            "form_url": "https://apply.example.com/jobs/1/apply",
            "observed_employer": "Example Corp", "observed_role": "Backend Engineer",
            "known_form": True, "authorization_id": "auth-1",
            "authorization_context": {"country": "wrong", "application_id": "wrong"}, "at": AT,
        }
        value.update(updates)
        return FILL.start_session(self.db, **value)

    def page(self, fields=None, **updates):
        value = {
            "page_id": "page-1", "page_index": 0,
            "page_url": "https://apply.example.com/jobs/1/apply",
            "fields": fields if fields is not None else self.standard_fields(),
            "legal_items": [], "restricted_requests": [], "final_page": True,
        }
        value.update(updates)
        return value

    def standard_fields(self):
        return [
            {"field_id": "candidate_name", "question": "Full name", "selector": "#name",
             "control": "text", "required": True, "sensitivity": "normal",
             "source_kind": "fact", "source_id": "fact-name"},
            {"field_id": "work_auth", "question": "Are you authorized to work?", "selector": "#auth",
             "control": "radio", "required": True, "sensitivity": "normal",
             "source_kind": "answer"},
            {"field_id": "resume_upload", "question": "Resume", "selector": "#resume",
             "control": "file", "required": True, "sensitivity": "normal",
             "upload_kind": "resume"},
            {"field_id": "truth", "question": "I certify this is accurate", "selector": "#truth",
             "control": "standard_attestation", "required": True, "sensitivity": "normal"},
            {"field_id": "submit", "question": "Submit application", "selector": "#submit",
             "control": "submit", "required": True, "sensitivity": "normal"},
        ]

    def complete_page(self, page_id="page-1", worker_id="worker-1"):
        rows = self.db.execute(
            "SELECT step_id, expected_sha256 FROM fill_steps WHERE session_id='session-1' AND page_id=? "
            "ORDER BY ordinal", (page_id,),
        ).fetchall()
        for row in rows:
            FILL.complete_step(self.db, "session-1", worker_id, row["step_id"], row["expected_sha256"], AT)
        return FILL.checkpoint_page(
            self.db, "session-1", worker_id, page_id, f"checkpoint-{page_id}", AT
        )

    def reacquire(self, worker_id="worker-2"):
        APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "user_resolved", at=AT)
        return APPLICATIONS.acquire_next(self.db, worker_id, at=AT)

    def test_immigration_answer_from_another_application_pauses_before_filling(self):
        self.db.execute("DELETE FROM answers WHERE answer_id='answer-auth'")
        ANSWERS.add_answer(self.db, {
            "answer_id": "answer-auth-broad", "canonical_id": "work_authorized_now",
            "canonical_meaning": "Are you authorized to work?", "answer": True,
            "answer_type": "stable_fact", "source_type": "user_confirmed",
            "confirmation_status": "confirmed", "confirmed_at": AT.isoformat(),
            "validity_class": "stable", "scope": {"country": "US"},
            "auto_fill_allowed": True, "auto_submit_allowed": True,
        })
        self.start()
        result = FILL.observe_page(
            self.db, "session-1", "worker-1", self.candidate_path,
            self.page(fields=[self.standard_fields()[1]]), AT,
        )
        self.assertEqual(result["state"], "waiting_for_user_answer")
        self.assertIn("immigration_recheck_required:work_auth", result["reasons"])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM fill_steps WHERE session_id='session-1'"
        ).fetchone()[0], 0)

    def test_identity_mismatch_pauses_for_takeover(self):
        result = self.start(observed_employer="Unrelated Corp")
        self.assertEqual(result["state"], "waiting_for_user_takeover")
        self.assertIn("employer_mismatch", result["reasons"])
        self.assertEqual(self.db.execute(
            "SELECT state FROM applications WHERE application_id='app-1'"
        ).fetchone()[0], "waiting_for_user_takeover")
        self.reacquire()
        with self.assertRaisesRegex(ValueError, "new fill session"):
            FILL.resume_session(
                self.db, "session-1", "worker-2", "auth-1", {"country": "US"},
                self.candidate_path, AT,
            )

    def test_new_question_pauses_then_resumes_without_restarting_completed_pages(self):
        self.start()
        first_fields = [self.standard_fields()[0]]
        FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                          self.page(fields=first_fields, final_page=False), AT)
        checkpoint = self.complete_page()
        unknown = [{"field_id": "availability", "question": "When can you start?",
                    "selector": "#start", "control": "text", "required": True,
                    "sensitivity": "normal", "source_kind": "answer"},
                   self.standard_fields()[-1]]
        result = FILL.observe_page(
            self.db, "session-1", "worker-1", self.candidate_path,
            self.page(fields=unknown, page_id="page-2", page_index=1,
                      page_url="https://apply.example.com/jobs/1/apply/2",
                      predecessor_checkpoint_sha256=checkpoint["checkpoint_sha256"]), AT,
        )
        self.assertEqual(result["state"], "waiting_for_user_answer")
        self.add_answer("answer-start", "availability", "When can you start?", "Two weeks")
        self.reacquire()
        resumed = FILL.resume_session(
            self.db, "session-1", "worker-2", "auth-1", {"country": "US"}, self.candidate_path, AT
        )
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(self.db.execute(
            "SELECT status FROM fill_pages WHERE session_id='session-1' AND page_id='page-1'"
        ).fetchone()[0], "completed")
        self.complete_page("page-2", "worker-2")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM fill_checkpoints").fetchone()[0], 2)

    def test_private_export_has_no_submission_action_or_terminal_values(self):
        self.start(form_url="https://apply.example.com/jobs/1/apply?session_token=secret")
        FILL.observe_page(
            self.db, "session-1", "worker-1", self.candidate_path,
            self.page(page_url="https://apply.example.com/jobs/1/apply?session_token=secret"), AT
        )
        output = self.root / "private" / "page-actions.json"
        result = FILL.export_page(
            self.db, "session-1", "worker-1", "page-1", output, AT
        )
        package = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(package["stop_before_submit"])
        self.assertIsNone(package["submission_action"])
        self.assertNotIn("submit", {action["operation"] for action in package["actions"]})
        self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
        self.assertFalse(result["contains_submission_action"])
        self.assertNotIn("session_token", package["page_url"])
        stored_urls = " ".join(row[0] for row in self.db.execute(
            "SELECT form_url FROM fill_sessions UNION ALL SELECT page_url FROM fill_pages"
        ))
        self.assertNotIn("secret", stored_urls)
        terminal = json.dumps([row[0] for row in self.db.execute("SELECT metadata_json FROM fill_events")])
        self.assertNotIn("Verified Candidate", terminal)
        with self.assertRaisesRegex(ValueError, "already exists"):
            FILL.export_page(self.db, "session-1", "worker-1", "page-1", output, AT)

    def test_context_rejects_unrelated_or_sensitive_properties(self):
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            self.start(authorization_context={"country": "US", "password": "do-not-store"})

    def test_incorrect_autofill_hash_pauses_without_recording_field(self):
        self.start()
        FILL.observe_page(
            self.db, "session-1", "worker-1", self.candidate_path,
            self.page(fields=[self.standard_fields()[0]]), AT,
        )
        step = self.db.execute("SELECT step_id FROM fill_steps").fetchone()
        result = FILL.complete_step(
            self.db, "session-1", "worker-1", step["step_id"], "wrong", AT
        )
        self.assertEqual(result["state"], "waiting_for_user_takeover")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM application_fields").fetchone()[0], 0)
        self.reacquire()
        resumed = FILL.resume_session(
            self.db, "session-1", "worker-2", "auth-1", {"country": "US"}, self.candidate_path, AT
        )
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM fill_steps WHERE session_id='session-1' AND status='pending'"
        ).fetchone()[0], 1)

    def test_unlocked_fact_and_unapproved_upload_pause(self):
        self.start()
        fields = [
            {"field_id": "location", "question": "Location", "selector": "#location",
             "control": "text", "required": True, "sensitivity": "normal",
             "source_kind": "fact", "source_id": "fact-unlocked"},
            {"field_id": "cover", "question": "Cover letter", "selector": "#cover",
             "control": "file", "required": False, "sensitivity": "normal",
             "upload_kind": "cover_letter"},
        ]
        result = FILL.observe_page(
            self.db, "session-1", "worker-1", self.candidate_path, self.page(fields=fields), AT
        )
        self.assertIn("candidate_fact_not_locked:location", result["reasons"])
        self.assertIn("unapproved_upload:cover", result["reasons"])

    def test_cross_origin_and_captcha_pause(self):
        self.start()
        result = FILL.observe_page(
            self.db, "session-1", "worker-1", self.candidate_path,
            self.page(page_url="https://evil.example.net/collect", restricted_requests=["captcha"]), AT,
        )
        self.assertEqual(result["state"], "waiting_for_user_takeover")
        self.assertIn("unexpected_navigation", result["reasons"])
        self.assertIn("captcha", result["reasons"])

    def test_successful_fill_stops_before_submit_and_registers_inventory(self):
        self.start()
        planned = FILL.observe_page(
            self.db, "session-1", "worker-1", self.candidate_path, self.page(), AT
        )
        self.assertEqual(planned["step_count"], 3)
        self.complete_page()
        result = FILL.finish_session(
            self.db, "session-1", "worker-1", "inventory-1", AT
        )
        self.assertFalse(result["submission_performed"])
        self.assertEqual(result["application_state"], "waiting_for_submission_approval")
        inventory = self.db.execute(
            "SELECT * FROM form_inventories WHERE inventory_id='inventory-1'"
        ).fetchone()
        self.assertEqual(json.loads(inventory["required_field_ids_json"]), ["candidate_name", "work_auth"])
        self.assertEqual(json.loads(inventory["legal_items_json"]), ["standard_attestation"])
        self.assertEqual(json.loads(inventory["uploads_json"]), [
            {"kind": "resume", "version_id": "resume-1"}
        ])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM submission_evidence").fetchone()[0], 0)

    def test_finish_requires_observation_of_submit_control(self):
        self.start()
        FILL.observe_page(
            self.db, "session-1", "worker-1", self.candidate_path,
            self.page(fields=[self.standard_fields()[0]]), AT,
        )
        self.complete_page()
        with self.assertRaisesRegex(ValueError, "submit control"):
            FILL.finish_session(self.db, "session-1", "worker-1", "inventory-1", AT)


if __name__ == "__main__":
    unittest.main()
