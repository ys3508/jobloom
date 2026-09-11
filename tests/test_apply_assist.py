"""Sorting a pasted form into who may answer each question.

The module under test decides nothing of its own: it orders the existing rules and gives each
question a lane. So these tests are about the ordering and about the two places a lane could
be wrong in a way that costs something — a sensitive question that stops looking sensitive
because it was worded like a story, and a question the corpus already maps to a canonical
meaning being sent to the StoryBank as if it were prose.

The setup mirrors `tests/test_field_policy.py` rather than importing it, for the same reason
that file gives: importing one test module from another made results depend on invocation
order.
"""

import importlib.util
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"apply_assist_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


ASSIST = load_script("apply_assist")
ANSWERS = load_script("answer_library")

CONTEXT = {"application_id": "app-test", "country": "US"}


class SplitTest(unittest.TestCase):
    def test_every_segment_is_returned_including_the_dropped_ones(self):
        segments = ASSIST.split_questions("Application Form\n*Required\nEmail Address\n")
        self.assertEqual([s["text"] for s in segments],
                         ["Application Form", "*Required", "Email Address"])
        self.assertEqual([s["skipped"] for s in segments], [True, True, False])

    def test_a_span_points_back_at_the_pasted_text(self):
        text = "  Email Address\nPhone Number\n"
        segments = ASSIST.split_questions(text)
        for segment in segments:
            start, end = segment["span"]
            self.assertEqual(text[start:end], segment["text"])

    def test_oversized_paste_is_refused_rather_than_truncated(self):
        with self.assertRaises(ValueError):
            ASSIST.split_questions("x" * 100_001)


class LaneTest(unittest.TestCase):
    """The ordering, on a database with no profile and no answers.

    What a populated library and a locked profile do is exercised over HTTP in
    `tests/test_jobloom_app.py`, where the service resolves the snapshot itself. These are the
    rules that must hold before any of that: which step may overrule which.
    """

    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        ANSWERS.initialize(self.connection)

    def tearDown(self):
        self.connection.close()

    def classify(self, question, facts=None, snapshot=None):
        return ASSIST.classify_question(self.connection, question, snapshot_sha256=snapshot,
                                        context=CONTEXT, facts=facts or [])

    def test_a_sensitive_question_stays_manual_however_it_is_worded(self):
        """The asymmetry `field_policy` is built on, checked in the direction that matters.

        Each of these carries a narrative cue *and* a domain rule. A lane that let the cue win
        would put a model's sentence into a legal, compensation or EEO field.
        """
        for question in (
            "Tell us about a time when you required visa sponsorship.",
            "Describe a time your salary expectations were not met.",
            "Why are you willing to disclose your race/ethnicity to us?",
            "Walk us through how you are related to an employee here.",
        ):
            with self.subTest(question=question):
                result = self.classify(question)
                self.assertEqual(result["lane"], "manual_only")
                self.assertFalse(result["narrative_hint"])
                self.assertEqual(result["related_fact_ids"], [])

    def test_a_reviewed_meaning_that_is_sensitive_is_manual_even_when_the_label_is_not(self):
        """The meaning is the better evidence, and it may only add caution."""
        ANSWERS.add_question_form(self.connection, "eeo.race",
                                  "Which of these best describes you", verified_by_user=True)
        result = self.classify("Which of these best describes you")
        self.assertEqual(result["lane"], "manual_only")
        self.assertEqual(result["reason"], "meaning:voluntary_eeo")

    def test_a_known_canonical_meaning_is_not_sent_to_the_storybank(self):
        """"How did you hear about this opportunity?" carries `how ... you` and is a dropdown."""
        ANSWERS.add_question_form(self.connection, "discovery_source",
                                  "How did you hear about this opportunity?",
                                  verified_by_user=True)
        result = self.classify("How did you hear about this opportunity?")
        self.assertEqual(result["lane"], "you_answer")
        self.assertEqual(result["canonical_id"], "discovery_source")
        self.assertEqual(result["source"], "answer_library")
        self.assertFalse(result["narrative_hint"])

    def test_a_contested_mapping_pauses_rather_than_picking_one(self):
        for canonical_id in ("contact.email", "contact.phone"):
            ANSWERS.add_question_form(self.connection, canonical_id, "Reach you at",
                                      verified_by_user=True)
        result = self.classify("Reach you at")
        self.assertEqual(result["lane"], "you_answer")
        self.assertEqual(result["reason"], "question_mapping_conflict")
        self.assertIsNone(result["canonical_id"])

    def test_a_profile_meaning_without_locked_materials_does_not_resolve(self):
        """No snapshot to resolve against is a stated reason, never a fall-through to a value."""
        ANSWERS.add_question_form(self.connection, "contact.email", "Email Address",
                                  verified_by_user=True)
        result = self.classify("Email Address", snapshot=None)
        self.assertEqual(result["lane"], "you_answer")
        self.assertEqual(result["reason"],
                         "application_materials_not_locked_to_active_profile")

    def test_an_unmapped_story_question_reaches_the_storybank_with_related_facts(self):
        facts = [{"id": "fact-1", "value": "Built a clinical trial database of 400+ trials",
                  "evidence_strength": "direct", "status": "confirmed"}]
        result = self.classify(
            "Please describe a time when you built a clinical trial database.", facts)
        self.assertEqual(result["lane"], "narrative_gap")
        self.assertTrue(result["narrative_hint"])

    def test_related_facts_are_offered_only_as_facts_and_never_as_an_answer(self):
        """The lane carries fact ids. It must not carry a composed sentence under any key."""
        facts = [{"id": "fact-1", "value": "Built a clinical trial database of 400+ trials",
                  "evidence_strength": "direct", "status": "confirmed"}]
        result = self.classify(
            "Please describe a time when you built a clinical trial database.", facts)
        self.assertNotIn("answer", result)
        self.assertNotIn("draft", result)
        self.assertFalse(result["answer_exists"])

    def test_an_unknown_plain_question_is_the_users_to_answer(self):
        result = self.classify("Preferred start date")
        self.assertEqual(result["lane"], "you_answer")
        self.assertEqual(result["reason"], "new_question")

    def test_every_lane_carries_the_same_keys_and_never_submit_ready(self):
        """A caller reading a key that only some lanes carried is how a default became a value."""
        results = [self.classify("Race/Ethnicity (voluntary)"),
                   self.classify("Preferred start date"),
                   self.classify("Tell us about a project you are proud of.")]
        shape = set(results[0])
        for result in results:
            self.assertEqual(set(result), shape)
            self.assertIs(result["auto_submit_ready"], False)
            self.assertIs(result["auto_fill_ready"], False)

    def test_counts_and_blocking_cover_every_question_once(self):
        out = ASSIST.classify(
            self.connection,
            ["Race/Ethnicity (voluntary)", "Preferred start date",
             "Tell us about a project you are proud of."],
            snapshot_sha256=None, context=CONTEXT, facts=[])
        self.assertEqual(sum(out["counts"].values()), 3)
        self.assertEqual(out["blocking"], 3)
        self.assertEqual(set(out["counts"]), set(ASSIST.LANES))

    def test_an_answer_waiting_on_authorization_counts_as_blocking(self):
        self.assertIn("answer_needs_authorization", ASSIST.BLOCKING_LANES)
        self.assertNotIn("answer_ready", ASSIST.BLOCKING_LANES)
        self.assertNotIn("profile_ready", ASSIST.BLOCKING_LANES)

    def test_an_empty_or_oversized_page_is_refused(self):
        with self.assertRaises(ValueError):
            ASSIST.classify(self.connection, [], snapshot_sha256=None, context=CONTEXT,
                            facts=[])
        with self.assertRaises(ValueError):
            ASSIST.classify(self.connection, ["q"] * 251, snapshot_sha256=None,
                            context=CONTEXT, facts=[])


if __name__ == "__main__":
    unittest.main()


class MaterialTest(unittest.TestCase):
    """What a narrative question is offered to write from, and what that offer claims."""

    FACTS = [
        {"id": "fact-trials", "status": "confirmed",
         "value": "Built a clinical trial database covering 400+ trials using SAS and SQL"},
        {"id": "fact-groups", "status": "confirmed",
         "value": "Ran two focus groups on three pharmaceutical products"},
        {"id": "fact-draft", "status": "proposed",
         "value": "Built a clinical trial dashboard nobody confirmed"},
    ]

    def test_a_whole_question_finds_material_where_the_requirement_matcher_could_not(self):
        """`evidence_matcher.related_facts` needs every token; a question has "please" in it."""
        material = ASSIST.related_material(
            "Please describe a time when you built a clinical trial database.", self.FACTS)
        self.assertEqual([item["id"] for item in material], ["fact-trials"])
        self.assertEqual(material[0]["overlap"], ["built", "clinical", "database", "trial"])

    def test_a_question_about_something_never_done_is_offered_nothing(self):
        self.assertEqual(
            ASSIST.related_material("Tell us about leading a fundraising campaign.", self.FACTS),
            [])

    def test_unconfirmed_facts_are_never_offered_as_material(self):
        material = ASSIST.related_material("Describe the dashboard you built.", self.FACTS)
        self.assertNotIn("fact-draft", [item["id"] for item in material])

    def test_a_question_of_only_stopwords_offers_nothing_rather_than_everything(self):
        self.assertEqual(ASSIST.related_material("Tell us about you", self.FACTS), [])

    def test_the_offer_is_capped_and_ordered_by_overlap(self):
        facts = [{"id": f"fact-{n}", "status": "confirmed", "value": "clinical trial database"}
                 for n in range(20)]
        facts.append({"id": "fact-best", "status": "confirmed",
                      "value": "clinical trial database built with SAS"})
        material = ASSIST.related_material(
            "Describe the clinical trial database you built with SAS.", facts)
        self.assertEqual(len(material), ASSIST.MATERIAL_LIMIT)
        self.assertEqual(material[0]["id"], "fact-best")
