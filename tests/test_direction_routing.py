"""Phase 2 routing semantics: field scoping, tokenizer, seniority, credentials,
duty detection, criteria and sponsorship.

Roughly half of these are false-positive guards: cases containing a trigger term
that must NOT be discarded. A silent discard is invisible to the user, so each
guard pins a role this portfolio is explicitly meant to keep.
"""

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"routing_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


DIRECTIONS = load_script("direction_core")
INGEST = load_script("ingest_job")


def profile(**updates):
    value = {
        "schema_version": "0.1.0", "direction_id": "lsc", "name": "Life Sciences Consulting",
        "role_family": "consulting.life_sciences",
        "target_titles": ["Life Sciences Consultant"],
        "auxiliary_titles": ["Market Research Analyst"],
        "positive_keywords": ["market access", "claims data"],
        "negative_keywords": ["quota"],
        "precision_keywords": ["HEOR"],
        "discovery_keywords": ["survival analysis"],
        "hard_exclusion_keywords": ["commission-only", "unpaid internship", "pay-to-join"],
        "criteria": {}, "parent_direction_id": None,
    }
    value.update(updates)
    return value


def candidate(certifications=None, facts=None, search=None, **authorization):
    auth = {"country": "US", "authorized_now": True, "sponsorship_now": False,
            "sponsorship_future": False, "employer_action_required": False, "confirmed": True}
    auth.update(authorization)
    return {"work_authorization": auth, "search": search or {}, "facts": facts or [],
            "certifications": certifications or []}


def job(**updates):
    card = {
        "job_id": "job-1", "canonical_url": "https://example.com/jobs/1",
        "employer": "Example Health", "title": "Life Sciences Consultant", "country": "US",
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


class FieldScopingTests(unittest.TestCase):
    def test_untrusted_description_never_reaches_a_decision(self):
        result = route(title="Warehouse Picker",
                       description="Life Sciences Consultant. commission-only. market access.")
        self.assertEqual(result["decision"], "fail")
        self.assertIn("outside_direction_title_scope", result["hard_failures"])
        self.assertEqual(sum(len(hits) for hits in result["field_hits"].values()), 0)

    def test_description_is_permanently_outside_the_routing_surface(self):
        self.assertNotIn("description", DIRECTIONS.ROUTING_FIELDS)
        self.assertIn("description", DIRECTIONS.ROUTING_DENYLIST)
        self.assertFalse(set(DIRECTIONS.ROUTING_FIELDS) & DIRECTIONS.ROUTING_DENYLIST)

    def test_target_title_outside_the_title_field_is_a_reference_not_a_match(self):
        result = route(title="Executive Assistant",
                       responsibilities=["Schedule travel for our Life Sciences Consultant team"])
        self.assertEqual(result["decision"], "review")
        self.assertIn("contextual_title_reference_only", result["review_reasons"])
        self.assertEqual(result["field_hits"]["target_titles"], [])

    def test_page_controlled_skills_cannot_forge_a_title_match(self):
        result = route(title="Warehouse Picker",
                       required_skills=["Experience supporting a Life Sciences Consultant"])
        self.assertEqual(result["decision"], "review")
        self.assertIn("contextual_title_reference_only", result["review_reasons"])

    def test_wrong_typed_routing_fields_are_rejected_not_coerced(self):
        for field, value in (("title", ["Senior Data Analyst"]),
                             ("required_skills", {"note": "commission-only role"}),
                             ("responsibilities", "not a list")):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, f"malformed job card field: {field}"):
                    route(**{field: value})

    def test_precision_keywords_are_matched_and_ranked(self):
        result = route(preferred_skills=["HEOR modelling"])
        self.assertEqual(result["decision"], "match")
        self.assertEqual(result["ranking_signals"]["precision_keyword_terms"], ["HEOR"])

    def test_missing_routing_fields_are_reported_not_assumed(self):
        card = job()
        del card["responsibilities"]
        result = DIRECTIONS.route_job(profile(), candidate(), card)
        self.assertIn("routing_surface_incomplete", result["notes"])


class TokenizerFalsePositiveTests(unittest.TestCase):
    """Substring matching produced every one of these; token matching must not."""

    def test_short_terms_do_not_match_inside_longer_words(self):
        cases = [
            ("CRO", ["Microsoft Excel"]),
            ("AI", ["Maintain claims and email records"]),
            ("ML", ["HTML and XML reporting"]),
            ("R", ["Prior experience required", "Microsoft Excel"]),
        ]
        for term, skills in cases:
            with self.subTest(term=term):
                result = route(profile(positive_keywords=[term]), required_skills=skills)
                self.assertEqual(result["field_hits"]["positive_keywords"], [])

    def test_a_short_term_still_matches_a_real_standalone_mention(self):
        result = route(profile(positive_keywords=["R"]), required_skills=["SAS, R, and Python"])
        self.assertEqual(len(result["field_hits"]["positive_keywords"]), 1)

    def test_sales_does_not_match_salesforce(self):
        result = route(profile(negative_keywords=["sales"]), required_skills=["Salesforce CRM data"])
        self.assertEqual(result["field_hits"]["negative_keywords"], [])

    def test_employer_name_is_never_keyword_matched(self):
        result = route(profile(negative_keywords=["sales"]), employer="Salesforce")
        self.assertEqual(result["field_hits"]["negative_keywords"], [])

    def test_hyphenation_no_longer_evades_a_hard_exclusion(self):
        result = route(compensation_structure=["Compensation is commission only"])
        self.assertEqual(result["decision"], "fail")
        self.assertIn("direction_hard_exclusion", result["hard_failures"])


class HardExclusionTierTests(unittest.TestCase):
    def test_structured_pay_model_is_decisive(self):
        result = route(compensation_structure=["pay to join program; paid training required"])
        self.assertEqual(result["decision"], "fail")

    def test_negated_prose_is_triaged_rather_than_discarded(self):
        result = route(responsibilities=[
            "We do not offer commission-only compensation; all analysts are salaried"])
        self.assertEqual(result["decision"], "review")
        self.assertIn("direction_hard_exclusion_context_only", result["review_reasons"])

    def test_skills_are_outside_hard_exclusion_scope(self):
        result = route(required_skills=["commission-only sales experience"])
        self.assertEqual(result["hard_failures"], [])


class SeniorityTests(unittest.TestCase):
    def test_punctuated_and_abbreviated_seniority_no_longer_evades(self):
        for title in ("Senior Life Sciences Consultant", "Sr. Life Sciences Consultant",
                      "Senior, Life Sciences Consultant", "Life Sciences Consultant (Senior)",
                      "Senior-Level Life Sciences Consultant"):
            with self.subTest(title=title):
                result = route(title=title)
                self.assertIn("seniority_outside_portfolio", result["hard_failures"])

    def test_domain_vocabulary_is_not_a_seniority_signal(self):
        cases = ["Senior Care Life Sciences Consultant", "Lead Generation Life Sciences Consultant",
                 "Principal Component Life Sciences Consultant", "Staff Nurse Life Sciences Consultant"]
        for title in cases:
            with self.subTest(title=title):
                result = route(title=title)
                self.assertNotIn("seniority_outside_portfolio", result["hard_failures"])
                self.assertIn("seniority_token_context_suppressed", result["notes"])

    def test_blocked_tokens_are_exactly_what_the_user_specified(self):
        self.assertEqual(DIRECTIONS.BLOCKED_SENIORITY_TOKENS,
                         frozenset({"senior", "sr", "snr", "lead", "principal", "staff"}))

    def test_stated_experience_above_range_fails_only_when_required(self):
        required = route(experience={"min_years": 8, "max_years": None,
                                     "basis": "minimum", "strictness": "required"})
        self.assertIn("experience_requirement_above_candidate_range", required["hard_failures"])
        preferred = route(experience={"min_years": 8, "max_years": None,
                                      "basis": "minimum", "strictness": "preferred"})
        self.assertEqual(preferred["decision"], "review")
        self.assertIn("experience_preference_above_candidate_range", preferred["review_reasons"])

    def test_zero_to_three_years_passes_without_inferring_from_title(self):
        result = route(experience={"min_years": 0, "max_years": 3,
                                   "basis": "range", "strictness": "required"})
        self.assertEqual(result["decision"], "match")
        self.assertEqual(result["seniority_assessment"]["experience"]["state"], "within_reach")

    def test_malformed_experience_is_reviewed_never_raised(self):
        result = route(experience={"min_years": "eight"})
        self.assertEqual(result["decision"], "review")
        self.assertIn("experience_field_malformed", result["review_reasons"])


class CredentialTests(unittest.TestCase):
    def test_a_held_credential_no_longer_rejects_the_candidate(self):
        """The previous check failed candidates who actually held the licence."""
        result = route(cand=candidate(certifications=["Registered Nurse"]),
                       required_certifications=["RN"])
        self.assertEqual(result["hard_failures"], [])
        self.assertIn("credential_alias_not_in_candidate_profile:rn", result["review_reasons"])

    def test_punctuation_and_parenthetical_forms_normalize(self):
        for required, held in (("R.N.", "RN"), ("Pharm.D.", "PharmD"), ("Registered Nurse", "RN")):
            with self.subTest(required=required):
                result = route(cand=candidate(certifications=[held]),
                               required_certifications=[required])
                self.assertEqual(result["hard_failures"], [])

    def test_a_credential_not_held_fails_whatever_it_is(self):
        result = route(cand=candidate(certifications=[]), required_certifications=["CCRP"])
        self.assertIn("required_credential_not_held:ccrp", result["hard_failures"])

    def test_placeholder_values_are_not_credentials(self):
        result = route(required_certifications=["None", "N/A"])
        self.assertEqual(result["decision"], "match")
        self.assertEqual(result["credential_findings"], [])

    def test_prose_in_the_credential_field_is_reviewed_not_failed(self):
        result = route(required_certifications=[
            "Must hold an active clinical license in the state of employment"])
        self.assertEqual(result["decision"], "review")
        self.assertIn("required_credential_unparsed:required_certifications.0",
                      result["review_reasons"])

    def test_a_preferred_credential_never_rejects(self):
        result = route(required_certifications=["RN preferred"])
        self.assertEqual(result["hard_failures"], [])

    def test_no_clinical_licence_list_is_hardcoded(self):
        source = (ROOT / "skills" / "jobloom" / "scripts" / "direction_core.py").read_text()
        self.assertNotIn("required_clinical_license_missing", source)
        self.assertNotIn('"pharmd"}', source)


class DutyDetectionTests(unittest.TestCase):
    """The user's keep-list: analytics roles that merely contain sales vocabulary."""

    def test_quota_carrying_sales_is_excluded(self):
        result = route(profile(target_titles=["Account Executive"]), title="Account Executive",
                       responsibilities=["carry a quota", "cold calling into new accounts"])
        self.assertEqual(result["decision"], "fail")
        self.assertIn("quota_carrying_sales_title", result["hard_failures"])
        self.assertIn("quota_carrying_sales_duties", result["hard_failures"])

    def test_analytics_roles_with_sales_vocabulary_are_kept(self):
        cases = [
            ("Sales Operations Analyst",
             ["quota setting and territory alignment", "incentive compensation reporting"]),
            ("Commercial Insights Analyst",
             ["sales performance analysis", "territory level reporting"]),
            ("Business Development Analyst",
             ["market sizing analysis", "opportunity research"]),
        ]
        for title, responsibilities in cases:
            with self.subTest(title=title):
                result = route(profile(target_titles=[title]), title=title,
                               responsibilities=responsibilities)
                self.assertNotEqual(result["decision"], "fail")

    def test_the_joint_commission_is_not_a_sales_signal(self):
        result = route(profile(target_titles=["Clinical Data Analyst"]), title="Clinical Data Analyst",
                       responsibilities=["Support The Joint Commission accreditation reporting"])
        self.assertEqual(result["decision"], "match")

    def test_data_and_product_pipelines_are_not_sales_signals(self):
        result = route(profile(target_titles=["Healthcare Data Analyst"]), title="Healthcare Data Analyst",
                       responsibilities=["Build ETL data pipelines", "Analyze the oncology product pipeline"])
        self.assertEqual(result["decision"], "match")

    def test_generic_business_vocabulary_is_never_a_signal(self):
        forbidden = {"sales", "commercial", "business development", "commission", "territory",
                     "pipeline", "sell", "revenue", "quota", "account", "client", "customer"}
        signals = set(DIRECTIONS.SALES_ROLE_TITLES) | set(DIRECTIONS.SALES_OWNERSHIP_SIGNALS)
        self.assertEqual(forbidden & signals, set())

    def test_pure_erp_configuration_is_excluded_but_clinical_build_is_kept(self):
        erp = route(profile(target_titles=["ERP Implementation Consultant"]),
                    title="ERP Implementation Consultant",
                    responsibilities=["sap modules configuration", "user provisioning"])
        self.assertIn("pure_erp_it_configuration", erp["hard_failures"])
        clinical = route(profile(target_titles=["Clinical Informatics Analyst"]),
                         title="Clinical Informatics Analyst",
                         responsibilities=["Epic build support", "EHR data quality analysis"])
        self.assertNotEqual(clinical["decision"], "fail")

    def test_mandatory_advanced_methods_route_to_review_but_preferred_gaps_do_not(self):
        mandatory = route(required_skills=["pharmacoepidemiology"])
        self.assertEqual(mandatory["decision"], "review")
        self.assertIn("advanced_requirement_mandatory", mandatory["review_reasons"])
        preferred = route(preferred_skills=["pharmacoepidemiology"])
        self.assertEqual(preferred["decision"], "match")
        self.assertIn("advanced_requirement_preferred_only", preferred["notes"])

    def test_coordinator_roles_are_demoted_never_failed(self):
        prof = profile(target_titles=["Clinical Data Coordinator", "Clinical Data Analyst"])
        coordinator = route(prof, title="Clinical Data Coordinator")
        analyst = route(prof, title="Clinical Data Analyst")
        self.assertEqual(coordinator["decision"], "match")
        self.assertEqual(coordinator["hard_failures"], [])
        self.assertEqual(coordinator["ranking_signals"]["career_growth_penalty"], 1)
        self.assertEqual(analyst["ranking_score"] - coordinator["ranking_score"], 15)


class CriteriaTests(unittest.TestCase):
    def test_empty_criteria_never_discard_a_job(self):
        prof = profile(criteria={key: ([] if key in DIRECTIONS.LIST_CRITERIA else None)
                                 for key in DIRECTIONS.CRITERIA_KEYS})
        result = route(prof)
        self.assertEqual(result["decision"], "match")
        self.assertEqual(set(result["criteria_evaluated"]), set(DIRECTIONS.CRITERIA_KEYS))
        self.assertTrue(all(status == "not_configured"
                            for status in result["criteria_evaluated"].values()))

    def test_every_criteria_key_is_reported_even_when_absent(self):
        result = route(profile(criteria={}))
        self.assertEqual(set(result["criteria_evaluated"]), set(DIRECTIONS.CRITERIA_KEYS))

    def test_a_direction_may_narrow_but_never_widen_the_candidate_profile(self):
        cand = candidate(search={"countries": ["US"]})
        widening = route(profile(criteria={"countries": ["US", "CA"]}), cand, country="CA")
        self.assertIn("direction_criteria_widens_candidate_profile:countries",
                      widening["criteria_conflicts"])
        self.assertIn("direction_country_outside_scope", widening["hard_failures"])

    def test_a_remote_job_is_not_discarded_by_a_location_list(self):
        result = route(profile(criteria={"locations": ["Boston, MA"]}), work_arrangement="remote")
        self.assertEqual(result["decision"], "match")
        self.assertEqual(result["criteria_evaluated"]["locations"], "not_applicable_remote")

    def test_criteria_without_a_jobcard_field_say_so_instead_of_implying_enforcement(self):
        result = route(profile(criteria={"industries": ["pharma"], "company_sizes": ["1-50"]}))
        self.assertEqual(result["criteria_evaluated"]["industries"], "no_jobcard_field")
        self.assertEqual(result["criteria_evaluated"]["company_sizes"], "no_jobcard_field")

    def test_seniority_criteria_are_inert_because_they_hold_job_families(self):
        result = route(profile(criteria={"seniority": ["entry", "analyst"]}))
        self.assertEqual(result["criteria_evaluated"]["seniority"], "title_token_gate_only")
        self.assertEqual(result["decision"], "match")

    def test_travel_limit_null_applies_no_travel_filter(self):
        result = route(profile(criteria={"travel_limit": None}))
        self.assertEqual(result["decision"], "match")


class SponsorshipTests(unittest.TestCase):
    def test_no_sponsorship_required_is_not_a_refusal(self):
        result = route(cand=candidate(sponsorship_future=True),
                       sponsorship="unknown",
                       sponsorship_statements=["No sponsorship required for this role."])
        self.assertNotEqual(result["decision"], "fail")
        self.assertEqual(result["sponsorship_assessment"]["effective"], "unknown")

    def test_an_explicit_refusal_is_detected_from_posting_text(self):
        result = route(cand=candidate(sponsorship_now=True), sponsorship="unknown",
                       sponsorship_statements=[
                           "We are unable to sponsor or take over sponsorship of an employment visa."])
        self.assertEqual(result["decision"], "fail")
        self.assertIn("required_sponsorship_not_supported", result["hard_failures"])

    def test_trial_sponsor_language_is_not_immigration_sponsorship(self):
        result = route(cand=candidate(sponsorship_future=True), sponsorship="unknown",
                       sponsorship_statements=["You will manage the sponsor-CRO relationship."])
        self.assertNotEqual(result["decision"], "fail")
        self.assertIn("sponsorship_statement_non_visa_sense", result["review_reasons"])

    def test_an_h1b_transfer_need_alone_is_honored(self):
        result = route(cand=candidate(employer_action_required=True), sponsorship="does_not_support")
        self.assertIn("required_sponsorship_not_supported", result["hard_failures"])
        self.assertIn("sponsorship_required_by:employer_action_required", result["notes"])

    def test_authorization_now_is_never_used_as_a_sponsorship_proxy(self):
        result = route(cand=candidate(authorized_now=False), sponsorship="does_not_support")
        self.assertEqual(result["hard_failures"], [])

    def test_conflicting_signals_are_distinguishable_from_unknown(self):
        result = route(cand=candidate(sponsorship_future=True), sponsorship="unknown",
                       sponsorship_statements=["We are unable to sponsor employment visas.",
                                               "Visa sponsorship available for strong candidates."])
        self.assertEqual(result["sponsorship_assessment"]["priority"], 1)
        self.assertIn("employer_sponsorship_conflict_requires_user_resolution", result["review_reasons"])
        unknown = route(cand=candidate(sponsorship_future=True), sponsorship="unknown")
        self.assertEqual(unknown["sponsorship_assessment"]["priority"], 2)

    def test_ingestion_extracts_bounded_sponsorship_segments(self):
        card = INGEST.build_card(
            "Data Analyst. We are unable to sponsor employment visas. Manage the sponsor-CRO tie.",
            "https://example.com/j/1", "text")
        self.assertEqual(card["schema_version"], "0.3.0")
        self.assertEqual(len(card["sponsorship_statements"]), 2)
        self.assertTrue(card["extraction"]["sponsorship_scan"]["scanned"])
        self.assertFalse(card["requirements_reviewed"])


class TrustBoundaryTests(unittest.TestCase):
    def test_an_unreviewed_card_can_never_reach_match(self):
        result = route(requirements_reviewed=False)
        self.assertEqual(result["decision"], "review")
        self.assertIn("job_card_unreviewed", result["review_reasons"])

    def test_incomplete_work_authorization_fails_closed(self):
        broken = candidate()
        del broken["work_authorization"]["sponsorship_future"]
        result = route(cand=broken)
        self.assertIn("work_authorization_incomplete", result["hard_failures"])

    def test_ranking_never_rescues_a_hard_failure(self):
        result = route(cand=candidate(sponsorship_future=True), title="Senior Life Sciences Consultant",
                       sponsorship="supports")
        self.assertEqual(result["decision"], "fail")
        self.assertGreater(result["ranking_score"], DIRECTIONS.RANKING_BASE)

    def test_hard_failures_are_order_independent(self):
        result = route(title="Senior Warehouse Picker", required_certifications=["CCRP"])
        self.assertEqual(result["hard_failures"], sorted(set(result["hard_failures"])))


if __name__ == "__main__":
    unittest.main()
