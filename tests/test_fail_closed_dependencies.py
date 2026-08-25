"""Negative tests: a missing cross-module safety table must block, never silently skip.

Every case initializes the complete backend, builds real state, then drops one
required table immediately before the guarded call. Each guarded call must fail
closed with a RuntimeError naming the missing table.
"""

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
    spec = importlib.util.spec_from_file_location(f"failclosed_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


APPLICATIONS = load_script("application_core")
ANSWERS = load_script("answer_library")
RESUMES = load_script("resume_core")
CANDIDATE = load_script("candidate_core")
COVERS = load_script("cover_letter_core")
ARCHIVE = load_script("archive_core")
OUTCOMES = load_script("outcome_core")
PRE_SUBMIT = load_script("pre_submit_core")
FILL = load_script("fill_core")
DIRECTIONS = load_script("direction_core")
AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class FailClosedDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        for module in (APPLICATIONS, ANSWERS, RESUMES, CANDIDATE,
                       COVERS, ARCHIVE, OUTCOMES, PRE_SUBMIT, FILL):
            module.initialize(self.db)
        self.addCleanup(self.db.close)
        self.prepare()

    def drop(self, table):
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.execute(f"DROP TABLE {table}")
        self.db.commit()

    def assert_fails_closed(self, table, call):
        self.drop(table)
        with self.assertRaises(RuntimeError) as caught:
            call()
        self.assertIn(table, str(caught.exception))

    def prepare(self):
        resume_source = self.root / "resume.txt"
        resume_source.write_text("Verified Python experience\n", encoding="utf-8")
        RESUMES.register_version(
            self.db, self.root / "resumes", resume_source, "resume-1",
            "master_source", "general", at=AT,
        )
        facts = [
            {"id": "fact-1", "type": "skill", "value": "Python", "status": "confirmed",
             "locked": False, "evidence_strength": "direct"},
            {"id": "fact-name", "type": "identity", "value": "Verified Candidate",
             "status": "locked", "locked": True, "evidence_strength": "direct"},
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
        self.candidate_path = self.root / "candidate.json"
        self.candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        self.manifest_path = self.root / "claims.json"
        self.manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-1", "claim_text": "Python", "fact_ids": ["fact-1"],
            "evidence_strength": "direct", "exact_locked_value_preserved": False,
        }]}), encoding="utf-8")
        CANDIDATE.register_snapshot(
            self.db, self.root / "candidates", self.candidate_path, "user", AT
        )
        RESUMES.approve_version(
            self.db, "resume-1", self.candidate_path, self.manifest_path, "user", AT
        )
        card = {
            "job_id": "job-1", "canonical_url": "https://example.com/jobs/1",
            "employer": "Example Corp", "title": "Backend Engineer",
            "location": "New York, NY", "country": "US", "status": "open",
            "source": "company_site", "ats": "greenhouse",
        }
        APPLICATIONS.ingest_job(self.db, card, at=AT)
        APPLICATIONS.create_application(self.db, "app-1", "job-1", "broad", "approved_queue", AT)
        for state, actor, reason in (
            ("pending_analysis", "system", "analysis"),
            ("broad_recommended", "system", "match"),
            ("approved", "user", "approved"),
            ("materials_in_progress", "system", "materials"),
        ):
            APPLICATIONS.transition(self.db, "app-1", state, actor, reason, at=AT)
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        RESUMES.lock_materials(self.db, "app-1", lock_id="lock-1", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "ready", at=AT)
        APPLICATIONS.acquire_next(self.db, "worker-1", at=AT)
        ANSWERS.add_answer(self.db, {
            "answer_id": "answer-auth", "canonical_id": "work_authorized_now",
            "canonical_meaning": "Authorized to work now", "answer": True,
            "answer_type": "time_sensitive_fact", "source_type": "user_confirmed",
            "confirmation_status": "confirmed", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=30)).isoformat(),
            "validity_class": "per_application", "scope": {"application_id": "app-1"},
            "auto_fill_allowed": True, "auto_submit_allowed": True,
        })

    # --- archive_core -----------------------------------------------------

    def test_recording_a_fact_field_requires_the_candidate_fact_registry(self):
        self.assert_fails_closed("candidate_facts", lambda: ARCHIVE.record_field(
            self.db, "app-1", "candidate_name", "Full name", "Verified Candidate",
            "fact", "fact-name", "locked", "normal", AT,
        ))

    def test_recording_an_answer_field_requires_the_answer_library(self):
        self.assert_fails_closed("answers", lambda: ARCHIVE.record_field(
            self.db, "app-1", "work_auth", "Authorized to work?", True,
            "answer", "answer-auth", "active", "normal", AT,
        ))

    # --- candidate_core ---------------------------------------------------

    def _new_candidate(self):
        value = json.loads(self.candidate_path.read_text(encoding="utf-8"))
        value["facts"].append({
            "id": "fact-sql", "type": "skill", "value": "SQL", "status": "confirmed",
            "locked": False, "evidence_strength": "direct",
        })
        value.pop("content_sha256")
        value["content_sha256"] = RESUMES.canonical_hash(value)
        path = self.root / "candidate-2.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def _register_new_snapshot(self):
        CANDIDATE.register_snapshot(
            self.db, self.root / "candidates", self._new_candidate(), "user", AT
        )

    def test_snapshot_registration_requires_material_locks(self):
        self.assert_fails_closed("material_locks", self._register_new_snapshot)

    def test_snapshot_registration_requires_the_answer_library(self):
        self.assert_fails_closed("answers", self._register_new_snapshot)

    def test_snapshot_registration_requires_pre_submit_reviews(self):
        self.assert_fails_closed("pre_submit_reviews", self._register_new_snapshot)

    # --- resume_core ------------------------------------------------------

    def test_resume_approval_requires_candidate_snapshots(self):
        source = self.root / "second.txt"
        source.write_text("Second resume\n", encoding="utf-8")
        RESUMES.register_version(
            self.db, self.root / "resumes", source, "resume-2",
            "master_source", "general", at=AT,
        )
        self.assert_fails_closed("candidate_snapshots", lambda: RESUMES.approve_version(
            self.db, "resume-2", self.candidate_path, self.manifest_path, "user", AT
        ))

    # --- fill_core --------------------------------------------------------

    def test_fill_fact_resolution_requires_candidate_snapshots(self):
        self.assert_fails_closed("candidate_snapshots", lambda: FILL._candidate_facts(
            self.db, "app-1", self.candidate_path
        ))

    # --- application_core -------------------------------------------------

    def test_transition_requires_pre_submit_reviews_for_review_invalidation(self):
        self.assert_fails_closed("pre_submit_reviews", lambda: APPLICATIONS.release_lease(
            self.db, "app-1", "worker-1", "waiting_for_user_answer", "new_question", at=AT
        ))

    # --- direction_core ---------------------------------------------------

    def test_direction_revocation_requires_material_locks(self):
        DIRECTIONS.initialize(self.db)
        registered = DIRECTIONS.register_direction(self.db, {
            "schema_version": "0.1.0", "direction_id": "backend", "name": "Backend",
            "role_family": "engineering.backend", "target_titles": ["Backend Engineer"],
            "positive_keywords": [], "negative_keywords": [], "precision_keywords": [],
            "criteria": {}, "parent_direction_id": None,
        }, at=AT)
        DIRECTIONS.approve_direction(
            self.db, "backend", "user", registered["profile_sha256"], AT
        )
        self.assert_fails_closed("material_locks", lambda: DIRECTIONS.revoke_direction(
            self.db, "backend", "user", "no_longer_targeted", at=AT
        ))


if __name__ == "__main__":
    unittest.main()
