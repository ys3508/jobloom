import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "application_core.py"


class ApplicationCoreCliTests(unittest.TestCase):
    def test_cli_ingest_create_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "jobloom.db"
            card = root / "job-card.json"
            card.write_text(json.dumps({
                "job_id": "job-cli", "canonical_url": "https://example.com/jobs/cli",
                "employer": "Example", "title": "Analyst", "location": "Remote",
                "status": "open", "description_sha256": "cli-description-hash",
                "requisition_id": "REQ-CLI",
            }), encoding="utf-8")
            base = [sys.executable, str(SCRIPT), "--db", str(database)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            inserted = subprocess.run(
                base + ["ingest-job", "--card", str(card)], check=True, capture_output=True, text=True
            )
            created = subprocess.run(base + [
                "create-application", "--application-id", "app-cli", "--job-id", "job-cli",
                "--category", "broad",
            ], check=True, capture_output=True, text=True)
            status = subprocess.run(base + ["status"], check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(inserted.stdout)["decision"], "inserted")
            self.assertEqual(json.loads(created.stdout)["decision"], "created")
            self.assertEqual(json.loads(status.stdout), {
                "jobs": 1, "applications": 1, "by_state": {"discovered": 1},
            })
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
