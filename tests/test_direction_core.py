import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"direction_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


DIRECTIONS = load_script("direction_core")
APPLICATIONS = load_script("application_core")
RESUMES = load_script("resume_core")
CANDIDATES = load_script("candidate_core")
ANSWERS = load_script("answer_library")
PRE_SUBMIT = load_script("pre_submit_core")
AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class DirectionCoreTests(unittest.TestCase):
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
        ANSWERS.initialize(self.db)
        PRE_SUBMIT.initialize(self.db)
        CANDIDATES.initialize(self.db)
        self.addCleanup(self.db.close)
        self.candidate_path, self.manifest_path = self.make_candidate()
        CANDIDATES.register_snapshot(
            self.db, self.root / "candidates", self.candidate_path, "user", AT
        )
        self.add_master_resume()
        self.add_job()

    def make_candidate(self):
        facts = [
            {"id": "fact-python", "type": "skill", "value": "Python", "keywords": ["python"],
             "status": "locked", "locked": True, "evidence_strength": "direct"},
            {"id": "fact-aws", "type": "skill", "value": "AWS", "keywords": ["aws"],
             "status": "confirmed", "locked": False, "evidence_strength": "transferable"},
            {"id": "fact-sql", "type": "skill", "value": "SQL", "keywords": ["sql"],
             "status": "confirmed", "locked": False, "evidence_strength": "direct"},
        ]
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": False, "employer_action_required": False, "confirmed": True,
            },
            "search": {
                "countries": ["US"], "locations": ["New York, NY"],
                "work_arrangements": ["hybrid"], "employment_types": ["full_time"],
                "salary_floor": 100000, "salary_currency": "USD", "excluded_employers": [],
            },
            "citizenships": [], "security_clearances": [], "certifications": [], "facts": facts,
        }
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        candidate_path = self.root / "candidate.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        manifest_path = self.root / "claims.json"
        manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-python", "claim_text": "Python", "fact_ids": ["fact-python"],
            "evidence_strength": "direct", "exact_locked_value_preserved": True,
        }]}), encoding="utf-8")
        return candidate_path, manifest_path

    def add_master_resume(self):
        source = self.root / "master.txt"
        source.write_text("Python\n", encoding="utf-8")
        RESUMES.register_version(
            self.db, self.root / "resumes", source, "master-1", "master_source", "unassigned", at=AT
        )
        RESUMES.approve_version(
            self.db, "master-1", self.candidate_path, self.manifest_path, "user", AT
        )

    def job_card(self, **updates):
        card = {
            "job_id": "job-1", "canonical_url": "https://example.com/jobs/1",
            "employer": "Example Corp", "title": "Backend Engineer", "country": "US",
            "location": "New York, NY", "work_arrangement": "hybrid",
            "employment_type": "full_time", "salary": {"currency": "USD", "min": 120000,
            "max": 150000, "unit": "YEAR"}, "status": "open", "sponsorship": "supports",
            "citizenship_required": None, "security_clearance_required": None,
            "required_certifications": [], "required_skills": ["Python"],
            "preferred_skills": ["AWS"], "already_applied": False, "high_value": False,
            "requirements_reviewed": True,
        }
        card.update(updates)
        return card

    def add_job(self, **updates):
        return APPLICATIONS.ingest_job(self.db, self.job_card(**updates), at=AT)

    def profile(self, direction_id="backend", parent=None, **updates):
        value = {
            "schema_version": "0.1.0", "direction_id": direction_id,
            "name": "Backend Engineering", "role_family": "engineering.backend",
            "target_titles": ["Backend Engineer", "Backend Developer"],
            "positive_keywords": ["Python", "SQL"], "negative_keywords": ["commission only"],
            "precision_keywords": ["distributed systems"],
            "criteria": {"countries": ["US"], "work_arrangements": ["hybrid"]},
            "parent_direction_id": parent,
        }
        value.update(updates)
        return value

    def approve_direction(self, direction_id="backend", profile=None):
        registered = DIRECTIONS.register_direction(self.db, profile or self.profile(direction_id), AT)
        return DIRECTIONS.approve_direction(
            self.db, direction_id, "user", registered["profile_sha256"], AT
        )

    def register_portfolio(self, allocations, portfolio_id="portfolio-1"):
        return DIRECTIONS.register_portfolio(self.db, {
            "schema_version": "0.1.0", "portfolio_id": portfolio_id,
            "name": "Primary search portfolio", "allocations": allocations,
        }, AT)

    def generate_and_approve_plan(self, plan_id="plan-1", direction_id="backend"):
        generated = DIRECTIONS.generate_plan(
            self.db, plan_id, direction_id, "job-1", self.candidate_path, AT
        )
        approved = DIRECTIONS.approve_plan(
            self.db, plan_id, self.candidate_path, "user", generated["plan_sha256"], AT
        )
        return generated, approved

    def add_direction_resume(self, plan_id="plan-1", version_id="direction-1"):
        source = self.root / f"{version_id}.txt"
        source.write_text("Python\n", encoding="utf-8")
        result = RESUMES.register_version(
            self.db, self.root / "resumes", source, version_id, "direction", "backend",
            "master-1", at=AT, adaptation_plan_id=plan_id,
        )
        RESUMES.approve_version(
            self.db, version_id, self.candidate_path, self.manifest_path, "user", AT
        )
        return result

    def test_direction_requires_exact_user_approval_and_approved_parent(self):
        registered = DIRECTIONS.register_direction(self.db, self.profile(), AT)
        with self.assertRaisesRegex(ValueError, "user actor"):
            DIRECTIONS.approve_direction(
                self.db, "backend", "system", registered["profile_sha256"], AT
            )
        with self.assertRaisesRegex(ValueError, "hash"):
            DIRECTIONS.approve_direction(self.db, "backend", "user", "wrong", AT)
        approved = DIRECTIONS.approve_direction(
            self.db, "backend", "user", registered["profile_sha256"], AT
        )
        self.assertEqual(approved["status"], "approved")
        with self.assertRaisesRegex(ValueError, "parent.*approved"):
            DIRECTIONS.register_direction(
                self.db, self.profile("platform", parent="missing"), AT
            )

    def test_structured_routing_separates_hard_and_soft_keywords(self):
        profile = self.profile()
        profile.update({
            "schema_version": "0.2.0",
            "hard_exclusion_keywords": ["commission-only", "unpaid internship"],
            "discovery_keywords": ["survival analysis"],
            "auxiliary_titles": ["AI Product Analyst"],
        })
        candidate = json.loads(self.candidate_path.read_text())
        soft = DIRECTIONS.route_job(
            profile, candidate, self.job_card(summary="commercial sales analytics")
        )
        self.assertNotEqual(soft["decision"], "fail")
        hard = DIRECTIONS.route_job(
            profile, candidate, self.job_card(summary="This is a commission-only role")
        )
        self.assertEqual(hard["decision"], "fail")
        self.assertEqual(hard["field_hits"]["hard_exclusion_keywords"][0]["field"], "summary")

    def test_sponsorship_and_rolling_portfolio_are_ranking_signals(self):
        profile = self.profile()
        candidate = json.loads(self.candidate_path.read_text())
        candidate["work_authorization"]["sponsorship_future"] = True
        result = DIRECTIONS.route_job(profile, candidate, self.job_card(sponsorship="supports"))
        self.assertEqual(result["sponsorship_priority"], 3)
        targets = DIRECTIONS.rolling_allocation_targets([
            {"direction_id": "consulting", "weight_percent": 35},
            {"direction_id": "clinical", "weight_percent": 30},
            {"direction_id": "pharma", "weight_percent": 20},
            {"direction_id": "biostats", "weight_percent": 10},
            {"direction_id": "stretch", "weight_percent": 5},
        ], ["consulting"] * 7 + ["clinical"] * 6)
        self.assertEqual(targets[0]["direction_id"], "pharma")
        self.assertEqual(targets[0]["target_count"], 4)

    def test_portfolio_atomically_approves_weighted_directions(self):
        backend = DIRECTIONS.register_direction(self.db, self.profile("backend"), AT)
        data = DIRECTIONS.register_direction(
            self.db, self.profile("data", name="Data", role_family="data.analytics"), AT
        )
        allocations = [
            {"direction_id": "backend", "profile_sha256": backend["profile_sha256"],
             "weight_percent": 60},
            {"direction_id": "data", "profile_sha256": data["profile_sha256"],
             "weight_percent": 40},
        ]
        registered = self.register_portfolio(allocations)
        with self.assertRaisesRegex(ValueError, "user actor"):
            DIRECTIONS.approve_portfolio(
                self.db, "portfolio-1", "system", registered["portfolio_sha256"], AT
            )
        with self.assertRaisesRegex(ValueError, "hash"):
            DIRECTIONS.approve_portfolio(self.db, "portfolio-1", "user", "wrong", AT)
        approved = DIRECTIONS.approve_portfolio(
            self.db, "portfolio-1", "user", registered["portfolio_sha256"], AT
        )
        self.assertEqual(approved["direction_count"], 2)
        self.assertEqual(approved["total_weight_percent"], 100)
        statuses = dict(self.db.execute(
            "SELECT direction_id, status FROM search_directions"
        ).fetchall())
        self.assertEqual(statuses, {"backend": "approved", "data": "approved"})

    def test_portfolio_rejects_invalid_weights_and_stale_direction_hash(self):
        backend = DIRECTIONS.register_direction(self.db, self.profile("backend"), AT)
        allocation = {"direction_id": "backend", "profile_sha256": backend["profile_sha256"],
                      "weight_percent": 99}
        with self.assertRaisesRegex(ValueError, "total exactly 100"):
            self.register_portfolio([allocation])
        allocation["weight_percent"] = 100
        allocation["profile_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash does not match"):
            self.register_portfolio([allocation])

    def test_active_portfolio_bounds_plan_routing(self):
        backend = DIRECTIONS.register_direction(self.db, self.profile("backend"), AT)
        registered = self.register_portfolio([{
            "direction_id": "backend", "profile_sha256": backend["profile_sha256"],
            "weight_percent": 100,
        }])
        DIRECTIONS.approve_portfolio(
            self.db, "portfolio-1", "user", registered["portfolio_sha256"], AT
        )
        outside = DIRECTIONS.register_direction(
            self.db, self.profile("outside", name="Outside"), AT
        )
        DIRECTIONS.approve_direction(
            self.db, "outside", "user", outside["profile_sha256"], AT
        )
        with self.assertRaisesRegex(ValueError, "outside the active approved portfolio"):
            DIRECTIONS.generate_plan(
                self.db, "plan-outside", "outside", "job-1", self.candidate_path, AT
            )

    def test_superseded_portfolio_blocks_old_direction_resume_selection(self):
        backend = DIRECTIONS.register_direction(self.db, self.profile("backend"), AT)
        portfolio = self.register_portfolio([{
            "direction_id": "backend", "profile_sha256": backend["profile_sha256"],
            "weight_percent": 100,
        }])
        DIRECTIONS.approve_portfolio(
            self.db, "portfolio-1", "user", portfolio["portfolio_sha256"], AT
        )
        self.generate_and_approve_plan()
        self.add_direction_resume()
        self.assertEqual(
            RESUMES.select_approved(self.db, "backend", "direction")["version_id"],
            "direction-1",
        )
        data = DIRECTIONS.register_direction(
            self.db, self.profile("data", name="Data", role_family="data.analytics"), AT
        )
        replacement = self.register_portfolio([{
            "direction_id": "data", "profile_sha256": data["profile_sha256"],
            "weight_percent": 100,
        }], portfolio_id="portfolio-2")
        DIRECTIONS.approve_portfolio(
            self.db, "portfolio-2", "user", replacement["portfolio_sha256"], AT
        )
        with self.assertRaisesRegex(ValueError, "outside the active approved portfolio"):
            RESUMES.select_approved(self.db, "backend", "direction")

    def test_plan_is_value_free_and_preserves_evidence_strength(self):
        self.approve_direction()
        result = DIRECTIONS.generate_plan(
            self.db, "plan-1", "backend", "job-1", self.candidate_path, AT
        )
        plan = result["plan"]
        self.assertEqual(plan["recommended_kind"], "direction")
        self.assertEqual(plan["evidence"][0]["strength"], "direct")
        self.assertIn("fact-python", plan["proposed_changes"]["reorder_or_emphasize_fact_ids"])
        self.assertIn("transferable_as_direct", plan["constraints"]["forbidden"])
        encoded = json.dumps(plan)
        self.assertNotIn("AWS", encoded)
        self.assertNotIn("100000", encoded)

    def test_unreviewed_or_out_of_direction_job_blocks_plan(self):
        self.approve_direction()
        self.db.execute(
            "UPDATE jobs SET job_card_json=? WHERE job_id='job-1'",
            (json.dumps(self.job_card(requirements_reviewed=False)),),
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "user-reviewed"):
            DIRECTIONS.generate_plan(
                self.db, "plan-1", "backend", "job-1", self.candidate_path, AT
            )
        self.db.execute(
            "UPDATE jobs SET job_card_json=? WHERE job_id='job-1'",
            (json.dumps(self.job_card(title="Product Manager")),),
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "outside"):
            DIRECTIONS.generate_plan(
                self.db, "plan-1", "backend", "job-1", self.candidate_path, AT
            )

    def test_plan_approval_revalidates_candidate_and_job_hashes(self):
        self.approve_direction()
        generated = DIRECTIONS.generate_plan(
            self.db, "plan-1", "backend", "job-1", self.candidate_path, AT
        )
        with self.assertRaisesRegex(ValueError, "user actor"):
            DIRECTIONS.approve_plan(
                self.db, "plan-1", self.candidate_path, "system", generated["plan_sha256"], AT
            )
        self.db.execute(
            "UPDATE jobs SET job_card_json=? WHERE job_id='job-1'",
            (json.dumps(self.job_card(high_value=True)),),
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "JobCard is stale"):
            DIRECTIONS.approve_plan(
                self.db, "plan-1", self.candidate_path, "user", generated["plan_sha256"], AT
            )

    def test_derived_resume_requires_approved_matching_plan(self):
        self.approve_direction()
        source = self.root / "direction.txt"
        source.write_text("Python\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "adaptation plan"):
            RESUMES.register_version(
                self.db, self.root / "resumes", source, "direction-1", "direction", "backend",
                "master-1", at=AT,
            )
        generated, _ = self.generate_and_approve_plan()
        self.add_direction_resume()
        selected = RESUMES.select_approved(self.db, "backend", "direction")
        self.assertEqual(selected["version_id"], "direction-1")
        self.assertEqual(generated["plan"]["base_resume_version_id"], "master-1")

    def test_resume_approval_rejects_candidate_changed_after_plan(self):
        self.approve_direction()
        self.generate_and_approve_plan()
        source = self.root / "direction.txt"
        source.write_text("Python\n", encoding="utf-8")
        RESUMES.register_version(
            self.db, self.root / "resumes", source, "direction-1", "direction", "backend",
            "master-1", at=AT, adaptation_plan_id="plan-1",
        )
        candidate = json.loads(self.candidate_path.read_text(encoding="utf-8"))
        candidate["facts"].append({
            "id": "fact-go", "type": "skill", "value": "Go", "keywords": ["go"],
            "status": "confirmed", "locked": False, "evidence_strength": "direct",
        })
        candidate.pop("content_sha256")
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        self.candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        # A real profile change is re-registered by the user; the approved plan must
        # still be rejected as stale afterwards.
        CANDIDATES.register_snapshot(
            self.db, self.root / "candidates", self.candidate_path, "user", AT
        )
        with self.assertRaisesRegex(ValueError, "candidate profile is stale"):
            RESUMES.approve_version(
                self.db, "direction-1", self.candidate_path, self.manifest_path, "user", AT
            )

    def test_existing_direction_resume_supports_direct_reuse_and_precision_plan(self):
        self.approve_direction()
        self.generate_and_approve_plan()
        self.add_direction_resume()
        reuse = DIRECTIONS.generate_plan(
            self.db, "plan-reuse", "backend", "job-1", self.candidate_path, AT
        )
        self.assertEqual(reuse["plan"]["recommended_kind"], "direct_reuse")
        self.db.execute(
            "UPDATE jobs SET job_card_json=? WHERE job_id='job-1'",
            (json.dumps(self.job_card(required_skills=["Python", "SQL"])),),
        )
        self.db.commit()
        lightweight = DIRECTIONS.generate_plan(
            self.db, "plan-lightweight", "backend", "job-1", self.candidate_path, AT
        )
        self.assertEqual(lightweight["plan"]["recommended_kind"], "lightweight")
        self.db.execute(
            "UPDATE jobs SET job_card_json=? WHERE job_id='job-1'",
            (json.dumps(self.job_card(high_value=True)),),
        )
        self.db.commit()
        precision = DIRECTIONS.generate_plan(
            self.db, "plan-precision", "backend", "job-1", self.candidate_path, AT
        )
        self.assertEqual(precision["plan"]["recommended_kind"], "precision")

    def test_direction_revocation_invalidates_material_lock(self):
        self.approve_direction()
        self.generate_and_approve_plan()
        self.add_direction_resume()
        APPLICATIONS.create_application(
            self.db, "app-1", "job-1", "broad", "stop_before_submit", AT
        )
        APPLICATIONS.transition(self.db, "app-1", "pending_analysis", "system", "analysis", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "broad_recommended", "system", "match", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "approved", "user", "approved", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "materials_in_progress", "system", "materials", at=AT)
        RESUMES.bind_version(self.db, "app-1", "direction-1", at=AT)
        RESUMES.lock_materials(self.db, "app-1", lock_id="lock-1", at=AT)
        DIRECTIONS.revoke_direction(self.db, "backend", "user", "changed search strategy", AT)
        lock = self.db.execute(
            "SELECT invalidation_reason FROM material_locks WHERE lock_id='lock-1'"
        ).fetchone()
        self.assertEqual(lock["invalidation_reason"], "direction_revoked")
        with self.assertRaisesRegex(ValueError, "not user-approved"):
            RESUMES.select_approved(self.db, "backend")


if __name__ == "__main__":
    unittest.main()
