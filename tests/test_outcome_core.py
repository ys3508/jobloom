import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"outcome_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


OUTCOMES = load_script("outcome_core")
APPLICATIONS = load_script("application_core")
RESUMES = load_script("resume_core")
AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class OutcomeCoreTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        APPLICATIONS.initialize(self.db)
        RESUMES.initialize(self.db)
        OUTCOMES.initialize(self.db)
        self.addCleanup(self.db.close)
        self.db.execute("""
            INSERT INTO resume_versions (
                version_id, kind, direction, status, snapshot_path, file_sha256,
                file_size, file_format, created_at
            ) VALUES ('resume-1', 'direction', 'backend', 'approved', '/private/resume.pdf', 'hash', 1, 'pdf', ?)
        """, (AT.isoformat(),))
        card = {
            "job_id": "job-1", "canonical_url": "https://example.com/jobs/1", "employer": "Example Corp",
            "title": "Backend Engineer", "location": "New York, NY", "work_arrangement": "hybrid",
            "source": "company_site", "ats": "greenhouse", "status": "open",
        }
        APPLICATIONS.ingest_job(self.db, card, at=AT)
        APPLICATIONS.create_application(self.db, "app-1", "job-1", "precision", at=AT)
        for state in ("pending_analysis", "precision_recommended"):
            APPLICATIONS.transition(self.db, "app-1", state, "system", "fixture", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "approved", "user", "fixture", at=AT)
        self.db.execute("""
            UPDATE applications SET state='submitted', submitted_at=?, resume_version_id='resume-1'
            WHERE application_id='app-1'
        """, (AT.isoformat(),))
        APPLICATIONS._event(self.db, "app-1", "system", "submitting", "submitted", "fixture", at=AT)
        self.db.commit()

    def move(self, state):
        return APPLICATIONS.transition(self.db, "app-1", state, "user", "outcome_confirmed", at=AT)

    def test_outcome_requires_guarded_application_state_history(self):
        with self.assertRaisesRegex(ValueError, "state history"):
            OUTCOMES.record_outcome(
                self.db, "outcome-1", "app-1", "recruiter_response", AT.isoformat(),
                "user_confirmation", "user", at=AT,
            )

    def test_user_can_record_verified_outcome_after_state_transition(self):
        self.move("recruiter_response")
        result = OUTCOMES.record_outcome(
            self.db, "outcome-1", "app-1", "recruiter_response", AT.isoformat(),
            "user_confirmation", "user", at=AT,
        )
        self.assertTrue(result["verified_by_user"])
        row = self.db.execute("SELECT * FROM outcome_records WHERE outcome_id='outcome-1'").fetchone()
        self.assertEqual(row["resume_version_id"], "resume-1")
        self.assertEqual(row["application_category"], "precision")

    def test_system_outcome_requires_reference_and_stores_only_hash(self):
        self.move("recruiter_response")
        with self.assertRaisesRegex(ValueError, "source reference"):
            OUTCOMES.record_outcome(
                self.db, "outcome-1", "app-1", "recruiter_response", AT.isoformat(),
                "email", "system", at=AT,
            )
        OUTCOMES.record_outcome(
            self.db, "outcome-1", "app-1", "recruiter_response", AT.isoformat(),
            "email", "system", "private-email-reference", AT,
        )
        values = " ".join(str(value) for row in self.db.execute(
            "SELECT source_reference_sha256 FROM outcome_records"
        ) for value in row)
        audit = " ".join(row[0] for row in self.db.execute("SELECT metadata_json FROM outcome_audit_events"))
        self.assertNotIn("private-email-reference", values + audit)

    def test_outcome_timestamp_requires_timezone_and_cannot_be_future(self):
        self.move("recruiter_response")
        with self.assertRaisesRegex(ValueError, "timezone"):
            OUTCOMES.record_outcome(
                self.db, "outcome-1", "app-1", "recruiter_response", "2026-08-25T12:00:00",
                "user_confirmation", "user", at=AT,
            )
        with self.assertRaisesRegex(ValueError, "future"):
            OUTCOMES.record_outcome(
                self.db, "outcome-2", "app-1", "recruiter_response",
                (AT + timedelta(hours=1)).isoformat(), "user_confirmation", "user", at=AT,
            )

    def test_model_usage_records_metadata_without_prompts(self):
        result = OUTCOMES.record_model_usage(
            self.db, "usage-1", "job_evaluation", "evidence_review", "low_cost",
            100, 25, 40, "model-small", 1200, 500, True, "app-1", "job-1", AT,
        )
        self.assertEqual(result["total_tokens"], 125)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(model_usage_events)")}
        self.assertNotIn("prompt", columns)
        self.assertNotIn("response", columns)

    def test_no_model_event_cannot_claim_tokens_or_cost(self):
        with self.assertRaisesRegex(ValueError, "no-model"):
            OUTCOMES.record_model_usage(
                self.db, "usage-1", "job_evaluation", "hard_filters", "none", 1, 0, at=AT
            )
        result = OUTCOMES.record_model_usage(
            self.db, "usage-2", "job_evaluation", "hard_filters", "none", 0, 0, at=AT
        )
        self.assertEqual(result["total_tokens"], 0)
        with self.assertRaisesRegex(ValueError, "no-model"):
            OUTCOMES.record_model_usage(
                self.db, "usage-3", "job_evaluation", "cache_lookup", "none", 0, 0,
                cache_hit=True, at=AT,
            )

    def test_model_operation_rejects_free_text(self):
        with self.assertRaisesRegex(ValueError, "snake_case"):
            OUTCOMES.record_model_usage(
                self.db, "usage-1", "other", "review candidate personal details", "low_cost",
                1, 1, model_name="model-small", at=AT,
            )

    def test_cached_tokens_cannot_exceed_input_tokens(self):
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            OUTCOMES.record_model_usage(
                self.db, "usage-1", "job_ingestion", "extract", "low_cost",
                10, 2, 11, "model-small", at=AT,
            )

    def test_user_time_is_bounded_and_source_labeled(self):
        with self.assertRaisesRegex(ValueError, "between 1"):
            OUTCOMES.record_user_time(
                self.db, "time-1", "job_review", 0, "timer", application_id="app-1", at=AT
            )
        result = OUTCOMES.record_user_time(
            self.db, "time-2", "job_review", 300, "user_reported", application_id="app-1", at=AT
        )
        self.assertEqual(result["duration_seconds"], 300)

    def test_report_tracks_funnel_dimensions_usage_and_small_sample_warning(self):
        self.move("recruiter_response")
        self.move("screening_call")
        self.move("interview")
        OUTCOMES.record_outcome(
            self.db, "outcome-1", "app-1", "interview", AT.isoformat(),
            "user_confirmation", "user", at=AT,
        )
        OUTCOMES.record_model_usage(
            self.db, "usage-1", "job_evaluation", "review", "low_cost",
            100, 20, 0, "model-small", application_id="app-1", job_id="job-1", at=AT,
        )
        OUTCOMES.record_user_time(
            self.db, "time-1", "job_review", 600, "timer", application_id="app-1", at=AT
        )
        report = OUTCOMES.report(self.db)
        self.assertEqual(report["funnel"]["applications_submitted"], 1)
        self.assertEqual(report["funnel"]["interviews"], 1)
        self.assertEqual(report["metrics"]["interview_rate"]["rate"], 1.0)
        self.assertEqual(report["metrics"]["model_tokens_per_interview"], 120.0)
        self.assertEqual(report["metrics"]["user_minutes_per_interview"], 10.0)
        self.assertEqual(report["dimensions"]["resume_version"][0]["value"], "resume-1")
        self.assertEqual(report["statistical_caution"]["status"], "insufficient_sample")
        self.assertIn("do not infer causation", report["statistical_caution"]["message"])


if __name__ == "__main__":
    unittest.main()
