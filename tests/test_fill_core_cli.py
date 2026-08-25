import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "fill_core.py"


class FillCoreCliTests(unittest.TestCase):
    def test_init_and_empty_status(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobloom.db"
            initialized = subprocess.run(
                ["python3", str(SCRIPT), "--db", str(database), "init"],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(json.loads(initialized.stdout)["status"], "initialized")
            status = subprocess.run(
                ["python3", str(SCRIPT), "--db", str(database), "status"],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(json.loads(status.stdout), {
                "sessions": 0, "by_status": {}, "checkpoints": 0,
            })
            self.assertEqual(os.stat(database).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
