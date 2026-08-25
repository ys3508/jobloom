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
    spec = importlib.util.spec_from_file_location(f"cover_letter_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


COVERS = load_script("cover_letter_core")
APPLICATIONS = load_script("application_core")
ANSWERS = load_script("answer_library")
RESUMES = load_script("resume_core")
ARCHIVE = load_script("archive_core")
PRE_SUBMIT = load_script("pre_submit_core")
CANDIDATE = load_script("candidate_core")
AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class CoverLetterCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.cover_store = self.root / "cover-letters"
        self.cover_source = self.root / "cover-letter.txt"
        self.cover_source.write_text("Verified Python experience for Example Corp.\n", encoding="utf-8")
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        APPLICATIONS.initialize(self.db)
        ANSWERS.initialize(self.db)
        RESUMES.initialize(self.db)
        COVERS.initialize(self.db)
        ARCHIVE.initialize(self.db)
        PRE_SUBMIT.initialize(self.db)
        self.addCleanup(self.db.close)
        self.candidate_path, self.manifest_path = self.make_candidate_and_manifest()

    def make_candidate_and_manifest(self):
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
        return candidate_path, manifest_path

    def add_application_in_materials(self, application_id="app-1", job_id="job-1"):
        card = {
            "job_id": job_id, "canonical_url": f"https://example.com/jobs/{job_id}",
            "employer": "Example Corp", "title": "Backend Engineer" if job_id == "job-1" else "Platform Engineer",
            "location": "New York, NY",
            "country": "US", "status": "open", "source": "company_site", "ats": "greenhouse",
        }
        APPLICATIONS.ingest_job(self.db, card, at=AT)
        APPLICATIONS.create_application(self.db, application_id, job_id, "precision", "approved_queue", AT)
        APPLICATIONS.transition(self.db, application_id, "pending_analysis", "system", "analysis", at=AT)
        APPLICATIONS.transition(self.db, application_id, "precision_recommended", "system", "match", at=AT)
        APPLICATIONS.transition(self.db, application_id, "approved", "user", "approved", at=AT)
        APPLICATIONS.transition(
            self.db, application_id, "materials_in_progress", "system", "materials", at=AT
        )

    def register_cover(self, version_id="cover-1", kind="reusable_template", parent=None,
                       application_id=None):
        return COVERS.register_version(
            self.db, self.cover_store, self.cover_source, version_id, kind, parent, application_id, AT
        )

    def approve_cover(self, version_id="cover-1", actor="user"):
        return COVERS.approve_version(
            self.db, version_id, self.candidate_path, self.manifest_path, actor, AT
        )

    def add_approved_resume(self):
        source = self.root / "resume.txt"
        source.write_text("Verified Python experience\n", encoding="utf-8")
        RESUMES.register_version(
            self.db, self.root / "resumes", source, "resume-1", "master_source", "general", at=AT
        )
        RESUMES.approve_version(
            self.db, "resume-1", self.candidate_path, self.manifest_path, "user", AT
        )

    def test_registration_creates_read_only_snapshot_and_rejects_duplicate(self):
        result = self.register_cover()
        snapshot = Path(result["snapshot_path"])
        self.assertEqual(snapshot.read_text(encoding="utf-8"), self.cover_source.read_text(encoding="utf-8"))
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o400)
        self.assertEqual(RESUMES.file_sha256(snapshot), result["file_sha256"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.register_cover()

    def test_application_specific_version_is_scoped_to_material_preparation(self):
        with self.assertRaisesRegex(ValueError, "application not found"):
            self.register_cover(kind="application_specific", application_id="app-missing")
        self.add_application_in_materials()
        result = self.register_cover(kind="application_specific", application_id="app-1")
        self.assertEqual(result["job_id"], "job-1")
        self.approve_cover()
        self.add_application_in_materials("app-2", "job-2")
        with self.assertRaisesRegex(ValueError, "another application"):
            COVERS.bind_version(self.db, "app-2", "cover-1", at=AT)

    def test_parent_must_be_approved_and_approval_requires_user(self):
        self.register_cover("cover-parent")
        with self.assertRaisesRegex(ValueError, "must be approved"):
            self.register_cover("cover-child", parent="cover-parent")
        with self.assertRaisesRegex(ValueError, "user actor"):
            self.approve_cover("cover-parent", "system")
        self.approve_cover("cover-parent")
        result = self.register_cover("cover-child", parent="cover-parent")
        self.assertEqual(result["status"], "draft")

    def test_binding_requires_approved_version_and_records_prepared_usage(self):
        self.add_application_in_materials()
        self.register_cover()
        with self.assertRaisesRegex(ValueError, "approved"):
            COVERS.bind_version(self.db, "app-1", "cover-1", at=AT)
        self.approve_cover()
        result = COVERS.bind_version(self.db, "app-1", "cover-1", at=AT)
        self.assertEqual(result["cover_letter_version_id"], "cover-1")
        usage = self.db.execute(
            "SELECT use_type, file_sha256 FROM cover_letter_usage WHERE application_id='app-1'"
        ).fetchone()
        self.assertEqual(usage["use_type"], "prepared")
        self.assertEqual(usage["file_sha256"], result["file_sha256"])

    def test_material_lock_captures_hash_and_tampering_blocks_use(self):
        self.add_application_in_materials()
        self.add_approved_resume()
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        result = self.register_cover()
        self.approve_cover()
        COVERS.bind_version(self.db, "app-1", "cover-1", at=AT)
        lock = RESUMES.lock_materials(self.db, "app-1", lock_id="lock-1", at=AT)
        self.assertEqual(lock["cover_letter_file_sha256"], result["file_sha256"])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM cover_letter_usage WHERE application_id='app-1' AND use_type='locked'"
        ).fetchone()[0], 1)
        snapshot = Path(result["snapshot_path"])
        snapshot.chmod(0o600)
        snapshot.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "ready", at=AT)

    def test_revocation_invalidates_material_lock(self):
        self.add_application_in_materials()
        self.add_approved_resume()
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        self.register_cover()
        self.approve_cover()
        COVERS.bind_version(self.db, "app-1", "cover-1", at=AT)
        RESUMES.lock_materials(self.db, "app-1", lock_id="lock-1", at=AT)
        with self.assertRaisesRegex(ValueError, "user actor"):
            COVERS.revoke_version(self.db, "cover-1", "system", "stale", AT)
        COVERS.revoke_version(self.db, "cover-1", "user", "stale", AT)
        row = self.db.execute(
            "SELECT invalidation_reason FROM material_locks WHERE lock_id='lock-1'"
        ).fetchone()
        self.assertEqual(row["invalidation_reason"], "cover_letter_revoked")

    def test_scope_change_after_lock_blocks_ready_state(self):
        self.add_application_in_materials()
        self.add_approved_resume()
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        self.register_cover(kind="application_specific", application_id="app-1")
        self.approve_cover()
        COVERS.bind_version(self.db, "app-1", "cover-1", at=AT)
        RESUMES.lock_materials(self.db, "app-1", lock_id="lock-1", at=AT)
        self.db.execute("UPDATE cover_letter_versions SET job_id=NULL WHERE version_id='cover-1'")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "scoped to another application"):
            APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "ready", at=AT)

    def test_submitted_cover_letter_is_physically_archived_with_exact_hash(self):
        self.add_application_in_materials()
        self.add_approved_resume()
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        cover = self.register_cover(kind="application_specific", application_id="app-1")
        self.approve_cover()
        COVERS.bind_version(self.db, "app-1", "cover-1", at=AT)
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
            self.db, "app-1", "work_auth", "Authorized to work?", True,
            "answer", "answer-auth", "active", "normal", AT,
        )
        PRE_SUBMIT.register_inventory(
            self.db, "inventory-1", "app-1", "https://example.com/jobs/job-1/apply",
            "Example Corp", "Backend Engineer", True, ["work_auth"],
            ["standard_attestation"], [], [
                {"kind": "resume", "version_id": "resume-1"},
                {"kind": "cover_letter", "version_id": "cover-1"},
            ], AT,
        )
        APPLICATIONS.release_lease(
            self.db, "app-1", "worker-1", "waiting_for_submission_approval", "filled", at=AT
        )
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-1", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=7)).isoformat(),
            "scope": {"country": "US", "application_id": "app-1"},
        })
        review = PRE_SUBMIT.create_review(
            self.db, "review-1", "inventory-1", "auth-1",
            {"country": "US", "application_id": "app-1"}, AT,
        )
        self.assertEqual(review["summary"]["materials"]["cover_letter_sha256"], cover["file_sha256"])
        PRE_SUBMIT.approve_review(self.db, "review-1", "user", review["summary_sha256"], AT)
        APPLICATIONS.transition(
            self.db, "app-1", "pre_submit_ready", "system", "checked",
            {"pre_submit_review_id": "review-1"}, AT,
        )
        APPLICATIONS.transition(
            self.db, "app-1", "submitting", "system", "submit_requested",
            {"authorization_id": "auth-1", "approved_queue": True}, AT,
        )
        APPLICATIONS.record_evidence(
            self.db, "evidence-1", "app-1", "confirmation_id", confirmation_id="CONF-1", at=AT
        )
        APPLICATIONS.transition(self.db, "app-1", "submitted", "system", "confirmed", at=AT)
        result = ARCHIVE.create_archive(
            self.db, self.root / "archive", "app-1", "archive-1", AT
        )
        archived = Path(result["archive_path"]) / "cover_letter_used.txt"
        self.assertEqual(ARCHIVE.file_sha256(archived), cover["file_sha256"])
        self.assertTrue((Path(result["archive_path"]) / "cover_letter_claims_manifest.json").is_file())
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM cover_letter_usage WHERE application_id='app-1' AND use_type='submitted'"
        ).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
