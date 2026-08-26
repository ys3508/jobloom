"""direction_baseline: a standing one-page resume per direction, with no JobCard.

The evidence record is a BaselinePlan: which confirmed facts the resume carries and in
what order, recorded as fact IDs and controlled reason codes only. Approving the plan
approves the selection; the resume bytes, its claims manifest and its file hash are
still approved separately.
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


APPLICATIONS = load_script("application_core")
RESUMES = load_script("resume_core")
DIRECTIONS = load_script("direction_core")
CANDIDATES = load_script("candidate_core")
MVP = load_script("mvp_core")
AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class BaselinePlanTests(unittest.TestCase):
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
        self.approve_direction()
        self.master_id = self.approve_master()

    # --- fixture ---------------------------------------------------------

    def write_candidate(self, extra_fact=False):
        facts = [
            {"id": "fact-name", "type": "identity", "value": "Verified Candidate",
             "status": "locked", "locked": True, "evidence_strength": "direct"},
            {"id": "fact-consulting", "type": "experience_claim",
             "value": "Advised life sciences clients on market strategy",
             "status": "confirmed", "locked": False, "evidence_strength": "direct"},
            {"id": "fact-vet", "type": "experience_claim", "value": "Ran a veterinary lab bench",
             "status": "confirmed", "locked": False, "evidence_strength": "direct"},
        ]
        if extra_fact:
            facts.append({"id": "fact-new", "type": "skill", "value": "SAS",
                          "status": "confirmed", "locked": False, "evidence_strength": "direct"})
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {"country": "US", "authorized_now": True, "sponsorship_now": False,
                                   "sponsorship_future": False, "employer_action_required": False,
                                   "confirmed": True},
            "search": {}, "facts": facts,
        }
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        suffix = "-b" if extra_fact else ""
        candidate_path = self.root / f"candidate{suffix}.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        manifest_path = self.root / f"claims{suffix}.json"
        manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [
            {"claim_id": "claim-1", "claim_text": "Verified Candidate", "fact_ids": ["fact-name"],
             "evidence_strength": "direct", "exact_locked_value_preserved": True}]}), encoding="utf-8")
        return candidate_path, manifest_path

    def baseline_manifest(self, fact_ids=("fact-name", "fact-consulting"), name="baseline-claims"):
        """A manifest claiming exactly the planned selection."""
        values = {"fact-name": "Verified Candidate",
                  "fact-consulting": "Advised life sciences clients on market strategy",
                  "fact-vet": "Ran a veterinary lab bench"}
        locked = {"fact-name"}
        claims = [{"claim_id": f"claim-{fact_id}", "claim_text": values[fact_id],
                   "fact_ids": [fact_id], "evidence_strength": "direct",
                   "exact_locked_value_preserved": fact_id in locked} for fact_id in fact_ids]
        path = self.root / f"{name}.json"
        path.write_text(json.dumps({"schema_version": "0.1.0", "claims": claims}), encoding="utf-8")
        return path

    def approve_direction(self, direction_id="lsc"):
        registered = DIRECTIONS.register_direction(self.db, {
            "schema_version": "0.1.0", "direction_id": direction_id, "name": "Consulting",
            "role_family": "consulting.life_sciences", "target_titles": ["Life Sciences Consultant"],
            "positive_keywords": [], "negative_keywords": [], "precision_keywords": [],
            "criteria": {"industries": ["life sciences"]}, "parent_direction_id": None,
        }, at=AT)
        portfolio = DIRECTIONS.register_portfolio(self.db, {
            "schema_version": "0.1.0", "portfolio_id": f"pf-{direction_id}", "name": "P",
            "allocations": [{"direction_id": direction_id,
                             "profile_sha256": registered["profile_sha256"],
                             "weight_percent": 100}]}, AT)
        DIRECTIONS.approve_portfolio(self.db, f"pf-{direction_id}", "user",
                                     portfolio["portfolio_sha256"], AT)
        return registered

    def approve_master(self, version_id="master-1"):
        source = self.root / f"{version_id}.docx"
        source.write_text("master resume", encoding="utf-8")
        RESUMES.register_version(self.db, self.root / "store", source, version_id,
                                 "master_source", "unassigned", None, actor="system", at=AT)
        RESUMES.approve_version(self.db, version_id, self.candidate_path, self.manifest_path,
                                "user", AT)
        return version_id

    SELECTION = [{"fact_id": "fact-name", "order": 0, "reason": "structural_identity"},
                 {"fact_id": "fact-consulting", "order": 1, "reason": "direction_core_evidence"}]
    EXCLUSIONS = [{"fact_id": "fact-vet", "reason": "outside_direction_scope"}]

    def generate_plan(self, plan_id="baseline-1", direction="lsc", selection=None, exclusions=None,
                      candidate_path=None, **kwargs):
        return DIRECTIONS.generate_baseline_plan(
            self.db, plan_id, direction, candidate_path or self.candidate_path,
            self.SELECTION if selection is None else selection,
            self.EXCLUSIONS if exclusions is None else exclusions, at=AT, **kwargs)

    def approve_plan(self, plan_id="baseline-1", generated=None):
        generated = generated or self.generate_plan(plan_id)
        return DIRECTIONS.approve_baseline_plan(
            self.db, plan_id, self.candidate_path, "user", generated["plan_sha256"], AT)

    def register_baseline(self, version_id="baseline-resume-1", plan_id="baseline-1",
                          parent=None, **kwargs):
        source = self.root / f"{version_id}.docx"
        source.write_text("one page for the direction", encoding="utf-8")
        return RESUMES.register_version(
            self.db, self.root / "store", source, version_id, "direction", "lsc",
            parent if parent is not None else self.master_id, actor="system", at=AT,
            source_mode="direction_baseline", baseline_plan_id=plan_id, **kwargs)

    # --- the plan --------------------------------------------------------

    def test_a_plan_records_selection_and_never_wording(self):
        plan = self.generate_plan()["plan"]
        self.assertEqual(plan["kind"], "direction_baseline")
        self.assertEqual([item["fact_id"] for item in plan["selection"]],
                         ["fact-name", "fact-consulting"])
        self.assertEqual([item["fact_id"] for item in plan["exclusions"]], ["fact-vet"])
        self.assertFalse(plan["constraints"]["evidence_strength_may_be_promoted"])
        self.assertIn("promoted_evidence_strength", plan["constraints"]["forbidden"])
        self.assertTrue(plan["constraints"]["plan_approves_selection_only"])
        serialized = json.dumps(plan)
        for value in ("Advised life sciences clients", "veterinary lab bench"):
            self.assertNotIn(value, serialized, "a plan carries fact IDs, never fact values")

    def test_a_plan_binds_the_master_candidate_and_direction_hashes(self):
        plan = self.generate_plan()["plan"]
        master = self.db.execute("SELECT * FROM resume_versions WHERE version_id=?",
                                 (self.master_id,)).fetchone()
        direction = self.db.execute("SELECT * FROM search_directions WHERE direction_id='lsc'").fetchone()
        candidate_hash = json.loads(self.candidate_path.read_text())["content_sha256"]
        self.assertEqual(plan["master_version_id"], master["version_id"])
        self.assertEqual(plan["master_file_sha256"], master["file_sha256"])
        self.assertEqual(plan["direction_profile_sha256"], direction["profile_sha256"])
        self.assertEqual(plan["candidate_profile_sha256"], candidate_hash)

    def test_a_plan_cannot_reference_an_unconfirmed_fact(self):
        with self.assertRaisesRegex(ValueError, "unconfirmed fact"):
            self.generate_plan(selection=[{"fact_id": "fact-ghost", "order": 0,
                                           "reason": "direction_core_evidence"}])

    def test_every_confirmed_fact_must_be_selected_or_explicitly_excluded(self):
        with self.assertRaisesRegex(ValueError, "selected or explicitly excluded"):
            self.generate_plan(exclusions=[])

    def test_a_plan_requires_an_approved_master_and_an_in_portfolio_direction(self):
        self.db.execute("UPDATE resume_versions SET status='draft' WHERE version_id=?",
                        (self.master_id,))
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "approved master_source"):
            self.generate_plan("baseline-x")

    def test_plan_approval_requires_the_user_and_the_exact_hash(self):
        generated = self.generate_plan()
        with self.assertRaisesRegex(ValueError, "user actor"):
            DIRECTIONS.approve_baseline_plan(self.db, "baseline-1", self.candidate_path,
                                             "system", generated["plan_sha256"], AT)
        with self.assertRaisesRegex(ValueError, "hash does not match"):
            DIRECTIONS.approve_baseline_plan(self.db, "baseline-1", self.candidate_path,
                                             "user", "0" * 64, AT)
        self.assertEqual(self.approve_plan(generated=generated)["status"], "approved")

    # --- registration gates ---------------------------------------------

    def test_an_unapproved_plan_cannot_register_a_resume(self):
        self.generate_plan()
        with self.assertRaisesRegex(ValueError, "not user-approved"):
            self.register_baseline()

    def test_a_missing_plan_cannot_register_a_resume(self):
        with self.assertRaisesRegex(ValueError, "requires an approved baseline plan"):
            self.register_baseline(plan_id=None)

    def test_direction_baseline_is_only_for_direction_resumes(self):
        source = self.root / "wrong.docx"
        source.write_text("x", encoding="utf-8")
        for kind in ("lightweight", "precision"):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(ValueError, "only direction resumes"):
                    RESUMES.register_version(self.db, self.root / f"store-{kind}", source,
                                             f"wrong-{kind}", kind, "lsc", self.master_id,
                                             actor="system", at=AT,
                                             source_mode="direction_baseline")
        with self.assertRaisesRegex(ValueError, "master_source must use user_provided"):
            RESUMES.register_version(self.db, self.root / "store2", source, "wrong-1",
                                     "master_source", "unassigned", None, actor="system", at=AT,
                                     source_mode="direction_baseline")

    def test_a_baseline_may_not_bind_a_jobcard_adaptation_plan(self):
        self.approve_plan()
        with self.assertRaisesRegex(ValueError, "must not bind a JobCard adaptation plan"):
            self.register_baseline(adaptation_plan_id="some-job-plan")

    def test_the_parent_must_be_the_master_the_plan_was_reviewed_against(self):
        self.approve_plan()
        source = self.root / "decoy.docx"
        source.write_text("decoy", encoding="utf-8")
        RESUMES.register_version(self.db, self.root / "decoy-store", source, "decoy-master",
                                 "master_source", "unassigned", None, actor="system", at=AT)
        with self.assertRaisesRegex(ValueError, "parent must be the planned master|must be approved"):
            self.register_baseline(parent="decoy-master")

    # --- approval and readiness ------------------------------------------

    def test_the_resume_bytes_still_need_their_own_user_approval(self):
        self.approve_plan()
        self.register_baseline()
        row = self.db.execute("SELECT status FROM resume_versions WHERE version_id='baseline-resume-1'").fetchone()
        self.assertEqual(row["status"], "draft", "approving a plan must not approve bytes")
        manifest = self.baseline_manifest()
        with self.assertRaisesRegex(ValueError, "user actor"):
            RESUMES.approve_version(self.db, "baseline-resume-1", self.candidate_path,
                                    manifest, "system", AT, rendered_page_count=1)
        approved = RESUMES.approve_version(self.db, "baseline-resume-1", self.candidate_path,
                                           manifest, "user", AT, rendered_page_count=1)
        self.assertEqual(approved["status"], "approved")
        row = self.db.execute("SELECT rendered_page_count FROM resume_versions "
                              "WHERE version_id='baseline-resume-1'").fetchone()
        self.assertEqual(row["rendered_page_count"], 1)

    def test_an_approved_baseline_resume_clears_the_readiness_blocker(self):
        self.assertIn("user_approved_direction_resume_required",
                      MVP.readiness(self.db, self.root)["onboarding"]["blockers"])
        self.approve_plan()
        self.register_baseline()
        RESUMES.approve_version(self.db, "baseline-resume-1", self.candidate_path,
                                self.baseline_manifest(), "user", AT, rendered_page_count=1)
        self.assertEqual(MVP._approved_direction_resumes(self.db), 1)
        self.assertEqual(MVP.readiness(self.db, self.root)["onboarding"]["blockers"], [])

    def test_the_manifest_must_claim_exactly_the_planned_selection(self):
        self.approve_plan()
        self.register_baseline()
        short = self.baseline_manifest(("fact-name",), "short")
        with self.assertRaisesRegex(ValueError, "does not match the approved baseline selection"):
            RESUMES.approve_version(self.db, "baseline-resume-1", self.candidate_path,
                                    short, "user", AT, rendered_page_count=1)
        smuggled = self.baseline_manifest(("fact-name", "fact-consulting", "fact-vet"), "smuggled")
        with self.assertRaisesRegex(ValueError, "does not match the approved baseline selection"):
            RESUMES.approve_version(self.db, "baseline-resume-1", self.candidate_path,
                                    smuggled, "user", AT, rendered_page_count=1)

    def test_a_baseline_must_confirm_a_single_rendered_page(self):
        self.approve_plan()
        self.register_baseline()
        manifest = self.baseline_manifest()
        for value in (None, 2, 0):
            with self.subTest(pages=value):
                with self.assertRaisesRegex(ValueError, "rendered_page_count of 1"):
                    RESUMES.approve_version(self.db, "baseline-resume-1", self.candidate_path,
                                            manifest, "user", AT, rendered_page_count=value)

    def test_a_tampered_plan_payload_is_refused(self):
        self.approve_plan()
        row = self.db.execute("SELECT plan_json FROM baseline_plans WHERE plan_id='baseline-1'").fetchone()
        plan = json.loads(row["plan_json"])
        plan["selection"] = []
        self.db.execute("UPDATE baseline_plans SET plan_json=? WHERE plan_id='baseline-1'",
                        (DIRECTIONS.canonical_json(plan),))
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "does not match its recorded hash"):
            self.register_baseline()

    def test_only_selected_locked_facts_must_stay_verbatim(self):
        plan = self.generate_plan()["plan"]
        selected = {item["fact_id"] for item in plan["selection"]}
        self.assertTrue(set(plan["locked_fact_ids_must_remain_exact"]) <= selected)

    def test_a_plan_carries_no_free_text(self):
        with self.assertRaisesRegex(ValueError, "carries no free text"):
            self.generate_plan(unsupported_terms=["client-facing analytics"])
        self.assertEqual(self.generate_plan("baseline-2")["plan"]["unsupported_terms"], [])

    def test_duplicate_exclusions_are_refused(self):
        with self.assertRaisesRegex(ValueError, "duplicate facts"):
            self.generate_plan(exclusions=[{"fact_id": "fact-vet", "reason": "outside_direction_scope"},
                                           {"fact_id": "fact-vet", "reason": "space_constrained"}])

    def test_revoking_the_direction_marks_the_plan_invalidated(self):
        self.approve_plan()
        DIRECTIONS.revoke_direction(self.db, "lsc", "user", "no_longer_targeted", at=AT)
        row = self.db.execute("SELECT status, invalidation_reason FROM baseline_plans "
                              "WHERE plan_id='baseline-1'").fetchone()
        self.assertEqual(row["status"], "invalidated")
        self.assertEqual(row["invalidation_reason"], "direction_revoked")

    def test_readiness_revalidates_the_plan_after_approval(self):
        """A plan edited after the resume was approved must stop counting as ready."""
        self.approve_plan()
        self.register_baseline()
        RESUMES.approve_version(self.db, "baseline-resume-1", self.candidate_path,
                                self.baseline_manifest(), "user", AT, rendered_page_count=1)
        self.assertEqual(MVP._approved_direction_resumes(self.db), 1)
        row = self.db.execute("SELECT plan_json FROM baseline_plans WHERE plan_id='baseline-1'").fetchone()
        plan = json.loads(row["plan_json"])
        plan["selection"] = []
        self.db.execute("UPDATE baseline_plans SET plan_json=? WHERE plan_id='baseline-1'",
                        (DIRECTIONS.canonical_json(plan),))
        self.db.commit()
        self.assertEqual(MVP._approved_direction_resumes(self.db), 0)
        self.assertIn("user_approved_direction_resume_required",
                      MVP.readiness(self.db, self.root)["onboarding"]["blockers"])

    def test_readiness_requires_a_recorded_single_page(self):
        self.approve_plan()
        self.register_baseline()
        RESUMES.approve_version(self.db, "baseline-resume-1", self.candidate_path,
                                self.baseline_manifest(), "user", AT, rendered_page_count=1)
        self.db.execute("UPDATE resume_versions SET rendered_page_count=2 "
                        "WHERE version_id='baseline-resume-1'")
        self.db.commit()
        self.assertEqual(MVP._approved_direction_resumes(self.db), 0)

    def test_a_replaced_candidate_snapshot_file_is_caught_on_every_use(self):
        self.approve_plan()
        row = self.db.execute("SELECT snapshot_path FROM candidate_snapshots "
                              "WHERE status='active'").fetchone()
        Path(row["snapshot_path"]).chmod(0o600)
        Path(row["snapshot_path"]).write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "snapshot hash mismatch"):
            self.register_baseline()

    def test_invalidating_a_plan_is_a_recorded_operation(self):
        self.approve_plan()
        second = self.generate_plan("baseline-2")
        result = DIRECTIONS.invalidate_baseline_plan(
            self.db, "baseline-1", "user", "superseded_by_v2", superseded_by="baseline-2", at=AT)
        self.assertEqual(result["status"], "invalidated")
        row = self.db.execute("SELECT status, invalidation_reason FROM baseline_plans "
                              "WHERE plan_id='baseline-1'").fetchone()
        self.assertEqual(row["status"], "invalidated")
        event = self.db.execute(
            "SELECT actor, event_type, reason_code, metadata_json FROM direction_events "
            "WHERE event_type='baseline_plan_invalidated'").fetchone()
        self.assertEqual(event["actor"], "user")
        self.assertEqual(event["reason_code"], "superseded_by_v2")
        self.assertEqual(json.loads(event["metadata_json"])["superseded_by"], "baseline-2")
        with self.assertRaisesRegex(ValueError, "already invalidated"):
            DIRECTIONS.invalidate_baseline_plan(self.db, "baseline-1", "user", "again", at=AT)

    def test_a_replacement_plan_must_be_live_and_same_direction(self):
        self.approve_plan()
        self.approve_direction("other")
        with self.assertRaisesRegex(ValueError, "replacement baseline plan not found"):
            DIRECTIONS.invalidate_baseline_plan(self.db, "baseline-1", "user", "x",
                                                superseded_by="ghost", at=AT)

    # --- fail closed on drift --------------------------------------------

    def test_a_changed_candidate_profile_invalidates_the_plan(self):
        self.approve_plan()
        changed_path, _ = self.write_candidate(extra_fact=True)
        CANDIDATES.register_snapshot(self.db, self.root / "cands", changed_path, "user", AT)
        with self.assertRaisesRegex(ValueError, "candidate profile is stale"):
            self.register_baseline()

    def test_a_revoked_direction_invalidates_the_plan(self):
        self.approve_plan()
        DIRECTIONS.revoke_direction(self.db, "lsc", "user", "no_longer_targeted", at=AT)
        with self.assertRaisesRegex(ValueError, "not user-approved|no longer approved|outside the active"):
            self.register_baseline()

    def test_a_changed_master_invalidates_the_plan(self):
        self.approve_plan()
        self.db.execute("UPDATE resume_versions SET file_sha256='0' WHERE version_id=?",
                        (self.master_id,))
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "master resume is stale|hash mismatch"):
            self.register_baseline()

    def test_a_plan_for_another_direction_is_refused(self):
        self.approve_plan()
        self.approve_direction("other")
        with self.assertRaisesRegex(ValueError, "another direction|outside the active"):
            source = self.root / "b2.docx"
            source.write_text("x", encoding="utf-8")
            RESUMES.register_version(self.db, self.root / "store3", source, "b2", "direction",
                                     "other", self.master_id, actor="system", at=AT,
                                     source_mode="direction_baseline", baseline_plan_id="baseline-1")


if __name__ == "__main__":
    unittest.main()
