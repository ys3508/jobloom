"""Phase 3: persisted routing records and rolling portfolio allocation.

Allocation runs strictly after routing and only orders jobs that already passed it,
so a portfolio weight can never rescue a hard-filter failure.
"""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"allocation_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


DIRECTIONS = load_script("direction_core")
APPLICATIONS = load_script("application_core")
RESUMES = load_script("resume_core")
AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)

WEIGHTS = [("consulting", 35), ("clinical", 30), ("pharma", 20), ("biostats", 10), ("stretch", 5)]
TITLES = {
    "consulting": "Life Sciences Consultant", "clinical": "Clinical Data Analyst",
    "pharma": "Commercial Insights Analyst", "biostats": "Research Biostatistician",
    "stretch": "Healthcare Data Scientist",
}


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        APPLICATIONS.initialize(self.db)
        RESUMES.initialize(self.db)
        DIRECTIONS.initialize(self.db)
        self.addCleanup(self.db.close)
        self.allocations = []
        for direction_id, weight in WEIGHTS:
            registered = DIRECTIONS.register_direction(self.db, {
                "schema_version": "0.1.0", "direction_id": direction_id, "name": direction_id,
                "role_family": f"family.{direction_id}", "target_titles": [TITLES[direction_id]],
                "positive_keywords": [], "negative_keywords": [], "precision_keywords": [],
                "criteria": {}, "parent_direction_id": None,
            }, at=AT)
            self.allocations.append({"direction_id": direction_id,
                                     "profile_sha256": registered["profile_sha256"],
                                     "weight_percent": weight})
        registered = DIRECTIONS.register_portfolio(self.db, {
            "schema_version": "0.1.0", "portfolio_id": "portfolio-1",
            "name": "Primary", "allocations": self.allocations,
        }, AT)
        DIRECTIONS.approve_portfolio(
            self.db, "portfolio-1", "user", registered["portfolio_sha256"], AT)

    def candidate(self):
        return {"work_authorization": {"country": "US", "authorized_now": True,
                                       "sponsorship_now": False, "sponsorship_future": False,
                                       "employer_action_required": False, "confirmed": True},
                "search": {}, "facts": [], "certifications": []}

    def job(self, job_id="job-1", direction_id="consulting", **updates):
        card = {
            "job_id": job_id, "canonical_url": f"https://example.com/jobs/{job_id}",
            "employer": "Example Health",
            "title": TITLES.get(direction_id, "Life Sciences Consultant"), "country": "US",
            "location": "New York, NY", "work_arrangement": "hybrid",
            "employment_type": "full_time", "salary": None, "status": "open",
            "sponsorship": "supports", "sponsorship_statements": [],
            "required_certifications": [], "preferred_certifications": [], "required_skills": [],
            "preferred_skills": [], "summary": None, "responsibilities": [],
            "compensation_structure": [], "seniority": "unknown", "experience": None,
            "requirements_reviewed": True,
        }
        card.update(updates)
        return card

    def record(self, record_id, direction_id, job_id=None, at=None, **updates):
        return DIRECTIONS.record_routing(
            self.db, record_id, direction_id,
            self.job(job_id or f"job-{record_id}", direction_id, **updates),
            self.candidate(), at or AT,
        )

    # --- persistence ----------------------------------------------------

    def test_a_routed_job_is_persisted_with_its_exact_hashes(self):
        result = self.record("rec-1", "consulting")
        self.assertEqual(result["decision"], "match")
        self.assertEqual(result["direction_id"], "consulting")
        self.assertEqual(result["portfolio_id"], "portfolio-1")
        self.assertEqual(len(result["job_card_sha256"]), 64)
        self.assertEqual(result["direction_profile_sha256"], self.allocations[0]["profile_sha256"])
        self.assertTrue(result["in_review_pool"])

    def test_recording_the_same_job_card_twice_is_idempotent(self):
        first = self.record("rec-1", "consulting")
        again = DIRECTIONS.record_routing(
            self.db, "rec-2", "consulting", self.job("job-rec-1", "consulting"),
            self.candidate(), AT)
        self.assertEqual(again["record_id"], first["record_id"])
        count = self.db.execute("SELECT COUNT(*) FROM routing_records").fetchone()[0]
        self.assertEqual(count, 1)

    def test_a_changed_job_card_invalidates_the_previous_record(self):
        self.record("rec-1", "consulting")
        updated = DIRECTIONS.record_routing(
            self.db, "rec-2", "consulting",
            self.job("job-rec-1", "consulting", summary="Now with market access work"),
            self.candidate(), AT + timedelta(hours=1))
        self.assertEqual(updated["record_id"], "rec-2")
        old = self.db.execute(
            "SELECT * FROM routing_records WHERE record_id='rec-1'").fetchone()
        self.assertEqual(old["invalidation_reason"], "job_card_changed")
        self.assertEqual(len(DIRECTIONS.review_pool(self.db)), 1)

    def test_routing_requires_an_approved_direction_inside_the_active_portfolio(self):
        DIRECTIONS.register_direction(self.db, {
            "schema_version": "0.1.0", "direction_id": "outside", "name": "Outside",
            "role_family": "family.outside", "target_titles": ["Life Sciences Consultant"],
            "positive_keywords": [], "negative_keywords": [], "precision_keywords": [],
            "criteria": {}, "parent_direction_id": None,
        }, at=AT)
        with self.assertRaisesRegex(ValueError, "approved search direction"):
            self.record("rec-1", "outside")

    def test_persisted_events_carry_counts_and_hashes_only(self):
        self.record("rec-1", "consulting", summary="commission-only role with quota")
        row = self.db.execute(
            "SELECT metadata_json FROM direction_events WHERE event_type='job_routed'").fetchone()
        metadata = json.loads(row["metadata_json"])
        self.assertEqual(set(metadata), {"job_id", "job_card_sha256",
                                         "hard_failure_count", "review_reason_count"})
        self.assertNotIn("commission", row["metadata_json"])

    # --- pool membership -------------------------------------------------

    def test_a_hard_failure_is_recorded_but_never_enters_the_pool(self):
        failed = self.record("rec-1", "consulting", title="Senior Life Sciences Consultant")
        self.assertEqual(failed["decision"], "fail")
        self.assertFalse(failed["in_review_pool"])
        self.assertIsNone(failed["entered_pool_at"])
        self.assertEqual(DIRECTIONS.review_pool(self.db), [])

    def test_a_weight_deficit_never_rescues_a_failed_job(self):
        for index in range(6):
            self.record(f"rec-c{index}", "consulting", at=AT + timedelta(minutes=index))
        self.record("rec-fail", "stretch", title="Senior Healthcare Data Scientist",
                    at=AT + timedelta(minutes=10))
        status = DIRECTIONS.portfolio_allocation_status(self.db)
        self.assertIn("stretch", status["underfilled_directions"])
        self.assertEqual([record["direction_id"] for record in DIRECTIONS.review_pool(self.db)],
                         ["consulting"] * 6)

    def test_review_decisions_join_the_pool_alongside_matches(self):
        self.record("rec-1", "clinical", at=AT)
        self.record("rec-2", "clinical", requirements_reviewed=False, at=AT + timedelta(minutes=1))
        pool = DIRECTIONS.review_pool(self.db)
        self.assertEqual([record["decision"] for record in pool], ["match", "review"])

    # --- allocation ------------------------------------------------------

    def test_deficits_come_from_persisted_history_not_a_supplied_list(self):
        for index in range(7):
            self.record(f"rec-c{index}", "consulting", at=AT + timedelta(minutes=index))
        for index in range(6):
            self.record(f"rec-h{index}", "clinical", at=AT + timedelta(minutes=10 + index))
        status = DIRECTIONS.portfolio_allocation_status(self.db)
        self.assertEqual(status["pool_size"], 13)
        self.assertEqual(status["targets"][0]["direction_id"], "pharma")
        self.assertEqual(status["targets"][0]["target_count"], 4)
        by_id = {item["direction_id"]: item for item in status["targets"]}
        self.assertEqual(by_id["consulting"]["reviewed_count"], 7)
        self.assertEqual(by_id["clinical"]["reviewed_count"], 6)

    def test_the_pool_window_slides(self):
        for index in range(25):
            self.record(f"rec-{index}", "consulting", at=AT + timedelta(minutes=index))
        self.assertEqual(len(DIRECTIONS.review_pool(self.db)), 20)
        self.assertEqual(DIRECTIONS.portfolio_allocation_status(self.db)["pool_size"], 20)
        self.assertEqual(
            DIRECTIONS.portfolio_allocation_status(self.db, window_size=5)["pool_size"], 5)

    def test_allocation_is_deterministic_for_the_same_history(self):
        for index in range(4):
            self.record(f"rec-{index}", "pharma", at=AT + timedelta(minutes=index))
        first = DIRECTIONS.portfolio_allocation_status(self.db)
        second = DIRECTIONS.portfolio_allocation_status(self.db)
        self.assertEqual(first, second)

    def test_sponsorship_investigation_is_surfaced_from_the_pool(self):
        candidate = self.candidate()
        candidate["work_authorization"]["sponsorship_future"] = True
        DIRECTIONS.record_routing(
            self.db, "rec-1", "pharma",
            self.job("job-1", "pharma", sponsorship="unknown"), candidate, AT)
        status = DIRECTIONS.portfolio_allocation_status(self.db)
        self.assertEqual(status["investigation_required_job_ids"], ["job-1"])

    def test_an_invalid_window_is_rejected(self):
        for value in (0, -1, True, "20"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    DIRECTIONS.review_pool(self.db, value)


if __name__ == "__main__":
    unittest.main()
