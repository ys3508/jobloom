"""What a regex split is not allowed to prove.

`branches_of` cuts on `or` with no syntax tree, so a constraint shared across the split
survives only in the branch it was written next to. `(Python or R) and SQL` becomes
`Python` / `R AND SQL`, and a profile with Python alone satisfies it. The same happens without
brackets: `5+ years of production experience with Python or R` leaves the years, the
production and the experience in the Python branch and degrades the other to a bare `R`.

Patching the splitter further would be guessing at scope, and the guesses would keep arriving
as duration, domain, seniority and licence. So the contract changes instead: a raw sentence
cannot prove satisfaction. Only a parse somebody reviewed, or a closed template with no room
for a shared modifier, may produce `meets`.

The second family is inside one obligation: `match_requirement_prose` aggregates several
concepts at their strongest, so a mention-only skill list beside a directly evidenced
presentation reports `direct` for both. Taking the weakest obligation outside cannot undo
that, because the promotion already happened inside.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"parse_contract_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


P = load_script("disposition_proposals")


def fact(fid, ftype, value, **kw):
    return {"id": fid, "type": ftype, "status": "confirmed", "locked": False,
            "evidence_strength": "direct", "value": value, **kw}


PYTHON_ONLY = [fact("s1", "experience_claim", "Wrote Python scripts", keywords=["Python"])]
R_ONCE = [fact("s2", "experience_claim", "Used R for a class assignment", keywords=["R"])]
BACHELOR = [fact("e1", "education", "Bachelor of Science – Medical Technology")]
MIXED = [fact("s3", "skill", "Analysis", keywords=["Analysis"]),
         fact("s4", "experience_claim", "Delivered a presentation")]


class SharedScopeTests(unittest.TestCase):
    """A constraint written once must not apply to only one side of an `or`."""

    def assertNotMeets(self, requirement, facts):
        found = P.propose(requirement, facts)
        self.assertNotEqual(found["proposed_disposition"], P.MEETS,
                            f"{requirement!r} met on {facts[0]['value']!r}")
        return found

    def test_a_bracketed_alternative_keeps_what_follows_it(self):
        self.assertNotMeets("(Python or R) and SQL", PYTHON_ONLY)

    def test_a_bracketed_degree_alternative_keeps_what_follows_it(self):
        self.assertNotMeets(
            "(Bachelor's or Master's degree) and 5 years of product management experience",
            BACHELOR)

    def test_a_leading_duration_applies_to_both_alternatives(self):
        self.assertNotMeets("5+ years of production experience with Python or R", R_ONCE)

    def test_a_leading_activity_applies_to_both_alternatives(self):
        self.assertNotMeets("Experience building production models in Python or R", R_ONCE)

    def test_such_a_requirement_is_marked_ambiguous_rather_than_parsed(self):
        for requirement in ("(Python or R) and SQL",
                            "5+ years of production experience with Python or R"):
            with self.subTest(requirement=requirement):
                parse = P.parse_requirement(requirement)
                self.assertNotIn(parse["parse_status"], P.MEETS_ELIGIBLE)


class ConceptAggregationTests(unittest.TestCase):
    """Every mandatory concept in an obligation is answered separately."""

    def test_a_mention_only_concept_is_not_lifted_by_a_direct_one(self):
        found = P.propose("Analysis communication skills", MIXED)
        self.assertNotEqual(found["evidence_class"], "direct")
        self.assertNotEqual(found["proposed_disposition"], P.MEETS)

    def test_the_same_holds_for_other_concept_pairs(self):
        for requirement in ("Research communication skills", "Written communication skills"):
            with self.subTest(requirement=requirement):
                found = P.propose(requirement, MIXED)
                self.assertNotEqual(found["evidence_class"], "direct")

    def test_each_concept_keeps_its_own_strength_and_facts(self):
        found = P.propose("Analysis communication skills", MIXED)
        concepts = [entry for ob in found["obligations"]
                    for entry in (ob.get("concepts") or [])]
        self.assertTrue(concepts, found)
        for entry in concepts:
            with self.subTest(concept=entry.get("concept")):
                self.assertIn("strength", entry)
                self.assertIn("fact_ids", entry)

    def test_the_obligation_takes_the_weakest_mandatory_concept(self):
        found = P.propose("Analysis communication skills", MIXED)
        obligation = found["obligations"][0]
        strengths = [entry["strength"] for entry in obligation["concepts"]]
        self.assertEqual(obligation["strength"],
                         min(strengths, key=lambda name: P.EVIDENCE_ORDER[name]))


class ParseStatusTests(unittest.TestCase):

    def test_raw_prose_is_never_reviewed_complete(self):
        for requirement in ("Strong communication skills",
                            "Experience with Python and SQL in a regulated environment"):
            with self.subTest(requirement=requirement):
                self.assertNotIn(P.parse_requirement(requirement)["parse_status"],
                                 P.MEETS_ELIGIBLE)

    def test_a_closed_degree_template_is_complete(self):
        for requirement in ("Bachelor's degree", "Education: Bachelor's degree or higher",
                            "Master's degree or higher"):
            with self.subTest(requirement=requirement):
                self.assertEqual(P.parse_requirement(requirement)["parse_status"],
                                 P.CLOSED_TEMPLATE)

    def test_a_flat_credential_alternative_list_is_complete(self):
        parse = P.parse_requirement(
            "MD, PharmD, NP, PA, RN, MPH, or 5+ years in clinical health IT")
        self.assertEqual(parse["parse_status"], P.CLOSED_TEMPLATE)

    def test_a_credential_list_with_a_distributing_tail_is_not(self):
        """"MD, PharmD, or MPH in public health" shares a field nobody attributed."""
        parse = P.parse_requirement("MD, PharmD, or MPH in public health")
        self.assertNotIn(parse["parse_status"], P.MEETS_ELIGIBLE)

    def test_the_parse_records_what_it_came_from(self):
        parse = P.parse_requirement("Bachelor's degree")
        self.assertEqual(parse["source_text"], "Bachelor's degree")
        self.assertTrue(parse["source_sha256"])
        self.assertTrue(parse["parse_version"])

    def test_every_obligation_carries_its_source_span(self):
        parse = P.parse_requirement("Experience with Python and SQL")
        for branch in parse["branches"]:
            for obligation in branch["obligations"]:
                start, end = obligation["span"]
                self.assertEqual(parse["source_text"][start:end], obligation["text"])


class MeetsInvariantTests(unittest.TestCase):
    """One place that says what `meets` requires, checked wherever one is produced."""

    def test_the_invariant_names_all_five_conditions(self):
        parse = P.parse_requirement("Strong communication skills")
        problems = P.meets_invariant_problems(parse, [{"strength": "direct",
                                                       "concepts": [], "fact_ids": ["f1"],
                                                       "residue": []}])
        self.assertIn("parse_not_eligible_to_conclude", problems)

    def test_an_unresolved_span_blocks_meets(self):
        parse = P.parse_requirement("Bachelor's degree")
        problems = P.meets_invariant_problems(
            parse, [{"strength": "direct", "concepts": [], "fact_ids": ["f1"],
                     "residue": ["executive"]}])
        self.assertIn("unresolved_span", problems)

    def test_a_concept_without_its_own_facts_blocks_meets(self):
        parse = P.parse_requirement("Bachelor's degree")
        problems = P.meets_invariant_problems(
            parse, [{"strength": "direct", "fact_ids": ["f1"], "residue": [],
                     "concepts": [{"concept": "analysis", "strength": "direct",
                                   "fact_ids": []}]}])
        self.assertIn("concept_without_facts", problems)

    def test_no_proposal_is_meets_while_the_invariant_reports_a_problem(self):
        cases = (("(Python or R) and SQL", PYTHON_ONLY),
                 ("Analysis communication skills", MIXED),
                 ("5+ years of production experience with Python or R", R_ONCE),
                 ("Strong communication skills", MIXED))
        for requirement, facts in cases:
            with self.subTest(requirement=requirement):
                found = P.propose(requirement, facts)
                if found["proposed_disposition"] == P.MEETS:
                    self.assertEqual(found.get("meets_invariant_problems"), [])


class ReviewIsSomethingAPersonDoesTests(unittest.TestCase):
    """`reviewed_complete` was granted by a regex, which is not what the word means.

    A template match says a machine recognised a shape. Whether anybody looked at this
    sentence is a different fact, and the status name was asserting the second while
    establishing only the first.
    """

    def test_a_template_match_is_not_called_reviewed(self):
        parse = P.parse_requirement("Bachelor's degree")
        self.assertEqual(parse["parse_status"], P.CLOSED_TEMPLATE)
        self.assertIsNone(parse["reviewed_by"])

    def test_a_closed_template_may_still_conclude(self):
        self.assertIn(P.CLOSED_TEMPLATE, P.MEETS_ELIGIBLE)

    def test_a_registry_entry_is_what_makes_a_parse_reviewed(self):
        text = "Experience with Python and SQL in a regulated environment"
        parse = P.parse_requirement(text)
        self.assertEqual(parse["parse_status"], P.UNREVIEWED)
        registry = {parse["source_sha256"]: {"parse_version": parse["parse_version"],
                                             "ast_sha256": parse["ast_sha256"],
                                             "approved_by": "sissi",
                                             "approved_at": "2026-09-10"}}
        reviewed = P.parse_requirement(text, registry=registry)
        self.assertEqual(reviewed["parse_status"], P.REVIEWED_COMPLETE)
        self.assertEqual(reviewed["reviewed_by"], "sissi")

    def test_a_registry_entry_from_an_older_distiller_does_not_apply(self):
        text = "Experience with Python and SQL"
        parse = P.parse_requirement(text)
        registry = {parse["source_sha256"]: {"parse_version": "requirement-parse/1900-01-01",
                                             "ast_sha256": parse["ast_sha256"],
                                             "approved_by": "sissi",
                                             "approved_at": "2026-09-10"}}
        stale = P.parse_requirement(text, registry=registry)
        self.assertNotEqual(stale["parse_status"], P.REVIEWED_COMPLETE)
        self.assertTrue(stale["registry_note"])

    def test_a_registry_entry_for_a_different_tree_does_not_apply(self):
        """Approving the text is not approving whatever a later splitter makes of it."""
        text = "Experience with Python and SQL"
        parse = P.parse_requirement(text)
        registry = {parse["source_sha256"]: {"parse_version": parse["parse_version"],
                                             "ast_sha256": "0" * 64,
                                             "approved_by": "sissi",
                                             "approved_at": "2026-09-10"}}
        stale = P.parse_requirement(text, registry=registry)
        self.assertNotEqual(stale["parse_status"], P.REVIEWED_COMPLETE)


class ProvenanceIsCheckedTests(unittest.TestCase):
    """The hash and the version were written down and never read."""

    def test_a_source_hash_that_does_not_match_blocks_meets(self):
        parse = P.parse_requirement("Bachelor's degree")
        parse["source_sha256"] = "0" * 64
        self.assertIn("source_hash_mismatch", P.meets_invariant_problems(parse, []))

    def test_a_parse_from_another_version_blocks_meets(self):
        parse = P.parse_requirement("Bachelor's degree")
        parse["parse_version"] = "requirement-parse/1900-01-01"
        self.assertIn("parse_version_mismatch", P.meets_invariant_problems(parse, []))

    def test_a_span_that_does_not_match_its_source_blocks_meets(self):
        parse = P.parse_requirement("Bachelor's degree")
        parse["branches"][0]["obligations"][0]["span"] = [0, 3]
        self.assertIn("span_does_not_match_source", P.meets_invariant_problems(parse, []))

    def test_an_artifact_with_no_provenance_blocks_meets(self):
        self.assertIn("missing_provenance", P.meets_invariant_problems({}, []))


class ClosedTemplatesAreActuallyClosedTests(unittest.TestCase):
    """A template that reads only the letters is not closed over what it skipped."""

    BS = [fact("e1", "education", "Bachelor of Science – Medical Technology")]

    def test_a_number_the_template_never_read_blocks_it(self):
        """"Bachelor's degree, 5+" met on the degree; the 5 was invisible to the word scan."""
        for requirement in ("Bachelor's degree, 5+", "Bachelor's degree + 3"):
            with self.subTest(requirement=requirement):
                parse = P.parse_requirement(requirement)
                self.assertNotIn(parse["parse_status"], P.MEETS_ELIGIBLE)
                self.assertNotEqual(P.propose(requirement, self.BS)["proposed_disposition"],
                                    P.MEETS)

    def test_characters_outside_the_word_scan_block_it(self):
        parse = P.parse_requirement("Bachelor's degree 学历")
        self.assertNotIn(parse["parse_status"], P.MEETS_ELIGIBLE)
        self.assertNotEqual(P.propose("Bachelor's degree 学历", self.BS)["proposed_disposition"],
                            P.MEETS)

    def test_a_comma_list_is_not_a_bare_degree_level(self):
        """It is a list of alternatives, and the list template is the one that reads it."""
        parse = P.parse_requirement("MPH, MS, or MA")
        self.assertEqual(parse["template"], "credential_alternatives")
        self.assertEqual([branch["text"] for branch in parse["branches"]],
                         ["MPH", "MS", "MA"])

    def test_a_bare_level_still_reaches_the_template(self):
        for requirement in ("Bachelor's degree", "Education: Bachelor's degree or higher",
                            "Bachelor's degree required"):
            with self.subTest(requirement=requirement):
                self.assertEqual(P.parse_requirement(requirement)["template"],
                                 "bare_degree_level")


class CredentialsComeFromTheRecordThatCarriesThemTests(unittest.TestCase):
    """A licence is not a degree, and `held_degrees` was answering for both."""

    ADDRESS = [fact("e1", "education",
                    "Bachelor of Science – Nursing; Philadelphia PA May 2018")]
    NURSE = [fact("c1", "certification", "Registered Nurse (RN), Massachusetts license"),
             fact("e1", "education", "Bachelor of Science – Nursing")]

    def test_a_state_in_an_education_line_is_not_a_licence(self):
        """"Philadelphia PA" made the profile hold a physician assistant licence."""
        found = P.propose("MD, DO, or PA", self.ADDRESS)
        self.assertNotEqual(found["proposed_disposition"], P.MEETS)

    def test_a_licence_recorded_as_a_certification_answers_a_licence_requirement(self):
        found = P.propose("RN, NP, or PA", self.NURSE)
        self.assertEqual(found["proposed_disposition"], P.MEETS)
        self.assertEqual(found["supporting_fact_ids"], ["c1"])

    def test_an_academic_credential_still_comes_from_the_education_record(self):
        mph = [fact("e2", "education", "Master of Public Health – Environmental Health")]
        found = P.propose("MPH, MS, or MA", mph)
        self.assertEqual(found["proposed_disposition"], P.MEETS)
        self.assertEqual(found["supporting_fact_ids"], ["e2"])

    def test_an_absent_licence_names_the_record_that_would_carry_it(self):
        found = P.propose("RN, NP, or PA", [fact("e1", "education", "Bachelor of Science")])
        self.assertIsNone(found["proposed_disposition"])
        self.assertIn("certification", found["short_reason"])


if __name__ == "__main__":
    unittest.main()
