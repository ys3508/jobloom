"""Which queued openings a person could settle, and what the page is allowed to say about them.

The module reads a built queue and a pull of JobCards and returns a list. Its risk is not in
the reading — it is in quietly acquiring an opinion. So most of these tests are about what must
*not* appear: a suggested verdict, a default, a card that reached the list without the employer
having said anything, or a verdict that outlived the card it was read from.
"""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"sponsorship_triage_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


TRIAGE = load_script("sponsorship_triage")

SAYS_NO = ("We are currently unable to consider candidates who require, or will require in "
           "the future, sponsorship for work authorization.")


def card(job_id, *, sponsorship="unknown", statements=(), description=""):
    return {"job_id": job_id, "employer": "Beghou Consulting", "title": "Consultant",
            "location": "Boston", "canonical_url": f"https://example.test/{job_id}",
            "sponsorship": sponsorship, "sponsorship_statements": list(statements),
            "description": description, "description_sha256": "0" * 64}


class NeedsTriageTest(unittest.TestCase):
    def test_both_halves_are_required(self):
        """Unknown with nothing said is not triageable, and a settled card is not either."""
        self.assertTrue(TRIAGE.needs_triage(card("a", statements=[SAYS_NO])))
        self.assertFalse(TRIAGE.needs_triage(card("b", statements=[])))
        self.assertFalse(TRIAGE.needs_triage(
            card("c", sponsorship="does_not_support", statements=[SAYS_NO])))
        self.assertFalse(TRIAGE.needs_triage(
            card("d", sponsorship="supports", statements=[SAYS_NO])))


class ContextTest(unittest.TestCase):
    def test_context_places_the_sentence_in_the_posting(self):
        description = "A" * 400 + SAYS_NO + "B" * 400
        context = TRIAGE.statement_context(description, SAYS_NO)
        self.assertTrue(context["located"])
        self.assertTrue(context["before"].endswith("A"))
        self.assertTrue(context["after"].startswith("B"))

    def test_a_statement_that_is_not_in_the_description_says_so(self):
        """Rather than returning empty context that reads as 'the sentence stood alone'."""
        context = TRIAGE.statement_context("nothing like it here", SAYS_NO)
        self.assertFalse(context["located"])


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        os.chmod(self.root, 0o700)
        (self.root / "jobs-wide").mkdir()
        self.write_card(card("job-1", statements=[SAYS_NO],
                             description="Intro. " + SAYS_NO + " Outro."))
        self.write_card(card("job-2"))
        self.write_card(card("job-3", sponsorship="does_not_support", statements=[SAYS_NO]))
        (self.root / "review-queue-test.json").write_text(json.dumps({
            "rows": [{"job_id": f"job-{n}", "lane": "x", "rank": n} for n in (1, 2, 3)]
        }), encoding="utf-8")

    def write_card(self, value):
        (self.root / "jobs-wide" / f"{value['job_id']}.json").write_text(
            json.dumps(value), encoding="utf-8")

    def test_only_the_openings_a_person_could_settle_are_listed(self):
        report = TRIAGE.build(self.root)
        self.assertEqual(report["queued"], 3)
        self.assertEqual(report["needing_triage"], 1)
        self.assertEqual(report["items"][0]["job_id"], "job-1")

    def test_no_verdict_is_suggested_defaulted_or_ranked(self):
        """The three choices are offered as a set, in a fixed order, with nothing selected."""
        item = TRIAGE.build(self.root)["items"][0]
        self.assertEqual(item["verdicts"], ["supports", "does_not_support", "unclear"])
        for key in ("verdict", "suggested", "suggested_verdict", "likely", "default"):
            self.assertNotIn(key, item)

    def test_the_card_is_carried_as_it_stands_not_resolved(self):
        item = TRIAGE.build(self.root)["items"][0]
        self.assertEqual(item["sponsorship"], "unknown")

    def test_a_verdict_has_two_hashes_to_bind_to(self):
        """An edited card and an edited sentence are both things a verdict must not survive."""
        item = TRIAGE.build(self.root)["items"][0]
        self.assertRegex(item["job_card_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(item["statements"][0]["statement_sha256"], r"^[0-9a-f]{64}$")

    def test_editing_the_card_changes_the_hash_a_verdict_bound_to(self):
        before = TRIAGE.build(self.root)["items"][0]["job_card_sha256"]
        edited = card("job-1", statements=[SAYS_NO],
                      description="Intro. " + SAYS_NO + " Outro. Now with more.")
        self.write_card(edited)
        after = TRIAGE.build(self.root)["items"][0]["job_card_sha256"]
        self.assertNotEqual(before, after)

    def test_the_report_says_it_wrote_nothing(self):
        self.assertIs(TRIAGE.build(self.root)["writes"], False)

    def test_a_queued_opening_with_no_card_is_counted_not_dropped_silently(self):
        (self.root / "review-queue-test.json").write_text(json.dumps({
            "rows": [{"job_id": "job-absent"}]}), encoding="utf-8")
        report = TRIAGE.build(self.root)
        self.assertEqual(report["cards_missing"], 1)

    def test_no_built_queue_is_refused_rather_than_guessed_at(self):
        (self.root / "review-queue-test.json").unlink()
        with self.assertRaises(ValueError):
            TRIAGE.build(self.root)

    def test_the_queue_it_read_is_named_back(self):
        self.assertEqual(TRIAGE.build(self.root)["queue_file"], "review-queue-test.json")



TRIAL_SENSE = ("Assists with coordinating Investigator Meeting attendees and items "
               "with Sponsor(s)")
VISA_SENSE = ("Veeva Systems does not anticipate providing sponsorship for employment "
              "visa status (e.g., H-1B).")


class HintTest(unittest.TestCase):
    """The reading-order hint, and the three things it is not allowed to be."""

    def test_the_employment_sense_is_told_from_the_clinical_one(self):
        self.assertEqual(TRIAGE.statement_hint(SAYS_NO), "employment_or_visa_signal")
        self.assertEqual(TRIAGE.statement_hint(VISA_SENSE), "employment_or_visa_signal")
        self.assertEqual(TRIAGE.statement_hint(TRIAL_SENSE), "possible_trial_sponsor_only")

    def test_a_card_carrying_both_senses_is_its_own_tier(self):
        both = [{"hint": "employment_or_visa_signal"}, {"hint": "possible_trial_sponsor_only"}]
        self.assertEqual(TRIAGE.card_hint(both), "mixed_signal")
        self.assertEqual(TRIAGE.card_hint(both[:1]), "employment_or_visa_signal")
        self.assertEqual(TRIAGE.card_hint(both[1:]), "possible_trial_sponsor_only")


class HintDoesNotDecideTest(unittest.TestCase):
    """A hint orders the page. It may not remove a card, settle a field, or pick a verdict."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        os.chmod(self.root, 0o700)
        (self.root / "jobs-wide").mkdir()
        for job_id, statements in (("job-visa", [SAYS_NO]),
                                   ("job-trial", [TRIAL_SENSE]),
                                   ("job-both", [SAYS_NO, TRIAL_SENSE])):
            value = card(job_id, statements=statements,
                         description="Intro. " + " ".join(statements) + " Outro.")
            (self.root / "jobs-wide" / f"{job_id}.json").write_text(
                json.dumps(value), encoding="utf-8")
        (self.root / "review-queue-test.json").write_text(json.dumps({
            "rows": [{"job_id": j} for j in ("job-trial", "job-both", "job-visa")]
        }), encoding="utf-8")
        self.report = TRIAGE.build(self.root)

    def test_a_trial_only_card_is_kept_not_filtered_out(self):
        self.assertEqual(self.report["needing_triage"], 3)
        self.assertIn("job-trial", [item["job_id"] for item in self.report["items"]])

    def test_the_hint_orders_the_page(self):
        self.assertEqual([item["hint"] for item in self.report["items"]],
                         ["employment_or_visa_signal", "mixed_signal",
                          "possible_trial_sponsor_only"])
        self.assertEqual(self.report["hint_counts"],
                         {"employment_or_visa_signal": 1, "mixed_signal": 1,
                          "possible_trial_sponsor_only": 1})

    def test_the_hint_never_becomes_a_verdict_or_settles_the_field(self):
        for item in self.report["items"]:
            self.assertEqual(item["sponsorship"], "unknown")
            self.assertEqual(item["verdicts"], list(TRIAGE.VERDICTS))
            for key in ("verdict", "suggested", "suggested_verdict", "likely", "default",
                        "eligibility", "hard_filter_failures"):
                self.assertNotIn(key, item)

    def test_a_trial_only_card_still_offers_all_three_verdicts(self):
        trial = next(i for i in self.report["items"] if i["job_id"] == "job-trial")
        self.assertEqual(trial["verdicts"], ["supports", "does_not_support", "unclear"])


class GroupingTest(unittest.TestCase):
    """One reading per sentence; one decision per opening."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        os.chmod(self.root, 0o700)
        (self.root / "jobs-wide").mkdir()
        # The same sentence in three openings, two of them the same employer, plus one
        # opening whose sentence differs by a single word.
        almost = SAYS_NO.replace("currently ", "")
        for job_id, text in (("job-a", SAYS_NO), ("job-b", SAYS_NO), ("job-c", SAYS_NO),
                             ("job-d", almost)):
            value = card(job_id, statements=[text], description="Intro. " + text + " Outro.")
            value["employer"] = "Beghou Consulting" if job_id != "job-c" else "Other Co"
            (self.root / "jobs-wide" / f"{job_id}.json").write_text(
                json.dumps(value), encoding="utf-8")
        (self.root / "review-queue-test.json").write_text(json.dumps({
            "rows": [{"job_id": j} for j in ("job-a", "job-b", "job-c", "job-d")]
        }), encoding="utf-8")
        self.report = TRIAGE.build(self.root)

    def test_identical_sentences_group_and_a_one_word_difference_does_not(self):
        groups = self.report["groups"]
        self.assertEqual([group["occurrences"] for group in groups], [3, 1])
        self.assertNotEqual(groups[0]["statement_sha256"], groups[1]["statement_sha256"])

    def test_every_opening_keeps_its_own_identity_inside_a_group(self):
        members = self.report["groups"][0]["members"]
        self.assertEqual(len(members), 3)
        for member in members:
            self.assertRegex(member["job_card_sha256"], r"^[0-9a-f]{64}$")
            self.assertIn("job_id", member)
        self.assertEqual(len({member["job_card_sha256"] for member in members}), 3)

    def test_a_group_never_carries_an_employer_level_identity(self):
        """Grouping is by sentence. Two openings at one employer are still two openings."""
        for group in self.report["groups"]:
            for key in ("employer", "employers", "applies_to_employer", "company_rule"):
                self.assertNotIn(key, group)

    def test_nothing_in_a_group_is_selected_by_default(self):
        for group in self.report["groups"]:
            for member in group["members"]:
                self.assertIs(member["selected"], False)

    def test_a_group_offers_the_three_verdicts_and_suggests_none(self):
        for group in self.report["groups"]:
            self.assertEqual(group["verdicts"], list(TRIAGE.VERDICTS))
            self.assertNotIn("verdict", group)

    def test_every_card_still_appears_as_its_own_item(self):
        self.assertEqual(self.report["needing_triage"], 4)


class DisplayBlockTest(unittest.TestCase):
    """Merging is a way of showing sentences and never a new identity for them."""

    def setUp(self):
        self.second = ("Applicants must be authorized to work in the US on a permanent "
                       "and ongoing basis.")
        self.description = "Intro paragraph. " + SAYS_NO + " " + self.second + " Outro."
        self.statements = []
        for text in (SAYS_NO, self.second):
            entry = {"text": text, "statement_sha256": TRIAGE.resume_core.canonical_hash(text),
                     "hint": TRIAGE.statement_hint(text)}
            entry.update(TRIAGE.statement_context(self.description, text))
            self.statements.append(entry)

    def test_adjacent_sentences_become_one_block(self):
        blocks = TRIAGE.display_blocks(self.description, self.statements)
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0]["merged"])

    def test_a_block_is_the_posting_s_own_run_not_a_concatenation(self):
        blocks = TRIAGE.display_blocks(self.description, self.statements)
        self.assertIn(blocks[0]["text"], self.description)

    def test_a_block_names_every_statement_inside_it(self):
        blocks = TRIAGE.display_blocks(self.description, self.statements)
        self.assertEqual(blocks[0]["statement_sha256s"],
                         [s["statement_sha256"] for s in self.statements])

    def test_the_underlying_statements_keep_text_span_and_hash(self):
        for statement in self.statements:
            start, end = statement["span"]
            self.assertEqual(self.description[start:end], statement["text"])
            self.assertRegex(statement["statement_sha256"], r"^[0-9a-f]{64}$")

    def test_a_block_has_no_identity_of_its_own(self):
        """Nothing binds to a block, so it must not carry a hash that could be bound to."""
        blocks = TRIAGE.display_blocks(self.description, self.statements)
        for key in ("block_sha256", "sha256", "statement_sha256", "id"):
            self.assertNotIn(key, blocks[0])

    def test_distant_sentences_are_not_merged(self):
        far = "Intro. " + SAYS_NO + ("filler " * 200) + self.second + " Outro."
        statements = []
        for text in (SAYS_NO, self.second):
            entry = {"text": text, "statement_sha256": TRIAGE.resume_core.canonical_hash(text),
                     "hint": TRIAGE.statement_hint(text)}
            entry.update(TRIAGE.statement_context(far, text))
            statements.append(entry)
        self.assertEqual(len(TRIAGE.display_blocks(far, statements)), 2)

    def test_a_statement_that_cannot_be_located_keeps_its_own_block(self):
        orphan = {"text": "Never written in the posting.", "hint": "possible_trial_sponsor_only",
                  "statement_sha256": TRIAGE.resume_core.canonical_hash("orphan"),
                  "before": "", "after": "", "located": False, "span": None}
        blocks = TRIAGE.display_blocks(self.description, self.statements + [orphan])
        self.assertEqual(len(blocks), 2)
        self.assertIs(blocks[-1]["located"], False)

if __name__ == "__main__":
    unittest.main()
