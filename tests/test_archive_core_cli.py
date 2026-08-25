import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
APPLICATION_SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "application_core.py"
ARCHIVE_SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "archive_core.py"


class ArchiveCoreCliTests(unittest.TestCase):
    def test_init_status_and_empty_tracker_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "jobloom.db"
            subprocess.run(
                [sys.executable, str(APPLICATION_SCRIPT), "--db", str(database), "init"],
                check=True, capture_output=True, text=True,
            )
            base = [sys.executable, str(ARCHIVE_SCRIPT), "--db", str(database), "--archive-root", str(root / "archive")]
            initialized = subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            tracker_path = root / "tracker.json"
            tracker = subprocess.run(
                base + ["tracker-source", "--output", str(tracker_path)], check=True, capture_output=True, text=True
            )
            status = subprocess.run(base + ["status"], check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(initialized.stdout)["status"], "initialized")
            self.assertEqual(json.loads(tracker.stdout)["row_count"], 0)
            self.assertEqual(json.loads(tracker_path.read_text(encoding="utf-8"))["applications"], [])
            self.assertEqual(json.loads(status.stdout), {
                "archives": 0, "by_status": {}, "pending_submissions": 0, "recorded_fields": 0,
            })
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
