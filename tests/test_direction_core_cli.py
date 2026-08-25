import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "direction_core.py"


class DirectionCoreCliTests(unittest.TestCase):
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
                "directions": 0, "directions_by_status": {},
                "plans": 0, "plans_by_status": {},
            })
            self.assertEqual(os.stat(database).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
