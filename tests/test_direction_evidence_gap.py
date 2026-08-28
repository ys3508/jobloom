"""Obligation-aware warning terms and required-skill evidence coverage.

The worked example is a Research / Clinical Research Data direction: a posting whose
title matches the direction exactly but whose mandatory requirements are a hospital EHR
stack the candidate has never touched. Title identity must not carry that posting to an
auto-match, and the same terms listed as *preferred* must not cost it anything.
"""

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"evidence_gap_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


DIRECTIONS = load_script("direction_core")

# Terms that are only a problem when the posting makes them mandatory.
WARNING_TERMS = [
    "Epic", "Clarity", "Caboodle", "Cogito", "REDCap", "EDC", "CTMS", "CDISC", "SDTM",
    "ADaM", "HL7", "FHIR", "ICD-10", "CPT coding", "Tableau", "Power BI",
]


def profile(**updates):
    value = {
        "schema_version": "0.1.0", "direction_id": "research-clinical-data",
        "name": "Research / Clinical Research Data", "role_family": "analytics.research_data",
        "target_titles": [
            "Research Data Analyst", "Clinical Research Data Analyst",
            "Health Research Data Analyst", "Public Health Data Analyst",
            "Healthcare Data Analyst", "Clinical Data Analyst",
        ],
        "auxiliary_titles": ["Research Analyst", "Data Analyst"],
        "positive_keywords": ["research data", "clinical trial", "data quality",
                              "statistical analysis", "data management"],
        "negative_keywords": [],
        "precision_keywords": ["study design"],
        "discovery_keywords": ["data validation"],
        "hard_exclusion_keywords": ["commission-only"],
        "warning_keywords": WARNING_TERMS,
        "criteria": {"industries": ["healthcare", "clinical", "public health"]},
        "parent_direction_id": None,
    }
    value.update(updates)
    return value


def candidate(**updates):
    auth = {"country": "US", "authorized_now": True, "sponsorship_now": False,
            "sponsorship_future": False, "employer_action_required": False, "confirmed": True}
    facts = [{"id": "fact-tools", "value": "R",
              "keywords": ["SAS", "SQL", "statistical", "analysis", "data", "cleaning",
                           "management", "quality", "control", "research", "clinical",
                           "trial", "database", "visualization"]}]
    value = {"work_authorization": auth, "search": {}, "facts": facts, "certifications": []}
    value.update(updates)
    return value


def job(**updates):
    card = {
        "job_id": "job-1", "canonical_url": "https://example.com/jobs/1",
        "employer": "Example Health System", "title": "Healthcare Data Analyst", "country": "US",
        "location": "New York, NY", "work_arrangement": "hybrid", "employment_type": "full_time",
        "salary": None, "status": "open", "sponsorship": "supports", "sponsorship_statements": [],
        "required_certifications": [], "preferred_certifications": [], "required_skills": [],
        "preferred_skills": [], "summary": None, "responsibilities": [],
        "compensation_structure": [], "seniority": "unknown", "experience": None,
        "requirements_reviewed": True,
    }
    card.update(updates)
    return card


def route(prof=None, cand=None, **card_updates):
    return DIRECTIONS.route_job(prof or profile(), cand or candidate(), job(**card_updates))


EHR_STACK = ["Epic", "Clarity", "Caboodle", "Power BI"]
COVERED_SKILLS = ["R", "SAS", "statistical analysis", "data cleaning"]


class WarningObligationTests(unittest.TestCase):
    def test_mandatory_warning_terms_demote_a_matching_title(self):
        result = route(required_skills=EHR_STACK)
        self.assertEqual(result["decision"], "review")
        self.assertIn("warning_term_required", result["review_reasons"])
        self.assertEqual(result["ranking_signals"]["warning_terms_required"],
                         ["Caboodle", "Clarity", "Epic", "Power BI"])
        self.assertLess(result["ranking_score"],
                        route(required_skills=COVERED_SKILLS)["ranking_score"])

    def test_preferred_warning_terms_cost_nothing(self):
        result = route(required_skills=COVERED_SKILLS, preferred_skills=EHR_STACK)
        self.assertEqual(result["decision"], "match")
        self.assertNotIn("warning_term_required", result["review_reasons"])
        self.assertIn("warning_term_preferred_only", result["notes"])
        self.assertEqual(result["ranking_signals"]["warning_penalty"], 0)
        self.assertEqual(result["ranking_signals"]["warning_terms_preferred_only"],
                         ["Caboodle", "Clarity", "Epic", "Power BI"])

    def test_a_term_required_and_preferred_counts_once_as_required(self):
        result = route(required_skills=["Epic"] + COVERED_SKILLS, preferred_skills=["Epic"])
        self.assertEqual(result["ranking_signals"]["warning_terms_required"], ["Epic"])
        self.assertEqual(result["ranking_signals"]["warning_terms_preferred_only"], [])

    def test_warning_terms_are_never_read_from_prose(self):
        result = route(required_skills=COVERED_SKILLS,
                       summary="Our team is migrating off Epic and Tableau this year.",
                       responsibilities=["Retire the legacy Power BI dashboards"])
        self.assertEqual(result["decision"], "match")
        self.assertEqual(result["ranking_signals"]["warning_terms_required"], [])


class EvidenceSupportTests(unittest.TestCase):
    def test_title_match_without_evidence_support_is_surfaced(self):
        result = route(required_skills=EHR_STACK)
        self.assertIn("title_match_without_evidence_support", result["review_reasons"])
        self.assertEqual(result["ranking_signals"]["required_skills_stated"], 4)
        self.assertEqual(result["ranking_signals"]["required_skills_covered_by_facts"], 0)
        self.assertEqual(
            [item["requirement"] for item in result["required_skill_evidence"]], EHR_STACK)
        self.assertTrue(all(item["covered_by_candidate_fact"] is False
                            for item in result["required_skill_evidence"]))

    def test_covered_requirements_keep_the_match(self):
        result = route(required_skills=COVERED_SKILLS)
        self.assertEqual(result["decision"], "match")
        self.assertNotIn("title_match_without_evidence_support", result["review_reasons"])
        self.assertEqual(result["ranking_signals"]["required_skills_covered_by_facts"], 4)

    def test_a_thin_requirement_list_is_too_noisy_to_demote(self):
        result = route(required_skills=["Epic", "Caboodle"])
        self.assertNotIn("title_match_without_evidence_support", result["review_reasons"])
        self.assertEqual(result["ranking_signals"]["evidence_gap_penalty"], 0)

    def test_half_covered_requirements_are_not_demoted(self):
        result = route(required_skills=["R", "SAS", "Epic", "Caboodle"])
        self.assertNotIn("title_match_without_evidence_support", result["review_reasons"])

    def test_evidence_support_never_applies_without_a_title_match(self):
        result = route(title="Warehouse Picker", required_skills=EHR_STACK)
        self.assertEqual(result["decision"], "fail")
        self.assertNotIn("title_match_without_evidence_support", result["review_reasons"])

    def test_requirement_text_is_returned_but_never_persisted_in_reason_codes(self):
        result = route(required_skills=EHR_STACK)
        for reason in result["review_reasons"] + result["hard_failures"] + result["notes"]:
            for term in EHR_STACK:
                self.assertNotIn(term.casefold(), reason.casefold())


class ProfileCompatibilityTests(unittest.TestCase):
    def test_a_profile_without_warning_keywords_behaves_as_before(self):
        without = profile()
        del without["warning_keywords"]
        result = route(prof=without, required_skills=EHR_STACK)
        self.assertNotIn("warning_term_required", result["review_reasons"])
        self.assertEqual(result["ranking_signals"]["warning_terms_required"], [])

    def test_validation_never_adds_warning_keywords_to_an_existing_profile(self):
        without = profile()
        del without["warning_keywords"]
        before = DIRECTIONS.canonical_hash(DIRECTIONS.validate_profile(without))
        self.assertNotIn("warning_keywords", DIRECTIONS.validate_profile(without))
        self.assertEqual(before, DIRECTIONS.canonical_hash(DIRECTIONS.validate_profile(without)))

    def test_unknown_profile_fields_are_still_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing or unknown"):
            DIRECTIONS.validate_profile(profile(warning_keyword=["Epic"]))

    def test_missing_required_profile_fields_are_still_rejected(self):
        value = profile()
        del value["target_titles"]
        with self.assertRaisesRegex(ValueError, "missing or unknown"):
            DIRECTIONS.validate_profile(value)

    def test_warning_keywords_are_bounded_like_every_other_group(self):
        with self.assertRaisesRegex(ValueError, "warning_keywords"):
            DIRECTIONS.validate_profile(profile(warning_keywords=["Epic", "epic"]))


if __name__ == "__main__":
    unittest.main()
