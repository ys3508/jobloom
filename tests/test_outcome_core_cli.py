import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
APPLICATION_SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "application_core.py"
RESUME_SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "resume_core.py"
OUTCOME_SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "outcome_core.py"


class OutcomeCoreCliTests(unittest.TestCase):
    def test_init_status_and_empty_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "jobloom.db"
            for script in (APPLICATION_SCRIPT, RESUME_SCRIPT):
                subprocess.run(
                    [sys.executable, str(script), "--db", str(database), "init"],
                    check=True, capture_output=True, text=True,
                )
            base = [sys.executable, str(OUTCOME_SCRIPT), "--db", str(database)]
            initialized = subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            report_path = root / "outcomes.json"
            report = subprocess.run(
                base + ["report", "--output", str(report_path)], check=True, capture_output=True, text=True
            )
            status = subprocess.run(base + ["status"], check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(initialized.stdout)["status"], "initialized")
            self.assertEqual(json.loads(report.stdout)["submitted"], 0)
            value = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(value["funnel"]["jobs_discovered"], 0)
            self.assertEqual(value["metrics"]["interview_rate"]["rate"], None)
            self.assertEqual(json.loads(status.stdout), {
                "outcomes": 0, "model_usage_events": 0, "user_time_events": 0,
            })
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)
            self.assertEqual(report_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
