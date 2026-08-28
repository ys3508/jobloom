import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "answer_library.py"


class AnswerLibraryCliTests(unittest.TestCase):
    def test_cli_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "jobloom.db"
            entry_path = root / "entry.json"
            authorization_path = root / "authorization.json"
            context_path = root / "context.json"
            confirmed = datetime.now(timezone.utc).replace(microsecond=0)
            expires = confirmed + timedelta(days=7)
            entry_path.write_text(json.dumps({
                "answer_id": "answer-cli", "canonical_id": "work_authorized_now",
                "canonical_meaning": "Currently authorized to work", "answer": True,
                "answer_type": "time_sensitive_fact", "source_type": "user_confirmed",
                "confirmation_status": "confirmed", "confirmed_at": confirmed.isoformat(),
                "expires_at": expires.isoformat(), "validity_class": "event_driven",
                "scope": {"country": "US", "application_id": "app-cli"}, "auto_fill_allowed": True,
                "auto_submit_allowed": False,
            }), encoding="utf-8")
            authorization_path.write_text(json.dumps({
                "authorization_id": "auth-cli", "confirmed_at": confirmed.isoformat(),
                "expires_at": expires.isoformat(), "scope": {"country": "US", "queue_id": "queue-cli"},
            }), encoding="utf-8")
            context_path.write_text(
                json.dumps({"country": "US", "queue_id": "queue-cli", "application_id": "app-cli"}),
                encoding="utf-8",
            )

            base = [sys.executable, str(SCRIPT), "--db", str(database)]
            subprocess.run(base + ["init"], check=True, capture_output=True, text=True)
            subprocess.run(base + ["add-answer", "--entry", str(entry_path)], check=True, capture_output=True, text=True)
            subprocess.run(base + [
                "add-form", "--canonical-id", "work_authorized_now", "--question", "Authorized to work now?",
            ], check=True, capture_output=True, text=True)
            subprocess.run(base + ["add-authorization", "--entry", str(authorization_path)], check=True, capture_output=True, text=True)
            completed = subprocess.run(base + [
                "match", "--question", "Authorized to work now?", "--context", str(context_path),
                "--authorization-id", "auth-cli",
            ], check=True, capture_output=True, text=True)
            result = json.loads(completed.stdout)
            self.assertEqual(result["decision"], "use")
            self.assertTrue(result["auto_fill_ready"])
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
