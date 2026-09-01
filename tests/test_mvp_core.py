import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "mvp_core.py"


def load_script():
    spec = importlib.util.spec_from_file_location("mvp_core_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


MVP = load_script()
from tests.pdf_fixture import synthetic_pdf

AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class MvpCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "private"
        self.database = self.root / "jobloom.db"
        self.root.mkdir(mode=0o700)
        self.db = sqlite3.connect(self.database)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.db.close)

    def test_initializes_every_component_with_private_permissions(self):
        result = MVP.initialize(self.db, self.root, self.database)
        self.assertEqual(result["component_count"], 10)
        self.assertEqual(os.stat(self.database).st_mode & 0o777, 0o600)
        for name in MVP.PRIVATE_DIRECTORIES:
            self.assertEqual(os.stat(self.root / name).st_mode & 0o777, 0o700)
        tables = MVP._tables(self.db)
        self.assertFalse(MVP.REQUIRED_TABLES - tables)

    def test_empty_backend_is_implementation_ready_but_not_operational(self):
        MVP.initialize(self.db, self.root, self.database)
        report = MVP.readiness(self.db, self.root, AT)
        self.assertTrue(report["implementation"]["ready"])
        self.assertFalse(report["onboarding"]["ready"])
        self.assertFalse(report["live_job_evaluation"]["ready"])
        self.assertFalse(report["fill_queue"]["ready"])
        self.assertIn(
            "active_user_registered_candidate_required", report["onboarding"]["blockers"]
        )
        self.assertIn("real_user_reviewed_job_required", report["live_job_evaluation"]["blockers"])
        self.assertFalse(report["safety"]["submission_authorized"])
        self.assertTrue(report["safety"]["requires_real_user_owned_inputs_and_approvals"])

    def test_status_contains_counts_not_private_values(self):
        MVP.initialize(self.db, self.root, self.database)
        result = MVP.status(self.db)
        self.assertTrue(result["initialized"])
        self.assertEqual(result["candidate"]["snapshots"], 0)
        self.assertEqual(result["answers"]["entries"], 0)
        self.assertEqual(result["applications"]["applications"], 0)

    def test_uninitialized_database_reports_missing_tables(self):
        result = MVP.status(self.db)
        self.assertFalse(result["initialized"])
        self.assertIn("candidate_snapshots", result["missing_tables"])

    def test_user_provided_direction_resume_satisfies_onboarding_readiness(self):
        MVP.initialize(self.db, self.root, self.database)

        candidate = {
            "schema_version": "0.2.0",
            "profile_id": "candidate-1",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": True, "employer_action_required": False,
                "confirmed": True,
            },
            "search": {},
            "facts": [{
                "id": "fact-1", "type": "skill", "value": "Python",
                "status": "confirmed", "locked": False, "evidence_strength": "direct",
            }],
        }
        candidate["content_sha256"] = MVP.resume_core.canonical_hash(candidate)
        candidate_path = self.root / "candidate.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        MVP.candidate_core.register_snapshot(
            self.db, self.root / "candidates", candidate_path, "user", AT
        )

        profile = {
            "schema_version": "0.1.0", "direction_id": "backend",
            "name": "Backend Engineering", "role_family": "engineering.backend",
            "target_titles": ["Backend Engineer"], "positive_keywords": ["Python"],
            "negative_keywords": [], "precision_keywords": [],
            "criteria": {"countries": ["US"]}, "parent_direction_id": None,
        }
        direction = MVP.direction_core.register_direction(self.db, profile, AT)
        portfolio = MVP.direction_core.register_portfolio(self.db, {
            "schema_version": "0.1.0", "portfolio_id": "portfolio-1",
            "name": "Primary portfolio", "allocations": [{
                "direction_id": "backend", "profile_sha256": direction["profile_sha256"],
                "weight_percent": 100,
            }],
        }, AT)
        MVP.direction_core.approve_portfolio(
            self.db, "portfolio-1", "user", portfolio["portfolio_sha256"], AT
        )

        resume_path = self.root / "backend-resume.pdf"
        resume_path.write_bytes(synthetic_pdf(["Python"]))
        MVP.resume_core.register_version(
            self.db, self.root / "resumes", resume_path, "backend-upload-1",
            "direction", "backend", source_mode="user_provided", at=AT,
        )
        manifest_path = self.root / "claims-manifest.json"
        manifest_path.write_text(json.dumps({
            "schema_version": "0.1.0", "claims": [{
                "claim_id": "claim-1", "claim_text": "Python", "fact_ids": ["fact-1"],
                "evidence_strength": "direct", "exact_locked_value_preserved": False,
            }],
        }), encoding="utf-8")
        MVP.resume_core.approve_version(
            self.db, "backend-upload-1", candidate_path, manifest_path, "user", AT
        )

        self.assertEqual(MVP._approved_direction_resumes(self.db), 1)
        report = MVP.readiness(self.db, self.root, AT)
        self.assertTrue(report["onboarding"]["ready"])
        self.assertNotIn(
            "user_approved_direction_resume_required", report["onboarding"]["blockers"]
        )


if __name__ == "__main__":
    unittest.main()
