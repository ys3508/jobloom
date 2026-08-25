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
    spec = importlib.util.spec_from_file_location(f"archive_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


ARCHIVE = load_script("archive_core")
APPLICATIONS = load_script("application_core")
ANSWERS = load_script("answer_library")
RESUMES = load_script("resume_core")
OUTCOMES = load_script("outcome_core")
PRE_SUBMIT = load_script("pre_submit_core")
AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class ArchiveCoreTests(unittest.TestCase):
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
        ARCHIVE.initialize(self.db)
        OUTCOMES.initialize(self.db)
        PRE_SUBMIT.initialize(self.db)
        self.addCleanup(self.db.close)
        self.prepare_application()

    def prepare_application(self):
        resume_source = self.root / "resume.txt"
        resume_source.write_text("Verified Python experience\n", encoding="utf-8")
        RESUMES.register_version(
            self.db, self.root / "resumes", resume_source, "resume-1", "master_source", "general", at=AT
        )
        fact = {
            "id": "fact-1", "type": "skill", "value": "Python", "status": "confirmed",
            "locked": False, "evidence_strength": "direct",
        }
        candidate = {"schema_version": "0.2.0", "profile_id": "candidate-1", "facts": [fact]}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        candidate_path = self.root / "candidate.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        manifest_path = self.root / "claims.json"
        manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-1", "claim_text": "Python", "fact_ids": ["fact-1"],
            "evidence_strength": "direct", "exact_locked_value_preserved": False,
        }]}), encoding="utf-8")
        RESUMES.approve_version(self.db, "resume-1", candidate_path, manifest_path, "user", AT)

        card = {
            "job_id": "job-1", "canonical_url": "https://example.com/jobs/1", "employer": "Example Corp",
            "title": "Backend Engineer", "location": "New York, NY", "work_arrangement": "hybrid",
            "status": "open", "source": "company_site", "ats": "greenhouse",
        }
        APPLICATIONS.ingest_job(self.db, card, at=AT)
        APPLICATIONS.create_application(
            self.db, "app-1", "job-1", "precision", "approved_queue", AT
        )
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
            "canonical_meaning": "Authorized to work now", "answer": True,
            "answer_type": "time_sensitive_fact", "source_type": "user_confirmed",
            "confirmation_status": "confirmed", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=30)).isoformat(), "validity_class": "event_driven",
            "auto_fill_allowed": True, "auto_submit_allowed": True,
        })
        ARCHIVE.record_field(
            self.db, "app-1", "work_auth", "Are you authorized to work?", True,
            "answer", "answer-auth", "active", "normal", AT,
        )
        ARCHIVE.record_field(
            self.db, "app-1", "home_address", "Full address", "123 Private Street",
            "fact", "fact-address", "locked", "address", AT,
        )
        ARCHIVE.record_field(
            self.db, "app-1", "birth_date", "Date of birth", "2000-01-01",
            "fact", "fact-birth-date", "locked", "date_of_birth", AT,
        )
        PRE_SUBMIT.register_inventory(
            self.db, "inventory-1", "app-1", "https://example.com/jobs/1/apply",
            "Example Corp", "Backend Engineer", True,
            ["work_auth", "home_address", "birth_date"], ["standard_attestation"], [],
            [{"kind": "resume", "version_id": "resume-1"}], AT,
        )

    def submit(self, evidence_reference=None):
        APPLICATIONS.release_lease(
            self.db, "app-1", "worker-1", "waiting_for_submission_approval", "filled", at=AT
        )
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-1", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=7)).isoformat(),
            "scope": {"country": "US", "queue_id": "queue-1"},
        })
        review = PRE_SUBMIT.create_review(
            self.db, "review-1", "inventory-1", "auth-1",
            {"country": "US", "queue_id": "queue-1"}, AT,
        )
        PRE_SUBMIT.approve_review(self.db, "review-1", "user", review["summary_sha256"], AT)
        APPLICATIONS.transition(
            self.db, "app-1", "pre_submit_ready", "system", "checked",
            {"pre_submit_review_id": "review-1"}, AT,
        )
        APPLICATIONS.transition(
            self.db, "app-1", "submitting", "system", "submit_requested",
            {"authorization_id": "auth-1", "approved_queue": True}, AT,
        )
        if evidence_reference:
            APPLICATIONS.record_evidence(
                self.db, "evidence-1", "app-1", "success_page", reference=str(evidence_reference), at=AT
            )
        else:
            APPLICATIONS.record_evidence(
                self.db, "evidence-1", "app-1", "confirmation_id", confirmation_id="CONF-123", at=AT
            )
        APPLICATIONS.transition(self.db, "app-1", "submitted", "system", "confirmed", at=AT)

    def test_archive_requires_confirmed_submission(self):
        with self.assertRaisesRegex(ValueError, "submitted resume usage"):
            ARCHIVE.create_archive(self.db, self.root / "archive", "app-1", "archive-1", AT)

    def test_archive_copies_exact_materials_and_redacts_answers(self):
        self.submit()
        result = ARCHIVE.create_archive(self.db, self.root / "archive", "app-1", "archive-1", AT)
        archive_path = Path(result["archive_path"])
        answers = json.loads((archive_path / "answers_snapshot.json").read_text(encoding="utf-8"))
        fields = {field["field_id"]: field for field in answers["fields"]}
        self.assertIs(fields["work_auth"]["value"], True)
        self.assertEqual(fields["home_address"]["value"], "[REDACTED]")
        self.assertNotIn("birth_date", fields)
        self.assertNotIn("2000-01-01", (archive_path / "answers_snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(result["redaction"], {"included": 1, "redacted": 1, "omitted": 1})
        resume_copy = archive_path / "resume_used.txt"
        source = Path(self.db.execute(
            "SELECT snapshot_path FROM resume_versions WHERE version_id='resume-1'"
        ).fetchone()[0])
        self.assertEqual(ARCHIVE.file_sha256(resume_copy), ARCHIVE.file_sha256(source))
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o400 for path in archive_path.iterdir()))

    def test_archive_is_idempotent_and_verifiable(self):
        self.submit()
        first = ARCHIVE.create_archive(self.db, self.root / "archive", "app-1", "archive-1", AT)
        second = ARCHIVE.create_archive(self.db, self.root / "archive", "app-1", "different-id", AT)
        self.assertEqual(second["archive_id"], first["archive_id"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM submission_archives").fetchone()[0], 1)

    def test_archive_verification_detects_tampering(self):
        self.submit()
        result = ARCHIVE.create_archive(self.db, self.root / "archive", "app-1", "archive-1", AT)
        job_card = Path(result["archive_path"]) / "job_card.json"
        job_card.chmod(0o600)
        job_card.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            ARCHIVE.verify_archive(self.db, "archive-1", AT)

    def test_archive_verification_rejects_untracked_files(self):
        self.submit()
        result = ARCHIVE.create_archive(self.db, self.root / "archive", "app-1", "archive-1", AT)
        extra = Path(result["archive_path"]) / "extra.txt"
        extra.write_text("not tracked", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "untracked"):
            ARCHIVE.verify_archive(self.db, "archive-1", AT)

    def test_local_evidence_file_is_physically_copied(self):
        evidence = self.root / "success.png"
        evidence.write_bytes(b"fake-png-evidence")
        self.submit(evidence)
        result = ARCHIVE.create_archive(self.db, self.root / "archive", "app-1", "archive-1", AT)
        copied = Path(result["archive_path"]) / "confirmation.png"
        self.assertEqual(copied.read_bytes(), evidence.read_bytes())

    def test_sensitive_field_cannot_be_misclassified_as_normal(self):
        with self.assertRaisesRegex(ValueError, "cannot be classified as normal"):
            ARCHIVE.record_field(
                self.db, "app-1", "passport_number", "Passport number", "secret",
                "fact", "fact-passport", "locked", "normal", AT,
            )

    def test_archive_event_metadata_never_contains_field_values(self):
        metadata = " ".join(row[0] for row in self.db.execute("SELECT metadata_json FROM archive_events"))
        self.assertNotIn("123 Private Street", metadata)
        self.assertNotIn("2000-01-01", metadata)
        self.assertNotIn("birth_date", metadata)

    def test_answer_value_must_match_active_library_source(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            ARCHIVE.record_field(
                self.db, "app-1", "work_auth_2", "Are you authorized?", False,
                "answer", "answer-auth", "active", "normal", AT,
            )

    def test_tracker_source_is_derived_from_backend_state(self):
        self.submit()
        OUTCOMES.record_model_usage(
            self.db, "usage-1", "form_filling", "fill_known_fields", "low_cost",
            100, 20, 0, "model-small", application_id="app-1", job_id="job-1", at=AT,
        )
        self.assertEqual(ARCHIVE.status(self.db)["pending_submissions"], 1)
        ARCHIVE.create_archive(self.db, self.root / "archive", "app-1", "archive-1", AT)
        self.assertEqual(ARCHIVE.status(self.db)["pending_submissions"], 0)
        source = ARCHIVE.tracker_source(self.db)
        self.assertEqual(source["row_count"], 1)
        row = source["applications"][0]
        self.assertEqual(row["employer"], "Example Corp")
        self.assertEqual(row["resume_version"], "resume-1")
        self.assertEqual(row["current_status"], "submitted")
        self.assertEqual(row["model_usage"], 120)
        self.assertNotIn("work_auth", json.dumps(source))


if __name__ == "__main__":
    unittest.main()
