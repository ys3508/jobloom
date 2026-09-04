"""Carrying an approved resume across a change of CandidateSnapshot.

The state this exists for is easy to reach and has no other way out: register a new profile,
and every material lock bound to the old snapshot is invalidated while the resume that held it
stays `approved` against a snapshot that is no longer active. `bind_version` then refuses it,
`approve_version` takes only drafts, and the recorded hash is not editable. What is under test
is the successor path out of that, and every gate it must not skip.

Every value is visibly synthetic and every path is a temporary directory.
"""

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "jobloom" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load(name):
    spec = importlib.util.spec_from_file_location(f"migration_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


MIGRATION = load("resume_migration")
RESUMES = load("resume_core")
APPLICATIONS = load("application_core")
CANDIDATES = load("candidate_core")
ANSWERS = load("answer_library")
COVERS = load("cover_letter_core")
PRE_SUBMIT = load("pre_submit_core")
from tests.pdf_fixture import synthetic_pdf  # noqa: E402

AT = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
LATER = AT + timedelta(days=1)
FACT = {"id": "fact-1", "type": "skill", "value": "Probe analysis", "status": "confirmed",
        "locked": False, "evidence_strength": "direct"}
ADDED = {"id": "fact-profile-contact-email", "type": "contact",
         "canonical_id": "contact.email", "value": "probe@example.invalid",
         "status": "locked", "locked": True, "evidence_strength": "direct"}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = self.root / "resumes"
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        for module in (RESUMES, APPLICATIONS, CANDIDATES, ANSWERS, COVERS, PRE_SUBMIT,
                       MIGRATION):
            module.initialize(self.db)
        self.addCleanup(self.db.close)

        self.first = self.register_candidate([FACT])
        self.pdf = self.root / "resume-a.pdf"
        self.pdf.write_bytes(synthetic_pdf(["Probe analysis, as approved"]))
        RESUMES.register_version(self.db, self.store, self.pdf, "resume-a", "direction",
                                 "probe-direction", actor="user", at=AT,
                                 source_mode="user_provided")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-1", "claim_text": "Probe analysis", "fact_ids": ["fact-1"],
            "evidence_strength": "direct", "exact_locked_value_preserved": False,
        }]}), encoding="utf-8")
        RESUMES.approve_version(self.db, "resume-a", self.candidate_path(self.first),
                                self.manifest, "user", AT)
        self.prepare_application()

    # ---- fixtures --------------------------------------------------------------

    def register_candidate(self, facts, at=AT):
        candidate = {
            "schema_version": "0.2.0", "profile_id": "probe",
            "work_authorization": {"country": "US", "authorized_now": True,
                                   "sponsorship_now": False, "sponsorship_future": False,
                                   "employer_action_required": False, "confirmed": True},
            "search": {}, "facts": facts}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        path = self.root / f"candidate-{candidate['content_sha256'][:12]}.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        CANDIDATES.register_snapshot(self.db, self.root / "candidates", path, "user", at)
        return candidate["content_sha256"]

    def candidate_path(self, content_sha256):
        return self.root / f"candidate-{content_sha256[:12]}.json"

    def prepare_application(self):
        APPLICATIONS.ingest_job(self.db, {
            "job_id": "job-1", "canonical_url": "https://example.invalid/jobs/1",
            "employer": "Probe Corp", "title": "Analyst", "location": "Testville",
            "country": "US", "employment_type": "full_time", "status": "open"}, at=AT)
        APPLICATIONS.create_application(self.db, "app-1", "job-1", "precision",
                                        "approved_queue", AT)
        for state, reason in (("pending_analysis", "analysis"),
                              ("precision_recommended", "match"), ("approved", "approved"),
                              ("materials_in_progress", "materials")):
            APPLICATIONS.transition(self.db, "app-1", state,
                                    "user" if state == "approved" else "system", reason, at=AT)
        RESUMES.bind_version(self.db, "app-1", "resume-a", at=AT)
        RESUMES.lock_materials(self.db, "app-1", lock_id="lock-1", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "ready", at=AT)

    def move_the_profile(self):
        """Register a snapshot that only adds facts, as the profile intake does."""
        self.second = self.register_candidate([FACT, ADDED], at=LATER)
        return self.second

    def version(self, version_id):
        return self.db.execute("SELECT * FROM resume_versions WHERE version_id=?",
                               (version_id,)).fetchone()

    def state(self):
        return self.db.execute("SELECT state FROM applications WHERE application_id='app-1'"
                               ).fetchone()[0]

    def live_lock(self):
        return self.db.execute(
            "SELECT * FROM material_locks WHERE application_id='app-1' "
            "AND invalidated_at IS NULL").fetchone()

    def prepare(self, predecessor="resume-a", successor="resume-a-next"):
        return MIGRATION.prepare_successor(self.db, self.store, predecessor, successor, LATER)

    def approve(self, successor="resume-a-next", snapshot=None, actor="user"):
        return MIGRATION.approve_successor(
            self.db, successor, self.candidate_path(snapshot or self.second), actor, LATER)

    # ---- the state this exists for ---------------------------------------------

    def test_a_profile_change_strands_the_approved_resume(self):
        """Not a defect being fixed here — the condition the successor path answers."""
        self.move_the_profile()
        self.assertIsNone(self.live_lock())
        self.assertEqual(self.version("resume-a")["status"], "approved")
        APPLICATIONS.transition(self.db, "app-1", "materials_in_progress", "user",
                                "candidate_snapshot_changed", at=LATER)
        with self.assertRaisesRegex(ValueError, "not the active registered snapshot"):
            RESUMES.bind_version(self.db, "app-1", "resume-a", at=LATER)
        with self.assertRaisesRegex(ValueError, "only a draft resume can be approved"):
            RESUMES.approve_version(self.db, "resume-a", self.candidate_path(self.second),
                                    self.manifest, "user", LATER)

    def test_a_master_source_is_not_something_an_application_waits_on(self):
        """It is refused for an application by kind, whatever snapshot approved it.

        Listing it among the resumes an application is waiting on read as a job to do, and
        it also had no `blocked_reason` — an empty cell where an answer belongs, because the
        reason only looked at `source_mode` and this one is `user_provided` too.
        """
        self.db.execute(
            "INSERT INTO resume_versions (version_id, kind, direction, status, snapshot_path, "
            "file_sha256, file_size, file_format, candidate_profile_sha256, created_at, "
            "source_mode) VALUES ('master-1', 'master_source', 'unassigned', 'approved', ?, "
            "'m', 1, 'docx', ?, ?, 'user_provided')",
            (str(self.root / "master.docx"), self.first, AT.isoformat()))
        self.db.commit()
        self.move_the_profile()
        listed = MIGRATION.stranded(self.db)
        self.assertEqual([row["version_id"] for row in listed], ["resume-a"])

    def test_every_row_that_cannot_be_carried_says_why(self):
        self.move_the_profile()
        self.db.execute("UPDATE resume_versions SET source_mode='generated' "
                        "WHERE version_id='resume-a'")
        self.db.commit()
        row = MIGRATION.stranded(self.db)[0]
        self.assertFalse(row["migratable"])
        self.assertTrue(row["blocked_reason"])

    def test_stranded_names_them_and_migrates_none(self):
        self.move_the_profile()
        rows = MIGRATION.stranded(self.db)
        self.assertEqual([row["version_id"] for row in rows], ["resume-a"])
        self.assertTrue(rows[0]["migratable"])
        self.assertTrue(rows[0]["lock_lost"])
        self.assertEqual(rows[0]["application_id"], "app-1")
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM resume_migrations").fetchone()[0], 0)

    # ---- preparing ---------------------------------------------------------------

    def test_the_successor_is_the_same_bytes_under_a_new_id(self):
        self.move_the_profile()
        result = self.prepare()
        successor = self.version("resume-a-next")
        self.assertTrue(result["same_bytes"])
        self.assertEqual(successor["file_sha256"], self.version("resume-a")["file_sha256"])
        self.assertEqual(RESUMES.file_sha256(Path(successor["snapshot_path"])),
                         RESUMES.file_sha256(self.pdf))
        self.assertNotEqual(successor["snapshot_path"],
                            self.version("resume-a")["snapshot_path"])

    def test_the_predecessor_is_untouched(self):
        self.move_the_profile()
        before = dict(self.version("resume-a"))
        self.prepare()
        self.assertEqual(dict(self.version("resume-a")), before)

    def test_the_successor_is_its_own_draft_and_approves_nothing(self):
        self.move_the_profile()
        result = self.prepare()
        successor = self.version("resume-a-next")
        self.assertEqual(successor["status"], "draft")
        self.assertIsNone(successor["candidate_profile_sha256"])
        self.assertIsNone(successor["parent_version_id"])
        self.assertFalse(result["approved"])

    def test_a_successor_may_be_prepared_before_the_profile_moves(self):
        """Preparing is not approving. Approving it there is refused."""
        with self.assertRaisesRegex(ValueError, "already approved against the active profile"):
            self.prepare()

    def test_only_a_user_provided_direction_resume_takes_this_path(self):
        self.move_the_profile()
        self.db.execute("UPDATE resume_versions SET source_mode='generated' "
                        "WHERE version_id='resume-a'")
        with self.assertRaisesRegex(ValueError, "user-provided direction resumes only"):
            self.prepare()
        self.db.execute("UPDATE resume_versions SET source_mode='direction_baseline' "
                        "WHERE version_id='resume-a'")
        with self.assertRaisesRegex(ValueError, "user-provided direction resumes only"):
            self.prepare()

    def test_a_tampered_predecessor_file_stops_before_anything_is_registered(self):
        self.move_the_profile()
        stored = Path(self.version("resume-a")["snapshot_path"])
        stored.chmod(0o600)
        stored.write_bytes(synthetic_pdf(["Something else entirely"]))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.prepare()
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM resume_migrations").fetchone()[0], 0)
        self.assertIsNone(self.version("resume-a-next"))

    def test_one_migration_per_resume_at_a_time(self):
        self.move_the_profile()
        self.prepare()
        with self.assertRaisesRegex(ValueError, "already in progress"):
            self.prepare(successor="resume-a-third")

    # ---- approving ----------------------------------------------------------------

    def test_a_snapshot_that_only_adds_facts_revalidates_the_old_manifest(self):
        """The prediction the whole path rests on: adding facts breaks no existing claim."""
        self.move_the_profile()
        self.prepare()
        result = self.approve()
        self.assertEqual(result["approved_against"], self.second)
        successor = self.version("resume-a-next")
        self.assertEqual(successor["status"], "approved")
        self.assertEqual(successor["candidate_profile_sha256"], self.second)

    def test_a_snapshot_missing_a_cited_fact_refuses_approval(self):
        self.second = self.register_candidate([ADDED], at=LATER)
        self.prepare()
        with self.assertRaisesRegex(ValueError, "unavailable fact"):
            self.approve()
        self.assertEqual(self.version("resume-a-next")["status"], "draft")

    def test_a_claim_that_would_now_inflate_its_evidence_refuses_approval(self):
        weakened = dict(FACT, evidence_strength="transferable")
        self.second = self.register_candidate([weakened, ADDED], at=LATER)
        self.prepare()
        with self.assertRaisesRegex(ValueError, "inflates its supporting evidence"):
            self.approve()

    def test_a_cited_fact_that_became_locked_refuses_the_old_manifest(self):
        """Revalidation is doing work, not a formality.

        A claim citing a fact the new snapshot locks must say it preserved that fact's exact
        value. The old manifest was written when the fact was merely confirmed and says
        nothing of the kind, so it is refused and the user has to look at the claim again.
        """
        self.second = self.register_candidate(
            [dict(FACT, status="locked", locked=True), ADDED], at=LATER)
        self.prepare()
        with self.assertRaisesRegex(ValueError, "must preserve exact locked values"):
            self.approve()
        self.assertEqual(self.version("resume-a-next")["status"], "draft")

    def test_a_changed_value_on_an_unlocked_cited_fact_does_not_refuse(self):
        """Recorded because it is the honest limit of what the manifest promises.

        A manifest pins the exact value only of a fact that is locked. A confirmed fact whose
        wording changes still supports the claim as far as this check is concerned, and saying
        otherwise here would claim a guarantee the format does not carry. What does catch it
        is the review the user is being asked for; nothing automatic does.
        """
        self.second = self.register_candidate(
            [dict(FACT, value="Probe analysis, reworded"), ADDED], at=LATER)
        self.prepare()
        self.approve()
        self.assertEqual(self.version("resume-a-next")["status"], "approved")

    def test_a_manifest_changed_since_preparation_refuses_approval(self):
        """The manifest revalidated has to be the one that was approved before.

        Approval copies the manifest into the store beside the resume, so the file the
        migration records is not the working copy the user wrote; this tampers with the stored
        one, which is the only way the check is reachable and the only thing it guards.
        """
        self.move_the_profile()
        recorded = Path(self.prepare()["claims_manifest_path"])
        recorded.chmod(0o600)
        recorded.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-1", "claim_text": "Something broader", "fact_ids": ["fact-1"],
            "evidence_strength": "direct", "exact_locked_value_preserved": False,
        }]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest has changed"):
            self.approve()
        self.assertEqual(self.version("resume-a-next")["status"], "draft")

    def test_approval_requires_the_user(self):
        self.move_the_profile()
        self.prepare()
        with self.assertRaisesRegex(ValueError, "requires the user actor"):
            self.approve(actor="system")
        self.assertEqual(self.version("resume-a-next")["status"], "draft")

    def test_approving_against_a_snapshot_that_never_became_active_is_refused(self):
        """A successor cannot be approved against a profile the user has not registered."""
        self.move_the_profile()
        self.prepare()
        third = self.register_candidate([FACT, ADDED, {
            "id": "fact-2", "type": "skill", "value": "Other", "status": "confirmed",
            "locked": False, "evidence_strength": "direct"}], at=LATER)
        with self.assertRaisesRegex(ValueError, "not the active user-registered snapshot"):
            MIGRATION.approve_successor(self.db, "resume-a-next",
                                        self.candidate_path(self.second), "user", LATER)
        self.assertEqual(self.version("resume-a-next")["status"], "draft")
        del third

    # ---- binding -------------------------------------------------------------------

    def test_binding_needs_an_approved_successor(self):
        self.move_the_profile()
        self.prepare()
        with self.assertRaisesRegex(ValueError, "has not been approved"):
            MIGRATION.bind_for_application(self.db, "app-1", "resume-a-next", "user", LATER)
        self.assertIsNone(self.live_lock())

    def test_the_application_comes_back_on_the_successor(self):
        self.move_the_profile()
        self.prepare()
        self.approve()
        self.assertEqual(self.state(), "ready_to_fill")
        result = MIGRATION.bind_for_application(self.db, "app-1", "resume-a-next", "user", LATER)
        self.assertEqual(result["status"], "bound")
        self.assertEqual(self.state(), "ready_to_fill")
        lock = self.live_lock()
        self.assertEqual(lock["resume_version_id"], "resume-a-next")
        self.assertEqual(lock["resume_file_sha256"], self.version("resume-a")["file_sha256"])

    def test_the_old_lock_stays_as_history(self):
        self.move_the_profile()
        self.prepare()
        self.approve()
        MIGRATION.bind_for_application(self.db, "app-1", "resume-a-next", "user", LATER)
        old = self.db.execute("SELECT * FROM material_locks WHERE lock_id='lock-1'").fetchone()
        self.assertIsNotNone(old["invalidated_at"])
        self.assertEqual(old["invalidation_reason"], "candidate_snapshot_changed")
        self.assertEqual(old["resume_version_id"], "resume-a")

    def test_the_new_lock_points_at_the_new_snapshot(self):
        self.move_the_profile()
        self.prepare()
        self.approve()
        MIGRATION.bind_for_application(self.db, "app-1", "resume-a-next", "user", LATER)
        bound = self.version(self.live_lock()["resume_version_id"])
        self.assertEqual(bound["candidate_profile_sha256"], self.second)

    def test_a_cover_letter_left_behind_stops_before_anything_moves(self):
        """`lock_materials` would refuse it too, but only after the application had moved.

        Checked first and by name, so the answer is about the cover letter rather than an
        error deep in a migration that has already rebound the resume. Carrying one across is
        the same successor dance for a second document, and doing it as a side effect here
        would approve something the user was never shown.
        """
        self.db.execute(
            "INSERT INTO cover_letter_versions (version_id, kind, status, snapshot_path, "
            "file_sha256, file_size, file_format, candidate_profile_sha256, created_at) "
            "VALUES ('cover-1', 'direction', 'approved', ?, 'f', 1, 'pdf', ?, ?)",
            (str(self.root / "cover.pdf"), self.first, AT.isoformat()))
        self.db.execute("UPDATE applications SET cover_letter_version_id='cover-1' "
                        "WHERE application_id='app-1'")
        self.db.commit()
        self.move_the_profile()
        self.prepare()
        self.approve()
        with self.assertRaisesRegex(ValueError, "cover letter needs its own migration"):
            MIGRATION.bind_for_application(self.db, "app-1", "resume-a-next", "user", LATER)
        self.assertEqual(self.state(), "ready_to_fill")
        self.assertIsNone(self.live_lock())
        self.assertEqual(
            self.db.execute("SELECT resume_version_id FROM applications "
                            "WHERE application_id='app-1'").fetchone()[0], "resume-a")

    def test_a_failure_part_way_leaves_preparation_in_progress_not_readiness(self):
        """The stopping point has to be a true statement about the application."""
        self.move_the_profile()
        self.prepare()
        self.approve()
        stored = Path(self.version("resume-a-next")["snapshot_path"])
        stored.chmod(0o600)
        stored.write_bytes(synthetic_pdf(["Swapped after approval"]))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            MIGRATION.bind_for_application(self.db, "app-1", "resume-a-next", "user", LATER)
        self.assertEqual(self.state(), "materials_in_progress")
        self.assertIsNone(self.live_lock())

    def test_readiness_is_never_restored_without_a_live_lock(self):
        self.move_the_profile()
        with self.assertRaisesRegex(ValueError, "material lock"):
            APPLICATIONS.transition(self.db, "app-1", "materials_in_progress", "user",
                                    "candidate_snapshot_changed", at=LATER)
            APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "user", "wishful",
                                    at=LATER)

    def test_a_migration_is_bound_once(self):
        self.move_the_profile()
        self.prepare()
        self.approve()
        MIGRATION.bind_for_application(self.db, "app-1", "resume-a-next", "user", LATER)
        with self.assertRaisesRegex(ValueError, "has not been approved"):
            MIGRATION.bind_for_application(self.db, "app-1", "resume-a-next", "user", LATER)

    def test_nothing_here_touches_a_real_private_root(self):
        for shape in (".job" + "loom", "/Us" + "ers/", "/ho" + "me/"):
            for number, line in enumerate(
                    Path(__file__).read_text(encoding="utf-8").splitlines(), start=1):
                self.assertNotIn(shape, line, f"line {number}")


if __name__ == "__main__":
    unittest.main()
