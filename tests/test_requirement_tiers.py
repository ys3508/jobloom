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
    """Three lanes, and the queue never compares across them.

    The defect this replaces: one ordered score for every posting. A posting whose
    requirements could not be parsed has zero known gaps, and zero known gaps sorted like
    "nothing is missing", so postings nobody had evaluated outranked postings that had been.
    """

    def row(self, weight=85, unique_direct=0, unique_gaps=0, unique_adjacent=0,
            assessment=TIERS.FULLY_ASSESSED, parsed=2, stated=2, direct=0, covered=0,
            employer="A", title="A"):
        must_have = {"assessment": assessment, "unique_direct": unique_direct,
                     "unique_gaps": unique_gaps, "unique_adjacent": unique_adjacent,
                     "direct": unique_direct, "gaps": unique_gaps,
                     "parsed_lines": parsed, "stated_lines": stated}
        return {
            "weight_percent": weight,
            "evidence": {"direct": direct, "covered": covered, "technical_hits": 0},
            "tiers": {MUST: must_have, PREFERRED: {}, UNKNOWN: {}},
            "ranking_score": 0, "employer": employer, "title": title,
        }

    # ---- which lane ---------------------------------------------------------------

    def test_a_posting_with_nothing_parsed_is_unassessed(self):
        row = self.row(assessment=TIERS.UNASSESSED, parsed=0, stated=14)
        self.assertEqual(QUEUE.lane(row), QUEUE.LANE_UNASSESSED)

    def test_a_known_gap_puts_a_posting_in_the_gapped_lane(self):
        self.assertEqual(QUEUE.lane(self.row(unique_direct=1, unique_gaps=1)),
                         QUEUE.LANE_GAPPED)

    def test_adjacent_only_evidence_is_a_known_shortfall_not_a_pass(self):
        """Transferable evidence on a mandatory requirement is not meeting it."""
        self.assertEqual(QUEUE.lane(self.row(unique_direct=1, unique_adjacent=1)),
                         QUEUE.LANE_GAPPED)

    def test_assessed_and_covered_is_the_clear_lane(self):
        self.assertEqual(QUEUE.lane(self.row(unique_direct=2)), QUEUE.LANE_CLEAR)

    def test_a_row_with_no_tiers_is_treated_as_unassessed(self):
        row = self.row()
        row.pop("tiers")
        self.assertEqual(QUEUE.lane(row), QUEUE.LANE_UNASSESSED)

    # ---- what the lanes stop --------------------------------------------------------

    def test_an_unparsed_posting_cannot_outrank_an_assessed_clear_one(self):
        """The acceptance check: zero gaps from ignorance must not look like zero gaps."""
        ignorant = self.row(assessment=TIERS.UNASSESSED, parsed=0, stated=14,
                            unique_direct=0, unique_gaps=0)
        assessed = self.row(unique_direct=1, unique_gaps=0)
        self.assertLess(QUEUE.sort_key(assessed), QUEUE.sort_key(ignorant))

    def test_an_unparsed_posting_cannot_outrank_an_assessed_gapped_one(self):
        """A gap that was actually found outranks a posting nobody could read."""
        ignorant = self.row(assessment=TIERS.UNASSESSED, parsed=0, stated=14)
        found_gaps = self.row(unique_direct=2, unique_gaps=3, parsed=5, stated=5)
        self.assertLess(QUEUE.sort_key(found_gaps), QUEUE.sort_key(ignorant))

    def test_the_biostatistician_shape_outranks_the_unread_ones(self):
        """4 of 5 must-haves parsed and 3 real gaps, against a queue of parsed 0/0."""
        biostat = self.row(unique_direct=2, unique_gaps=3, parsed=4, stated=5,
                           assessment=TIERS.PARTIALLY_ASSESSED)
        unread = [self.row(assessment=TIERS.UNASSESSED, parsed=0, stated=n)
                  for n in (0, 1, 3, 6)]
        for other in unread:
            with self.subTest(stated=other["tiers"][MUST]["stated_lines"]):
                self.assertLess(QUEUE.sort_key(biostat), QUEUE.sort_key(other))

    def test_a_thinly_parsed_posting_is_not_promoted_for_its_silence(self):
        """1 of 14 parsed with a lucky hit does not outrank 14 of 14 parsed and covered."""
        thin = self.row(unique_direct=1, unique_gaps=0, parsed=1, stated=14,
                        assessment=TIERS.PARTIALLY_ASSESSED)
        thorough = self.row(unique_direct=1, unique_gaps=0, parsed=14, stated=14)
        # Same lane and same coverage, so the queue does not claim one is better — but the
        # row carries what a reader needs to tell them apart.
        self.assertEqual(QUEUE.lane(thin), QUEUE.lane(thorough))
        self.assertNotEqual(thin["tiers"][MUST]["parsed_lines"],
                            thorough["tiers"][MUST]["parsed_lines"])
        self.assertEqual(thin["tiers"][MUST]["assessment"], TIERS.PARTIALLY_ASSESSED)

    # ---- ordering inside a lane -----------------------------------------------------

    def test_within_a_lane_more_unique_coverage_wins(self):
        self.assertLess(QUEUE.sort_key(self.row(unique_direct=3)),
                        QUEUE.sort_key(self.row(unique_direct=1)))

    def test_within_the_gapped_lane_fewer_gaps_wins(self):
        self.assertLess(QUEUE.sort_key(self.row(unique_direct=1, unique_gaps=1)),
                        QUEUE.sort_key(self.row(unique_direct=1, unique_gaps=8)))

    def test_repeating_a_requirement_does_not_buy_a_higher_rank(self):
        """`direct` counts lines and may repeat; ordering reads `unique_direct` only."""
        repeated = self.row(unique_direct=1)
        repeated["tiers"][MUST]["direct"] = 9
        self.assertEqual(QUEUE.sort_key(repeated), QUEUE.sort_key(self.row(unique_direct=1)))

    def test_more_unique_coverage_still_beats_repetition(self):
        one_repeated = self.row(unique_direct=1)
        one_repeated["tiers"][MUST]["direct"] = 9
        self.assertLess(QUEUE.sort_key(self.row(unique_direct=2)),
                        QUEUE.sort_key(one_repeated))

    def test_direction_weight_comes_first_inside_a_lane(self):
        heavy = self.row(weight=85, unique_direct=0, unique_gaps=9)
        light = self.row(weight=5, unique_direct=4, unique_gaps=9)
        self.assertLess(QUEUE.sort_key(heavy), QUEUE.sort_key(light))

    def test_the_lane_comes_before_direction_weight(self):
        """A heavy direction does not lift a posting out of the lane it belongs to."""
        heavy_unread = self.row(weight=85, assessment=TIERS.UNASSESSED, parsed=0)
        light_assessed = self.row(weight=5, unique_direct=1)
        self.assertLess(QUEUE.sort_key(light_assessed), QUEUE.sort_key(heavy_unread))


class AssessmentStateTests(unittest.TestCase):
    """`unassessed` is a third answer, not a quiet version of one of the other two."""

    FACTS = [{"id": "fact-sas", "type": "experience_claim",
              "value": "Built SAS analysis programs", "status": "confirmed", "locked": False,
              "evidence_strength": "direct", "keywords": ["SAS"]}]

    def test_no_parsed_must_have_is_unassessed(self):
        text = posting("Requirements", "- Strong academic track record",
                       "- Excellent verbal and written communication skills")
        must = TIERS.summarize(text, self.FACTS)["tiers"][MUST]
        self.assertEqual(must["assessment"], TIERS.UNASSESSED)
        self.assertEqual(must["parsed_lines"], 0)
        self.assertEqual(must["unique_gaps"], 0)

    def test_a_posting_with_no_must_have_lines_at_all_is_unassessed(self):
        must = TIERS.summarize(posting("Nice to have", "- Experience with SAS"),
                               self.FACTS)["tiers"][MUST]
        self.assertEqual(must["assessment"], TIERS.UNASSESSED)

    def test_some_parsed_and_some_not_is_partially_assessed(self):
        text = posting("Requirements", "- Experience with SAS",
                       "- Strong academic track record")
        must = TIERS.summarize(text, self.FACTS)["tiers"][MUST]
        self.assertEqual(must["assessment"], TIERS.PARTIALLY_ASSESSED)
        self.assertEqual(must["parsed_lines"], 1)
        self.assertEqual(must["unrecognised_lines"], 1)

    def test_everything_parsed_is_fully_assessed(self):
        text = posting("Requirements", "- Experience with SAS", "- Experience with Python")
        must = TIERS.summarize(text, self.FACTS)["tiers"][MUST]
        self.assertEqual(must["assessment"], TIERS.FULLY_ASSESSED)
        self.assertEqual(must["unrecognised_lines"], 0)

    def test_the_unread_requirements_are_kept_verbatim(self):
        """A count looks like a small number; the sentences show what was not evaluated."""
        text = posting("Requirements", "- Experience with SAS",
                       "- Strong academic track record")
        must = TIERS.summarize(text, self.FACTS)["tiers"][MUST]
        self.assertEqual(must["unrecognised_requirements"], ["Strong academic track record"])

    def test_unparsed_is_never_counted_as_covered_preferred_or_gap(self):
        text = posting("Requirements", "- Strong academic track record")
        must = TIERS.summarize(text, self.FACTS)["tiers"][MUST]
        self.assertEqual((must["unique_direct"], must["unique_adjacent"], must["unique_gaps"]),
                         (0, 0, 0))
        self.assertEqual(must["stated_lines"], 1)
        self.assertEqual(TIERS.summarize(text, self.FACTS)["tiers"][PREFERRED]["stated_lines"], 0)


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


class SectionBoundaryTests(unittest.TestCase):
    """A sub-heading inside a requirement list must stop the list or re-weight it.

    Komodo's Infrastructure Engineer had 27 must-haves because an AI-expectations
    sub-heading, a "we'll prioritize" transition, two salary sub-headings, a policy section
    and a location section all failed to end the block above them.
    """

    def test_an_unrecognised_colon_subheading_ends_the_list(self):
        text = posting("Requirements", "- Experience with SAS",
                       "San Francisco Bay Area and New York City:", "- $154,000—$195,000 USD")
        self.assertEqual(tiers_of(text), [(MUST, "Experience with SAS")])

    def test_a_colon_subheading_that_states_a_weight_reopens_at_that_weight(self):
        text = posting("Nice to have", "- Experience with Sigma",
                       "Expectations of AI Use in this role (required):",
                       "- Fluent with AI coding assistants")
        self.assertEqual(tiers_of(text),
                         [(PREFERRED, "Experience with Sigma"),
                          (MUST, "Fluent with AI coding assistants")])

    def test_a_prioritize_transition_opens_a_preferred_block(self):
        text = posting("Requirements", "- Experience with SAS",
                       "Additional skills and experience we'll prioritize…",
                       "- FinOps or cloud cost-optimization experience")
        self.assertEqual(tiers_of(text),
                         [(MUST, "Experience with SAS"),
                          (PREFERRED, "FinOps or cloud cost-optimization experience")])

    def test_a_curly_apostrophe_heading_still_ends_the_list(self):
        """21 of the 112 postings write the heading with U+2019."""
        text = posting("Requirements", "- Experience with SAS",
                       "Where You’ll Work", "- Hybrid, three days in the Boston office")
        self.assertEqual(tiers_of(text), [(MUST, "Experience with SAS")])

    def test_a_curly_responsibilities_heading_still_ends_the_list(self):
        text = posting("Requirements", "- Experience with SAS",
                       "What You’ll Do", "- Run the weekly report")
        self.assertEqual(tiers_of(text), [(MUST, "Experience with SAS")])


class NotARequirementTests(unittest.TestCase):
    """Lines inside a requirement block that are not requirements of this kind."""

    def test_a_bare_pay_range_is_not_a_requirement(self):
        """Found by the corpus-wide check: the words the existing filter matches are absent."""
        for line in ("$195,000—$225,000 USD", "$70,700—$88,400", "120,000 - 140,000 USD"):
            with self.subTest(line=line):
                self.assertEqual(tiers_of(posting("Requirements", f"- {line}")), [])

    def test_a_tracking_tag_is_not_a_requirement(self):
        self.assertEqual(tiers_of(posting("Nice to have", "- #LI-Remote")), [])

    def test_a_sponsorship_statement_is_left_to_the_sponsorship_path(self):
        """It is a requirement, and not this kind: `field_policy` already owns it."""
        for line in ("We are currently unable to consider candidates who require sponsorship "
                     "for work authorization",
                     "Qualified candidates must be legally authorized to be employed in the US"):
            with self.subTest(line=line[:50]):
                self.assertEqual(tiers_of(posting("Requirements", f"- {line}")), [])

    def test_an_ordinary_requirement_beside_them_survives(self):
        text = posting("Requirements", "- Experience with SAS", "- #LI-Remote",
                       "- $70,700—$88,400 USD",
                       "- We cannot provide visa sponsorship")
        self.assertEqual(tiers_of(text), [(MUST, "Experience with SAS")])


class ParentheticalScopeTests(unittest.TestCase):
    """A weight word inside a bracket qualifies the bracket, not the line."""

    def test_a_parenthetical_preferred_does_not_downgrade_the_requirement(self):
        entry = one(posting("Requirements",
                            "- 3+ years in Life Sciences Consulting "
                            "(Business or Management Consulting preferred)"))
        self.assertEqual(entry["tier"], UNKNOWN)
        self.assertEqual(entry["reason"], "cue_scope_is_local")

    def test_a_trailing_preferred_outside_the_bracket_still_applies(self):
        entry = one(posting("Requirements",
                            "- Experience with ERP tools (e.g., NetSuite, Tableau) preferred"))
        self.assertEqual(entry["tier"], PREFERRED)

    def test_a_bracket_that_opens_the_line_labels_the_line(self):
        for line in ("(Preferred) Familiarity with dbt", "[Preferred] Familiarity with dbt"):
            with self.subTest(line=line):
                self.assertEqual(one(posting("Requirements", f"- {line}"))["tier"], PREFERRED)

    def test_a_line_that_is_only_a_parenthetical_is_not_local(self):
        self.assertEqual(one(posting("Requirements", "- Snowflake (preferred)"))["tier"],
                         PREFERRED)
