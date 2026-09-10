"""What a partial concept hit is not allowed to endorse.

Every case here was produced by the shipped proposer and is a promotion: a fact covering one
word of a requirement was made to answer the whole sentence. The pattern is always the same —
`match_requirement_prose` resolves the concepts it knows, ignores every qualifier and
obligation it does not, and aggregates what it found at its strongest; the proposer then reads
a local `direct` as a whole-sentence `meets`.

Twenty-nine tests passed over that behaviour, so these exist to cover the boundary the others
never touched: not whether a proposal is well-formed, but whether it is entitled to be made.

The rule they all express: **`meets` requires every mandatory obligation in the sentence to be
resolved and evidenced.** Unresolved residue caps the answer at `partially_meets`, or leaves
it unproposed. It never disappears.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"overreach_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


P = load_script("disposition_proposals")


def fact(fid, ftype, value, **kw):
    return {"id": fid, "type": ftype, "status": "confirmed", "locked": False,
            "evidence_strength": "direct", "value": value, **kw}


PRESENTATION = [fact("f1", "experience_claim", "Produced a classroom presentation")]
DATABASE = [fact("f2", "experience_claim", "Built a database for information management")]
STATS = [fact("f3", "experience_claim", "Performed statistical analysis")]
MBA = [fact("f4", "education", "Master of Business Administration")]
MPH = [fact("f5", "education", "Master of Public Health – Environmental Health Sciences")]


class NotMeetsTests(unittest.TestCase):
    """Reproduced from the shipped proposer. Each returned `meets`."""

    def assertNotMeets(self, requirement, facts):
        found = P.propose(requirement, facts)
        self.assertNotEqual(
            found["proposed_disposition"], P.MEETS,
            f"{requirement!r} was answered by {facts[0]['value']!r}: "
            f"{found['short_reason']}")
        return found

    def test_a_classroom_presentation_is_not_executive_level_communication(self):
        found = self.assertNotMeets("Executive-level communication skills", PRESENTATION)
        self.assertIn("executive", " ".join(found.get("unverified_obligations") or []).lower())

    def test_building_a_database_is_not_time_management(self):
        self.assertNotMeets("Strong organizational and time management skills", DATABASE)

    def test_building_a_database_does_not_lead_teams_or_run_many_projects(self):
        found = self.assertNotMeets(
            "Strong organizational, time management and project management skills; able to "
            "lead teams and manage multiple projects simultaneously", DATABASE)
        unverified = " ".join(found.get("unverified_obligations") or []).lower()
        self.assertIn("lead teams", unverified)
        self.assertIn("multiple projects", unverified)

    def test_statistical_analysis_is_not_competitive_programming(self):
        found = self.assertNotMeets(
            "A strong analytical thinker and coder; ACM-ICPC, IOI or IPSC experience", STATS)
        self.assertIn("coder", " ".join(found.get("unverified_obligations") or []).lower())

    def test_an_mba_does_not_satisfy_a_clinical_credential_alternative(self):
        """The alternatives are named credentials; holding some other master is not one."""
        self.assertNotMeets(
            "MD, PharmD, NP, PA, RN, MPH, or 5+ years embedded in clinical practice", MBA)

    def test_the_same_credential_list_is_met_by_the_exact_credential(self):
        """And the branch that does apply is matched by name, not by level."""
        found = P.propose(
            "MD, PharmD, NP, PA, RN, MPH, or 5+ years embedded in clinical practice", MPH)
        self.assertEqual(found["proposed_disposition"], P.MEETS)
        self.assertEqual(found["supporting_fact_ids"], ["f5"])
        self.assertIn("MPH", found["short_reason"])


class QualifiersSurviveTests(unittest.TestCase):
    """A qualifier is an obligation. It does not vanish when the noun it modifies matches."""

    QUALIFIED = (
        ("Executive-level communication skills", "executive"),
        ("Communicating with senior stakeholders", "senior"),
        ("Explaining analytics to non-technical audiences", "non-technical"),
        ("Ability to lead teams", "lead teams"),
        ("Manage multiple projects simultaneously", "multiple projects"),
        ("Thrive in a fast-paced, dynamic environment", "fast-paced"),
        ("A strong coder", "coder"),
        ("Design modular, extensible systems", "modular"),
    )

    def test_each_qualifier_is_reported_as_unverified(self):
        facts = PRESENTATION + DATABASE + STATS
        for requirement, qualifier in self.QUALIFIED:
            with self.subTest(requirement=requirement):
                found = P.propose(requirement, facts)
                self.assertNotEqual(found["proposed_disposition"], P.MEETS)
                self.assertIn(qualifier,
                              " ".join(found.get("unverified_obligations") or []).lower())


class CompoundEvidenceClassTests(unittest.TestCase):
    """A compound requirement is only as strong as its weakest necessary part."""

    FACTS = [
        fact("f-direct", "experience_claim", "Built and maintained SQL databases",
             keywords=["SQL"]),
        fact("f-list", "skill", "Programming: Python, SQL", keywords=["Python", "SQL"]),
    ]

    def test_the_weakest_component_constrains_the_class(self):
        found = P.propose("Experience with SQL and Python", self.FACTS)
        self.assertNotEqual(found["proposed_disposition"], P.MEETS)
        self.assertIn(found.get("evidence_class"), (None, "mention_only", "transferable"))

    def test_obligations_carry_their_own_fact_ids(self):
        found = P.propose("Experience with SQL and Python", self.FACTS)
        obligations = found.get("obligations") or []
        self.assertTrue(obligations, found)
        for entry in obligations:
            with self.subTest(obligation=entry.get("obligation")):
                self.assertIn("fact_ids", entry)


class AbsenceIsNeverAMismatchTests(unittest.TestCase):
    """No completeness assertion exists in the schema, so nothing can be established absent.

    A profile with no Docker fact has not told us the candidate cannot use Docker; an earliest
    recorded role in 2019 has not told us nothing came before it; an absent degree has not
    told us the education list is finished. Until a scoped completeness assertion exists,
    every one of these is `no_supporting_evidence_found`.
    """

    def test_a_missing_tool_is_not_a_mismatch(self):
        found = P.propose("Production experience with Docker and Snowflake", STATS)
        self.assertIsNone(found["proposed_disposition"])
        self.assertEqual(found["candidate_evidence_status"], P.NO_EVIDENCE)

    def test_a_duration_beyond_the_recorded_span_is_not_a_mismatch(self):
        facts = [fact("f-job", "experience_header", "Analyst | Acme 06/2020 – 08/2021")]
        found = P.propose("Minimum of 10 years of managing analytics", facts)
        self.assertIsNone(found["proposed_disposition"])
        self.assertIn("recorded", found["short_reason"])

    def test_a_missing_degree_is_not_a_mismatch(self):
        found = P.propose("MS or PhD in Statistics or Biostatistics", MPH)
        self.assertIsNone(found["proposed_disposition"])
        self.assertEqual(found["candidate_evidence_status"], P.NO_EVIDENCE)

    def test_nothing_proposes_does_not_meet_without_a_confirmed_negative(self):
        facts = PRESENTATION + DATABASE + STATS + MPH
        for requirement in ("PhD in Biostatistics required",
                            "Production experience with Docker",
                            "Minimum of 20 years of leadership",
                            "Experience with REDCap and Epic"):
            with self.subTest(requirement=requirement):
                self.assertNotEqual(P.propose(requirement, facts)["proposed_disposition"],
                                    P.DOES_NOT_MEET)


if __name__ == "__main__":
    unittest.main()
