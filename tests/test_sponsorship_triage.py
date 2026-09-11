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


if __name__ == "__main__":
    unittest.main()
