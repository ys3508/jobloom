import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "mvp_core.py"


class MvpCoreCliTests(unittest.TestCase):
    def test_init_status_and_readiness_output(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "private"
            database = private / "jobloom.db"
            initialized = subprocess.run(
                ["python3", str(SCRIPT), "--db", str(database),
                 "--private-root", str(private), "init"],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(json.loads(initialized.stdout)["status"], "initialized")
            status = subprocess.run(
                ["python3", str(SCRIPT), "--db", str(database),
                 "--private-root", str(private), "status"],
                check=True, capture_output=True, text=True,
            )
            self.assertTrue(json.loads(status.stdout)["initialized"])
            output = private / "readiness.json"
            readiness = subprocess.run(
                ["python3", str(SCRIPT), "--db", str(database),
                 "--private-root", str(private), "readiness", "--output", str(output)],
                check=True, capture_output=True, text=True,
            )
            self.assertTrue(json.loads(readiness.stdout)["implementation_ready"])
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(report["onboarding"]["ready"])
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(database).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
