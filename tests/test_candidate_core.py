import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"candidate_core_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


CANDIDATES = load_script("candidate_core")
PRE_SUBMIT = load_script("pre_submit_core")
APPLICATIONS = load_script("application_core")
ANSWERS = load_script("answer_library")
RESUMES = load_script("resume_core")
ARCHIVE = load_script("archive_core")
AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class CandidateCoreTests(unittest.TestCase):
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
        PRE_SUBMIT.initialize(self.db)
        CANDIDATES.initialize(self.db)
        self.addCleanup(self.db.close)
        self.candidate_path = self.write_candidate("Verified Candidate")
        self.manifest_path = self.write_manifest()

    def write_candidate(self, value):
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": False, "employer_action_required": False, "confirmed": True,
            },
            "search": {},
            "facts": [{
                "id": "fact-name", "type": "identity", "value": value,
                "status": "locked", "locked": True, "evidence_strength": "direct",
            }],
        }
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        path = self.root / "candidate.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        return path

    def write_manifest(self):
        path = self.root / "claims.json"
        path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-name", "claim_text": "Verified Candidate",
            "fact_ids": ["fact-name"], "evidence_strength": "direct",
            "exact_locked_value_preserved": True,
        }]}), encoding="utf-8")
        return path

    def register_candidate(self):
        return CANDIDATES.register_snapshot(
            self.db, self.root / "candidates", self.candidate_path, "user", AT
        )

    def add_resume(self):
        source = self.root / "resume.txt"
        source.write_text("Verified Candidate\n", encoding="utf-8")
        RESUMES.register_version(
            self.db, self.root / "resumes", source, "resume-1", "master_source", "general", at=AT
        )
        return source

    def add_application_in_filling(self):
        card = {
            "job_id": "job-1", "canonical_url": "https://example.com/jobs/1",
            "employer": "Example", "title": "Engineer", "location": "New York",
            "country": "US", "status": "open",
        }
        APPLICATIONS.ingest_job(self.db, card, at=AT)
        APPLICATIONS.create_application(self.db, "app-1", "job-1", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "pending_analysis", "system", "analysis", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "broad_recommended", "system", "match", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "approved", "user", "approved", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "materials_in_progress", "system", "materials", at=AT)
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        RESUMES.lock_materials(self.db, "app-1", lock_id="lock-1", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "ready", at=AT)
        APPLICATIONS.acquire_next(self.db, "worker-1", at=AT)

    def test_registration_requires_user_and_creates_read_only_snapshot(self):
        with self.assertRaisesRegex(ValueError, "user actor"):
            CANDIDATES.register_snapshot(
                self.db, self.root / "candidates", self.candidate_path, "system", AT
            )
        result = self.register_candidate()
        snapshot = Path(result["snapshot_path"])
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o400)
        self.assertEqual(RESUMES.file_sha256(snapshot), result["file_sha256"])
        self.assertEqual(result["fact_count"], 1)
        repeated = self.register_candidate()
        self.assertEqual(repeated["content_sha256"], result["content_sha256"])

    def test_duplicate_or_inconsistent_fact_lock_is_rejected(self):
        candidate = json.loads(self.candidate_path.read_text(encoding="utf-8"))
        candidate["facts"][0]["locked"] = False
        candidate.pop("content_sha256")
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        self.candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.register_candidate()

    def test_credentials_are_rejected_from_candidate_profile(self):
        candidate = json.loads(self.candidate_path.read_text(encoding="utf-8"))
        candidate["api_key"] = "must-not-be-stored"
        candidate.pop("content_sha256")
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        self.candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "credentials and secrets"):
            self.register_candidate()

    def test_resume_approval_requires_active_registered_candidate(self):
        self.add_resume()
        with self.assertRaisesRegex(ValueError, "active user-registered"):
            RESUMES.approve_version(
                self.db, "resume-1", self.candidate_path, self.manifest_path, "user", AT
            )
        self.register_candidate()
        approved = RESUMES.approve_version(
            self.db, "resume-1", self.candidate_path, self.manifest_path, "user", AT
        )
        self.assertEqual(approved["status"], "approved")

    def test_application_field_must_match_registered_locked_fact(self):
        self.register_candidate()
        self.add_resume()
        RESUMES.approve_version(
            self.db, "resume-1", self.candidate_path, self.manifest_path, "user", AT
        )
        self.add_application_in_filling()
        with self.assertRaisesRegex(ValueError, "does not match"):
            ARCHIVE.record_field(
                self.db, "app-1", "candidate_name", "Full name", "Invented Name",
                "fact", "fact-name", "locked", "normal", AT,
            )
        recorded = ARCHIVE.record_field(
            self.db, "app-1", "candidate_name", "Full name", "Verified Candidate",
            "fact", "fact-name", "locked", "normal", AT,
        )
        self.assertEqual(recorded["status"], "recorded")
        with self.assertRaisesRegex(ValueError, "locked active CandidateFact"):
            ARCHIVE.record_field(
                self.db, "app-1", "other", "Other", "value",
                "fact", "fact-missing", "locked", "normal", AT,
            )

    def test_new_snapshot_invalidates_locks_and_dependent_answers(self):
        self.register_candidate()
        self.add_resume()
        RESUMES.approve_version(
            self.db, "resume-1", self.candidate_path, self.manifest_path, "user", AT
        )
        self.add_application_in_filling()
        ANSWERS.add_answer(self.db, {
            "answer_id": "answer-name", "canonical_id": "candidate_name",
            "canonical_meaning": "Candidate name", "answer": "Verified Candidate",
            "answer_type": "stable_fact", "source_type": "verified_candidate_fact",
            "source_ref": "fact-name", "confirmation_status": "confirmed",
            "confirmed_at": AT.isoformat(), "validity_class": "stable", "scope": {},
            "auto_fill_allowed": True, "auto_submit_allowed": True,
            "dependent_fact_ids": ["fact-name"],
        })
        self.write_candidate("Updated Candidate")
        changed = self.register_candidate()
        self.assertEqual(changed["changed_fact_count"], 1)
        self.assertEqual(self.db.execute(
            "SELECT invalidation_reason FROM material_locks WHERE lock_id='lock-1'"
        ).fetchone()[0], "candidate_snapshot_changed")
        self.assertEqual(self.db.execute(
            "SELECT status FROM answers WHERE answer_id='answer-name'"
        ).fetchone()[0], "stale")


if __name__ == "__main__":
    unittest.main()
