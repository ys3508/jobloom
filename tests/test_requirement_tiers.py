"""Which requirements a posting says it must have, and which it only wishes for.

Every rule here was derived from the 112 postings in the 2026-09-07 queue before it was
written, and the numbers are in `requirement_tiers`'s module docstring. These tests pin the
three that matter: the line beats the heading, two weights in one sentence is not a tie to
break, and a heading that does not state a weight does not get to imply one.

`unknown` is the tier under the most pressure to be quietly resolved, so most of what follows
is about it staying where it is.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"requirement_tiers_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


TIERS = load_script("requirement_tiers")
QUEUE = load_script("review_queue")

MUST, PREFERRED, UNKNOWN = TIERS.MUST_HAVE, TIERS.PREFERRED, TIERS.UNKNOWN


def posting(*sections):
    return "\n".join(sections)


def tiers_of(text):
    return [(entry["tier"], entry["line"]) for entry in TIERS.tier_lines(text)]


def one(text):
    lines = TIERS.tier_lines(text)
    assert len(lines) == 1, [entry["line"] for entry in lines]
    return lines[0]


class HeadingTests(unittest.TestCase):

    def test_an_explicit_required_heading_makes_its_lines_must_have(self):
        for heading in ("Requirements", "Required Qualifications", "Minimum Qualifications",
                        "Basic Qualifications", "Must Have", "You'll need to have"):
            with self.subTest(heading=heading):
                entry = one(posting(heading, "- Experience with SAS"))
                self.assertEqual(entry["tier"], MUST)
                self.assertEqual(entry["reason"], "heading_states_required")

    def test_an_explicit_preferred_heading_makes_its_lines_preferred(self):
        for heading in ("Preferred Qualifications", "Nice to have", "Bonus points",
                        "Desired Qualifications", "Preferred Skills"):
            with self.subTest(heading=heading):
                entry = one(posting(heading, "- Experience with SAS"))
                self.assertEqual(entry["tier"], PREFERRED)
                self.assertEqual(entry["reason"], "heading_states_preferred")

    def test_a_heading_that_states_no_weight_leaves_its_lines_unknown(self):
        """The 548 lines this is about were all silently must-have before."""
        for heading in ("Qualifications", "About You", "What you'll bring",
                        "Knowledge, Skills, and Abilities", "What we're looking for",
                        "Who you are", "The ideal candidate"):
            with self.subTest(heading=heading):
                entry = one(posting(heading, "- Experience with SAS"))
                self.assertEqual(entry["tier"], UNKNOWN)
                self.assertEqual(entry["reason"], "heading_does_not_state_weight")

    def test_an_explicit_tail_settles_an_ambiguous_stem(self):
        """Verbatim from the corpus, colon and all — that is what makes it heading-shaped."""
        entry = one(posting("What you bring to Komodo Health (required):",
                            "- Experience with SAS"))
        self.assertEqual(entry["tier"], MUST)

    def test_an_ambiguous_stem_without_the_tail_stays_ambiguous(self):
        entry = one(posting("What you bring to Komodo Health:", "- Experience with SAS"))
        self.assertEqual(entry["tier"], UNKNOWN)

    def test_lines_before_any_heading_are_not_tiered_at_all(self):
        self.assertEqual(tiers_of("We are hiring a data analyst.\n- Experience with SAS"), [])

    def test_a_non_requirement_heading_ends_the_section(self):
        """Responsibilities do not inherit the weight of the requirements above them."""
        text = posting("Requirements", "- Experience with SAS",
                       "Responsibilities", "- Run the weekly report")
        self.assertEqual(tiers_of(text), [(MUST, "Experience with SAS")])


class LineBeatsHeadingTests(unittest.TestCase):
    """77 lines say preferred under a Required heading; effectively one says the reverse."""

    def test_a_preferred_line_under_a_required_heading_is_preferred(self):
        entry = one(posting("Requirements", "- Experience with Tableau preferred"))
        self.assertEqual(entry["tier"], PREFERRED)
        self.assertEqual(entry["reason"], "line_states_preferred")

    def test_a_required_line_under_a_preferred_heading_is_must_have(self):
        entry = one(posting("Nice to have", "- SAS is required"))
        self.assertEqual(entry["tier"], MUST)
        self.assertEqual(entry["reason"], "line_states_required")

    def test_a_required_line_under_an_ambiguous_heading_is_must_have(self):
        entry = one(posting("Qualifications", "- Minimum 5 years of clinical research"))
        self.assertEqual(entry["tier"], MUST)

    def test_preferred_but_not_required_is_one_statement_not_two(self):
        for line in ("MBA preferred but not required",
                     "Industry data experience is a plus, but not required",
                     "Desirable, but not required: Python"):
            with self.subTest(line=line):
                entry = one(posting("Requirements", f"- {line}"))
                self.assertEqual(entry["tier"], PREFERRED)
                self.assertEqual(entry["reason"], "line_states_preferred_not_required")


class AmbiguityTests(unittest.TestCase):

    def test_two_weights_in_one_sentence_is_unknown_not_a_tie_to_break(self):
        entry = one(posting("Qualifications",
                            "- Data analysis required, coding skills and lab work are a plus"))
        self.assertEqual(entry["tier"], UNKNOWN)
        self.assertEqual(entry["reason"], "line_states_both")
        self.assertEqual(len(entry["cues"]), 2)

    def test_a_semicolon_separates_two_requirements_written_on_one_line(self):
        text = posting("Qualifications",
                       "- Bachelor's degree required; advanced degree (MBA, MPH) a plus")
        self.assertEqual(tiers_of(text),
                         [(MUST, "Bachelor's degree required"),
                          (PREFERRED, "advanced degree (MBA, MPH) a plus")])

    def test_a_clause_split_keeps_the_line_it_came_from(self):
        entries = TIERS.tier_lines(
            posting("Qualifications", "- SAS required; Python preferred"))
        for entry in entries:
            self.assertIn("from_line", entry)
            self.assertEqual(entry["from_line"], "SAS required; Python preferred")

    def test_a_split_that_does_not_resolve_leaves_the_whole_line_unknown(self):
        """Every clause must carry exactly one kind of cue, or nothing has been separated."""
        text = posting("Qualifications",
                       "- SAS required and Python preferred; Tableau also required or preferred")
        self.assertEqual([tier for tier, _ in tiers_of(text)], [UNKNOWN])

    def test_a_comma_is_never_a_split_point(self):
        """The corpus is full of commas inside tool lists; splitting there invents things."""
        self.assertIsNone(TIERS.split_clauses("Python, R, SAS required, Tableau preferred"))


class UnknownIsNotDowngradedTests(unittest.TestCase):
    """The rule most likely to be quietly broken by a later convenience."""

    def test_unknown_never_appears_as_preferred_in_a_summary(self):
        text = posting("Qualifications", "- Experience with SAS", "- Experience with R")
        report = TIERS.summarize(text, [])
        self.assertEqual(report["tiers"][UNKNOWN]["stated_lines"], 2)
        self.assertEqual(report["tiers"][PREFERRED]["stated_lines"], 0)
        self.assertEqual(report["tiers"][MUST]["stated_lines"], 0)

    def test_every_reason_code_maps_to_exactly_one_tier(self):
        self.assertEqual(set(TIERS.REASONS.values()), set(TIERS.TIERS))
        for reason, tier in TIERS.REASONS.items():
            with self.subTest(reason=reason):
                self.assertIn(tier, TIERS.TIERS)

    def test_no_reason_code_turns_an_absent_weight_into_preferred(self):
        for reason, tier in TIERS.REASONS.items():
            if "does_not_state" in reason or reason == "no_heading":
                self.assertEqual(tier, UNKNOWN)


class ProvenanceTests(unittest.TestCase):
    """A classification that cannot be checked against the posting cannot be disputed."""

    def test_every_line_carries_its_reason_and_the_cue_that_fired(self):
        entry = one(posting("Requirements", "- Tableau experience preferred"))
        self.assertEqual(entry["reason"], "line_states_preferred")
        self.assertEqual(entry["cues"], ["preferred"])
        self.assertEqual(entry["heading"], "requirements")

    def test_the_offset_points_at_the_requirement_in_the_original_text(self):
        text = posting("Requirements", "- Experience with SAS", "- Experience with R")
        for entry in TIERS.tier_lines(text):
            with self.subTest(line=entry["line"]):
                self.assertEqual(text[entry["offset"]:entry["offset"] + len(entry["line"])],
                                 entry["line"])

    def test_the_offset_skips_the_bullet_rather_than_pointing_at_it(self):
        text = posting("Requirements", "• Experience with SAS")
        entry = one(text)
        self.assertTrue(text[entry["offset"]:].startswith("Experience"))


class CoverageTests(unittest.TestCase):

    FACTS = [
        {"id": "fact-sas", "type": "experience_claim", "value": "Built SAS analysis programs",
         "status": "confirmed", "locked": False, "evidence_strength": "direct",
         "keywords": ["SAS"]},
        {"id": "fact-tab", "type": "experience_claim", "value": "Some Tableau exposure",
         "status": "confirmed", "locked": False, "evidence_strength": "transferable",
         "keywords": ["Tableau"]},
    ]

    def test_coverage_and_gaps_are_reported_per_tier(self):
        text = posting("Requirements", "- Experience with SAS",
                       "Nice to have", "- Experience with Python")
        report = TIERS.summarize(text, self.FACTS)
        self.assertEqual(report["tiers"][MUST]["direct"], 1)
        self.assertEqual(report["tiers"][MUST]["gaps"], 0)
        self.assertEqual(report["tiers"][MUST]["direct_terms"], ["SAS"])
        self.assertEqual(report["tiers"][PREFERRED]["direct"], 0)
        self.assertEqual(report["tiers"][PREFERRED]["gaps"], 1)

    def test_counts_are_lines_so_they_add_up_with_stated_lines(self):
        """Covered terms against uncovered terms compared two different things."""
        text = posting("Requirements", "- Experience with SAS", "- Experience with Python")
        must = TIERS.summarize(text, self.FACTS)["tiers"][MUST]
        self.assertEqual(must["direct"] + must["adjacent"] + must["gaps"]
                         + must["unrecognised_lines"], must["stated_lines"])

    def test_transferable_evidence_never_counts_as_covering_a_must_have(self):
        """The whole reason for a must-have column is that adjacent experience misses it."""
        report = TIERS.summarize(posting("Requirements", "- Experience with Tableau"),
                                 self.FACTS)
        must = report["tiers"][MUST]
        self.assertEqual(must["direct"], 0)
        self.assertEqual(must["adjacent"], 1)
        self.assertEqual(must["adjacent_terms"], ["Tableau"])

    def test_adjacent_evidence_is_reported_rather_than_dropped(self):
        report = TIERS.summarize(posting("Requirements", "- Experience with Tableau"),
                                 self.FACTS)
        self.assertNotIn("Tableau", report["tiers"][MUST]["gap_terms"])


class OrderingTests(unittest.TestCase):
    """Must-have coverage before total coverage; must-have gaps before both."""

    def row(self, weight=85, must_direct=0, must_gaps=0, direct=0, covered=0,
            employer="A", title="A"):
        return {
            "weight_percent": weight,
            "evidence": {"direct": direct, "covered": covered, "technical_hits": 0},
            "tiers": {MUST: {"direct": must_direct, "gaps": must_gaps},
                      PREFERRED: {}, UNKNOWN: {}},
            "ranking_score": 0, "employer": employer, "title": title,
        }

    def test_a_covered_must_have_outranks_more_total_coverage(self):
        covered_must = self.row(must_direct=1, direct=1, covered=1)
        many_others = self.row(must_direct=0, direct=5, covered=9)
        self.assertLess(QUEUE.sort_key(covered_must), QUEUE.sort_key(many_others))

    def test_fewer_must_have_gaps_outranks_more_when_coverage_ties(self):
        few = self.row(must_direct=1, must_gaps=1)
        many = self.row(must_direct=1, must_gaps=8)
        self.assertLess(QUEUE.sort_key(few), QUEUE.sort_key(many))

    def test_direction_weight_still_comes_first(self):
        heavy = self.row(weight=85, must_direct=0, must_gaps=9)
        light = self.row(weight=5, must_direct=4, must_gaps=0)
        self.assertLess(QUEUE.sort_key(heavy), QUEUE.sort_key(light))

    def test_a_row_without_tiers_still_sorts(self):
        """Ordering must not depend on a key a caller might not have computed."""
        row = self.row()
        row.pop("tiers")
        QUEUE.sort_key(row)


if __name__ == "__main__":
    unittest.main()


class FoundBySamplingTests(unittest.TestCase):
    """Defects that only a real posting produced. Each one cost a real misclassification."""

    def test_a_negated_cue_does_not_create_a_must_have(self):
        """"prior knowledge is not required" was matching on the word being negated."""
        for line in ("Interest in life science (prior knowledge is not required)",
                     "This is not a requirement",
                     "Industry experience is not required"):
            with self.subTest(line=line):
                self.assertIsNone(TIERS.MUST_CUE.search(line))

    def test_a_benefits_heading_ends_the_requirement_list(self):
        """833 of 2,383 tiered lines were benefits and interview logistics inheriting a tier."""
        text = posting("Nice to have", "- Experience with Sigma",
                       "Perks & Benefits", "- Retirement programs", "- Flexible PTO")
        self.assertEqual(tiers_of(text), [(PREFERRED, "Experience with Sigma")])

    def test_an_interview_process_heading_ends_the_requirement_list(self):
        text = posting("Requirements", "- Experience with SAS",
                       "Interviewing with Veeva", "- A conversation with the hiring manager",
                       "- A practical case exercise")
        self.assertEqual(tiers_of(text), [(MUST, "Experience with SAS")])

    def test_a_compensation_heading_ends_the_requirement_list(self):
        text = posting("Requirements", "- Experience with SAS",
                       "Compensation & Total Rewards", "- Competitive base plus equity")
        self.assertEqual(tiers_of(text), [(MUST, "Experience with SAS")])

    def test_bonus_as_pay_is_not_a_preferred_cue(self):
        """"eligible for our Annual Performance Bonus Plan" is not an opinion about a skill."""
        self.assertIsNone(TIERS.PREFERRED_CUE.search(
            "This role is eligible for participation in our Annual Performance Bonus Plan"))
        self.assertIsNotNone(TIERS.PREFERRED_CUE.search("Bonus points for Kubernetes"))

    def test_headings_written_as_a_sentence_are_still_headings(self):
        for heading in ("BONUS IF YOU HAVE", "Desirable, but not required:"):
            with self.subTest(heading=heading):
                entry = one(posting("Qualifications", heading, "- Experience with SAS"))
                self.assertEqual(entry["tier"], PREFERRED)
