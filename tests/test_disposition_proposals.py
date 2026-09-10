"""What the confirmed facts say about a requirement, and what they refuse to say.

The rule the whole module turns on: **absence of evidence is not evidence of absence.** A
requirement nothing matched gets no proposed disposition — it gets a status saying nothing was
found, which is a different statement. `does_not_meet` is proposed only where the facts
positively establish the mismatch.

Two of these tests exist because the first version of the module broke the rule in ways that
only real postings showed.
"""

import importlib.util
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"proposals_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


P = load_script("disposition_proposals")

FACTS = [
    {"id": "fact-mph", "type": "education", "status": "confirmed", "locked": False,
     "evidence_strength": "direct",
     "value": "Master of Public Health – Environmental Health Science "
              "(Certificate in Biostatistics)"},
    {"id": "fact-bs", "type": "education", "status": "confirmed", "locked": False,
     "evidence_strength": "direct", "value": "Bachelor of Science – Medical Technology"},
    {"id": "fact-job1", "type": "experience_header", "status": "confirmed", "locked": False,
     "evidence_strength": "direct", "value": "Database Specialist | AstraZeneca 06/2020 – 08/2021"},
    {"id": "fact-job2", "type": "experience_header", "status": "confirmed", "locked": False,
     "evidence_strength": "direct", "value": "Data Analyst | Immunology 09/2023 - 10/2024"},
    {"id": "fact-prog", "type": "skill", "status": "confirmed", "locked": False,
     "evidence_strength": "direct", "value": "Programming: R, SAS, SQL, SPSS, Python",
     "keywords": ["R", "SAS", "SQL", "Python"]},
]


def propose(requirement):
    return P.propose(requirement, FACTS)


class CertificateIsNotADegreeTests(unittest.TestCase):
    """The substitution this repository refuses, in the one place it was happening."""

    def test_a_certificate_named_beside_a_degree_is_not_the_degree_field(self):
        held = {entry["fact_id"]: entry for entry in P.held_degrees(FACTS)}
        self.assertNotIn("biostatistics", held["fact-mph"]["fields"])
        self.assertIn("public health", held["fact-mph"]["fields"])

    def test_statistics_does_not_match_inside_biostatistics(self):
        """Without a word boundary, a Biostatistics certificate satisfied Statistics."""
        self.assertEqual(P.degree_fields("Master of Science in Biostatistics"),
                         ["biostatistics"])
        self.assertNotIn("statistics", P.degree_fields("MPH (Certificate in Biostatistics)"))

    def test_the_mph_does_not_satisfy_an_ms_in_statistics(self):
        """And the answer is "not recorded", not "does not hold": nothing asserts the
        education list is complete."""
        found = propose("MS or PhD in Statistics or Biostatistics")
        self.assertIsNone(found["proposed_disposition"])
        self.assertIn("education record is complete", found["short_reason"])
        self.assertIn("fact-mph", found["supporting_fact_ids"])


class CredentialTests(unittest.TestCase):
    """A named credential is matched by name; only a bare level uses the level ladder."""

    def test_a_credential_list_is_met_only_by_a_credential_it_names(self):
        found = propose("MD, PharmD, NP, PA, RN, MPH, or 5+ years in clinical practice")
        self.assertEqual(found["proposed_disposition"], P.MEETS)
        self.assertIn("MPH", found["short_reason"])
        self.assertEqual(found["supporting_fact_ids"], ["fact-mph"])

    def test_a_spelled_out_degree_matches_the_abbreviation_a_posting_uses(self):
        self.assertEqual(P.credentials_in("Master of Public Health – Env Health"), ["MPH"])

    def test_a_state_abbreviation_is_not_a_credential(self):
        self.assertEqual(P.credentials_in("Converse University; Spartanburg, SC May 2019"), [])

    def test_a_bare_level_uses_the_ladder(self):
        found = propose("Bachelor's degree required")
        self.assertEqual(found["proposed_disposition"], P.MEETS)

    def test_a_named_field_narrows_a_credential_match(self):
        self.assertIsNone(propose("MS in Biology")["proposed_disposition"])

    def test_a_level_nobody_holds_is_not_a_mismatch(self):
        """No completeness assertion exists, so an absent doctorate stays unproposed."""
        found = propose("PhD in Biostatistics required")
        self.assertIsNone(found["proposed_disposition"])
        self.assertEqual(found["candidate_evidence_status"], P.NO_EVIDENCE)


class DurationTests(unittest.TestCase):
    """A recorded span is reported. It is never read as the whole of a career."""

    def test_the_span_is_read_from_the_dated_headers(self):
        span = P.career_span_years(FACTS, today=date(2026, 9, 10))
        self.assertEqual(span["earliest"], "2020-06-01")
        self.assertEqual(span["latest"], "2024-10-01")
        self.assertAlmostEqual(span["years"], 4.3, delta=0.2)

    def test_a_requirement_longer_than_the_span_is_not_a_mismatch(self):
        found = P.propose("Minimum of 10 years of managing analytics", FACTS)
        self.assertIsNone(found["proposed_disposition"])
        self.assertIn("nothing asserts the employment record is complete",
                      found["short_reason"])

    def test_the_span_is_still_reported_with_its_facts(self):
        found = P.propose("Minimum of 10 years of managing analytics", FACTS)
        self.assertIn("4.3 years", found["short_reason"])
        self.assertEqual(found["supporting_fact_ids"], ["fact-job1", "fact-job2"])

    def test_a_long_enough_span_does_not_make_the_requirement_met(self):
        found = P.propose("3+ years of clinical trial operations", FACTS)
        self.assertIsNone(found["proposed_disposition"])


class AbsenceIsNotProofTests(unittest.TestCase):
    """The rule the module exists to keep, now with no exceptions at all."""

    def test_a_sentence_no_rule_reads_gets_no_disposition(self):
        found = propose("Ability to travel up to 10%")
        self.assertIsNone(found["proposed_disposition"])
        self.assertEqual(found["candidate_evidence_status"], P.NO_EVIDENCE)

    def test_a_recognised_concept_with_no_match_is_absence_not_mismatch(self):
        found = propose("Strong organizational and time management skills")
        self.assertNotEqual(found["proposed_disposition"], P.DOES_NOT_MEET)

    def test_a_missing_tool_is_absence_not_mismatch(self):
        found = propose("Production experience with Docker and Snowflake")
        self.assertIsNone(found["proposed_disposition"])

    def test_a_mixed_tool_list_is_partial_and_names_what_is_missing(self):
        found = propose("Experience with Python and Snowflake")
        self.assertEqual(found["proposed_disposition"], P.PARTIALLY_MEETS)
        self.assertIn("Snowflake", found["short_reason"])

    def test_a_skills_list_is_mention_only_and_never_proposed_as_met(self):
        """"Programming: R, SAS, SQL, Python" is a list of names, not demonstrated use."""
        found = propose("Strong SQL and Python")
        self.assertEqual(found["proposed_disposition"], P.PARTIALLY_MEETS)
        self.assertEqual(found["evidence_class"], "mention_only")

    def test_no_unproposed_row_carries_a_disposition_by_accident(self):
        for requirement in ("Ability to travel up to 10%", "Strong academic track record"):
            with self.subTest(requirement=requirement):
                self.assertIsNone(propose(requirement)["proposed_disposition"])

    def test_unclear_ask_employer_is_never_proposed(self):
        for requirement in ("Experience with things", "Some familiarity, or equivalent"):
            with self.subTest(requirement=requirement):
                self.assertNotEqual(propose(requirement)["proposed_disposition"],
                                    "unclear_ask_employer")

    def test_does_not_meet_is_not_reachable_at_all_yet(self):
        """No completeness assertion exists in the schema, so nothing can establish absence."""
        for requirement in ("PhD in Biostatistics required", "Docker in production",
                            "Minimum of 20 years", "Experience with REDCap and Epic",
                            "MS in Statistics", "Strong communication skills"):
            with self.subTest(requirement=requirement):
                self.assertNotEqual(propose(requirement)["proposed_disposition"],
                                    P.DOES_NOT_MEET)


class ProvenanceTests(unittest.TestCase):

    def test_every_factual_comparison_cites_fact_ids(self):
        for requirement in ("MS or PhD in Statistics or Biostatistics",
                            "Minimum of 10 years of managing analytics",
                            "Bachelor's degree required"):
            with self.subTest(requirement=requirement):
                found = propose(requirement)
                self.assertTrue(found["supporting_fact_ids"], found["short_reason"])

    def test_every_proposal_needs_confirmation_and_decides_nothing(self):
        for requirement in ("MS in Statistics", "Ability to travel", "At Beghou, you'll join"):
            with self.subTest(requirement=requirement):
                found = propose(requirement)
                self.assertTrue(found["requires_user_confirmation"])
                self.assertIsNone(found["final_disposition"])

    def test_boilerplate_is_not_a_candidate_comparison(self):
        for line in ("At Beghou, you'll join a highly collaborative team",
                     "We are an equal opportunity employer",
                     "We cannot provide visa sponsorship"):
            with self.subTest(line=line):
                found = propose(line)
                self.assertEqual(found["proposed_disposition"], P.NOT_A_REQUIREMENT)
                self.assertEqual(found["candidate_evidence_status"], P.NOT_APPLICABLE)


class AnnotateTests(unittest.TestCase):

    def sheet(self):
        return {"survivors": [{"employer": "Unlearn", "title": "Biostatistician",
                               "location": "SF", "lane": "partial_with_known_shortfall",
                               "lane_rank": 1, "parsed_lines": 4, "stated_lines": 5,
                               "unread_requirements": [
                                   {"requirement": "MS or PhD in Statistics or Biostatistics",
                                    "disposition": None, "note": None},
                                   {"requirement": "Strong communication skills",
                                    "disposition": None, "note": None}]}],
                "blocked": [{"employer": "Beghou", "decision": "hard_reject"}]}

    def test_annotate_fills_proposals_and_leaves_the_blocked_group_alone(self):
        sheet = P.annotate(self.sheet(), FACTS)
        self.assertIsNone(sheet["survivors"][0]["unread_requirements"][0]
                          ["proposed_disposition"])
        self.assertIsNone(sheet["survivors"][0]["unread_requirements"][1]
                          ["proposed_disposition"])
        self.assertEqual(sheet["blocked"][0]["decision"], "hard_reject")
        self.assertNotIn("unread_requirements", sheet["blocked"][0])

    def test_the_summary_says_it_decides_nothing(self):
        sheet = P.annotate(self.sheet(), FACTS)
        self.assertIn("requires user confirmation", sheet["proposals"]["decides_nothing"])
        self.assertEqual(sheet["proposals"]["counts"], {"unproposed": 2})

    def test_postings_with_the_least_unresolved_are_surfaced_first(self):
        sheet = P.annotate(self.sheet(), FACTS)
        sheet["survivors"].append({"employer": "X", "title": "Y", "location": "Z",
                                   "lane": "partial_no_known_gap", "lane_rank": 2,
                                   "parsed_lines": 1, "stated_lines": 9,
                                   "unread_requirements": []})
        self.assertEqual(P.decisive_first(sheet)[0]["employer"], "X")

    def test_the_rendered_sheet_states_that_absence_is_not_proof(self):
        text = P.render(P.annotate(self.sheet(), FACTS))
        self.assertIn("unrecorded, not unmet", text)
        self.assertIn("Nothing is proposed as `does_not_meet`", text)
        self.assertIn("fact-mph", text)

    def test_the_rendered_sheet_explains_what_meets_requires(self):
        text = P.render(P.annotate(self.sheet(), FACTS))
        self.assertIn("comes from a parse, not from a sentence", text)
        self.assertIn("nothing substantive may be left unread", text)
        self.assertIn("recognised by rule with nobody reviewing the line", text)

    def test_every_row_shows_its_parse_status_and_why_it_cannot_conclude(self):
        text = P.render(P.annotate(self.sheet(), FACTS))
        self.assertIn("- **parse:** `ambiguous`", text)
        self.assertIn("- **parse:** `unreviewed`", text)
        self.assertIn("nothing here concludes `meets`", text)
        self.assertIn("field biostatistics, statistics is written next to one alternative",
                      text)


if __name__ == "__main__":
    unittest.main()
