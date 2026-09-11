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
        self.assertEqual(row["applied_evidence"], "stated at decision")
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
        # The interview confirms the first one went through; the second is a decision only.
        self.assertEqual(summary["by_verdict"]["apply"],
                         {"saved": 1, "applied": 1, "confirmed_submitted": 1, "with_outcome": 1})
        self.assertEqual(summary["by_verdict"]["review"],
                         {"saved": 1, "applied": 1, "confirmed_submitted": 0, "with_outcome": 0})

    def test_a_malformed_judgement_is_dropped_rather_than_stored_as_text(self):
        SAVED.save(self.db, card(), actor="user",
                   judgement={"covered": "four", "verdict": "apply"}, at=AT)
        row = SAVED.tracker_rows(self.db, today=TODAY)[0]
        self.assertEqual(row["verdict"], "apply")
        self.assertIsNone(row["covered"])


class ConfirmedSubmissionTests(unittest.TestCase):
    """The rung between "decided to apply" and "there is submission evidence"."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        SAVED.initialize(self.db)
        self.addCleanup(self.db.close)

    def _applied(self, url="https://jobs.example.com/1"):
        SAVED.save(self.db, card(url=url), actor="user", decision=SAVED.APPLIED, at=AT)
        return url

    def _row(self, url):
        return {row["job_url"]: row for row in SAVED.tracker_rows(self.db, today=TODAY)}[url]

    def test_a_decision_to_apply_is_not_a_submission(self):
        # The press happens before the form opens, so nothing about it says the form was
        # finished. This is the whole reason the rung exists.
        url = self._applied()
        self.assertEqual(self._row(url)["applied_evidence"], "stated at decision")
        self.assertIsNone(self._row(url)["submitted_confirmed_at"])
        self.assertEqual(SAVED.status(self.db, today=TODAY)["confirmed_submitted"], 0)

    def test_confirming_says_so_and_says_when(self):
        url = self._applied()
        result = SAVED.confirm_submitted(self.db, url, at=AT)
        self.assertFalse(result["already_confirmed"])
        self.assertEqual(self._row(url)["applied_evidence"], "confirmed after applying")
        self.assertEqual(self._row(url)["submitted_confirmed_at"], AT.isoformat())

    def test_confirming_twice_keeps_the_first_time(self):
        # The question is "was it finished", and the first yes answered it. A later press
        # would move the date to when somebody happened to press again.
        url = self._applied()
        SAVED.confirm_submitted(self.db, url, at=AT)
        later = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
        result = SAVED.confirm_submitted(self.db, url, at=later)
        self.assertTrue(result["already_confirmed"])
        self.assertEqual(self._row(url)["submitted_confirmed_at"], AT.isoformat())

    def test_a_job_only_kept_cannot_be_confirmed_as_submitted(self):
        SAVED.save(self.db, card(), actor="user", decision=SAVED.LATER, at=AT)
        with self.assertRaises(ValueError):
            SAVED.confirm_submitted(self.db, "https://jobs.example.com/1", at=AT)

    def test_an_unknown_job_cannot_be_confirmed(self):
        with self.assertRaises(ValueError):
            SAVED.confirm_submitted(self.db, "https://jobs.example.com/nope", at=AT)

    def test_an_outcome_only_the_employer_could_send_confirms_it(self):
        # A rejection could not have arrived unless the form went through.
        url = self._applied()
        result = SAVED.record_outcome(self.db, url, "rejected", at=AT)
        self.assertTrue(result["confirmed_submitted_by_outcome"])
        self.assertEqual(self._row(url)["applied_evidence"], "confirmed after applying")

    def test_silence_and_withdrawal_confirm_nothing(self):
        # `no_response` is the absence of evidence, and a withdrawal routinely happens
        # partway through the form. Neither says the application was ever submitted.
        for url, outcome in (("https://jobs.example.com/1", "no_response"),
                             ("https://jobs.example.com/2", "withdrawn")):
            self._applied(url)
            result = SAVED.record_outcome(self.db, url, outcome, at=AT)
            self.assertFalse(result["confirmed_submitted_by_outcome"], outcome)
            self.assertEqual(self._row(url)["applied_evidence"], "stated at decision")
        self.assertEqual(SAVED.status(self.db, today=TODAY)["confirmed_submitted"], 0)

    def test_an_outcome_never_overwrites_an_earlier_confirmation(self):
        url = self._applied()
        SAVED.confirm_submitted(self.db, url, at=AT)
        later = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
        result = SAVED.record_outcome(self.db, url, "interview", at=later)
        self.assertFalse(result["confirmed_submitted_by_outcome"])
        self.assertEqual(self._row(url)["submitted_confirmed_at"], AT.isoformat())

    def test_the_gap_between_deciding_and_finishing_is_reported_not_divided_away(self):
        self._applied("https://jobs.example.com/1")
        self._applied("https://jobs.example.com/2")
        self._applied("https://jobs.example.com/3")
        SAVED.confirm_submitted(self.db, "https://jobs.example.com/1", at=AT)
        summary = SAVED.status(self.db, today=TODAY)
        self.assertEqual(summary["applied"], 3)
        self.assertEqual(summary["confirmed_submitted"], 1)
        self.assertEqual(summary["stated_not_confirmed"], 2)

    def test_a_table_made_before_the_column_existed_gains_it_and_keeps_its_rows(self):
        # Rows recorded before the distinction existed predate it, so they stay
        # unconfirmed: that is what they honestly are.
        old = sqlite3.connect(":memory:")
        old.row_factory = sqlite3.Row
        self.addCleanup(old.close)
        SAVED.initialize(old)
        old.execute("ALTER TABLE saved_jobs DROP COLUMN submitted_confirmed_at")
        old.commit()
        SAVED.save(old, card(), actor="user", decision=SAVED.APPLIED, at=AT)
        SAVED.initialize(old)
        rows = SAVED.tracker_rows(old, today=TODAY)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["submitted_confirmed_at"])
        self.assertEqual(rows[0]["applied_evidence"], "stated at decision")


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

    def _application_for_the_saved_job(self, application_id="app-2", job_id="job-2"):
        job = {"job_id": job_id, "canonical_url": "https://jobs.example.com/1",
               "employer": "Acme Health", "title": "Clinical Data Analyst"}
        APPLICATIONS.ingest_job(self.db, job, at=AT)
        APPLICATIONS.create_application(self.db, application_id, job_id, at=AT)

    def test_an_application_record_alone_does_not_earn_the_evidenced_label(self):
        """It used to. Merely having a row meant "tracked application" — the label reserved
        for the one rung backed by positive employer evidence — and this test asserted it.

        The set behind the label was `_tracked_application_urls`, which exists to stop the
        two sheets counting one job twice and says nothing about whether anything was sent.
        It was safe only while no job appeared on both sides; recording a hand-made
        application as a saved job is what put one there.
        """
        SAVED.save(self.db, card(), actor="user", at=AT)
        self._application_for_the_saved_job()
        row = SAVED.tracker_rows(self.db, today=TODAY)[0]
        self.assertEqual(row["applied_evidence"], "")
        # Still joined for counting: the job is not reported twice.
        self.assertEqual(row["current_status"], "Applied")

    def test_a_hand_made_application_reports_as_the_users_word(self):
        """An application row plus the user's confirmation is rung 2, never rung 3."""
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED, at=AT)
        self._application_for_the_saved_job()
        SAVED.confirm_submitted(self.db, "https://jobs.example.com/1", at=AT)
        row = SAVED.tracker_rows(self.db, today=TODAY)[0]
        self.assertEqual(row["applied_evidence"], "confirmed after applying")

    def _submitted_history(self, application_id="app-2", state="submitted"):
        """The two rows a real `submitted` transition leaves: the stamp and the event.

        A minimal credible fixture rather than a direct state edit. What the label reads is
        the *event*, so a test that only set the state would assert nothing about the rule —
        and a test that only set the state is what the old version of this did, which is why
        a hand-set `submitted_at` used to count.
        """
        self.db.execute(
            "UPDATE applications SET state=?, submitted_at=? WHERE application_id=?",
            (state, AT.isoformat(), application_id))
        self.db.execute(
            "INSERT INTO application_events (application_id, created_at, actor,"
            " from_state, to_state, reason_code, metadata_json)"
            " VALUES (?, ?, 'system', 'submitting', 'submitted', 'confirmation_received', '{}')",
            (application_id, AT.isoformat()))

    def test_a_submission_application_core_saw_is_named_apart_from_a_self_report(self):
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED, at=AT)
        self._application_for_the_saved_job()
        SAVED.confirm_submitted(self.db, "https://jobs.example.com/1", at=AT)
        self._submitted_history()
        row = SAVED.tracker_rows(self.db, today=TODAY)[0]
        self.assertEqual(row["applied_evidence"], "tracked application")

    def test_a_submitted_application_later_withdrawn_is_still_evidenced(self):
        """Withdrawing does not un-send an application; the employer still has it.

        The previous rule read the current state and dropped these, which took a real
        submission out of the denominator of every rate computed over it.
        """
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED, at=AT)
        self._application_for_the_saved_job()
        self._submitted_history(state="withdrawn")
        self.assertEqual(SAVED.tracker_rows(self.db, today=TODAY)[0]["applied_evidence"],
                         "tracked application")

    def test_a_post_submission_state_with_the_event_is_evidenced(self):
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED, at=AT)
        self._application_for_the_saved_job()
        self._submitted_history(state="interview")
        self.assertEqual(SAVED.tracker_rows(self.db, today=TODAY)[0]["applied_evidence"],
                         "tracked application")

    def test_a_submitted_at_with_no_transition_behind_it_is_not_evidenced(self):
        """A stamp written around the engine proves nothing about what was sent."""
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED, at=AT)
        self._application_for_the_saved_job()
        self.db.execute("UPDATE applications SET state='submitted', submitted_at=? "
                        "WHERE application_id='app-2'", (AT.isoformat(),))
        self.assertEqual(SAVED.tracker_rows(self.db, today=TODAY)[0]["applied_evidence"],
                         "stated at decision")

    def test_an_event_with_no_stamp_beside_it_is_not_evidenced(self):
        """The two are written by one transition; one without the other has been edited."""
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED, at=AT)
        self._application_for_the_saved_job()
        self.db.execute(
            "INSERT INTO application_events (application_id, created_at, actor,"
            " from_state, to_state, reason_code, metadata_json)"
            " VALUES ('app-2', ?, 'system', 'submitting', 'submitted', 'x', '{}')",
            (AT.isoformat(),))
        self.assertEqual(SAVED.tracker_rows(self.db, today=TODAY)[0]["applied_evidence"],
                         "stated at decision")

    def test_a_hand_made_submission_has_no_submitted_event_and_stays_rung_two(self):
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED, at=AT)
        self._application_for_the_saved_job()
        SAVED.confirm_submitted(self.db, "https://jobs.example.com/1", at=AT)
        self.assertEqual(SAVED.tracker_rows(self.db, today=TODAY)[0]["applied_evidence"],
                         "confirmed after applying")


class ReferenceTests(unittest.TestCase):
    """A confirmation reference is kept whole or refused. It is never shortened."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        APPLICATIONS.initialize(self.db)
        SAVED.initialize(self.db)
        self.addCleanup(self.db.close)
        SAVED.save(self.db, card(), actor="user", decision=SAVED.APPLIED, at=AT)

    def confirm(self, reference, kind="confirmation_id"):
        return SAVED.confirm_submitted(self.db, "https://jobs.example.com/1",
                                       reference_kind=kind, reference=reference, at=AT)

    def stored(self):
        return self.db.execute(
            "SELECT submitted_confirmed_at, submitted_reference FROM saved_jobs").fetchone()

    def test_exactly_the_limit_is_stored_whole(self):
        reference = "A" * SAVED.MAX_REFERENCE
        self.confirm(reference)
        self.assertEqual(self.stored()["submitted_reference"], reference)

    def test_one_past_the_limit_refuses_the_whole_confirmation(self):
        with self.assertRaises(ValueError):
            self.confirm("A" * (SAVED.MAX_REFERENCE + 1))
        row = self.stored()
        self.assertIsNone(row["submitted_confirmed_at"])
        self.assertIsNone(row["submitted_reference"])

    def test_the_limit_counts_code_points_not_bytes(self):
        """Stated because "characters" stops being obvious once the string is not ASCII.

        An astral character is one code point and four UTF-8 bytes; a limit counted in bytes
        would refuse a quarter of what this accepts, and neither rule is wrong — but only one
        of them can be the documented one.
        """
        self.confirm("\U0001F600" * SAVED.MAX_REFERENCE)
        self.assertEqual(len(self.stored()["submitted_reference"]), SAVED.MAX_REFERENCE)

    def test_surrounding_whitespace_is_not_part_of_what_was_typed(self):
        self.confirm("  RQ-4077023  ")
        self.assertEqual(self.stored()["submitted_reference"], "RQ-4077023")

    def test_whitespace_does_not_let_a_value_past_the_limit(self):
        with self.assertRaises(ValueError):
            self.confirm(" " + "A" * (SAVED.MAX_REFERENCE + 1) + " ")

    def test_a_refusal_names_no_value(self):
        secret = "RQ-" + "9" * SAVED.MAX_REFERENCE
        with self.assertRaises(ValueError) as caught:
            self.confirm(secret)
        self.assertNotIn("9", str(caught.exception))
        self.assertNotIn("RQ-", str(caught.exception))

    def test_a_reference_that_is_not_text_is_refused(self):
        for value in (5, ["RQ-1"], {"id": "RQ-1"}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.confirm(value)

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
