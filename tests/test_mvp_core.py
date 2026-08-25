import importlib.util
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


if __name__ == "__main__":
    unittest.main()
