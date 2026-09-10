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
        # Every branch's obligations are reported, so `coder` cannot vanish because another
        # alternative came closer.
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


class BranchStructureTests(unittest.TestCase):
    """A requirement is AND of obligations within OR of branches, and nothing skips that.

    `_credential_proposal` used to answer the whole sentence before it was split, so a degree
    the profile holds satisfied everything standing beside it. And it matched credentials and
    fields as two independent sets, which loses the pairing: an MS in Biology satisfied
    "MS in Statistics or PhD in Biology" by taking the credential from one branch and the
    field from the other.
    """

    BS = [fact("e1", "education", "Bachelor of Science – Medical Technology")]
    MPH = [fact("e2", "education", "Master of Public Health – Environmental Health Sciences")]
    MS_BIOLOGY = [fact("e3", "education", "Master of Science in Biology")]

    def assertNotMeets(self, requirement, facts):
        found = P.propose(requirement, facts)
        self.assertNotEqual(found["proposed_disposition"], P.MEETS,
                            f"{requirement!r} answered by {facts[0]['value']!r}")
        return found

    def test_a_degree_does_not_answer_what_stands_beside_it(self):
        self.assertNotMeets(
            "Bachelor's degree and 5 years of product management experience", self.BS)

    def test_a_degree_does_not_answer_a_qualifier_beside_it(self):
        self.assertNotMeets(
            "Bachelor's degree and executive-level communication skills", self.BS)

    def test_two_credentials_joined_by_and_both_have_to_be_held(self):
        self.assertNotMeets("MD and MPH required", self.MPH)

    def test_a_credential_and_its_field_stay_in_the_same_branch(self):
        """(MS AND Statistics) OR (PhD AND Biology) — not {MS,PhD} × {Statistics,Biology}."""
        self.assertNotMeets("MS in Statistics or PhD in Biology", self.MS_BIOLOGY)

    def test_the_branch_that_does_match_resolves_on_its_own_pairing(self):
        """The positive control for the pairing, which no longer ends in `meets`.

        `MS in Biology` is answered by the held degree, and the proposal says so with that
        fact's id. It stops at `partially_meets` because credential-plus-field prose is not
        one of the reviewed templates: what a field written on one alternative governs is
        the question the parse refuses to guess at, and this shape is only safe because both
        alternatives happen to carry their own. `parse_requirement` does not know that
        "happen to" is not a rule.
        """
        found = P.propose("MS in Statistics or MS in Biology", self.MS_BIOLOGY)
        self.assertEqual(found["proposed_disposition"], P.PARTIALLY_MEETS)
        matched = [entry for entry in found["obligations"] if entry["strength"] == "direct"]
        self.assertEqual([entry["obligation"] for entry in matched], ["MS in Biology"])
        self.assertEqual(matched[0]["fact_ids"], ["e3"])
        self.assertIn("parse_not_reviewed_complete", found["meets_invariant_problems"])

    def test_every_proposal_carries_its_obligations(self):
        """The two v2 `meets` had empty obligation lists, so nothing could be checked."""
        for requirement, facts in (
                ("Bachelor's degree or higher", self.BS),
                ("MD, PharmD, NP, PA, RN, MPH, or 5+ years in clinical practice", self.MPH)):
            with self.subTest(requirement=requirement):
                found = P.propose(requirement, facts)
                self.assertTrue(found["obligations"], found)
                for entry in found["obligations"]:
                    self.assertIn("fact_ids", entry)
                    self.assertIn("obligation", entry)


class DegreeHasOneRouteTests(unittest.TestCase):
    """A degree the credential resolver declined is not answered by the concept matcher.

    The `degree` concept fires on any education fact naming a degree, so "advanced degree in
    Engineering" came back directly evidenced by a Medical Technology bachelor's — a second
    route to a credential, past the resolver that checks the field.
    """

    BS = [fact("e1", "education", "Bachelor of Science – Medical Technology")]

    def test_an_unrecognised_level_is_unparsed_not_evidenced(self):
        found = P.propose("Advanced degree in Engineering", self.BS)
        kinds = {entry["kind"] for entry in found["obligations"]}
        self.assertEqual(kinds, {"unparsed"})
        self.assertEqual(found["proposed_disposition"], None)

    def test_a_recognised_level_still_goes_through_the_credential_resolver(self):
        found = P.propose("Master's degree in Engineering", self.BS)
        self.assertEqual([entry["kind"] for entry in found["obligations"]], ["credential"])
        self.assertEqual(found["obligations"][0]["strength"], "none")


class ResidueProvesConsumptionTests(unittest.TestCase):
    """`meets` needs the requirement text consumed, not merely free of known qualifiers.

    A curated qualifier list can only ever catch the wording someone already thought of.
    "Advanced" and "in Mandarin" are not on it, and disappeared.
    """

    PRESENTATION = [fact("c1", "experience_claim", "Delivered a presentation")]
    BORROWABLE = [
        fact("c1", "experience_claim", "Delivered a presentation"),
        fact("c2", "experience_claim", "Performed executive-level inventory classification"),
    ]

    def test_an_unlisted_intensity_word_is_residue(self):
        found = P.propose("Advanced communication skills", self.PRESENTATION)
        self.assertNotEqual(found["proposed_disposition"], P.MEETS)
        self.assertTrue(found.get("unresolved_text"), found)

    def test_an_unlisted_scope_phrase_is_residue(self):
        found = P.propose("Communication skills in Mandarin", self.PRESENTATION)
        self.assertNotEqual(found["proposed_disposition"], P.MEETS)
        self.assertIn("mandarin", " ".join(found.get("unresolved_text") or []).lower())

    def test_a_qualifier_cannot_be_borrowed_from_an_unrelated_fact(self):
        """"executive-level" came from an inventory-classification fact the answer never cited."""
        found = P.propose("Executive-level communication skills", self.BORROWABLE)
        self.assertNotEqual(found["proposed_disposition"], P.MEETS)

    def test_a_fully_consumed_requirement_leaves_no_residue(self):
        """The positive control for consumption: nothing unread, and still not `meets`.

        "Communication skills" is read completely — no qualifier survives, and the
        presentation fact answers the concept directly. What is missing is a reviewed parse:
        a bare capability noun is exactly where "delivered a presentation" was being read as
        the whole of what an employer means, so prose does not conclude, it reports.
        """
        found = P.propose("Communication skills", self.PRESENTATION)
        self.assertEqual(found.get("unresolved_text"), [])
        self.assertEqual(found["obligations"][0]["strength"], "direct")
        self.assertEqual(found["proposed_disposition"], P.PARTIALLY_MEETS)
        self.assertEqual(found["meets_invariant_problems"], ["parse_not_reviewed_complete"])


class RenderingTests(unittest.TestCase):
    """The sheet a person confirms from must not hide the half of a sentence that decides it."""

    LONG = ("Demonstrated organizational, time management, and project management skills, "
            "with the ability to lead teams and manage multiple projects simultaneously "
            "while documenting changes to established operational workflows and process "
            "flows for a regulated environment")

    def sheet(self):
        return {"survivors": [{"employer": "E", "title": "T", "location": "L",
                               "lane": "partial_no_known_gap", "lane_rank": 1,
                               "parsed_lines": 1, "stated_lines": 9,
                               "unread_requirements": [
                                   {"requirement": self.LONG, "disposition": None,
                                    "note": None}]}],
                "blocked": []}

    def test_the_whole_requirement_is_shown(self):
        text = P.render(P.annotate(self.sheet(), [fact("c1", "experience_claim",
                                                       "Built a database")]))
        self.assertIn("while documenting changes", text)
        self.assertIn("manage multiple projects simultaneously", text)

    def test_nothing_is_truncated_without_saying_so(self):
        text = P.render(P.annotate(self.sheet(), [fact("c1", "experience_claim",
                                                       "Built a database")]))
        for line in text.splitlines():
            if line.startswith("|") and "…" in line:
                self.fail(f"silently shortened: {line[:90]}")

    def test_every_unresolved_obligation_reaches_still_to_verify(self):
        sheet = P.annotate(self.sheet(), [fact("c1", "experience_claim", "Built a database")])
        item = sheet["survivors"][0]["unread_requirements"][0]
        unresolved = [entry["obligation"] for entry in item["obligations"]
                      if entry["strength"] == "none"]
        self.assertTrue(unresolved, item)
        text = P.render(sheet)
        for obligation in unresolved:
            with self.subTest(obligation=obligation[:40]):
                self.assertIn(obligation[:40], text)


class DocstringTests(unittest.TestCase):

    def test_the_module_does_not_advertise_a_path_it_closed(self):
        source = (ROOT / "skills" / "jobloom" / "scripts"
                  / "disposition_proposals.py").read_text(encoding="utf-8")
        head = source[:source.index('"""', source.index('"""') + 3)]
        self.assertNotIn("a **duration** longer than", head)
        self.assertIn("does_not_meet", head)
