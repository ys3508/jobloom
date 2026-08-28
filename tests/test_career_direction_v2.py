import importlib.util
import json
import sys
import sqlite3
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "jobloom" / "scripts"
if str(SCRIPTS) not in sys.path: sys.path.insert(0, str(SCRIPTS))

def load(name):
    spec = importlib.util.spec_from_file_location(f"v2_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module

QUANTITY = load("quantity_extractor")
UNITS = load("evidence_units")
FACETS = load("facet_taxonomy")
ONTOLOGY = load("capability_ontology")
PIPELINE = load("career_direction_pipeline")
TITLES = load("title_ontology")
MARKET = load("market_profile")
CORE = load("career_direction_core")

SNAPSHOT = "a" * 64

def fact(fid, value, strength="direct", kind="experience_claim"):
    return {"id": fid, "value": value, "keywords": [], "status": "confirmed", "locked": False,
            "evidence_strength": strength, "type": kind}

def candidate_facts():
    return [
        fact("f1", "I analyzed statistical results and delivered findings for 12 studies."),
        fact("f2", "I developed R programs for regression analyses."),
        fact("f3", "I led study design and sampling plans."),
        fact("f4", "I queried research data using SQL."),
    ]

class QuantityTests(unittest.TestCase):
    def test_false_positive_guards(self):
        for text in ("State University May 2023", "Applied Regression 1 & 2, Data Science 1&2", "MPH: GPA 4.0"):
            self.assertFalse(QUANTITY.is_quantified(text), text)

    def test_work_result_quantity_is_kept(self):
        self.assertTrue(QUANTITY.is_quantified("Led analysis for 12 clinical trials."))

class EvidenceUnitTests(unittest.TestCase):
    def test_units_preserve_provenance_and_signals(self):
        units = UNITS.build_units(candidate_facts(), SNAPSHOT, today=date(2026, 8, 28))
        self.assertEqual(len(units), 4)
        self.assertTrue(all(u["snapshot_sha256"] == SNAPSHOT for u in units))
        self.assertIn("quantified(+0.25)", units[0]["signals_fired"])

    def test_relation_never_exceeds_source(self):
        facts = [fact("skill", "R", "mention_only", "skill")]
        units = UNITS.build_units(facts, SNAPSHOT)
        refs = FACETS.assign_capabilities(facts, units, ONTOLOGY.load_ontology(), mode="verified")
        self.assertTrue(refs)
        self.assertTrue(all(ref["relation_strength"] == "mention_only" for ref in refs))

    def test_gap_truth_table(self):
        self.assertEqual(FACETS.classify_gap(False, False, False), "real_gap")
        self.assertEqual(FACETS.classify_gap(True, False, False), "hidden_strength")
        self.assertEqual(FACETS.classify_gap(True, True, False), "evidence_gap")
        self.assertEqual(FACETS.classify_gap(True, True, True), "resume_gap")
        self.assertEqual(FACETS.classify_gap(True, None, False), "not_yet_presented")

class TitleTests(unittest.TestCase):
    def test_equity_research_is_excluded(self):
        result = TITLES.resolve({"title":"Research Analyst", "summary":"sell-side equity securities"})
        self.assertEqual(result["status"], "fail")

    def test_level_is_stripped_and_senior_care_is_guarded(self):
        self.assertEqual(TITLES.resolve({"title":"Research Data Analyst II"})["level_token"], "ii")
        self.assertEqual(TITLES.resolve({"title":"Senior Care Coordinator"})["surface"], "senior care coordinator")

    def test_bare_data_analyst_is_ambiguous(self):
        self.assertEqual(TITLES.resolve({"title":"Data Analyst"})["status"], "ambiguous")

    def test_research_analyst_context_selects_exactly_one_function(self):
        result = TITLES.resolve({"title":"Research Analyst", "summary":"consumer insights and brand tracking"})
        self.assertEqual(result["function_ids"], ["fn.pharma-insights"])

    def test_two_titles_share_one_function(self):
        self.assertEqual(TITLES.resolve({"title":"Biostatistician"})["function_ids"],
                         TITLES.resolve({"title":"Biostatistics Analyst"})["function_ids"])

    def test_body_title_never_binds(self):
        result = TITLES.resolve({"title":"Office Coordinator", "summary":"Partner with our Clinical Data Analyst team"})
        self.assertEqual(result["status"], "unmapped")
        self.assertIn("contextual_title_reference_only", result["notes"])

class MarketTests(unittest.TestCase):
    def test_single_employer_terms_do_not_become_required(self):
        cards = [{"canonical_url":f"https://x/{i}", "employer":"One Hospital", "title":"Analyst",
                  "location":"Boston", "required_skills":["Epic"]} for i in range(20)]
        cards += [{"canonical_url":f"https://y/{i}", "employer":f"Other {i}", "title":"Analyst",
                   "location":"Boston", "required_skills":["R"]} for i in range(10)]
        profile = MARKET.aggregate(cards, profile_id="m1", title_id="t1", region={"country":"US"}, seniority_band="ic_2", window={"days":90})
        self.assertNotIn("Epic", [x["term"] for x in profile["required_terms"]])
        self.assertTrue(profile["sample"]["single_employer_risk"])

    def test_small_market_fails_closed(self):
        profile = MARKET.unavailable("m", "t", {"country":"US"})
        self.assertFalse(profile["sample"]["sufficient"])

class PipelineTests(unittest.TestCase):
    def proposal(self, goals=None):
        return PIPELINE.generate(proposal_id="v2-test", candidate={"search":{}}, facts=candidate_facts(),
                                 mode="verified", snapshot_sha256=SNAPSHOT, goals=goals,
                                 today=date(2026,8,28), created_at=datetime(2026,8,28,tzinfo=timezone.utc))

    def test_v2_emits_independent_axes_without_overall_score(self):
        result = self.proposal(); self.assertEqual(result["engine_version"], "v2")
        self.assertTrue(result["recommendations"])
        direction = result["recommendations"][0]
        self.assertEqual(len(direction["axes"]), 8)
        self.assertNotIn("overall_score", direction)
        self.assertIsNone(direction["axes"]["market_capacity"]["value"])

    def test_output_contains_ids_not_fact_text(self):
        encoded = json.dumps(self.proposal())
        for item in candidate_facts(): self.assertNotIn(item["value"], encoded)

    def test_empty_goals_are_null_not_fifty(self):
        goals = {key: [] for key in FACETS.__dict__.get("LIST_KEYS", [])}
        result = self.proposal(goals={"desired_roles":[],"desired_industries":[],"skills_to_build":[],"avoid_roles":[],"avoid_industries":[]})
        self.assertFalse(result["goals_supplied"])
        self.assertIsNone(result["recommendations"][0]["axes"]["user_intent"]["value"])

    def test_materialization_keeps_existing_user_and_hash_gate(self):
        result = self.proposal(); rec = result["recommendations"][0]
        selection = {"portfolio_id":"v2-selected", "name":"V2", "allocations":[{"archetype_id":rec["archetype_id"], "weight_percent":100}]}
        adopted = CORE.materialize_selection(result, selection, actor="user", expected_proposal_sha256=result["proposal_sha256"], current_snapshot_sha256=SNAPSHOT, current_ontology_version=result["ontology_version"])
        self.assertEqual(len(adopted["profiles"]), 1)

    def test_stale_snapshot_and_ontology_are_rejected(self):
        result = self.proposal(); rec = result["recommendations"][0]
        selection = {"portfolio_id":"v2-selected", "name":"V2", "allocations":[{"archetype_id":rec["archetype_id"], "weight_percent":100}]}
        with self.assertRaisesRegex(ValueError, "stale"):
            CORE.materialize_selection(result, selection, actor="user", expected_proposal_sha256=result["proposal_sha256"], current_snapshot_sha256="b" * 64, current_ontology_version=result["ontology_version"])
        with self.assertRaisesRegex(ValueError, "superseded"):
            CORE.materialize_selection(result, selection, actor="user", expected_proposal_sha256=result["proposal_sha256"], current_snapshot_sha256=SNAPSHOT, current_ontology_version="future")

    def test_pipeline_persists_artifacts_axes_and_hash_locked_proposal(self):
        db = sqlite3.connect(":memory:")
        result = PIPELINE.generate(proposal_id="stored", candidate={"search":{}}, facts=candidate_facts(),
                                   mode="verified", snapshot_sha256=SNAPSHOT, connection=db,
                                   today=date(2026,8,28), created_at=datetime(2026,8,28,tzinfo=timezone.utc))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM evidence_units").fetchone()[0], 4)
        self.assertEqual(db.execute("SELECT content_sha256 FROM direction_proposals").fetchone()[0], result["proposal_sha256"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM direction_proposal_axes").fetchone()[0], 8 * len(result["recommendations"]))
        db.close()

    def test_market_without_evidence_is_build_toward(self):
        market = MARKET.aggregate([
            {"canonical_url":f"https://m/{i}", "employer":f"Employer {i}", "title":"Analyst",
             "location":"Boston", "required_skills":["survey design"]} for i in range(25)],
            profile_id="m", title_id="t", region={"country":"US"}, seniority_band="ic_2", window={"days":90})
        result = PIPELINE.generate(proposal_id="market-only", candidate={"search":{}}, facts=[],
                                   mode="verified", snapshot_sha256=SNAPSHOT,
                                   market_profiles={"fn.pharma-insights":market})
        direction = result["recommendations"][0]
        self.assertEqual(direction["readiness"], "build_toward")
        self.assertEqual(direction["axes"]["evidence_fit"]["value"], 0)
        self.assertIn("no_candidate_evidence", direction["review_reasons"])

    def test_twenty_resume_calibration_reports_dead_and_overbroad(self):
        ontology = ONTOLOGY.load_ontology()
        resumes = [[fact(f"f{i}", "Performed statistical analysis")]
                   for i in range(20)]
        result = ONTOLOGY.calibrate(ontology, resumes)
        self.assertIn("cap.statistical-analysis", result["overbroad_skill_ids"])
        self.assertIn("cap.survey-design", result["dead_skill_ids"])

if __name__ == "__main__": unittest.main()
