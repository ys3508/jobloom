import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "career_direction_core.py"
SPEC = importlib.util.spec_from_file_location("career_direction_core_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def archetype(archetype_id, name, signals, titles=None, industries=None):
    return {
        "archetype_id": archetype_id, "name": name, "role_family": f"test.{archetype_id}",
        "target_titles": titles or [name], "auxiliary_titles": [],
        "industries": industries or [archetype_id],
        "evidence_signals": [
            {"term": term, "weight": weight, "core": core}
            for term, weight, core in signals
        ],
        "career_signals": [archetype_id], "positive_keywords": [archetype_id],
        "negative_keywords": [], "precision_keywords": [archetype_id],
        "discovery_keywords": [archetype_id], "hard_exclusion_keywords": [],
        "warning_keywords": [],
    }


def catalog():
    return {"schema_version": "0.1.0", "archetypes": [
        archetype("clinical", "Clinical Data Analyst",
                  [("clinical trial", 5, True), ("SAS", 3, False)],
                  industries=["healthcare"]),
        archetype("software", "Software Engineer",
                  [("Java", 5, True), ("Kubernetes", 3, False)],
                  industries=["technology"]),
    ]}


def candidate(status="confirmed"):
    return {
        "search": {"countries": ["US"], "work_arrangements": ["remote"],
                   "excluded_employers": ["Blocked Inc"]},
        "facts": [
            {"id": "f-clinical", "value": "Managed clinical trial data",
             "keywords": [], "status": status, "evidence_strength": "direct"},
            {"id": "f-sas", "value": "SAS", "keywords": [],
             "status": status, "evidence_strength": "strongly_related"},
        ],
    }


def goals(**updates):
    value = {
        "schema_version": "0.1.0", "goal_id": "goals-v1",
        "desired_roles": [], "desired_industries": [], "skills_to_build": [],
        "avoid_roles": [], "avoid_industries": [],
        "priorities": {"current_fit": 60, "career_value": 40},
    }
    value.update(updates)
    return value


def proposal(mode="verified", goal_value=None):
    profile = candidate("confirmed" if mode == "verified" else "proposed")
    return MODULE.generate_proposal(
        proposal_id="proposal-v1", candidate=profile, facts=profile["facts"], mode=mode,
        source_sha256="a" * 64, catalog=catalog(), goals=goal_value, max_directions=2,
        created_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )


class CareerDirectionProposalTests(unittest.TestCase):
    def test_evidence_drives_current_fit_and_ranking(self):
        result = proposal()
        self.assertEqual(result["recommendations"][0]["archetype_id"], "clinical")
        self.assertGreater(result["recommendations"][0]["current_fit"], 80)
        self.assertEqual(len(result["recommendations"]), 1)

    def test_every_score_is_explained_by_fact_ids(self):
        clinical = proposal()["recommendations"][0]
        self.assertEqual(clinical["supporting_fact_ids"], ["f-clinical", "f-sas"])
        self.assertEqual(clinical["evidence"][0]["fact_ids"], ["f-clinical"])
        self.assertNotIn("Managed clinical trial data", json.dumps(clinical))

    def test_missing_core_signal_is_an_explicit_gap(self):
        software = next(item for item in proposal(goal_value=goals(
            desired_roles=["Software Engineer"]))["recommendations"]
                        if item["archetype_id"] == "software")
        self.assertEqual(software["core_gaps"], ["Java"])
        self.assertEqual(software["tier"], "exploratory")

    def test_operational_preferences_narrow_generated_profile(self):
        profile = proposal()["recommendations"][0]["profile"]
        self.assertEqual(profile["criteria"]["countries"], ["US"])
        self.assertEqual(profile["criteria"]["work_arrangements"], ["remote"])
        self.assertEqual(profile["criteria"]["excluded_companies"], ["Blocked Inc"])

    def test_resume_never_invents_career_value_without_goals(self):
        result = proposal()
        self.assertIsNone(result["recommendations"][0]["career_value"])
        self.assertIn("career_goals_not_supplied", result["review_reasons"])

    def test_explicit_goals_produce_career_value(self):
        result = proposal(goal_value=goals(desired_roles=["Clinical Data Analyst"],
                                           desired_industries=["healthcare"]))
        clinical = result["recommendations"][0]
        self.assertEqual(clinical["career_value"], 100)
        self.assertEqual(len(result["recommendations"]), 1)

    def test_empty_goal_template_is_treated_as_no_goals(self):
        result = proposal(goal_value=goals())
        self.assertFalse(result["goals_supplied"])
        self.assertIsNone(result["recommendations"][0]["career_value"])
        self.assertIn("career_goals_not_supplied", result["review_reasons"])

    def test_generic_goal_tokens_do_not_match_longer_controlled_terms(self):
        for generic in ("data", "research", "analyst", "coordinator"):
            result = proposal(goal_value=goals(desired_roles=[generic]))
            self.assertTrue(all(item["career_value"] == 0
                                for item in result["recommendations"]), generic)
            self.assertIn("career_goals_unresolved", result["review_reasons"])

    def test_avoid_goal_demotes_a_direction(self):
        result = proposal(goal_value=goals(avoid_industries=["healthcare"],
                                           priorities={"current_fit": 0, "career_value": 100}))
        clinical = next(item for item in result["recommendations"]
                        if item["archetype_id"] == "clinical")
        self.assertEqual(clinical["career_value"], 0)

    def test_summary_hit_is_not_treated_as_direct_work_evidence(self):
        profile = candidate()
        profile["facts"] = [{
            "id": "f-summary", "type": "professional_summary",
            "value": "Clinical trial professional", "keywords": [],
            "status": "confirmed", "evidence_strength": "direct",
        }]
        result = MODULE.generate_proposal(
            proposal_id="summary-v1", candidate=profile, facts=profile["facts"], mode="verified",
            source_sha256="c" * 64, catalog=catalog(), max_directions=2,
            created_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        )
        evidence = result["recommendations"][0]["evidence"][0]
        self.assertEqual(evidence["strength"], "mention_only")
        self.assertEqual(evidence["source_evidence"][0]["declared_strength"], "direct")
        self.assertEqual(evidence["source_evidence"][0]["effective_strength"], "mention_only")
        self.assertEqual(result["recommendations"][0]["confidence"], "low")

    def test_provisional_hits_are_capped_and_never_high_confidence(self):
        result = proposal(mode="provisional")
        clinical = result["recommendations"][0]
        self.assertTrue(all(MODULE.EVIDENCE_ORDER[item["strength"]]
                            <= MODULE.EVIDENCE_ORDER["transferable"]
                            for item in clinical["evidence"]))
        self.assertNotEqual(clinical["confidence"], "high")
        self.assertFalse(clinical["decision_grade"])

    def test_suggested_weights_are_positive_and_total_one_hundred(self):
        allocations = proposal()["suggested_portfolio"]["allocations"]
        self.assertEqual(sum(item["weight_percent"] for item in allocations), 100)
        self.assertTrue(all(item["weight_percent"] > 0 for item in allocations))

    def test_uploaded_material_proposal_is_provisional_and_not_adoptable(self):
        result = proposal(mode="provisional")
        self.assertFalse(result["adoptable"])
        self.assertIn("candidate_facts_require_confirmation", result["review_reasons"])

    def test_provisional_proposal_cannot_be_materialized(self):
        result = proposal(mode="provisional")
        selection = {"portfolio_id": "selected-v1", "name": "Selected", "allocations": [
            {"archetype_id": "clinical", "weight_percent": 100}]}
        with self.assertRaisesRegex(ValueError, "provisional"):
            MODULE.materialize_selection(result, selection, actor="user",
                                         expected_proposal_sha256=result["proposal_sha256"])

    def test_verified_selection_materializes_valid_profiles_and_portfolio(self):
        result = proposal(goal_value=goals(
            desired_roles=["Clinical Data Analyst", "Software Engineer"]))
        selection = {"portfolio_id": "selected-v1", "name": "Selected", "allocations": [
            {"archetype_id": "clinical", "weight_percent": 80},
            {"archetype_id": "software", "weight_percent": 20},
        ]}
        adopted = MODULE.materialize_selection(
            result, selection, actor="user", expected_proposal_sha256=result["proposal_sha256"])
        self.assertEqual(len(adopted["profiles"]), 2)
        self.assertEqual(sum(item["weight_percent"]
                             for item in adopted["portfolio"]["allocations"]), 100)
        self.assertTrue(adopted["registration_required"])
        self.assertTrue(adopted["approval_required"])

    def test_materialization_requires_user_and_exact_hash(self):
        result = proposal()
        selection = {"portfolio_id": "selected-v1", "name": "Selected", "allocations": [
            {"archetype_id": "clinical", "weight_percent": 100}]}
        with self.assertRaisesRegex(ValueError, "user actor"):
            MODULE.materialize_selection(result, selection, actor="system",
                                         expected_proposal_sha256=result["proposal_sha256"])
        with self.assertRaisesRegex(ValueError, "hash"):
            MODULE.materialize_selection(result, selection, actor="user",
                                         expected_proposal_sha256="0" * 64)

    def test_invalid_goal_priorities_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "totaling 100"):
            proposal(goal_value=goals(priorities={"current_fit": 70, "career_value": 70}))

    def test_default_catalog_is_valid(self):
        value = json.loads(MODULE.CATALOG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(MODULE.validate_catalog(value)["archetypes"]), 7)

    def test_catalog_never_forces_an_unsupported_direction(self):
        result = MODULE.generate_proposal(
            proposal_id="empty-v1", candidate={"search": {}}, facts=[], mode="verified",
            source_sha256="b" * 64, catalog=catalog(), max_directions=2,
            created_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        )
        self.assertEqual(result["recommendations"], [])
        self.assertIsNone(result["suggested_portfolio"])
        self.assertFalse(result["adoptable"])
        self.assertIn("no_supported_direction_in_catalog", result["review_reasons"])

    def test_material_proposal_is_provisional_and_emits_fact_review(self):
        with tempfile.TemporaryDirectory() as temp:
            material = Path(temp) / "resume.txt"
            material.write_text(
                "EXPERIENCE\nClinical Trial Data Analyst\nManaged clinical trial data using SAS\n",
                encoding="utf-8",
            )
            catalog_path = Path(temp) / "catalog.json"
            catalog_path.write_text(json.dumps(catalog()), encoding="utf-8")
            result, packet = MODULE.propose_material(
                material, catalog_path, "upload-v1", max_directions=2)
        self.assertEqual(result["mode"], "provisional")
        self.assertFalse(result["adoptable"])
        self.assertTrue(packet["facts"])
        self.assertTrue(all(fact["decision"] == "pending" for fact in packet["facts"]))

    def test_pdf_fallback_extracts_text_without_pdftotext(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "resume.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=612, height=792)
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 72 720 Td (EXPERIENCE) Tj "
                            b"0 -20 Td (Clinical trial data using SAS) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
            with path.open("wb") as output:
                writer.write(output)
            with patch.object(MODULE.extract_candidate_facts.shutil, "which", return_value=None):
                text = MODULE.extract_candidate_facts.extract_pdf(path)
        self.assertIn("Clinical trial data using SAS", text)


if __name__ == "__main__":
    unittest.main()
