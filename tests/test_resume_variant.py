"""ResumeVariant: one resume, several directions, its own weights.

A SearchPortfolio allocates the user's applications across every direction they pursue. A
variant allocates one resume's own share across the subset it can honestly answer. The two
weightings are separate objects over the same directions and need not agree.

Every covered direction still gets its own immutable ResumeVersion of the same bytes, so
selection, binding, locking and readiness stay per-direction and unchanged.
"""

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


RESUMES = load_script("resume_core")
DIRECTIONS = load_script("direction_core")
CANDIDATES = load_script("candidate_core")
APPLICATIONS = load_script("application_core")
MVP = load_script("mvp_core")
AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)

COVERED = ("research-data", "clinical-data", "biostatistics")
WEIGHTS = {"research-data": 60, "clinical-data": 25, "biostatistics": 15}


class VariantFixture:
    """Shared setup. Not a TestCase, so the suites below do not run each other's cases."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        os.chmod(self.root, 0o700)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        for name in ("application_core", "resume_core", "direction_core", "candidate_core",
                     "answer_library", "archive_core", "outcome_core", "pre_submit_core",
                     "fill_core", "cover_letter_core"):
            load_script(name).initialize(self.db)
        self.addCleanup(self.db.close)
        self.candidate_path, self.manifest_path = self.write_candidate()
        CANDIDATES.register_snapshot(self.db, self.root / "cands", self.candidate_path, "user", AT)
        self.hashes = self.approve_portfolio()
        self.resume = self.root / "resume-a.docx"
        self.resume.write_text("one page of healthcare research data evidence", encoding="utf-8")

    # --- fixture ---------------------------------------------------------

    def write_candidate(self):
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {"country": "US", "authorized_now": True,
                                   "sponsorship_now": False, "sponsorship_future": False,
                                   "employer_action_required": False, "confirmed": True},
            "search": {},
            "facts": [{"id": "fact-name", "type": "identity", "value": "Verified Candidate",
                       "status": "locked", "locked": True, "evidence_strength": "direct"}],
        }
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        candidate_path = self.root / "candidate.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        manifest_path = self.root / "claims.json"
        manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [
            {"claim_id": "claim-1", "claim_text": "Verified Candidate", "fact_ids": ["fact-name"],
             "evidence_strength": "direct", "exact_locked_value_preserved": True}]}),
            encoding="utf-8")
        return candidate_path, manifest_path

    def approve_portfolio(self):
        hashes = {}
        for direction_id in COVERED + ("consulting",):
            registered = DIRECTIONS.register_direction(self.db, {
                "schema_version": "0.1.0", "direction_id": direction_id, "name": direction_id,
                "role_family": f"analytics.{direction_id.replace('-', '_')}",
                "target_titles": [f"{direction_id} Analyst"], "positive_keywords": [],
                "negative_keywords": [], "precision_keywords": [],
                "criteria": {"industries": ["healthcare"]}, "parent_direction_id": None,
            }, at=AT)
            hashes[direction_id] = registered["profile_sha256"]
        # The portfolio spreads applications across four directions; the variant below
        # covers only three of them, with different weights.
        portfolio = DIRECTIONS.register_portfolio(self.db, {
            "schema_version": "0.1.0", "portfolio_id": "pf-1", "name": "P",
            "allocations": [
                {"direction_id": "research-data", "profile_sha256": hashes["research-data"],
                 "weight_percent": 40},
                {"direction_id": "clinical-data", "profile_sha256": hashes["clinical-data"],
                 "weight_percent": 25},
                {"direction_id": "biostatistics", "profile_sha256": hashes["biostatistics"],
                 "weight_percent": 15},
                {"direction_id": "consulting", "profile_sha256": hashes["consulting"],
                 "weight_percent": 20},
            ]}, AT)
        DIRECTIONS.approve_portfolio(self.db, "pf-1", "user", portfolio["portfolio_sha256"], AT)
        return hashes

    def coverage(self, weights=None):
        weights = weights or WEIGHTS
        return [{"direction_id": direction_id, "profile_sha256": self.hashes[direction_id],
                 "weight_percent": weight} for direction_id, weight in weights.items()]

    def register(self, variant_id="resume-a", coverage=None):
        return RESUMES.register_variant(
            self.db, variant_id, "Resume A - healthcare research data",
            coverage if coverage is not None else self.coverage(),
            self.resume, self.root / "store", AT)

    def approve(self, variant_id="resume-a", registered=None, actor="user"):
        registered = registered or self.register(variant_id)
        return RESUMES.approve_variant(
            self.db, variant_id, self.candidate_path, self.manifest_path, actor,
            registered["coverage_sha256"], AT)


class ResumeVariantTests(VariantFixture, unittest.TestCase):
    # --- registration ----------------------------------------------------

    def test_one_file_becomes_one_immutable_version_per_covered_direction(self):
        registered = self.register()
        self.assertEqual(registered["member_version_ids"],
                         ["resume-a--biostatistics", "resume-a--clinical-data",
                          "resume-a--research-data"])
        rows = self.db.execute(
            "SELECT direction, file_sha256, snapshot_path, source_mode, variant_id "
            "FROM resume_versions ORDER BY direction").fetchall()
        self.assertEqual([row["direction"] for row in rows], sorted(COVERED))
        self.assertEqual(len({row["file_sha256"] for row in rows}), 1,
                         "every member is the same bytes")
        self.assertEqual(len({row["snapshot_path"] for row in rows}), 3,
                         "each direction keeps its own physical artifact")
        for row in rows:
            self.assertEqual(row["source_mode"], "user_provided")
            self.assertEqual(row["variant_id"], "resume-a")

    def test_variant_weights_are_independent_of_portfolio_weights(self):
        self.register()
        variant = dict(self.db.execute(
            "SELECT direction_id, weight_percent FROM resume_variant_directions "
            "WHERE direction_id='research-data'").fetchone())
        portfolio = dict(self.db.execute(
            "SELECT direction_id, weight_percent FROM search_portfolio_directions "
            "WHERE direction_id='research-data'").fetchone())
        self.assertEqual(variant["weight_percent"], 60)
        self.assertEqual(portfolio["weight_percent"], 40)

    def test_weights_must_total_one_hundred(self):
        with self.assertRaisesRegex(ValueError, "total 100"):
            self.register(coverage=self.coverage({"research-data": 60, "clinical-data": 25}))

    def test_an_unapproved_direction_cannot_be_covered(self):
        registered = DIRECTIONS.register_direction(self.db, {
            "schema_version": "0.1.0", "direction_id": "draft-only", "name": "D",
            "role_family": "analytics.draft", "target_titles": ["Draft Analyst"],
            "positive_keywords": [], "negative_keywords": [], "precision_keywords": [],
            "criteria": {}, "parent_direction_id": None}, at=AT)
        coverage = [{"direction_id": "draft-only",
                     "profile_sha256": registered["profile_sha256"], "weight_percent": 100}]
        with self.assertRaisesRegex(ValueError, "not user-approved"):
            self.register(coverage=coverage)

    def test_a_moved_direction_profile_is_refused(self):
        coverage = self.coverage()
        coverage[0]["profile_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "profile has moved"):
            self.register(coverage=coverage)

    def test_a_failed_registration_leaves_no_partial_members(self):
        coverage = self.coverage()
        coverage[-1]["profile_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.register(coverage=coverage)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM resume_versions").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM resume_variants").fetchone()[0], 0)
        self.assertFalse((self.root / "store" / "resume-a--research-data").exists())

    # --- approval --------------------------------------------------------

    def test_one_user_approval_covers_every_member(self):
        result = self.approve()
        self.assertEqual(result["status"], "approved")
        statuses = {row["version_id"]: row["status"] for row in self.db.execute(
            "SELECT version_id, status FROM resume_versions")}
        self.assertEqual(set(statuses.values()), {"approved"})
        self.assertEqual(len(statuses), 3)

    def test_approval_requires_the_user_actor(self):
        with self.assertRaisesRegex(ValueError, "user actor"):
            self.approve(actor="system")

    def test_approval_requires_the_reviewed_coverage_hash(self):
        self.register()
        with self.assertRaisesRegex(ValueError, "coverage hash"):
            RESUMES.approve_variant(self.db, "resume-a", self.candidate_path,
                                    self.manifest_path, "user", "0" * 64, AT)

    def test_a_member_failure_approves_nothing(self):
        registered = self.register()
        # A claim that resolves to no fact fails validation for every member alike.
        broken = self.root / "broken-claims.json"
        broken.write_text(json.dumps({"schema_version": "0.1.0", "claims": [
            {"claim_id": "claim-x", "claim_text": "Invented", "fact_ids": ["fact-missing"],
             "evidence_strength": "direct"}]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            RESUMES.approve_variant(self.db, "resume-a", self.candidate_path, broken, "user",
                                    registered["coverage_sha256"], AT)
        statuses = {row["status"] for row in self.db.execute(
            "SELECT status FROM resume_versions")}
        self.assertEqual(statuses, {"draft"})
        self.assertEqual(self.db.execute(
            "SELECT status FROM resume_variants").fetchone()["status"], "draft")

    def test_every_covered_direction_can_select_the_resume(self):
        self.approve()
        for direction_id in COVERED:
            selected = RESUMES.select_approved(self.db, direction_id, "direction")
            self.assertIsNotNone(selected, direction_id)
            self.assertEqual(selected["version_id"], f"resume-a--{direction_id}")

    def test_an_approved_variant_counts_once_per_covered_direction_in_readiness(self):
        self.approve()
        self.assertEqual(MVP._approved_direction_resumes(self.db), 3)

    # --- revocation ------------------------------------------------------

    def test_revoking_the_variant_revokes_every_member(self):
        self.approve()
        result = RESUMES.revoke_variant(self.db, "resume-a", "user", "superseded", AT)
        self.assertEqual(len(result["revoked_version_ids"]), 3)
        self.assertEqual({row["status"] for row in self.db.execute(
            "SELECT status FROM resume_versions")}, {"revoked"})
        self.assertIsNone(RESUMES.select_approved(self.db, "research-data", "direction"))

    def test_revocation_requires_the_user_actor(self):
        self.approve()
        with self.assertRaisesRegex(ValueError, "user actor"):
            RESUMES.revoke_variant(self.db, "resume-a", "system", "superseded", AT)


class VariantAllocationTests(VariantFixture, unittest.TestCase):
    def route(self, job_id, direction_id, record_id=None):
        job = {"job_id": job_id, "canonical_url": f"https://example.com/{job_id}",
               "employer": "Example Health", "title": f"{direction_id} Analyst", "country": "US",
               "location": "NY", "work_arrangement": "hybrid", "employment_type": "full_time",
               "salary": None, "status": "open", "sponsorship": "supports",
               "sponsorship_statements": [], "required_certifications": [],
               "preferred_certifications": [], "required_skills": [], "preferred_skills": [],
               "summary": "healthcare research", "responsibilities": [],
               "compensation_structure": [], "seniority": "unknown", "experience": None,
               "requirements_reviewed": True}
        candidate = json.loads(self.candidate_path.read_text())
        return DIRECTIONS.record_routing(
            self.db, record_id or f"rec-{job_id}", direction_id, job, candidate, AT)

    def test_allocation_uses_the_variant_weights_over_its_own_directions(self):
        self.approve()
        self.route("job-1", "research-data")
        self.route("job-2", "consulting")
        status = DIRECTIONS.variant_allocation_status(self.db, "resume-a", window_size=10)
        by_direction = {item["direction_id"]: item for item in status["targets"]}
        self.assertEqual(sorted(by_direction), sorted(COVERED),
                         "a direction the resume does not cover is not the variant's business")
        self.assertEqual(by_direction["research-data"]["weight_percent"], 60)
        self.assertEqual(by_direction["research-data"]["reviewed_count"], 1)
        self.assertEqual(status["pool_size"], 1)

    def test_portfolio_allocation_still_sees_every_direction(self):
        self.approve()
        self.route("job-1", "research-data")
        self.route("job-2", "consulting")
        status = DIRECTIONS.portfolio_allocation_status(self.db, window_size=10)
        self.assertEqual(len(status["targets"]), 4)
        self.assertEqual(status["pool_size"], 2)

    def test_allocation_requires_an_approved_variant(self):
        self.register()
        with self.assertRaisesRegex(ValueError, "approved resume variant"):
            DIRECTIONS.variant_allocation_status(self.db, "resume-a")


if __name__ == "__main__":
    unittest.main()
