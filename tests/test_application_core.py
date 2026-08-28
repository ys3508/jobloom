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
    spec = importlib.util.spec_from_file_location(f"app_core_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


CORE = load_script("application_core")
ANSWERS = load_script("answer_library")
RESUMES = load_script("resume_core")
ARCHIVE = load_script("archive_core")
PRE_SUBMIT = load_script("pre_submit_core")
CANDIDATE = load_script("candidate_core")
AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


def card(job_id="job-1", url="https://example.com/jobs/1", **updates):
    value = {
        "job_id": job_id, "canonical_url": url, "employer": "Example Corp",
        "title": "Backend Engineer", "location": "New York, NY", "status": "open",
        "description_sha256": "description-hash-1", "requisition_id": "REQ-1",
    }
    value.update(updates)
    return value


class ApplicationCoreTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        CORE.initialize(self.db)
        ANSWERS.initialize(self.db)
        RESUMES.initialize(self.db)
        ARCHIVE.initialize(self.db)
        PRE_SUBMIT.initialize(self.db)
        CANDIDATE.initialize(self.db)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self.db.close)

    def add_job_and_application(self, application_id="app-1", policy="stop_before_submit", category="broad"):
        CORE.ingest_job(self.db, card(), at=AT)
        return CORE.create_application(self.db, application_id, "job-1", category, policy, AT)

    def move_to_ready(self, application_id="app-1"):
        CORE.transition(self.db, application_id, "pending_analysis", "system", "analysis_started", at=AT)
        CORE.transition(self.db, application_id, "broad_recommended", "system", "broad_match", at=AT)
        CORE.transition(self.db, application_id, "approved", "user", "user_approved", at=AT)
        CORE.transition(self.db, application_id, "materials_in_progress", "system", "materials_started", at=AT)
        self.install_materials(application_id)
        CORE.transition(self.db, application_id, "ready_to_fill", "system", "materials_ready", at=AT)

    def test_materials_can_be_replaced_before_filling_starts(self):
        """A resume bound in the wrong format must be replaceable while nobody is filling."""
        self.add_job_and_application()
        self.move_to_ready()
        CORE.transition(self.db, "app-1", "materials_in_progress", "system", "resume_format_changed",
                        at=AT)
        self.assertEqual(self.db.execute(
            "SELECT state FROM applications WHERE application_id='app-1'").fetchone()[0],
            "materials_in_progress")
        self.install_materials("app-1", suffix="-pdf")
        CORE.transition(self.db, "app-1", "ready_to_fill", "system", "materials_ready", at=AT)
        row = self.db.execute(
            "SELECT state, resume_version_id FROM applications WHERE application_id='app-1'"
        ).fetchone()
        self.assertEqual(row["state"], "ready_to_fill")
        self.assertEqual(row["resume_version_id"], "resume-app-1-pdf")
        locks = self.db.execute(
            "SELECT resume_version_id, invalidated_at FROM material_locks "
            "WHERE application_id='app-1' ORDER BY locked_at").fetchall()
        self.assertEqual(len(locks), 2)
        self.assertIsNotNone(locks[0]["invalidated_at"], "the first lock is invalidated by rebinding")
        self.assertIsNone(locks[1]["invalidated_at"])

    def test_materials_cannot_be_replaced_once_a_worker_is_filling(self):
        self.add_job_and_application()
        self.move_to_ready()
        CORE.acquire_next(self.db, "worker-1", at=AT)
        with self.assertRaisesRegex(ValueError, "invalid transition"):
            CORE.transition(self.db, "app-1", "materials_in_progress", "system", "too_late", at=AT)

    def install_materials(self, application_id="app-1", suffix=""):
        root = Path(self.temp_dir.name)
        source = root / f"{application_id}{suffix}.txt"
        source.write_text("Verified resume claim\n", encoding="utf-8")
        version_id = f"resume-{application_id}{suffix}"
        RESUMES.register_version(self.db, root / "store", source, version_id, "master_source", "general", at=AT)
        fact = {
            "id": "fact-1", "type": "skill", "value": "Verified resume claim",
            "evidence_strength": "direct", "status": "confirmed", "locked": False,
        }
        name_fact = {
            "id": "fact-name", "type": "identity", "value": "Verified Candidate",
            "evidence_strength": "direct", "status": "locked", "locked": True,
        }
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": False, "employer_action_required": False, "confirmed": True,
            },
            "search": {}, "facts": [fact, name_fact],
        }
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        candidate_path = root / f"candidate-{application_id}.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        manifest_path = root / f"manifest-{application_id}.json"
        manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-1", "claim_text": "Verified resume claim", "fact_ids": ["fact-1"],
            "evidence_strength": "direct", "exact_locked_value_preserved": False,
        }]}), encoding="utf-8")
        if not self.db.execute(
            "SELECT 1 FROM candidate_snapshots WHERE content_sha256=?",
            (candidate["content_sha256"],),
        ).fetchone():
            CANDIDATE.register_snapshot(
                self.db, root / "candidates", candidate_path, "user", AT
            )
        RESUMES.approve_version(self.db, version_id, candidate_path, manifest_path, "user", AT)
        RESUMES.bind_version(self.db, application_id, version_id, at=AT)
        RESUMES.lock_materials(self.db, application_id, lock_id=f"lock-{application_id}{suffix}", at=AT)

    def move_to_pre_submit(self, application_id="app-1"):
        self.move_to_ready(application_id)
        CORE.acquire_next(self.db, "worker-1", at=AT)
        ARCHIVE.record_field(
            self.db, application_id, "candidate_name", "Candidate name", "Verified Candidate",
            "fact", "fact-name", "locked", "normal", AT,
        )
        PRE_SUBMIT.register_inventory(
            self.db, f"inventory-{application_id}", application_id, "https://example.com/jobs/1/apply",
            "Example Corp", "Backend Engineer", True, ["candidate_name"],
            ["standard_attestation"], [],
            [{"kind": "resume", "version_id": f"resume-{application_id}"}], AT,
        )
        CORE.release_lease(
            self.db, application_id, "worker-1", "waiting_for_submission_approval", "form_filled", at=AT
        )
        self.add_authorization()
        review = PRE_SUBMIT.create_review(
            self.db, f"review-{application_id}", f"inventory-{application_id}", "auth-1",
            {"country": "US", "queue_id": "queue-1"}, AT,
        )
        PRE_SUBMIT.approve_review(self.db, review["review_id"], "user", review["summary_sha256"], AT)
        CORE.transition(
            self.db, application_id, "pre_submit_ready", "system", "pre_submit_passed",
            {"pre_submit_review_id": review["review_id"]}, AT,
        )

    def add_authorization(self, authorization_id="auth-1"):
        if self.db.execute("SELECT 1 FROM authorizations WHERE authorization_id=?", (authorization_id,)).fetchone():
            return
        ANSWERS.add_authorization(self.db, {
            "authorization_id": authorization_id,
            "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=7)).isoformat(),
            "scope": {"country": "US", "queue_id": "queue-1"},
        })

    def test_tracking_parameters_deduplicate_to_canonical_url(self):
        first = CORE.ingest_job(self.db, card(), at=AT)
        second = CORE.ingest_job(
            self.db,
            card(job_id="job-2", url="https://EXAMPLE.com/jobs/1/?utm_source=board#apply", requisition_id="REQ-2", description_sha256="different"),
            at=AT,
        )
        self.assertEqual(first["decision"], "inserted")
        self.assertEqual(second["decision"], "duplicate")
        self.assertEqual(second["reason"], "canonical_url")

    def test_employer_and_requisition_deduplicate_cross_board(self):
        CORE.ingest_job(self.db, card(), at=AT)
        result = CORE.ingest_job(
            self.db, card(job_id="job-2", url="https://board.test/j/2", description_sha256="different"), at=AT
        )
        self.assertEqual(result["reason"], "employer_requisition")

    def test_same_identity_without_strong_key_requires_review(self):
        CORE.ingest_job(self.db, card(requisition_id=None, description_sha256="hash-a"), at=AT)
        result = CORE.ingest_job(
            self.db, card(job_id="job-2", url="https://board.test/j/2", requisition_id=None, description_sha256="hash-b"), at=AT
        )
        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["reason"], "normalized_identity")

    def test_description_hash_does_not_cross_employers(self):
        CORE.ingest_job(self.db, card(requisition_id=None), at=AT)
        result = CORE.ingest_job(self.db, card(
            job_id="job-2", url="https://other.test/jobs/2", employer="Other Corp", requisition_id=None,
        ), at=AT)
        self.assertEqual(result["decision"], "inserted")

    def test_duplicate_application_is_blocked(self):
        self.add_job_and_application()
        result = CORE.create_application(self.db, "app-2", "job-1", at=AT)
        self.assertEqual(result["decision"], "duplicate_application")
        self.assertEqual(result["application_id"], "app-1")

    def test_imported_application_history_is_blocked(self):
        CORE.ingest_job(self.db, card(already_applied=True), at=AT)
        result = CORE.create_application(self.db, "app-1", "job-1", at=AT)
        self.assertEqual(result["decision"], "duplicate_application")
        self.assertEqual(result["state"], "external_history")

    def test_possible_duplicate_checks_related_application_history(self):
        self.add_job_and_application()
        second_card = card(
            job_id="job-2", url="https://board.test/jobs/2", requisition_id=None,
            description_sha256="different-hash",
        )
        self.db.execute("UPDATE jobs SET requisition_id=NULL WHERE job_id='job-1'")
        self.db.commit()
        inserted = CORE.ingest_job(self.db, second_card, allow_possible_duplicate=True, at=AT)
        self.assertEqual(inserted["decision"], "inserted_with_review")
        result = CORE.create_application(self.db, "app-2", "job-2", at=AT)
        self.assertEqual(result["decision"], "duplicate_application")

    def test_invalid_transition_is_rejected(self):
        self.add_job_and_application()
        with self.assertRaisesRegex(ValueError, "invalid transition"):
            CORE.transition(self.db, "app-1", "submitted", "system", "skip", at=AT)

    def test_approval_requires_user(self):
        self.add_job_and_application()
        CORE.transition(self.db, "app-1", "pending_analysis", "system", "analysis_started", at=AT)
        CORE.transition(self.db, "app-1", "broad_recommended", "system", "recommended", at=AT)
        with self.assertRaisesRegex(ValueError, "user actor"):
            CORE.transition(self.db, "app-1", "approved", "system", "auto_approved", at=AT)

    def test_atomic_acquire_only_leases_one_application(self):
        self.add_job_and_application()
        self.move_to_ready()
        first = CORE.acquire_next(self.db, "worker-1", at=AT)
        second = CORE.acquire_next(self.db, "worker-2", at=AT)
        self.assertEqual(first["application_id"], "app-1")
        self.assertIsNone(second)

    def test_expired_lease_can_be_reacquired(self):
        self.add_job_and_application()
        self.move_to_ready()
        CORE.acquire_next(self.db, "worker-1", lease_seconds=30, at=AT)
        reacquired = CORE.acquire_next(self.db, "worker-2", at=AT + timedelta(seconds=31))
        self.assertEqual(reacquired["worker_id"], "worker-2")
        self.assertEqual(reacquired["attempt"], 2)

    def test_wrong_worker_cannot_release_lease(self):
        self.add_job_and_application()
        self.move_to_ready()
        CORE.acquire_next(self.db, "worker-1", at=AT)
        with self.assertRaisesRegex(ValueError, "not owned"):
            CORE.release_lease(self.db, "app-1", "worker-2", "waiting_for_user_answer", "new_question", at=AT)

    def test_stop_before_submit_policy_cannot_be_overridden(self):
        self.add_job_and_application()
        self.move_to_pre_submit()
        self.add_authorization()
        with self.assertRaisesRegex(ValueError, "blocks submission"):
            CORE.transition(
                self.db, "app-1", "submitting", "system", "submit_requested",
                {"authorization_id": "auth-1"}, AT,
            )

    def test_submission_requires_real_current_authorization(self):
        self.add_job_and_application(policy="approved_queue")
        self.move_to_pre_submit()
        with self.assertRaisesRegex(ValueError, "does not match"):
            CORE.transition(
                self.db, "app-1", "submitting", "system", "submit_requested",
                {"authorization_id": "missing", "approved_queue": True}, AT,
            )

    def test_expired_submission_authorization_is_rejected(self):
        self.add_job_and_application(policy="approved_queue")
        self.move_to_pre_submit()
        self.db.execute("UPDATE authorizations SET expires_at=? WHERE authorization_id='auth-1'",
                        ((AT - timedelta(days=1)).isoformat(),))
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "expired"):
            CORE.transition(
                self.db, "app-1", "submitting", "system", "submit_requested",
                {"authorization_id": "auth-1", "approved_queue": True}, AT,
            )

    def test_summary_policy_uses_persisted_user_approved_review(self):
        self.add_job_and_application(policy="approved_after_summary")
        self.move_to_pre_submit()
        result = CORE.transition(
            self.db, "app-1", "submitting", "system", "submit_requested",
            {"authorization_id": "auth-1"}, AT,
        )
        self.assertEqual(result["state"], "submitting")

    def test_known_form_policy_uses_reviewed_inventory(self):
        self.add_job_and_application(policy="known_forms_only")
        self.move_to_pre_submit()
        result = CORE.transition(
            self.db, "app-1", "submitting", "system", "submit_requested",
            {"authorization_id": "auth-1"}, AT,
        )
        self.assertEqual(result["state"], "submitting")

    def test_submitted_requires_positive_evidence(self):
        self.add_job_and_application(policy="approved_queue")
        self.move_to_pre_submit()
        self.add_authorization()
        CORE.transition(
            self.db, "app-1", "submitting", "system", "submit_requested",
            {"authorization_id": "auth-1", "approved_queue": True}, AT,
        )
        with self.assertRaisesRegex(ValueError, "positive submission evidence"):
            CORE.transition(self.db, "app-1", "submitted", "system", "submit_clicked", at=AT)

    def test_success_evidence_allows_submitted_state(self):
        self.add_job_and_application(policy="approved_queue")
        self.move_to_pre_submit()
        self.add_authorization()
        CORE.transition(
            self.db, "app-1", "submitting", "system", "submit_requested",
            {"authorization_id": "auth-1", "approved_queue": True}, AT,
        )
        CORE.record_evidence(self.db, "ev-1", "app-1", "confirmation_id", confirmation_id="ABC-123", at=AT)
        result = CORE.transition(self.db, "app-1", "submitted", "system", "confirmation_received", at=AT)
        self.assertEqual(result["state"], "submitted")
        row = self.db.execute("SELECT confirmation_id FROM applications WHERE application_id='app-1'").fetchone()
        self.assertEqual(row["confirmation_id"], "ABC-123")
        usage = self.db.execute(
            "SELECT version_id, file_sha256 FROM resume_usage WHERE application_id='app-1' AND use_type='submitted'"
        ).fetchone()
        self.assertEqual(usage["version_id"], "resume-app-1")
        self.assertTrue(usage["file_sha256"])

    def test_evidence_cannot_be_recorded_before_submission_attempt(self):
        self.add_job_and_application()
        with self.assertRaisesRegex(ValueError, "submission attempt"):
            CORE.record_evidence(self.db, "ev-early", "app-1", "success_page", reference="confirmation.png", at=AT)

    def test_returning_to_fill_invalidates_pre_submit_check(self):
        self.add_job_and_application(policy="approved_queue")
        self.move_to_pre_submit()
        CORE.transition(self.db, "app-1", "waiting_for_user_answer", "system", "new_question", at=AT)
        CORE.transition(self.db, "app-1", "ready_to_fill", "system", "answer_received", at=AT)
        CORE.acquire_next(self.db, "worker-2", at=AT)
        row = self.db.execute("SELECT pre_submit_check_passed FROM applications WHERE application_id='app-1'").fetchone()
        self.assertEqual(row["pre_submit_check_passed"], 0)

    def test_uncertain_submission_requires_manual_resolution(self):
        self.add_job_and_application(policy="approved_queue")
        self.move_to_pre_submit()
        self.add_authorization()
        CORE.transition(
            self.db, "app-1", "submitting", "system", "submit_requested",
            {"authorization_id": "auth-1", "approved_queue": True}, AT,
        )
        CORE.transition(self.db, "app-1", "submission_uncertain", "system", "result_uncertain", at=AT)
        with self.assertRaisesRegex(ValueError, "explicit user resolution"):
            CORE.transition(
                self.db, "app-1", "submission_failed", "system", "automatic_retry",
                {"error_code": "website_failure"}, AT,
            )
        result = CORE.transition(
            self.db, "app-1", "submission_failed", "user", "user_resolved_not_submitted",
            {"error_code": "website_failure", "manual_resolution": True}, AT,
        )
        self.assertEqual(result["state"], "submission_failed")

    def test_event_log_contains_state_metadata_not_job_card(self):
        self.add_job_and_application()
        self.move_to_ready()
        logs = " ".join(row[0] for row in self.db.execute("SELECT metadata_json FROM application_events"))
        self.assertNotIn("Backend Engineer", logs)
        self.assertNotIn("description_sha256", logs)


if __name__ == "__main__":
    unittest.main()
