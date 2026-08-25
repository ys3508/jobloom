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
                "portfolios": 0, "portfolios_by_status": {},
                "plans": 0, "plans_by_status": {},
            })
            self.assertEqual(os.stat(database).st_mode & 0o777, 0o600)

    def test_register_and_approve_weighted_portfolio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "jobloom.db"
            direction_path = root / "direction.json"
            direction_path.write_text(json.dumps({
                "schema_version": "0.1.0", "direction_id": "healthcare-data",
                "name": "Healthcare Data", "role_family": "data.healthcare",
                "target_titles": ["Healthcare Data Analyst"],
                "positive_keywords": ["healthcare data"], "negative_keywords": [],
                "precision_keywords": ["clinical data"],
                "criteria": {"countries": ["US"]}, "parent_direction_id": None,
            }), encoding="utf-8")
            registered_direction = subprocess.run(
                ["python3", str(SCRIPT), "--db", str(database), "register-direction",
                 "--input", str(direction_path)],
                check=True, capture_output=True, text=True,
            )
            direction = json.loads(registered_direction.stdout)
            portfolio_path = root / "portfolio.json"
            portfolio_path.write_text(json.dumps({
                "schema_version": "0.1.0", "portfolio_id": "portfolio-1",
                "name": "Primary portfolio", "allocations": [{
                    "direction_id": "healthcare-data",
                    "profile_sha256": direction["profile_sha256"],
                    "weight_percent": 100,
                }],
            }), encoding="utf-8")
            registered_portfolio = subprocess.run(
                ["python3", str(SCRIPT), "--db", str(database), "register-portfolio",
                 "--input", str(portfolio_path)],
                check=True, capture_output=True, text=True,
            )
            portfolio = json.loads(registered_portfolio.stdout)
            approved = subprocess.run(
                ["python3", str(SCRIPT), "--db", str(database), "approve-portfolio",
                 "--portfolio-id", "portfolio-1", "--actor", "user",
                 "--portfolio-sha256", portfolio["portfolio_sha256"]],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(json.loads(approved.stdout)["status"], "approved")
            status = subprocess.run(
                ["python3", str(SCRIPT), "--db", str(database), "status"],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(json.loads(status.stdout)["portfolios_by_status"], {"approved": 1})


if __name__ == "__main__":
    unittest.main()
