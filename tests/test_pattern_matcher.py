import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "jobloom" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_script(name):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"pattern_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


MATCHER = load_script("pattern_matcher")
EVIDENCE = load_script("evidence_matcher")
ONTOLOGY = load_script("capability_ontology")


def token_pattern(*tokens, inflect=True, **updates):
    value = {
        "pattern_id": "pat.test", "type": "token_run", "lang": "en",
        "tokens": list(tokens), "inflect": inflect,
    }
    value.update(updates)
    return value


class PatternMatcherTests(unittest.TestCase):
    def test_controlled_inflection_fixes_the_observed_plural_failure(self):
        fact = {"value": "Led two focus groups and analyzed market data."}
        self.assertFalse(EVIDENCE.fact_supports("focus group", fact))
        self.assertTrue(MATCHER.match(token_pattern("focus", "group"), fact))

    def test_inflection_must_be_explicitly_enabled(self):
        fact = "Led two focus groups."
        self.assertFalse(MATCHER.match(token_pattern("focus", "group", inflect=False), fact))

    def test_token_runs_are_ordered_contiguous_and_never_cross_fact_surfaces(self):
        pattern = token_pattern("data", "management")
        self.assertFalse(MATCHER.match(pattern, "Managed data and later discussed management."))
        self.assertFalse(MATCHER.match(pattern, {"value": "data", "keywords": ["management"]}))

    def test_explicit_variants_are_supported(self):
        pattern = token_pattern("pharmaceutical", "market", variants=["pharma market"])
        self.assertTrue(MATCHER.match(pattern, "Reviewed the pharma market."))

    def test_chinese_uses_exact_substring_without_space_tokenization(self):
        pattern = {
            "pattern_id": "pat.zh", "type": "substring", "lang": "zh", "text": "焦点小组",
        }
        self.assertTrue(MATCHER.match(pattern, "我负责焦点小组与问卷设计"))
        self.assertFalse(MATCHER.match(pattern, "我负责问卷设计"))

    def test_chinese_token_run_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Chinese.*substring"):
            MATCHER.validate_pattern({
                "pattern_id": "pat.zh", "type": "token_run", "lang": "zh",
                "tokens": ["焦点小组"], "inflect": False,
            })

    def test_pattern_schema_rejects_unknown_fields(self):
        with self.assertRaisesRegex(ValueError, "missing or unknown"):
            MATCHER.validate_pattern(dict(token_pattern("data"), surprise=True))

    def test_single_letter_tool_uses_token_boundaries(self):
        pattern = token_pattern("R", inflect=False)
        self.assertTrue(MATCHER.match(pattern, "Programming: R, SAS, SQL"))
        self.assertFalse(MATCHER.match(pattern, "research reporting"))

    def test_semantic_anchor_is_capped_and_requires_confirmation_in_verified_mode(self):
        pattern = {
            "pattern_id": "pat.semantic", "type": "semantic_anchor", "lang": "en",
            "anchor": "designed and ran structured research", "max_grade": "transferable",
            "requires_confirmation": True,
        }
        self.assertTrue(MATCHER.match(pattern, "anything", semantic_result=True, verified=False))
        self.assertFalse(MATCHER.match(pattern, "anything", semantic_result=True, verified=True))
        self.assertTrue(MATCHER.match(
            pattern, "anything", semantic_result={"hit": True, "confirmed_by_user": True},
            verified=True,
        ))
        too_strong = dict(pattern, max_grade="strongly_related")
        with self.assertRaisesRegex(ValueError, "cannot exceed transferable"):
            MATCHER.validate_pattern(too_strong)


class CapabilityOntologyTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(ONTOLOGY.DEFAULT_ONTOLOGY_PATH.read_text(encoding="utf-8"))
        self.golden = json.loads(ONTOLOGY.DEFAULT_GOLDEN_PATH.read_text(encoding="utf-8"))

    def test_default_ontology_and_every_golden_pattern_validate(self):
        value = ONTOLOGY.load_ontology()
        self.assertEqual(value["ontology_version"], "2026.08.0")
        self.assertEqual(len(value["function_nodes"]), 3)

    def test_every_signature_capability_has_patterns(self):
        value = ONTOLOGY.validate_ontology(self.raw)
        by_id = {cap["capability_id"]: cap for cap in value["capabilities"]}
        signature_ids = {
            item["capability_id"]
            for node in value["function_nodes"] for item in node["capability_signature"]
        }
        self.assertFalse({cap_id for cap_id in signature_ids
                          if not by_id[cap_id]["evidence_patterns"]})

    def test_capabilities_are_global_and_can_be_shared_across_functions(self):
        value = ONTOLOGY.validate_ontology(self.raw)
        users = [node["function_id"] for node in value["function_nodes"]
                 if any(item["capability_id"] == "cap.statistical-analysis"
                        for item in node["capability_signature"])]
        self.assertEqual(len(users), 3)

    def test_tool_nodes_are_rejected(self):
        bad = copy.deepcopy(self.raw)
        skill = next(item for item in bad["capabilities"] if item["layer"] == "SKILL")
        skill["layer"] = "TOOL"
        with self.assertRaisesRegex(ValueError, "TOOL/EVIDENCE"):
            ONTOLOGY.validate_ontology(bad)

    def test_unknown_signature_capability_is_rejected(self):
        bad = copy.deepcopy(self.raw)
        bad["function_nodes"][0]["capability_signature"][0]["capability_id"] = "cap.missing"
        with self.assertRaisesRegex(ValueError, "existing SKILL"):
            ONTOLOGY.validate_ontology(bad)

    def test_function_parent_cycles_are_rejected(self):
        bad = copy.deepcopy(self.raw)
        first, second = bad["function_nodes"][:2]
        first["parents"] = [second["function_id"]]
        second["parents"] = [first["function_id"]]
        with self.assertRaisesRegex(ValueError, "DAG"):
            ONTOLOGY.validate_ontology(bad)

    def test_capability_rollup_cycles_are_rejected(self):
        bad = copy.deepcopy(self.raw)
        domains = [item for item in bad["capabilities"] if item["layer"] == "DOMAIN"]
        domains[0]["rollup_to"] = [domains[1]["capability_id"]]
        domains[1]["rollup_to"] = [domains[0]["capability_id"]]
        with self.assertRaisesRegex(ValueError, "rollup_to.*DAG"):
            ONTOLOGY.validate_ontology(bad)

    def test_labels_and_aliases_are_globally_unique(self):
        bad = copy.deepcopy(self.raw)
        bad["capabilities"][1]["aliases"] = [bad["capabilities"][0]["canonical_label"]]
        with self.assertRaisesRegex(ValueError, "globally unique"):
            ONTOLOGY.validate_ontology(bad)

    def test_dead_golden_pattern_rejects_the_artifact(self):
        ontology = ONTOLOGY.validate_ontology(self.raw)
        bad = copy.deepcopy(self.golden)
        sample = next(item for item in bad["samples"] if item["pattern_id"] == "pat.focus-group")
        sample["facts"] = ["No qualitative research was performed."]
        with self.assertRaisesRegex(ValueError, "pat.focus-group"):
            ONTOLOGY.validate_golden_samples(bad, ontology)


class EvidenceVocabularyTests(unittest.TestCase):
    def test_resume_and_matcher_share_one_evidence_order_object(self):
        import resume_core
        import evidence_matcher

        self.assertIs(resume_core.EVIDENCE_RANK, evidence_matcher.EVIDENCE_ORDER)
        self.assertEqual(set(evidence_matcher.STRENGTH_FACTORS),
                         set(evidence_matcher.EVIDENCE_ORDER))


if __name__ == "__main__":
    unittest.main()
