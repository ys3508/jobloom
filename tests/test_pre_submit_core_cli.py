import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
APPLICATION_SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "application_core.py"
PRE_SUBMIT_SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "pre_submit_core.py"


class PreSubmitCoreCliTests(unittest.TestCase):
    def test_init_and_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "jobloom.db"
            subprocess.run(
                [sys.executable, str(APPLICATION_SCRIPT), "--db", str(database), "init"],
                check=True, capture_output=True, text=True,
            )
            base = [sys.executable, str(PRE_SUBMIT_SCRIPT), "--db", str(database)]
            initialized = subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            status = subprocess.run(base + ["status"], check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(initialized.stdout)["status"], "initialized")
            self.assertEqual(json.loads(status.stdout), {"inventories": 0, "reviews": 0, "by_status": {}})
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
