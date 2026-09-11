"""Recording an application the user made by hand, and the rung it is allowed to claim.

The module climbs two of `saved_jobs`' three rungs. Almost every test here is about the third:
that nothing it writes can be mistaken for positive submission evidence, and that nothing it
writes can later let `application_core` be talked into the `submitted` state.
"""

import importlib.util
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"submission_record_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


RECORD = load_script("submission_record")
APPLICATIONS = load_script("application_core")
SAVED = load_script("saved_jobs")

AT = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
URL = "https://example.invalid/opening-1"


class SubmissionRecordTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        APPLICATIONS.initialize(self.db)
        SAVED.initialize(self.db)
        self.addCleanup(self.db.close)
        APPLICATIONS.ingest_job(self.db, {
            "job_id": "job-1", "canonical_url": URL,
            "employer": "Example Health", "title": "Data Analyst"}, at=AT)
        APPLICATIONS.create_application(self.db, "app-1", "job-1", at=AT)

    def counts(self):
        tables = [row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables}

    # ---- the rungs -------------------------------------------------------------

    def test_an_untouched_application_is_on_no_rung(self):
        report = RECORD.state(self.db, "app-1")
        self.assertEqual(report["rung"], 0)
        self.assertIsNone(report["intended_at"])
        self.assertIsNone(report["confirmed_at"])

    def test_the_two_presses_are_separate_rungs(self):
        RECORD.intend(self.db, "app-1", at=AT)
        self.assertEqual(RECORD.state(self.db, "app-1")["rung"], RECORD.RUNG_INTENDED)
        RECORD.confirm(self.db, "app-1", at=AT)
        self.assertEqual(RECORD.state(self.db, "app-1")["rung"], RECORD.RUNG_CONFIRMED)

    def test_confirming_without_an_intention_is_refused(self):
        """The gap between the two presses is the abandonment rate; there is no gap to record
        if the first press never happened."""
        with self.assertRaises(ValueError):
            RECORD.confirm(self.db, "app-1", at=AT)

    def test_confirming_twice_keeps_the_first_time(self):
        RECORD.intend(self.db, "app-1", at=AT)
        first = RECORD.confirm(self.db, "app-1", at=AT)["submitted_confirmed_at"]
        later = datetime(2026, 9, 12, tzinfo=timezone.utc)
        again = RECORD.confirm(self.db, "app-1", at=later)
        self.assertTrue(again["already_confirmed"])
        self.assertEqual(again["submitted_confirmed_at"], first)

    # ---- what it may never reach -----------------------------------------------

    def test_the_third_rung_is_never_claimed(self):
        RECORD.intend(self.db, "app-1", at=AT)
        result = RECORD.confirm(self.db, "app-1", reference_kind="confirmation_id",
                                reference="RQ-1", at=AT)
        self.assertIs(result["evidenced"], False)
        self.assertEqual(result["rung"], RECORD.RUNG_CONFIRMED)
        self.assertIs(RECORD.state(self.db, "app-1")["evidenced"], False)

    def test_a_typed_reference_never_reaches_submission_evidence(self):
        """That table is the gate `application_core.transition` reads before `submitted`.

        A reference typed at a keyboard is the user's word about an employer artefact. Writing
        it there would let the second rung's evidence open the third rung's gate.
        """
        RECORD.intend(self.db, "app-1", at=AT)
        RECORD.confirm(self.db, "app-1", reference_kind="confirmation_id",
                       reference="RQ-1", at=AT)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM submission_evidence").fetchone()[0], 0)

    def test_the_application_state_is_untouched(self):
        before = self.db.execute(
            "SELECT state, submitted_at FROM applications WHERE application_id='app-1'"
        ).fetchone()
        RECORD.intend(self.db, "app-1", at=AT)
        RECORD.confirm(self.db, "app-1", at=AT)
        after = self.db.execute(
            "SELECT state, submitted_at FROM applications WHERE application_id='app-1'"
        ).fetchone()
        self.assertEqual((before["state"], before["submitted_at"]),
                         (after["state"], after["submitted_at"]))

    def test_a_confirmation_does_not_make_the_application_submittable(self):
        """The whole point, stated as the thing a later caller might try."""
        RECORD.intend(self.db, "app-1", at=AT)
        RECORD.confirm(self.db, "app-1", reference_kind="success_page",
                       reference="a screenshot", at=AT)
        with self.assertRaises(ValueError):
            APPLICATIONS.transition(self.db, "app-1", "submitted", "user", "manual", at=AT)

    def test_only_saved_jobs_and_its_events_are_written(self):
        before = self.counts()
        RECORD.intend(self.db, "app-1", at=AT)
        RECORD.confirm(self.db, "app-1", at=AT)
        after = self.counts()
        changed = {table for table in after if after[table] != before[table]}
        self.assertEqual(changed, {"saved_jobs"})

    # ---- the reference ---------------------------------------------------------

    def test_a_reference_is_optional(self):
        RECORD.intend(self.db, "app-1", at=AT)
        result = RECORD.confirm(self.db, "app-1", at=AT)
        self.assertIsNone(result["reference_kind"])

    def test_a_reference_kind_must_be_one_the_corpus_already_names(self):
        RECORD.intend(self.db, "app-1", at=AT)
        with self.assertRaises(ValueError):
            RECORD.confirm(self.db, "app-1", reference_kind="a_feeling",
                           reference="pretty sure", at=AT)

    def test_a_kind_without_a_reference_and_a_reference_without_a_kind_are_both_refused(self):
        RECORD.intend(self.db, "app-1", at=AT)
        with self.assertRaises(ValueError):
            RECORD.confirm(self.db, "app-1", reference_kind="confirmation_id", at=AT)
        with self.assertRaises(ValueError):
            RECORD.confirm(self.db, "app-1", reference="RQ-1", at=AT)

    def test_the_reference_vocabulary_is_the_one_application_core_already_uses(self):
        """Shared on purpose: a second set of names for the same four things would be a
        second taxonomy. The rung is which table the reference is in, not what it is called."""
        self.assertEqual(set(RECORD.REFERENCE_KINDS), APPLICATIONS.SUCCESS_EVIDENCE_TYPES)

    # ---- the queue -------------------------------------------------------------

    def test_only_confirmed_openings_leave_the_pending_queue(self):
        self.assertEqual(RECORD.pending(self.db), {})
        RECORD.intend(self.db, "app-1", at=AT)
        self.assertEqual(RECORD.pending(self.db), {}, "an intention is not a submission")
        RECORD.confirm(self.db, "app-1", at=AT)
        self.assertEqual(list(RECORD.pending(self.db)), ["app-1"])

    def test_an_unknown_application_is_refused(self):
        for call in (lambda: RECORD.state(self.db, "app-absent"),
                     lambda: RECORD.intend(self.db, "app-absent"),
                     lambda: RECORD.confirm(self.db, "app-absent")):
            with self.assertRaises(ValueError):
                call()

    def test_the_saved_row_describes_the_same_opening_as_the_application(self):
        RECORD.intend(self.db, "app-1", at=AT)
        row = self.db.execute("SELECT * FROM saved_jobs").fetchone()
        self.assertEqual(row["job_url"], URL)
        self.assertEqual(row["decision"], SAVED.APPLIED)
        self.assertEqual(row["employer"], "Example Health")


if __name__ == "__main__":
    unittest.main()
