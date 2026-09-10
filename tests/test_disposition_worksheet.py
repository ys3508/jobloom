"""The sheet a person fills in before applying, and what it refuses to do for them.

Two rules carry the weight here. A hard filter is recorded per posting, never inferred across
an employer — an employer stating a sponsorship policy on one posting has said nothing about
another. And no exclusion is permanent: work-authorization facts expire and posting text
changes, so every one names what would reopen it.
"""

import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"disposition_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


SHEET = load_script("disposition_worksheet")
QUEUE = load_script("review_queue")

AT = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
STATEMENT = ("We are currently unable to consider candidates who require, or will require in "
             "the future, sponsorship for work authorization.")


def row(job_id, employer, lane, lane_rank, unread=("Ten years of leadership",)):
    return {"job_id": job_id, "employer": employer, "title": f"{job_id} role",
            "location": "Boston", "lane": lane, "lane_rank": lane_rank,
            "canonical_url": f"https://example.invalid/{job_id}",
            "apply_url": f"https://example.invalid/{job_id}/apply",
            "tiers": {SHEET.requirement_tiers.MUST_HAVE: {
                "assessment": "partially_assessed", "parsed_lines": 1, "stated_lines": 14,
                "direct_terms": ["SAS"], "adjacent_terms": [], "gap_terms": [],
                "unrecognised_requirements": list(unread)}}}


def reject():
    return SHEET.hard_reject(
        reason="employer_explicitly_refuses_future_sponsorship",
        candidate_fact="work_authorization.sponsorship_future = true (expires 2028-08-24)",
        employer_evidence=STATEMENT,
        recheck_trigger="sponsorship_future changes, the fact expires, or the posting changes")


class SplitTests(unittest.TestCase):

    def setUp(self):
        self.queue = {"openings_in_queue": 4, "rows": [
            row("job-a", "Beghou", QUEUE.LANE_CLEAR, 1),
            row("job-b", "Beghou", QUEUE.LANE_CLEAR, 2),
            row("job-c", "Unlearn", QUEUE.LANE_GAPPED, 1),
            row("job-d", "Veeva", QUEUE.LANE_GAPPED, 2)]}

    def test_survivors_come_first_and_carry_their_unread_requirements(self):
        sheet = SHEET.build(self.queue, {}, {"job-a": reject()}, at=AT)
        self.assertEqual([entry["job_id"] for entry in sheet["survivors"]],
                         ["job-b", "job-c", "job-d"])
        self.assertEqual(sheet["survivors"][0]["unread_requirements"],
                         [{"requirement": "Ten years of leadership", "disposition": None,
                           "note": None}])

    def test_a_blocked_posting_is_not_reviewed_line_by_line(self):
        """Its requirements are not why it is out; reading them buys nothing."""
        sheet = SHEET.build(self.queue, {}, {"job-a": reject()}, at=AT)
        blocked = sheet["blocked"][0]
        self.assertNotIn("unread_requirements", blocked)
        self.assertEqual(blocked["decision"], SHEET.BLOCKED)

    def test_a_block_does_not_spread_to_the_same_employer(self):
        """`job-b` is also Beghou and carries no statement of its own."""
        sheet = SHEET.build(self.queue, {}, {"job-a": reject()}, at=AT)
        self.assertIn("job-b", [entry["job_id"] for entry in sheet["survivors"]])
        self.assertEqual([entry["job_id"] for entry in sheet["blocked"]], ["job-a"])

    def test_both_lanes_are_reviewed(self):
        sheet = SHEET.build(self.queue, {}, {}, at=AT)
        lanes = {entry["lane"] for entry in sheet["survivors"]}
        self.assertEqual(lanes, {QUEUE.LANE_CLEAR, QUEUE.LANE_GAPPED})

    def test_the_unassessed_lane_is_not_in_the_pool_by_default(self):
        queue = {"openings_in_queue": 1,
                 "rows": [row("job-x", "Natera", QUEUE.LANE_UNASSESSED, 1)]}
        self.assertEqual(SHEET.build(queue, {}, {}, at=AT)["counts"]["survivors"], 0)

    def test_the_counts_describe_what_is_actually_in_the_sheet(self):
        sheet = SHEET.build(self.queue, {}, {"job-a": reject()}, at=AT)
        self.assertEqual(sheet["counts"]["survivors"], len(sheet["survivors"]))
        self.assertEqual(sheet["counts"]["blocked"], len(sheet["blocked"]))
        self.assertEqual(sheet["counts"]["unread_requirements"],
                         sum(len(e["unread_requirements"]) for e in sheet["survivors"]))


class HardRejectTests(unittest.TestCase):

    def test_an_exclusion_records_its_evidence_and_what_reopens_it(self):
        entry = reject()
        self.assertEqual(entry["employer_evidence"], STATEMENT)
        self.assertIn("2028-08-24", entry["candidate_fact"])
        self.assertIn("changes", entry["recheck_trigger"])

    def test_an_exclusion_without_evidence_is_refused(self):
        for missing in ("reason", "candidate_fact", "employer_evidence", "recheck_trigger"):
            with self.subTest(missing=missing):
                fields = {"reason": "r", "candidate_fact": "f",
                          "employer_evidence": "e", "recheck_trigger": "t", missing: "  "}
                with self.assertRaises(ValueError):
                    SHEET.hard_reject(**fields)

    def test_no_exclusion_is_permanent(self):
        """A recheck trigger is required, so nothing can be excluded for good."""
        with self.assertRaises(ValueError):
            SHEET.hard_reject(reason="r", candidate_fact="f", employer_evidence="e",
                              recheck_trigger="")


class RenderTests(unittest.TestCase):

    def sheet(self):
        queue = {"openings_in_queue": 2, "rows": [
            row("job-a", "Beghou", QUEUE.LANE_CLEAR, 1),
            row("job-c", "Unlearn", QUEUE.LANE_GAPPED, 1)]}
        return SHEET.build(queue, {}, {"job-a": reject()}, at=AT)

    def test_the_sheet_says_a_lane_is_not_a_reason_to_apply(self):
        text = SHEET.render(self.sheet())
        self.assertIn("not a reason to apply", text)
        self.assertIn("partially_assessed", text)
        self.assertIn("1 of 14 must-have lines evaluated", text)

    def test_the_blocked_section_shows_the_employer_sentence(self):
        text = SHEET.render(self.sheet())
        self.assertIn(STATEMENT, text)
        self.assertIn("recheck when", text)

    def test_the_allowed_dispositions_are_listed(self):
        text = SHEET.render(self.sheet())
        for name in SHEET.DISPOSITIONS:
            with self.subTest(name=name):
                self.assertIn(name, text)

    def test_a_pipe_in_a_requirement_does_not_break_the_table(self):
        queue = {"openings_in_queue": 1, "rows": [
            row("job-a", "X", QUEUE.LANE_CLEAR, 1, unread=("SQL | Python | R",))]}
        text = SHEET.render(SHEET.build(queue, {}, {}, at=AT))
        self.assertIn("SQL \\| Python \\| R", text)


if __name__ == "__main__":
    unittest.main()
