import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"pre_submit_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


PRE = load_script("pre_submit_core")
APPLICATIONS = load_script("application_core")
ANSWERS = load_script("answer_library")
RESUMES = load_script("resume_core")
ARCHIVE = load_script("archive_core")
CANDIDATE = load_script("candidate_core")
from tests.pdf_fixture import synthetic_pdf

AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class PreSubmitCoreTests(unittest.TestCase):
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
        CANDIDATE.initialize(self.db)
        ARCHIVE.initialize(self.db)
        PRE.initialize(self.db)
        self.addCleanup(self.db.close)
        self.prepare_filling_application()

    def prepare_filling_application(self):
        source = self.root / "resume.pdf"
        source.write_bytes(synthetic_pdf(["Verified Python experience"]))
        RESUMES.register_version(
            self.db, self.root / "resumes", source, "resume-1", "master_source", "general", at=AT
        )
        fact = {"id": "fact-1", "type": "skill", "value": "Python", "status": "confirmed",
                "locked": False, "evidence_strength": "direct"}
        candidate = {"schema_version": "0.2.0", "profile_id": "candidate-1", "facts": [fact]}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        candidate_path = self.root / "candidate.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        self.db.execute(
            "INSERT INTO candidate_snapshots VALUES (?, ?, ?, ?, 'active', ?, 'user', NULL, NULL)",
            (candidate["content_sha256"], "candidate-1", str(candidate_path),
             RESUMES.file_sha256(candidate_path), AT.isoformat()),
        )
        for fact_id, fact_type, value in (
            ("fact-1", "skill", "Python"), ("fact-name", "identity", "Verified Candidate")
        ):
            value_json = json.dumps(value, sort_keys=True, separators=(",", ":"))
            self.db.execute(
                # Columns named rather than positional: an additive migration to
                # `candidate_facts` should not break a fixture that does not care about the
                # new column.
                "INSERT INTO candidate_facts (content_sha256, fact_id, fact_type, "
                "value_json, status, locked, evidence_strength, expires_at, source_json, "
                "keywords_json, confirmed_at, invalidation_triggers_json, fact_sha256) "
                "VALUES (?, ?, ?, ?, 'locked', 1, 'direct', NULL, '{}', '[]', ?, '[]', ?)",
                (candidate["content_sha256"], fact_id, fact_type, value_json, AT.isoformat(),
                 RESUMES.canonical_hash({"id": fact_id, "value": value})),
            )
        self.db.commit()
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-1", "claim_text": "Python", "fact_ids": ["fact-1"],
            "evidence_strength": "direct", "exact_locked_value_preserved": False,
        }]}), encoding="utf-8")
        RESUMES.approve_version(self.db, "resume-1", candidate_path, manifest_path, "user", AT)
        card = {
            "job_id": "job-1", "canonical_url": "https://example.com/jobs/1", "employer": "Example Corp",
            "title": "Backend Engineer", "location": "New York, NY", "country": "US",
            "employment_type": "full_time", "status": "open",
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
        ANSWERS.add_answer(self.db, {
            "answer_id": "answer-auth", "canonical_id": "work_authorized_now",
            "canonical_meaning": "Authorized to work", "answer": True,
            "answer_type": "time_sensitive_fact", "source_type": "user_confirmed",
            "confirmation_status": "confirmed", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=30)).isoformat(), "validity_class": "per_application",
            "scope": {"country": "US", "application_id": "app-1"},
            "auto_fill_allowed": True, "auto_submit_allowed": True,
        })
        ARCHIVE.record_field(
            self.db, "app-1", "candidate_name", "Candidate name", "Verified Candidate",
            "fact", "fact-name", "locked", "normal", AT,
        )
        ARCHIVE.record_field(
            self.db, "app-1", "work_auth", "Authorized to work?", True,
            "answer", "answer-auth", "active", "normal", AT,
        )
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-1", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=7)).isoformat(),
            "scope": {"country": "US", "application_id": "app-1"},
        })

    def register_inventory(self, **updates):
        value = {
            "inventory_id": "inventory-1", "application_id": "app-1",
            "form_url": "https://example.com/jobs/1/apply", "observed_employer": "Example Corp",
            "observed_role": "Backend Engineer", "known_form": True,
            "required_field_ids": ["candidate_name", "work_auth"],
            "legal_items": ["standard_attestation"], "restricted_requests": [],
            "uploads": [{"kind": "resume", "version_id": "resume-1"}], "at": AT,
        }
        value.update(updates)
        return PRE.register_inventory(self.db, **value)

    def finish_fill(self):
        APPLICATIONS.release_lease(
            self.db, "app-1", "worker-1", "waiting_for_submission_approval", "filled", at=AT
        )

    def create_review(self, **updates):
        value = {
            "review_id": "review-1", "inventory_id": "inventory-1", "authorization_id": "auth-1",
            "authorization_context": {"country": "US", "application_id": "app-1"}, "at": AT,
        }
        value.update(updates)
        return PRE.create_review(self.db, **value)

    def test_inventory_must_be_captured_during_filling(self):
        self.finish_fill()
        with self.assertRaisesRegex(ValueError, "during filling"):
            self.register_inventory()

    def test_unknown_legal_term_is_rejected_at_inventory(self):
        with self.assertRaisesRegex(ValueError, "legal items require pause"):
            self.register_inventory(legal_items=["arbitration"])

    def test_mandatory_pause_blocks_review(self):
        self.register_inventory(restricted_requests=["captcha"])
        self.finish_fill()
        with self.assertRaisesRegex(ValueError, "mandatory_pause:captcha"):
            self.create_review()

    def test_job_identity_and_required_fields_must_match(self):
        self.register_inventory(observed_role="Unrelated Role", required_field_ids=["missing-field"])
        self.finish_fill()
        with self.assertRaisesRegex(ValueError, "role_mismatch") as captured:
            self.create_review()
        self.assertIn("required_field_missing:missing-field", str(captured.exception))

    def test_answer_must_remain_fresh_and_unchanged(self):
        self.register_inventory()
        self.finish_fill()
        self.db.execute("UPDATE answers SET status='stale' WHERE answer_id='answer-auth'")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "answer_stale:work_auth"):
            self.create_review()

    def test_authorization_scope_must_match_application_context(self):
        self.register_inventory()
        self.finish_fill()
        self.db.execute(
            "UPDATE authorizations SET scope_json=? WHERE authorization_id='auth-1'",
            (json.dumps({"country": "US", "application_id": "app-1", "queue_id": "queue-1"}),),
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "authorization_scope_mismatch"):
            self.create_review(authorization_context={"queue_id": "other-queue"})

    def test_summary_contains_sources_and_hash_but_not_values(self):
        self.register_inventory()
        self.finish_fill()
        review = self.create_review()
        encoded = json.dumps(review["summary"])
        self.assertIn("candidate_name", encoded)
        self.assertIn("fact-name", encoded)
        self.assertNotIn("Verified Candidate", encoded)
        self.assertNotIn('"value"', encoded)
        self.assertTrue(review["summary_sha256"])

    def test_approval_requires_user_and_exact_summary_hash(self):
        self.register_inventory()
        self.finish_fill()
        review = self.create_review()
        with self.assertRaisesRegex(ValueError, "user actor"):
            PRE.approve_review(self.db, "review-1", "system", review["summary_sha256"], AT)
        with self.assertRaisesRegex(ValueError, "hash"):
            PRE.approve_review(self.db, "review-1", "user", "wrong", AT)
        approved = PRE.approve_review(self.db, "review-1", "user", review["summary_sha256"], AT)
        self.assertEqual(approved["status"], "approved")

    def test_boolean_alone_cannot_enter_pre_submit_ready(self):
        self.register_inventory()
        self.finish_fill()
        with self.assertRaisesRegex(ValueError, "approved pre-submit review"):
            APPLICATIONS.transition(
                self.db, "app-1", "pre_submit_ready", "system", "caller_claimed_pass",
                {"pre_submit_check_passed": True}, AT,
            )

    def test_answer_invalidated_after_approval_blocks_state_transition(self):
        self.register_inventory()
        self.finish_fill()
        review = self.create_review()
        PRE.approve_review(self.db, "review-1", "user", review["summary_sha256"], AT)
        self.db.execute("UPDATE answers SET status='stale' WHERE answer_id='answer-auth'")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "no longer active"):
            APPLICATIONS.transition(
                self.db, "app-1", "pre_submit_ready", "system", "checked",
                {"pre_submit_review_id": "review-1"}, AT,
            )

    def test_approved_review_locks_authorization_and_is_invalidated_on_return(self):
        self.register_inventory()
        self.finish_fill()
        review = self.create_review()
        PRE.approve_review(self.db, "review-1", "user", review["summary_sha256"], AT)
        APPLICATIONS.transition(
            self.db, "app-1", "pre_submit_ready", "system", "checked",
            {"pre_submit_review_id": "review-1"}, AT,
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            APPLICATIONS.transition(
                self.db, "app-1", "submitting", "system", "submit",
                {"authorization_id": "other-auth", "approved_queue": True}, AT,
            )
        APPLICATIONS.transition(self.db, "app-1", "waiting_for_user_answer", "system", "changed", at=AT)
        row = self.db.execute("SELECT status FROM pre_submit_reviews WHERE review_id='review-1'").fetchone()
        self.assertEqual(row["status"], "invalidated")
        application = self.db.execute(
            "SELECT pre_submit_check_passed, pre_submit_review_id FROM applications WHERE application_id='app-1'"
        ).fetchone()
        self.assertEqual(application["pre_submit_check_passed"], 0)
        self.assertIsNone(application["pre_submit_review_id"])


if __name__ == "__main__":
    unittest.main()
