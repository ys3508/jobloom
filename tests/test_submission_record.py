"""Recording an application the user made by hand, and the rung it is allowed to claim.

The module climbs two of `saved_jobs`' three rungs. Almost every test here is about the third:
that nothing it writes can be mistaken for positive submission evidence, and that nothing it
writes can later let `application_core` be talked into the `submitted` state.
"""

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
RESUMES = load_script("resume_core")
ARCHIVE = load_script("archive_core")
PRE_SUBMIT = load_script("pre_submit_core")
ANSWERS = load_script("answer_library")
CANDIDATE = load_script("candidate_core")

from tests.pdf_fixture import synthetic_pdf  # noqa: E402

AT = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
URL = "https://example.invalid/opening-1"


class SubmissionFixture(unittest.TestCase):
    """The fixture mirrors `tests/test_application_core.py` rather than importing it, for the
    reason that file's neighbours give: importing one test module from another made results
    depend on which invocation ran. It goes all the way to `ready_to_fill` because the manual
    submission state is offered only from there and from `waiting_for_user_takeover`, and an
    application that never had a resume bound and locked was not submitted with one."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        for module in (APPLICATIONS, ANSWERS, RESUMES, ARCHIVE, PRE_SUBMIT, CANDIDATE):
            module.initialize(self.db)
        RECORD.initialize(self.db)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.db.close)
        APPLICATIONS.ingest_job(self.db, {
            "job_id": "job-1", "canonical_url": URL, "employer": "Example Health",
            "title": "Data Analyst", "location": "Boston, MA", "status": "open",
            "description_sha256": "d", "requisition_id": "REQ-1"}, at=AT)
        APPLICATIONS.create_application(self.db, "app-1", "job-1", at=AT)
        self.move_to_ready()

    def install_materials(self, application_id="app-1"):
        root = Path(self.temp.name)
        source = root / f"{application_id}.pdf"
        source.write_bytes(synthetic_pdf(["Verified resume claim"]))
        version_id = f"resume-{application_id}"
        RESUMES.register_version(self.db, root / "store", source, version_id,
                                 "master_source", "general", at=AT)
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {"country": "US", "authorized_now": True,
                                   "sponsorship_now": False, "sponsorship_future": False,
                                   "employer_action_required": False, "confirmed": True},
            "search": {}, "facts": [
                {"id": "fact-1", "type": "skill", "value": "Verified resume claim",
                 "evidence_strength": "direct", "status": "confirmed", "locked": False}]}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        candidate_path = root / "candidate.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-1", "claim_text": "Verified resume claim",
            "fact_ids": ["fact-1"], "evidence_strength": "direct",
            "exact_locked_value_preserved": False}]}), encoding="utf-8")
        CANDIDATE.register_snapshot(self.db, root / "candidates", candidate_path, "user", AT)
        RESUMES.approve_version(self.db, version_id, candidate_path, manifest_path, "user", AT)
        RESUMES.bind_version(self.db, application_id, version_id, at=AT)
        RESUMES.lock_materials(self.db, application_id,
                               lock_id=f"lock-{application_id}", at=AT)

    def move_to_ready(self, application_id="app-1"):
        for to_state, actor, reason in (
            ("pending_analysis", "system", "analysis_started"),
            ("broad_recommended", "system", "broad_match"),
            ("approved", "user", "user_approved"),
            ("materials_in_progress", "system", "materials_started"),
        ):
            APPLICATIONS.transition(self.db, application_id, to_state, actor, reason, at=AT)
        self.install_materials(application_id)
        APPLICATIONS.transition(self.db, application_id, "ready_to_fill", "system",
                                "materials_ready", at=AT)

    def counts(self):
        tables = [row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables}


class SubmissionRecordTest(SubmissionFixture):
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

    def application(self):
        return self.db.execute(
            "SELECT state, submitted_at FROM applications WHERE application_id='app-1'"
        ).fetchone()

    def test_the_application_leaves_ready_to_fill_but_is_not_submitted(self):
        """Both halves. Staying `ready_to_fill` leaves it acquirable by a worker that would
        fill a form already submitted; becoming `submitted` claims evidence nobody has."""
        self.assertEqual(self.application()["state"], "ready_to_fill")
        RECORD.intend(self.db, "app-1", at=AT)
        RECORD.confirm(self.db, "app-1", at=AT)
        row = self.application()
        self.assertEqual(row["state"], "submitted_by_user_unverified")
        self.assertNotEqual(row["state"], "submitted")
        self.assertIsNone(row["submitted_at"], "no stamp is invented for a rung-2 confirmation")

    def test_a_worker_cannot_acquire_it_afterwards(self):
        """`acquire_next` selects `ready_to_fill`, which is the state the confirmation leaves."""
        RECORD.intend(self.db, "app-1", at=AT)
        RECORD.confirm(self.db, "app-1", at=AT)
        self.assertIsNone(APPLICATIONS.acquire_next(self.db, "worker-1", at=AT))

    def test_it_was_acquirable_before_the_confirmation(self):
        """The other half: the test above would pass on an application nothing could acquire."""
        self.assertIsNotNone(APPLICATIONS.acquire_next(self.db, "worker-1", at=AT))

    def test_a_takeover_can_also_be_confirmed_as_submitted_by_hand(self):
        """Handing an application back to the user is the other way one gets filled by hand."""
        APPLICATIONS.acquire_next(self.db, "worker-1", at=AT)
        APPLICATIONS.release_lease(self.db, "app-1", "worker-1", "waiting_for_user_takeover",
                                   "handed_back", at=AT)
        RECORD.intend(self.db, "app-1", at=AT)
        RECORD.confirm(self.db, "app-1", at=AT)
        self.assertEqual(self.application()["state"], "submitted_by_user_unverified")

    def test_no_resume_usage_row_is_written(self):
        """`create_archive` reads one; writing it would be the rung-3 artefact without the rung."""
        RECORD.intend(self.db, "app-1", at=AT)
        RECORD.confirm(self.db, "app-1", at=AT)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM resume_usage WHERE use_type='submitted'").fetchone()[0], 0)

    def test_no_archive_exists_or_can_be_made(self):
        RECORD.intend(self.db, "app-1", at=AT)
        RECORD.confirm(self.db, "app-1", at=AT)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM submission_archives").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            ARCHIVE.create_archive(self.db, Path(self.temp.name) / "archives", "app-1", at=AT)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM submission_archives").fetchone()[0], 0)

    def test_a_system_actor_cannot_manufacture_a_manual_confirmation(self):
        """The one fact this state records is that a person did something."""
        for actor in ("system", "model", "worker-1"):
            with self.subTest(actor=actor):
                with self.assertRaises(ValueError):
                    APPLICATIONS.transition(
                        self.db, "app-1", "submitted_by_user_unverified", actor,
                        APPLICATIONS.MANUAL_SUBMISSION_REASON, at=AT)

    def test_the_user_actor_still_needs_the_pinned_reason_code(self):
        with self.assertRaises(ValueError):
            APPLICATIONS.transition(self.db, "app-1", "submitted_by_user_unverified",
                                    "user", "looked_done_to_me", at=AT)

    def test_a_refused_confirmation_leaves_neither_half_behind(self):
        """Half a rung or half a state would both survive a crash as a lie."""
        RECORD.intend(self.db, "app-1", at=AT)
        before_changes = self.db.total_changes
        with self.assertRaises(ValueError):
            RECORD.confirm(self.db, "app-1", reference_kind="confirmation_id",
                           reference="R" * (SAVED.MAX_REFERENCE + 1), at=AT)
        self.assertIsNone(self.db.execute(
            "SELECT submitted_confirmed_at FROM saved_jobs").fetchone()[0])
        self.assertEqual(self.application()["state"], "ready_to_fill")
        self.assertEqual(self.db.total_changes, before_changes)

    def test_a_second_confirmation_adds_no_second_transition(self):
        RECORD.intend(self.db, "app-1", at=AT)
        first = RECORD.confirm(self.db, "app-1", at=AT)["submitted_confirmed_at"]
        events = self.db.execute(
            "SELECT COUNT(*) FROM application_events WHERE to_state=?",
            ("submitted_by_user_unverified",)).fetchone()[0]
        again = RECORD.confirm(self.db, "app-1", at=datetime(2026, 9, 12, tzinfo=timezone.utc))
        self.assertEqual(again["submitted_confirmed_at"], first)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM application_events WHERE to_state=?",
            ("submitted_by_user_unverified",)).fetchone()[0], events)

    def test_a_confirmation_does_not_make_the_application_submittable(self):
        """The whole point, stated as the thing a later caller might try."""
        RECORD.intend(self.db, "app-1", at=AT)
        RECORD.confirm(self.db, "app-1", reference_kind="success_page",
                       reference="a screenshot", at=AT)
        with self.assertRaises(ValueError):
            APPLICATIONS.transition(self.db, "app-1", "submitted", "user", "manual", at=AT)

    def test_the_confirmation_writes_the_saved_job_and_the_state_and_nothing_else(self):
        before = self.counts()
        RECORD.intend(self.db, "app-1", at=AT)
        RECORD.confirm(self.db, "app-1", at=AT)
        after = self.counts()
        changed = {table for table in after if after[table] != before[table]}
        # `applications` is an UPDATE, so its count does not move; the event is the record.
        self.assertEqual(changed, {"saved_jobs", "application_events"})
        for table in ("submission_evidence", "submission_archives", "resume_usage"):
            self.assertEqual(after[table], before[table], table)


class ReadOnlyPathsTest(SubmissionFixture):
    """`state` said read-only while migrating the schema on first use. Now it is one."""

    def test_state_writes_nothing_at_all(self):
        RECORD.intend(self.db, "app-1", at=AT)
        before_changes = self.db.total_changes
        before_counts = self.counts()
        RECORD.state(self.db, "app-1")
        RECORD.state(self.db, "app-1")
        self.assertEqual(self.db.total_changes, before_changes)
        self.assertEqual(self.counts(), before_counts)

    def test_pending_writes_nothing_at_all(self):
        before_changes = self.db.total_changes
        RECORD.pending(self.db)
        self.assertEqual(self.db.total_changes, before_changes)

    def test_a_read_against_an_uninitialised_database_is_refused_not_migrated(self):
        """The proof that the migration left the read path: on a database without the table,
        `state` used to create it. Now it says so and writes nothing."""
        bare = sqlite3.connect(":memory:")
        bare.row_factory = sqlite3.Row
        APPLICATIONS.initialize(bare)
        self.addCleanup(bare.close)
        before = bare.total_changes
        with self.assertRaises(RuntimeError):
            RECORD.state(bare, "app-1")
        self.assertEqual(bare.total_changes, before)
        self.assertIsNone(bare.execute(
            "SELECT 1 FROM sqlite_master WHERE name='saved_jobs'").fetchone())

    def test_a_read_leaves_the_database_file_byte_identical(self):
        path = Path(self.temp.name) / "read-only.db"
        source = sqlite3.connect(str(path))
        source.row_factory = sqlite3.Row
        for module in (APPLICATIONS, RESUMES, ARCHIVE, PRE_SUBMIT):
            module.initialize(source)
        RECORD.initialize(source)
        source.commit()
        source.close()
        before = path.read_bytes()
        reader = sqlite3.connect(str(path))
        reader.row_factory = sqlite3.Row
        try:
            RECORD.pending(reader)
        finally:
            reader.close()
        self.assertEqual(path.read_bytes(), before)

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
