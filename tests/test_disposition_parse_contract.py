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
                self.assertNotEqual(parse["parse_status"], P.REVIEWED_COMPLETE)


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
                self.assertNotEqual(P.parse_requirement(requirement)["parse_status"],
                                    P.REVIEWED_COMPLETE)

    def test_a_closed_degree_template_is_complete(self):
        for requirement in ("Bachelor's degree", "Education: Bachelor's degree or higher",
                            "Master's degree or higher"):
            with self.subTest(requirement=requirement):
                self.assertEqual(P.parse_requirement(requirement)["parse_status"],
                                 P.REVIEWED_COMPLETE)

    def test_a_flat_credential_alternative_list_is_complete(self):
        parse = P.parse_requirement(
            "MD, PharmD, NP, PA, RN, MPH, or 5+ years in clinical health IT")
        self.assertEqual(parse["parse_status"], P.REVIEWED_COMPLETE)

    def test_a_credential_list_with_a_distributing_tail_is_not(self):
        """"MD, PharmD, or MPH in public health" shares a field nobody attributed."""
        parse = P.parse_requirement("MD, PharmD, or MPH in public health")
        self.assertNotEqual(parse["parse_status"], P.REVIEWED_COMPLETE)

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
        self.assertIn("parse_not_reviewed_complete", problems)

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


if __name__ == "__main__":
    unittest.main()
