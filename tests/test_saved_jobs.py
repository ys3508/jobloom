import importlib.util
import sqlite3
import unittest
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"saved_jobs_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


SAVED = load_script("saved_jobs")
APPLICATIONS = load_script("application_core")
OUTCOMES = load_script("outcome_core")
AT = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
TODAY = date(2026, 8, 29)


def card(url="https://jobs.example.com/1", **overrides):
    base = {
        "canonical_url": url, "title": "Clinical Data Analyst", "employer": "Acme Health",
        "location": "Boston, MA", "country": "US", "work_arrangement": "hybrid",
        "employment_type": "full_time", "source": "panel", "ats": "greenhouse",
        "extraction": {"ats": {"posted_at": "2026-08-01T00:00:00+00:00",
                               "deadline": None, "apply_url": f"{url}/apply"}},
    }
    base.update(overrides)
    return base


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        SAVED.initialize(self.db)
        self.addCleanup(self.db.close)

    def test_saving_needs_no_review_and_creates_no_application(self):
        # The pre-submission review gate stands between a card and being sent. Keeping a
        # note sends nothing, so it neither needs the gate nor relaxes it.
        result = SAVED.save(self.db, card(), actor="user", at=AT)
        self.assertEqual(result["decision"], "later")
        self.assertFalse(self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='applications'").fetchone())

    def test_the_posting_url_is_required_so_the_job_can_be_reopened(self):
        with self.assertRaises(ValueError) as caught:
            SAVED.save(self.db, card(url="local-file"), actor="user", at=AT)
        self.assertIn("reopened", str(caught.exception))

    def test_saving_the_same_job_twice_keeps_one_row_and_its_first_decision_time(self):
        SAVED.save(self.db, card(), actor="user", at=AT)
        later = datetime(2026, 9, 5, tzinfo=timezone.utc)
        result = SAVED.save(self.db, card(), actor="user", reason="second look", at=later)
        self.assertTrue(result["updated"])
        self.assertEqual(result["decided_at"], AT.isoformat())
        rows = SAVED.tracker_rows(self.db, today=TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "second look")

    def test_only_a_decision_a_person_would_press_a_button_for_is_accepted(self):
        # A skip is moving to the next job; it leaves no record by design.
        with self.assertRaises(ValueError):
            SAVED.save(self.db, card(), actor="user", decision="skipped", at=AT)

    def test_an_actor_is_required(self):
        with self.assertRaises(ValueError):
            SAVED.save(self.db, card(), actor="  ", at=AT)

    def test_forget_removes_it(self):
        SAVED.save(self.db, card(), actor="user", at=AT)
        self.assertEqual(SAVED.forget(self.db, "https://jobs.example.com/1")["removed"], 1)
        self.assertEqual(SAVED.tracker_rows(self.db, today=TODAY), [])


class PostingDateTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        SAVED.initialize(self.db)
        self.addCleanup(self.db.close)

    def test_days_open_is_computed_on_read_not_stored(self):
        # A stored copy would be wrong by exactly as long as the file had been sitting there.
        SAVED.save(self.db, card(), actor="user", at=AT)
        self.assertEqual(SAVED.tracker_rows(self.db, today=date(2026, 8, 29))[0]["days_open"], 28)
        self.assertEqual(SAVED.tracker_rows(self.db, today=date(2026, 9, 30))[0]["days_open"], 60)

    def test_a_deadline_is_only_ever_the_one_the_employer_stated(self):
        # Employers state a deadline on a small minority of postings. Deriving one for the
        # rest would put a date on the card that nobody wrote.
        rows = SAVED.tracker_rows(self.db, today=TODAY)
        SAVED.save(self.db, card(), actor="user", at=AT)
        self.assertIsNone(SAVED.tracker_rows(self.db, today=TODAY)[0]["deadline"])
        stated = card(url="https://jobs.example.com/2")
        stated["extraction"]["ats"]["deadline"] = "2026-09-30"
        SAVED.save(self.db, stated, actor="user", at=AT)
        saved = {row["job_url"]: row for row in SAVED.tracker_rows(self.db, today=TODAY)}
        self.assertEqual(saved["https://jobs.example.com/2"]["deadline"], "2026-09-30")

    def test_an_unparseable_posting_date_yields_no_age_rather_than_a_wrong_one(self):
        self.assertIsNone(SAVED.days_open("sometime last spring", TODAY))
        self.assertIsNone(SAVED.days_open(None, TODAY))


class AppliedTests(unittest.TestCase):
    """Applications made by hand were invisible: the tracker derived "Applied" by joining
    the applications table, so a job applied to outside the fill flow stayed "Saved"
    forever and the funnel collected nothing."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        SAVED.initialize(self.db)
        self.addCleanup(self.db.close)

    def test_saying_you_applied_is_recorded_as_yours_not_as_evidence(self):
        # `application_core`'s `submitted` requires a confirmation page or an account
        # record. Nothing here has seen any of that, and the row says which claim it is.
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED, at=AT)
        row = SAVED.tracker_rows(self.db, today=TODAY)[0]
        self.assertEqual(row["current_status"], "Applied")
        self.assertEqual(row["applied_evidence"], "self-reported")
        self.assertEqual(row["applied_at"], AT.isoformat())

    def test_the_apply_time_is_the_first_one_and_survives_a_change_of_mind(self):
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED, at=AT)
        later = datetime(2026, 9, 9, tzinfo=timezone.utc)
        SAVED.save(self.db, card(), actor="user", decision=SAVED.LATER, at=later)
        row = SAVED.tracker_rows(self.db, today=TODAY)[0]
        # An application already made is not undone by a change of mind about the next one.
        self.assertEqual(row["applied_at"], AT.isoformat())

    def test_an_outcome_belongs_to_something_you_applied_to(self):
        SAVED.save(self.db, card(), actor="user", at=AT)
        with self.assertRaises(ValueError) as caught:
            SAVED.record_outcome(self.db, "https://jobs.example.com/1", "interview", at=AT)
        self.assertIn("mark it applied first", str(caught.exception))

    def test_an_outcome_is_recorded_and_reported(self):
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED, at=AT)
        SAVED.record_outcome(self.db, "https://jobs.example.com/1", "interview", at=AT)
        row = SAVED.tracker_rows(self.db, today=TODAY)[0]
        self.assertEqual(row["outcome"], "interview")
        self.assertEqual(SAVED.status(self.db, today=TODAY)["outcomes"], {"interview": 1})

    def test_the_outcome_vocabulary_is_the_one_the_funnel_already_uses(self):
        # Borrowed rather than redefined, so the two halves cannot drift apart. The
        # `outcome_records` table itself cannot be reused: it has a foreign key to an
        # application, and an application made by hand has no row there.
        self.assertEqual(SAVED.OUTCOMES, OUTCOMES.OUTCOME_TYPES)
        with self.assertRaises(ValueError):
            SAVED.record_outcome(self.db, "https://jobs.example.com/1", "ghosted", at=AT)

    def test_a_skip_still_records_nothing(self):
        with self.assertRaises(ValueError):
            SAVED.save(self.db, card(), actor="user", decision="skipped", at=AT)


JUDGEMENT = {"verdict": "apply", "verdict_reason": "your evidence covers what it states",
             "direction": "Research / Clinical Research Data", "covered": 4, "stated": 4,
             "hidden_strength": 1, "evidence_gap": 0, "suggested_choice": "precision"}


class JudgementSnapshotTests(unittest.TestCase):
    """Without the call that preceded a reply, no reply can test whether the call was worth
    anything. It has to be the call as shown — directions are revised and the ontology is
    recalibrated, so recomputing later answers a different question."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        SAVED.initialize(self.db)
        self.addCleanup(self.db.close)

    def test_the_call_is_recorded_with_the_decision(self):
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED,
                   judgement=JUDGEMENT, at=AT)
        row = SAVED.tracker_rows(self.db, today=TODAY)[0]
        self.assertEqual(row["verdict"], "apply")
        self.assertEqual(row["direction"], "Research / Clinical Research Data")
        self.assertEqual((row["covered"], row["stated"]), (4, 4))

    def test_the_first_call_survives_a_later_decision(self):
        # The judgement the decision was weighed against is not rewritten by a later press.
        SAVED.save(self.db, card(), actor="user", judgement=JUDGEMENT, at=AT)
        later = datetime(2026, 9, 9, tzinfo=timezone.utc)
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED,
                   judgement={**JUDGEMENT, "verdict": "skip", "covered": 0}, at=later)
        row = SAVED.tracker_rows(self.db, today=TODAY)[0]
        self.assertEqual(row["verdict"], "apply")
        self.assertEqual(row["covered"], 4)
        self.assertEqual(row["current_status"], "Applied")

    def test_a_decision_without_a_call_is_recorded_and_counted_apart(self):
        # A CLI save has no panel behind it. It is kept, and reported as unmeasurable
        # rather than folded into the rates.
        SAVED.save(self.db, card(), actor="user", at=AT)
        self.assertIsNone(SAVED.tracker_rows(self.db, today=TODAY)[0]["verdict"])
        self.assertEqual(SAVED.status(self.db, today=TODAY)["without_recorded_verdict"], 1)

    def test_whether_the_suggestion_was_followed_is_derivable(self):
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED,
                   judgement={**JUDGEMENT, "suggested_choice": "broad"}, at=AT)
        SAVED.save(self.db, card(url="https://jobs.example.com/2"), actor="user",
                   decision=SAVED.APPLIED,
                   judgement={**JUDGEMENT, "suggested_choice": "precision"}, at=AT)
        rows = {row["job_url"]: row for row in SAVED.tracker_rows(self.db, today=TODAY)}
        self.assertTrue(rows["https://jobs.example.com/1"]["followed_suggestion"])
        self.assertFalse(rows["https://jobs.example.com/2"]["followed_suggestion"])

    def test_replies_are_reported_per_verdict_not_as_one_rate(self):
        # A rate over mixed verdicts says nothing about whether the verdict was worth
        # anything, which is the question.
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED,
                   judgement=JUDGEMENT, at=AT)
        SAVED.record_outcome(self.db, "https://jobs.example.com/1", "interview", at=AT)
        SAVED.save(self.db, card(url="https://jobs.example.com/2"), actor="user",
                   decision=SAVED.APPLIED, judgement={**JUDGEMENT, "verdict": "review"}, at=AT)
        summary = SAVED.status(self.db, today=TODAY)
        self.assertEqual(summary["by_verdict"]["apply"],
                         {"saved": 1, "applied": 1, "with_outcome": 1})
        self.assertEqual(summary["by_verdict"]["review"],
                         {"saved": 1, "applied": 1, "with_outcome": 0})

    def test_a_malformed_judgement_is_dropped_rather_than_stored_as_text(self):
        SAVED.save(self.db, card(), actor="user",
                   judgement={"covered": "four", "verdict": "apply"}, at=AT)
        row = SAVED.tracker_rows(self.db, today=TODAY)[0]
        self.assertEqual(row["verdict"], "apply")
        self.assertIsNone(row["covered"])


class ApplicationJoinTests(unittest.TestCase):
    """A kept job and an application are different records about one job. They are joined on
    the posting's URL so the two sheets add up without counting it twice."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        APPLICATIONS.initialize(self.db)
        SAVED.initialize(self.db)
        self.addCleanup(self.db.close)

    def test_a_kept_job_reads_as_saved_until_it_has_an_application(self):
        SAVED.save(self.db, card(), actor="user", at=AT)
        self.assertEqual(SAVED.tracker_rows(self.db, today=TODAY)[0]["current_status"], "Saved")

    def test_a_tracked_application_is_named_apart_from_a_self_report(self):
        SAVED.save(self.db, card(), actor="user", at=AT)
        job = {"job_id": "job-2", "canonical_url": "https://jobs.example.com/1",
               "employer": "Acme Health", "title": "Clinical Data Analyst"}
        APPLICATIONS.ingest_job(self.db, job, at=AT)
        APPLICATIONS.create_application(self.db, "app-2", "job-2", at=AT)
        row = SAVED.tracker_rows(self.db, today=TODAY)[0]
        self.assertEqual(row["applied_evidence"], "tracked application")

    def test_a_kept_job_that_was_applied_to_says_so(self):
        SAVED.save(self.db, card(), actor="user", at=AT)
        job = {"job_id": "job-1", "canonical_url": "https://jobs.example.com/1",
               "employer": "Acme Health", "title": "Clinical Data Analyst"}
        APPLICATIONS.ingest_job(self.db, job, at=AT)
        APPLICATIONS.create_application(self.db, "app-1", "job-1", at=AT)
        self.assertEqual(SAVED.tracker_rows(self.db, today=TODAY)[0]["current_status"], "Applied")

    def test_the_status_is_derived_not_stored(self):
        # Nothing has to remember to flip a flag when an application appears.
        SAVED.save(self.db, card(), actor="user", at=AT)
        stored = self.db.execute("SELECT decision FROM saved_jobs").fetchone()["decision"]
        self.assertEqual(stored, "later")


class StatusTests(unittest.TestCase):
    def test_status_counts_what_is_kept_never_what_was_seen(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        SAVED.initialize(db)
        self.addCleanup(db.close)
        SAVED.save(db, card(), actor="user", at=AT)
        SAVED.save(db, card(url="https://jobs.example.com/2"), actor="user", at=AT)
        summary = SAVED.status(db, today=TODAY)
        self.assertEqual(summary["saved"], 2)
        self.assertEqual(summary["applied"], 0)
        self.assertEqual(summary["median_days_open"], 28)


if __name__ == "__main__":
    unittest.main()
