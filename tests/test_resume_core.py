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
    spec = importlib.util.spec_from_file_location(f"resume_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


RESUMES = load_script("resume_core")
APPLICATIONS = load_script("application_core")
AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class ResumeCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.store = self.root / "store"
        self.source = self.root / "resume.txt"
        self.source.write_text("Python and verified work history\n", encoding="utf-8")
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        APPLICATIONS.initialize(self.db)
        RESUMES.initialize(self.db)
        self.addCleanup(self.db.close)

    def candidate_and_manifest(self, *, locked=False, fact_strength="direct", claim_strength="direct", exact=True):
        fact = {
            "id": "fact-1", "type": "skill", "value": "Python", "status": "locked" if locked else "confirmed",
            "locked": locked, "evidence_strength": fact_strength,
        }
        candidate = {"schema_version": "0.2.0", "profile_id": "candidate-1", "facts": [fact]}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        candidate_path = self.root / "candidate.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        manifest = {"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-1", "claim_text": "Python", "fact_ids": ["fact-1"],
            "evidence_strength": claim_strength, "exact_locked_value_preserved": exact,
        }]}
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return candidate_path, manifest_path

    def register(self, version_id="resume-1", kind="master_source", direction="general", parent=None):
        return RESUMES.register_version(
            self.db, self.store, self.source, version_id, kind, direction, parent, at=AT
        )

    def approve(self, version_id="resume-1", **manifest_options):
        candidate, manifest = self.candidate_and_manifest(**manifest_options)
        return RESUMES.approve_version(self.db, version_id, candidate, manifest, "user", AT)

    def add_application_in_materials(self, application_id="app-1"):
        card = {
            "job_id": "job-1", "canonical_url": "https://example.com/job/1", "employer": "Example",
            "title": "Engineer", "location": "New York", "status": "open",
        }
        APPLICATIONS.ingest_job(self.db, card, at=AT)
        APPLICATIONS.create_application(self.db, application_id, "job-1", at=AT)
        APPLICATIONS.transition(self.db, application_id, "pending_analysis", "system", "analysis", at=AT)
        APPLICATIONS.transition(self.db, application_id, "broad_recommended", "system", "match", at=AT)
        APPLICATIONS.transition(self.db, application_id, "approved", "user", "approved", at=AT)
        APPLICATIONS.transition(self.db, application_id, "materials_in_progress", "system", "materials", at=AT)

    def test_register_creates_read_only_immutable_snapshot(self):
        result = self.register()
        snapshot = Path(result["snapshot_path"])
        self.assertEqual(snapshot.read_text(encoding="utf-8"), self.source.read_text(encoding="utf-8"))
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o400)
        self.assertEqual(RESUMES.file_sha256(snapshot), result["file_sha256"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.register()

    def test_non_master_requires_approved_parent(self):
        self.register("master-1")
        with self.assertRaisesRegex(ValueError, "must be approved"):
            self.register("direction-1", "direction", "backend", "master-1")
        self.approve("master-1")
        result = self.register("direction-1", "direction", "backend", "master-1")
        self.assertEqual(result["status"], "draft")

    def test_approval_requires_user_and_valid_candidate_hash(self):
        self.register()
        candidate, manifest = self.candidate_and_manifest()
        with self.assertRaisesRegex(ValueError, "user actor"):
            RESUMES.approve_version(self.db, "resume-1", candidate, manifest, "system", AT)
        value = json.loads(candidate.read_text(encoding="utf-8"))
        value["profile_id"] = "tampered"
        candidate.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "content hash"):
            RESUMES.approve_version(self.db, "resume-1", candidate, manifest, "user", AT)

    def test_manifest_cannot_inflate_evidence(self):
        self.register()
        candidate, manifest = self.candidate_and_manifest(fact_strength="transferable", claim_strength="direct")
        with self.assertRaisesRegex(ValueError, "inflates"):
            RESUMES.approve_version(self.db, "resume-1", candidate, manifest, "user", AT)

    def test_locked_fact_requires_exact_preservation_attestation(self):
        self.register()
        candidate, manifest = self.candidate_and_manifest(locked=True, exact=False)
        with self.assertRaisesRegex(ValueError, "preserve exact locked"):
            RESUMES.approve_version(self.db, "resume-1", candidate, manifest, "user", AT)

    def test_approved_selection_rechecks_snapshot_hash(self):
        result = self.register()
        self.approve()
        selected = RESUMES.select_approved(self.db, "general")
        self.assertEqual(selected["version_id"], "resume-1")
        snapshot = Path(result["snapshot_path"])
        snapshot.chmod(0o600)
        snapshot.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            RESUMES.select_approved(self.db, "general")

    def test_approved_selection_rechecks_claims_manifest_hash(self):
        self.register()
        self.approve()
        row = self.db.execute("SELECT claims_manifest_path FROM resume_versions WHERE version_id='resume-1'").fetchone()
        manifest = Path(row["claims_manifest_path"])
        manifest.chmod(0o600)
        manifest.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "claims manifest snapshot hash mismatch"):
            RESUMES.select_approved(self.db, "general")

    def test_application_requires_lock_before_ready_or_acquire(self):
        self.register()
        self.approve()
        self.add_application_in_materials()
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        with self.assertRaisesRegex(ValueError, "no active material lock"):
            APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "ready", at=AT)
        lock = RESUMES.lock_materials(self.db, "app-1", lock_id="lock-1", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "ready", at=AT)
        acquired = APPLICATIONS.acquire_next(self.db, "worker-1", at=AT)
        self.assertEqual(acquired["application_id"], "app-1")
        self.assertEqual(lock["resume_file_sha256"], self.db.execute(
            "SELECT file_sha256 FROM resume_usage WHERE application_id='app-1' AND use_type='locked'"
        ).fetchone()[0])

    def test_rebinding_invalidates_old_material_lock(self):
        self.register("resume-1")
        self.approve("resume-1")
        self.source.write_text("Second verified resume\n", encoding="utf-8")
        self.register("resume-2")
        self.approve("resume-2")
        self.add_application_in_materials()
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        RESUMES.lock_materials(self.db, "app-1", lock_id="lock-1", at=AT)
        RESUMES.bind_version(self.db, "app-1", "resume-2", at=AT)
        old = self.db.execute("SELECT * FROM material_locks WHERE lock_id='lock-1'").fetchone()
        self.assertIsNotNone(old["invalidated_at"])
        self.assertEqual(old["invalidation_reason"], "resume_rebound")

    def test_revocation_invalidates_active_material_lock(self):
        self.register()
        self.approve()
        self.add_application_in_materials()
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        RESUMES.lock_materials(self.db, "app-1", lock_id="lock-1", at=AT)
        with self.assertRaisesRegex(ValueError, "user actor"):
            RESUMES.revoke_version(self.db, "resume-1", "system", "stale", AT)
        RESUMES.revoke_version(self.db, "resume-1", "user", "stale", AT)
        lock = self.db.execute("SELECT invalidation_reason FROM material_locks WHERE lock_id='lock-1'").fetchone()
        self.assertEqual(lock["invalidation_reason"], "resume_revoked")


if __name__ == "__main__":
    unittest.main()
