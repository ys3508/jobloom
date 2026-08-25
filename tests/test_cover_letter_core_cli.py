import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "cover_letter_core.py"


class CoverLetterCoreCliTests(unittest.TestCase):
    def test_init_and_empty_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "jobloom.db"
            initialized = subprocess.run(
                ["python3", str(SCRIPT), "--db", str(database), "--store", str(root / "covers"), "init"],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(json.loads(initialized.stdout)["status"], "initialized")
            status = subprocess.run(
                ["python3", str(SCRIPT), "--db", str(database), "status"],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(json.loads(status.stdout), {"versions": 0, "by_status": {}})
            self.assertEqual(os.stat(database).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
