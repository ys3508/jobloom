import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "resume_core.py"


from tests.pdf_fixture import synthetic_pdf

class ResumeCoreCliTests(unittest.TestCase):
    def test_register_and_status_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "jobloom.db"
            store = root / "resumes"
            source = root / "resume.pdf"
            source.write_bytes(synthetic_pdf(["Verified resume"]))
            base = [sys.executable, str(SCRIPT), "--db", str(database), "--store", str(store)]
            initialized = subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            registered = subprocess.run(base + [
                "register", "--file", str(source), "--version-id", "master-1",
                "--kind", "master_source", "--direction", "unassigned",
            ], check=True, capture_output=True, text=True)
            status = subprocess.run(base + ["status"], check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(initialized.stdout)["status"], "initialized")
            self.assertEqual(json.loads(registered.stdout)["status"], "draft")
            self.assertEqual(json.loads(status.stdout), {
                "versions": 1, "by_status": {"draft": 1}, "active_material_locks": 0,
            })
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
