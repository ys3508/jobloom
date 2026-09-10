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
    """The substitution this repository refuses, in the one place it was actually happening."""

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
        found = propose("MS or PhD in Statistics or Biostatistics")
        self.assertEqual(found["proposed_disposition"], P.DOES_NOT_MEET)
        self.assertIn("fact-mph", found["supporting_fact_ids"])
        self.assertIn("certificate is not the degree", found["short_reason"])


class DegreeLevelTests(unittest.TestCase):

    def test_a_requirement_naming_two_levels_is_met_by_the_lower(self):
        """"MS or PhD" is satisfied by a master's; taking the first match said "no doctorate"."""
        self.assertEqual(P.named_levels("MS or PhD in Statistics"), ["master", "doctorate"])

    def test_a_level_nobody_holds_is_a_positive_mismatch(self):
        found = propose("PhD in Biostatistics required")
        self.assertEqual(found["proposed_disposition"], P.DOES_NOT_MEET)
        self.assertIn("none reaches that level", found["short_reason"])

    def test_a_degree_with_no_field_named_is_met(self):
        found = propose("Bachelor's degree required")
        self.assertEqual(found["proposed_disposition"], P.MEETS)
        self.assertTrue(found["supporting_fact_ids"])


class OpenEndedFieldTests(unittest.TestCase):
    """An open list establishes nothing by not containing your field.

    The first version proposed `does_not_meet` against "a quantitative field (e.g.,
    Statistics…)" and "STEM … or equivalent" — over-claiming in exactly the way the rule
    forbids, and only visible on real postings.
    """

    def test_an_eg_list_does_not_settle_a_mismatch(self):
        found = propose("Bachelor's or Master's degree in a quantitative field "
                        "(e.g., Statistics, Mathematics, Computer Science)")
        self.assertIsNone(found["proposed_disposition"])
        self.assertEqual(found["candidate_evidence_status"], P.NO_EVIDENCE)

    def test_an_or_equivalent_clause_does_not_settle_a_mismatch(self):
        found = propose("Bachelor's degree in a STEM, quantitative, or healthcare "
                        "informatics field, or equivalent practical experience")
        self.assertIsNone(found["proposed_disposition"])

    def test_or_related_field_does_not_settle_a_mismatch(self):
        self.assertIsNone(propose("BS in Statistics or a related field")
                          ["proposed_disposition"])

    def test_a_closed_list_still_settles_it(self):
        self.assertEqual(propose("MS in Statistics or Biostatistics")["proposed_disposition"],
                         P.DOES_NOT_MEET)

    def test_an_unknown_field_word_is_not_read_as_no_field(self):
        """"a quantitative field" names a field; the vocabulary just does not hold it."""
        found = propose("Bachelor's degree in a quantitative field")
        self.assertIsNone(found["proposed_disposition"])
        self.assertIn("vocabulary does not hold", found["short_reason"])


class DurationTests(unittest.TestCase):

    def test_a_span_shorter_than_the_requirement_is_a_positive_mismatch(self):
        span = P.career_span_years(FACTS, today=date(2026, 9, 10))
        self.assertAlmostEqual(span["years"], 4.3, delta=0.2)
        found = P.propose("Minimum of 10 years of managing analytics", FACTS, span=span)
        self.assertEqual(found["proposed_disposition"], P.DOES_NOT_MEET)
        self.assertEqual(found["supporting_fact_ids"], ["fact-job1", "fact-job2"])

    def test_a_long_enough_span_does_not_make_the_requirement_met(self):
        """The span says nothing about the domain the requirement names."""
        span = P.career_span_years(FACTS, today=date(2026, 9, 10))
        found = P.propose("3+ years of clinical trial operations", FACTS, span=span)
        self.assertIsNone(found["proposed_disposition"])
        self.assertIn("do not establish", found["short_reason"])

    def test_the_span_is_read_from_the_dated_headers(self):
        span = P.career_span_years(FACTS, today=date(2026, 9, 10))
        self.assertEqual(span["earliest"], "2020-06-01")
        self.assertEqual(span["latest"], "2024-10-01")


class AbsenceIsNotProofTests(unittest.TestCase):
    """The rule the module exists to keep."""

    def test_a_sentence_no_rule_reads_gets_no_disposition(self):
        found = propose("Ability to travel up to 10%")
        self.assertIsNone(found["proposed_disposition"])
        self.assertEqual(found["candidate_evidence_status"], P.NO_EVIDENCE)
        self.assertIn("no controlled rule", found["short_reason"])

    def test_a_recognised_concept_with_no_match_is_absence_not_mismatch(self):
        """"Strong communication skills" unmatched says the profile does not phrase it so."""
        found = propose("Strong organizational and time management skills")
        self.assertIsNone(found["proposed_disposition"])
        self.assertIn("not a mismatch", found["short_reason"])

    def test_a_named_technology_the_profile_lacks_is_a_positive_mismatch(self):
        found = propose("Production experience with Docker and Snowflake")
        self.assertEqual(found["proposed_disposition"], P.DOES_NOT_MEET)
        self.assertIn("Docker", found["short_reason"])

    def test_a_skills_list_is_mention_only_and_never_proposed_as_met(self):
        """"Programming: R, SAS, SQL, Python" is a list of names, not demonstrated use.

        The resolver caps a skills-list fact at `mention_only`, and the proposal carries that
        through rather than reading a tool's presence in a list as meeting the requirement.
        """
        found = propose("Strong SQL and Python")
        self.assertEqual(found["proposed_disposition"], P.PARTIALLY_MEETS)
        self.assertEqual(found["evidence_class"], "mention_only")
        self.assertIn("fact-prog", found["supporting_fact_ids"])

    def test_a_mixed_tool_list_is_partial_and_names_both_halves(self):
        found = propose("Experience with Python and Snowflake")
        self.assertEqual(found["proposed_disposition"], P.PARTIALLY_MEETS)
        self.assertIn("covered: Python", found["short_reason"])
        self.assertIn("not covered: Snowflake", found["short_reason"])

    def test_a_tool_name_inside_a_longer_word_is_not_matched(self):
        """`Terra` must not match inside `Terraform`."""
        self.assertEqual(P.named_technologies("Terraform modules"), [])

    def test_no_unproposed_row_carries_a_disposition_by_accident(self):
        for requirement in ("Ability to travel up to 10%", "Strong academic track record",
                            "Excellent verbal and written communication skills"):
            with self.subTest(requirement=requirement):
                self.assertIsNone(propose(requirement)["proposed_disposition"])

    def test_unclear_ask_employer_is_never_proposed(self):
        """Whether an employer wrote ambiguously is not a fact about the candidate."""
        for requirement in ("Experience with things", "Some familiarity, or equivalent",
                            "Not a perfect match yet?"):
            with self.subTest(requirement=requirement):
                self.assertNotEqual(propose(requirement)["proposed_disposition"],
                                    "unclear_ask_employer")


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
        self.assertEqual(sheet["survivors"][0]["unread_requirements"][0]
                         ["proposed_disposition"], P.DOES_NOT_MEET)
        self.assertIsNone(sheet["survivors"][0]["unread_requirements"][1]
                          ["proposed_disposition"])
        self.assertEqual(sheet["blocked"][0]["decision"], "hard_reject")
        self.assertNotIn("unread_requirements", sheet["blocked"][0])

    def test_the_summary_says_it_decides_nothing(self):
        sheet = P.annotate(self.sheet(), FACTS)
        self.assertIn("requires user confirmation", sheet["proposals"]["decides_nothing"])
        self.assertEqual(sheet["proposals"]["counts"],
                         {P.DOES_NOT_MEET: 1, "unproposed": 1})

    def test_postings_with_a_blocking_proposal_are_surfaced_first(self):
        sheet = P.annotate(self.sheet(), FACTS)
        sheet["survivors"].append({"employer": "X", "title": "Y", "location": "Z",
                                   "lane": "partial_no_known_gap", "lane_rank": 1,
                                   "parsed_lines": 1, "stated_lines": 9,
                                   "unread_requirements": []})
        self.assertEqual(P.decisive_first(sheet)[0]["employer"], "Unlearn")

    def test_the_rendered_sheet_states_that_absence_is_not_proof(self):
        text = P.render(P.annotate(self.sheet(), FACTS))
        self.assertIn("Absence of evidence is not evidence of absence", text)
        self.assertIn("fact-mph", text)


if __name__ == "__main__":
    unittest.main()
