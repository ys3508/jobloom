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


class AcceptanceDebtTests(unittest.TestCase):
    """The five criteria the V2 commit left uncovered."""

    def goals(self, **updates):
        value = {"schema_version": "0.1.0", "goal_id": "g1", "desired_roles": [],
                 "desired_industries": [], "skills_to_build": [], "avoid_roles": [],
                 "avoid_industries": [], "priorities": {"current_fit": 50, "career_value": 50}}
        value.update(updates)
        return value

    def generate(self, facts=None, **kwargs):
        return PIPELINE.generate(proposal_id="p1", candidate={"search": {}},
                                 facts=facts if facts is not None else candidate_facts(),
                                 mode="verified", snapshot_sha256=SNAPSHOT,
                                 created_at=datetime(2026, 8, 28, tzinfo=timezone.utc), **kwargs)

    # 17: an excluded direction never reaches ready_now, however strong its evidence
    def test_user_excluded_direction_is_never_ready_now(self):
        baseline = self.generate()
        top = baseline["recommendations"][0]
        excluded = self.generate(goals=self.goals(avoid_roles=[top["name"]]))
        named = {d["name"]: d for d in excluded["recommendations"]}
        self.assertNotIn(top["name"], named,
                         "an excluded direction must not stay in the shown set as ready_now")
        for direction in excluded["recommendations"]:
            self.assertNotEqual(direction["name"], top["name"])

    # 22: one fact carrying a direction caps it at build_toward
    def test_single_fact_dominance_caps_readiness(self):
        facts = [fact("solo", "I analyzed statistical regression results for one study.")]
        result = self.generate(facts=facts)
        for direction in result["recommendations"]:
            self.assertNotIn(direction["readiness"], {"ready_now", "near_term"})

    # 21: shown directions must differ from one another
    def test_shown_directions_are_mutually_distinct(self):
        result = self.generate()
        ids = [d["function_id"] for d in result["recommendations"]]
        self.assertEqual(len(ids), len(set(ids)))

    # 23: the same evidence must not produce a different proposal
    def test_identical_evidence_reproduces_the_same_proposal(self):
        first, second = self.generate(), self.generate()
        self.assertEqual(first["evidence_unit_fingerprint"], second["evidence_unit_fingerprint"])
        self.assertEqual(first["proposal_sha256"], second["proposal_sha256"])

    # 23b: changed evidence must change the fingerprint
    def test_new_evidence_changes_the_fingerprint(self):
        extra = candidate_facts() + [fact("f9", "I built clinical trial databases for two compounds.")]
        self.assertNotEqual(self.generate()["evidence_unit_fingerprint"],
                            self.generate(facts=extra)["evidence_unit_fingerprint"])

    # 39: a low-confidence or thinly-observed title mapping stays out of the verified path
    def test_low_confidence_title_mapping_is_not_verified(self):
        surface = {"surface_id": "ts.x", "raw": "Data Analyst", "normalized": "data analyst",
                   "level_token": None,
                   "maps_to": [{"function_id": "fn.research-clinical-data", "confidence": 0.4,
                                "assigned_by": "model", "confirmed_by_user": False}],
                   "requires_domain_guard": True, "domain_guard_terms": ["research"],
                   "excluded_senses": [],
                   "provenance": {"postings_seen": 9, "distinct_employers": 2,
                                  "first_seen": "2026-03-01", "last_seen": "2026-08-01"}}
        self.assertFalse(TITLES.is_verified_mapping(surface, surface["maps_to"][0]))
        confident = dict(surface["maps_to"][0], confidence=0.8)
        thin = dict(surface, provenance=dict(surface["provenance"], distinct_employers=2))
        self.assertFalse(TITLES.is_verified_mapping(thin, confident))
        broad = dict(surface, provenance=dict(surface["provenance"], distinct_employers=5))
        self.assertTrue(TITLES.is_verified_mapping(broad, confident))

    # coverage block required by the specification
    def test_direction_reports_capability_and_core_coverage(self):
        result = self.generate()
        coverage = result["recommendations"][0]["evidence"]["coverage"]
        self.assertIn("capability", coverage)
        self.assertIn("core_capability", coverage)
        self.assertLessEqual(coverage["core_capability"]["covered"],
                             coverage["core_capability"]["required"])
        self.assertIsInstance(
            result["recommendations"][0]["evidence"]["unsupported_core_signals"], list)




class MarketThresholdTests(unittest.TestCase):
    """Sample floors and the absolute employer count behind term selection."""

    def card(self, index, employer, skills):
        return {"canonical_url": f"https://example.com/{index}", "employer": employer,
                "title": "Research Data Analyst", "location": "Boston, MA",
                "required_skills": skills, "required_certifications": []}

    def profile(self, cards):
        return MARKET.aggregate(cards, profile_id="m", title_id="t.x", region={"country": "US"},
                                seniority_band="ic_2", window={"days": 90})

    def test_one_employers_stack_never_becomes_a_core_requirement(self):
        cards = [self.card(i, "Mega Hospital", ["Epic", "SQL"]) for i in range(20)]
        cards += [self.card(100 + i, f"Lab {i}", ["R", "SQL"]) for i in range(5)]
        profile = self.profile(cards)
        self.assertFalse(profile["sample"]["sufficient"])
        self.assertTrue(profile["sample"]["single_employer_risk"])
        self.assertNotIn("Epic", [term["term"] for term in profile["required_terms"]])

    def test_ratio_alone_cannot_carry_a_term_past_the_employer_floor(self):
        # Two of five employers is 0.40 support, over the ratio and under the count.
        cards = [self.card(i, f"Emp {i % 5}", ["Tableau"] if i % 5 < 2 else ["R"])
                 for i in range(20)]
        profile = self.profile(cards)
        tableau = next(t for t in profile["required_terms"] + profile["preferred_terms"]
                       if t["term"] == "Tableau")
        self.assertGreaterEqual(tableau["employer_support"], MARKET.REQUIRED_SUPPORT)
        self.assertLess(tableau["naming_employers"], MARKET.MIN_EMPLOYERS_PER_TERM)
        self.assertNotIn("Tableau", [term["term"] for term in profile["required_terms"]])

    def test_small_employer_pool_raises_the_required_ratio(self):
        cards = [self.card(i, f"Emp {i // 2}", ["R"] + (["SAS"] if i < 8 else []))
                 for i in range(22)]
        profile = self.profile(cards)
        self.assertTrue(profile["sample"]["sufficient"])
        self.assertEqual(profile["sample"]["required_support_threshold"],
                         MARKET.REQUIRED_SUPPORT_SMALL_SAMPLE)

    def test_narrow_title_can_now_reach_sufficiency(self):
        # 21 postings across 9 employers: below the old 25/10 floor, above the new one.
        cards = [self.card(i, f"Emp {i % 9}", ["R"]) for i in range(21)]
        profile = self.profile(cards)
        self.assertTrue(profile["sample"]["sufficient"])
        self.assertEqual(profile["sample"]["insufficient_reasons"], [])

    def test_below_the_floor_still_fails_closed(self):
        cards = [self.card(i, f"Emp {i}", ["R"]) for i in range(7)]
        profile = self.profile(cards)
        self.assertFalse(profile["sample"]["sufficient"])
        self.assertIn("market_employers_below_minimum", profile["sample"]["insufficient_reasons"])
        self.assertIn("market_postings_below_minimum", profile["sample"]["insufficient_reasons"])



if __name__ == "__main__": unittest.main()
