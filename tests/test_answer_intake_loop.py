"""Proposing a question to the user, and writing down what they say.

The half of the loop the library never had. Everything downstream of a confirmed answer was
built and tested; the step where a person is asked and says yes was a hand-written JSON file,
so the library held nothing and an application sat unfilled. These are the terms of that step.

Every value here is visibly synthetic, and a guard checks this file for the shapes of real
ones. Nothing reads `.jobloom/`.
"""

import copy
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "jobloom" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(f"loop_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


ANSWERS = load("answer_library")
CANDIDATES = load("candidate_core")
RESUMES = load("resume_core")
APPLICATIONS = load("application_core")
PRE_SUBMIT = load("pre_submit_core")
PLAN = json.loads((ROOT / "skills" / "jobloom" / "assets" / "answer-intake-plan.json")
                  .read_text(encoding="utf-8"))
PLAN_PATH = ROOT / "skills" / "jobloom" / "assets" / "answer-intake-plan.json"

AT = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
CONTACT = ("contact.email", "contact.phone", "profile.linkedin")
SYNTHETIC = ("probe@example.invalid ǁ 555-0100 ǁ "
             "LinkedIn: https://example.invalid/in/probe")
REPLACEMENT = ("moved@example.invalid ǁ 555-0199 ǁ "
               "LinkedIn: https://example.invalid/in/moved")


class IntakeLoopTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db_path = self.root / "library.db"
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        for module in (ANSWERS, APPLICATIONS, RESUMES, PRE_SUBMIT, CANDIDATES):
            module.initialize(self.db)
        self.addCleanup(self.db.close)
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-probe", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=9)).isoformat(),
            "scope": {"country": "US"}})
        self.register(SYNTHETIC)

    # ---- driving the loop -----------------------------------------------------

    def register(self, contact):
        candidate = {
            "schema_version": "0.2.0", "profile_id": "probe",
            "work_authorization": {"country": "US", "authorized_now": True,
                                   "sponsorship_now": False, "sponsorship_future": False,
                                   "employer_action_required": False, "confirmed": True},
            "search": {},
            "facts": [{"id": "fact-0002", "type": "contact", "value": contact,
                       "status": "confirmed", "locked": False,
                       "evidence_strength": "direct"}]}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        path = self.root / f"candidate-{candidate['content_sha256'][:12]}.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        CANDIDATES.register_snapshot(self.db, self.root / "snapshots", path, "user", AT)

    def propose(self, round_name="contact"):
        return ANSWERS.propose_answers(self.db, PLAN, round_name, AT)

    def worksheet(self, confirm=CONTACT, **edits):
        sheet = copy.deepcopy(self.propose()["worksheet"])
        for entry in sheet["entries"]:
            entry["confirmed_by_user"] = entry["canonical_id"] in confirm
            entry.update(edits.get(entry["canonical_id"], {}))
        return sheet

    def confirm(self, sheet):
        return ANSWERS.confirm_answers(self.db, sheet, PLAN, AT)

    def active(self):
        return sorted(row["canonical_id"] for row in self.db.execute(
            "SELECT canonical_id FROM answers WHERE status='active'"))

    def snapshot_of_library(self):
        return ([tuple(row) for row in self.db.execute(
                    "SELECT answer_id, canonical_id, status FROM answers ORDER BY answer_id")],
                [tuple(row) for row in self.db.execute(
                    "SELECT event_type, entity_id FROM audit_events ORDER BY event_id")])

    def refuse(self, sheet, pattern):
        before = self.snapshot_of_library()
        with self.assertRaisesRegex(ValueError, pattern) as caught:
            self.confirm(sheet)
        self.assertEqual(self.snapshot_of_library(), before)
        for piece in ("probe@example.invalid", "555-0100", "example.invalid/in/probe"):
            self.assertNotIn(piece, str(caught.exception))

    # ---- proposing ------------------------------------------------------------

    def test_proposing_reads_the_fact_and_writes_no_answer(self):
        result = self.propose()
        self.assertEqual(result["summary"]["proposed"], 3)
        self.assertEqual(sorted(result["summary"]["canonical_ids"]), sorted(CONTACT))
        for entry in result["worksheet"]["entries"]:
            self.assertTrue(entry["answer"], entry["canonical_id"])
            self.assertFalse(entry["confirmed_by_user"])
        # A proposal is a question, not a decision.
        self.assertEqual(self.active(), [])

    def test_the_summary_names_meanings_and_counts_and_never_a_value(self):
        rendered = json.dumps(self.propose()["summary"], ensure_ascii=False)
        for piece in ("probe@example.invalid", "555-0100", "example.invalid"):
            self.assertNotIn(piece, rendered)

    def test_only_a_reviewed_round_may_be_proposed(self):
        """A round is a named set, so nobody proposes the seven with no form in front of them.

        Their wording has to come from a real employer page; proposing them from the reviewed
        corpus would set the reviewed wording to a guess.
        """
        for round_name in ("business", "everything", "", "legal"):
            with self.subTest(round_name=round_name):
                with self.assertRaisesRegex(ValueError, "no such reviewed round"):
                    self.propose(round_name)
        self.assertEqual(set(ANSWERS.TASK14_PROPOSAL_ROUNDS), {"contact"})

    def test_the_seven_business_meanings_are_not_in_this_round(self):
        proposed = set(self.propose()["summary"]["canonical_ids"])
        for target in ("work_authorized_now", "citizenship_status",
                       "permanent_residence_status", "current_country_of_residence",
                       "prior_employment_at_this_company",
                       "prior_employment_at_an_affiliate", "discovery_source"):
            self.assertNotIn(target, proposed)

    def test_a_part_the_fact_does_not_hold_is_left_absent_rather_than_guessed(self):
        self.register("only@example.invalid ǁ LinkedIn: https://example.invalid/in/x")
        entries = {entry["canonical_id"]: entry["answer"]
                   for entry in self.propose()["worksheet"]["entries"]}
        self.assertIsNone(entries["contact.phone"])
        self.assertTrue(entries["contact.email"])

    # ---- the worksheet cannot be swapped or edited into another shape ---------

    def test_an_edited_shape_no_longer_matches_its_proposal(self):
        """Answers may be edited. Nothing else may.

        The hash covers the reviewed arrangement and deliberately not the answer, so filling
        one in is expected and widening a scope is not.
        """
        for field, value in (("scope", {"country": "US"}),
                             ("validity_class", "per_application"),
                             ("auto_submit_allowed", True),
                             ("dependent_fact_ids", []),
                             ("invalidation_triggers", ["contact_details_changed"]),
                             ("canonical_id", "profile.website")):
            with self.subTest(field=field):
                sheet = self.worksheet()
                sheet["entries"][0][field] = value
                self.refuse(sheet, "no longer matches|nobody reviewed")

    def test_a_worksheet_naming_another_proposal_is_refused(self):
        sheet = self.worksheet()
        sheet["proposal_id"] = "proposal-somebody-elses"
        self.refuse(sheet, "no such proposal")

    def test_a_worksheet_that_drops_an_entry_is_refused(self):
        sheet = self.worksheet()
        sheet["entries"].pop()
        self.refuse(sheet, "no longer matches|does not cover")

    def test_a_worksheet_of_an_unexpected_shape_is_refused(self):
        for label, mutate in {
            "extra key": lambda s: s.update({"extra": "x"}),
            "missing key": lambda s: s.pop("note"),
            "another application": lambda s: s.update({"application_id": "app-other"}),
            "no entries": lambda s: s.update({"entries": []}),
            "entry extra key": lambda s: s["entries"][0].update({"invented": "x"}),
        }.items():
            with self.subTest(case=label):
                sheet = self.worksheet()
                mutate(sheet)
                self.refuse(sheet, "unexpected shape|reviewed application|no entries")

    def test_a_proposal_is_single_use(self):
        """Replaying a worksheet is how a replaced value comes back."""
        # The same worksheet twice, not two worksheets: building a second one would propose
        # again, and a fresh proposal is exactly what is allowed.
        sheet = self.worksheet()
        self.confirm(sheet)
        before = self.snapshot_of_library()
        with self.assertRaisesRegex(ValueError, "already been confirmed"):
            self.confirm(sheet)
        self.assertEqual(self.snapshot_of_library(), before)

    def test_a_profile_that_moved_under_the_worksheet_is_refused(self):
        """A proposed value describes a fact as it was when it was read."""
        sheet = self.worksheet()
        self.register(REPLACEMENT)
        self.refuse(sheet, "changed since this was proposed")

    # ---- confirmation is per entry, and never inferred -------------------------

    def test_an_unconfirmed_entry_is_left_out_rather_than_guessed(self):
        result = self.confirm(self.worksheet(confirm=("contact.email",)))
        self.assertEqual(result["written"], ["contact.email"])
        self.assertEqual(result["skipped"], ["contact.phone", "profile.linkedin"])
        self.assertEqual(self.active(), ["contact.email"])

    def test_confirming_nothing_writes_nothing_and_says_so(self):
        self.refuse(self.worksheet(confirm=()), "nothing was confirmed")

    def test_a_confirmed_entry_with_no_answer_is_refused(self):
        """Confirming a blank would file it as the user's word."""
        for blank in (None, "", "   ", 17):
            with self.subTest(answer=repr(blank)):
                self.refuse(self.worksheet(**{"contact.email": {"answer": blank}}),
                            "must carry an answer")

    def test_confirmation_must_be_stated_as_a_boolean(self):
        self.refuse(self.worksheet(**{"contact.email": {"confirmed_by_user": 1}}),
                    "stated as a boolean")

    # ---- what gets written ----------------------------------------------------

    def test_written_answers_carry_the_reviewed_shape(self):
        self.confirm(self.worksheet())
        for row in self.db.execute("SELECT * FROM answers WHERE status='active'"):
            with self.subTest(canonical_id=row["canonical_id"]):
                self.assertEqual(row["source_type"], "user_confirmed")
                self.assertEqual(row["auto_fill_allowed"], 1)
                self.assertEqual(row["auto_submit_allowed"], 0)
                self.assertEqual(row["validity_class"], "stable")
                self.assertEqual(json.loads(row["scope_json"]), {})
                self.assertEqual(json.loads(row["dependent_fact_ids_json"]), ["fact-0002"])
                self.assertEqual(json.loads(row["invalidation_triggers_json"]), [])

    def test_nothing_written_here_can_ever_submit(self):
        self.confirm(self.worksheet())
        submitting = self.db.execute(
            "SELECT COUNT(*) FROM answers WHERE auto_submit_allowed=1").fetchone()[0]
        self.assertEqual(submitting, 0)

    def test_confirming_the_same_value_again_does_not_make_a_second_answer(self):
        """Two active answers for one meaning is what `match_answer` calls a conflict."""
        self.confirm(self.worksheet())
        result = self.confirm(self.worksheet())
        self.assertEqual(result["written"], [])
        self.assertEqual(sorted(result["unchanged"]), sorted(CONTACT))
        self.assertEqual(self.active(), sorted(CONTACT))

    def test_a_changed_value_supersedes_rather_than_joins(self):
        self.confirm(self.worksheet())
        sheet = self.worksheet(**{"contact.email": {"answer": "other@example.invalid"}})
        result = self.confirm(sheet)
        self.assertEqual(result["written"], ["contact.email"])
        self.assertEqual(self.active(), sorted(CONTACT))
        superseded = self.db.execute(
            "SELECT COUNT(*) FROM answers WHERE status='superseded'").fetchone()[0]
        self.assertEqual(superseded, 1)

    def test_a_failure_partway_writes_nothing_at_all(self):
        """A library holding two of three is one where nobody can tell which is missing."""
        # Proposing writes an audit row of its own, so the snapshot is taken after it.
        sheet = self.worksheet()
        before = self.snapshot_of_library()
        with unittest.mock.patch.object(
                ANSWERS, "add_answer",
                side_effect=[None, RuntimeError("interrupted")]):
            with self.assertRaises(RuntimeError):
                self.confirm(sheet)
        self.assertEqual(self.snapshot_of_library(), before)
        self.assertEqual(self.active(), [])

    def test_the_audit_log_names_meanings_and_never_a_value(self):
        self.confirm(self.worksheet())
        logged = "\n".join(row["metadata_json"] for row in
                           self.db.execute("SELECT metadata_json FROM audit_events"))
        for piece in ("probe@example.invalid", "555-0100", "example.invalid/in/probe"):
            self.assertNotIn(piece, logged)
        self.assertIn("contact.email", logged)

    # ---- the loop closes ------------------------------------------------------

    def test_a_confirmed_answer_fills_and_then_goes_stale_with_its_fact(self):
        """Propose, confirm, match, edit the profile, and watch it stop matching."""
        self.confirm(self.worksheet())
        for target in CONTACT:
            ANSWERS.add_question_form(self.db, target, f"Reviewed wording for {target}")
        for target in CONTACT:
            with self.subTest(target=target, stage="confirmed"):
                match = ANSWERS.match_answer(
                    self.db, f"Reviewed wording for {target}", {"country": "US"},
                    "auth-probe", AT)
                self.assertTrue(match["auto_fill_ready"])
        self.register(REPLACEMENT)
        for target in CONTACT:
            with self.subTest(target=target, stage="profile changed"):
                match = ANSWERS.match_answer(
                    self.db, f"Reviewed wording for {target}", {"country": "US"},
                    "auth-probe", AT)
                self.assertEqual(match["reason"], "answer_stale")
                self.assertFalse(match["auto_fill_ready"])

    def test_an_answer_may_not_be_confirmed_against_a_fact_nobody_holds(self):
        """`require_dependent_facts` is on the real path, not beside it."""
        sheet = self.worksheet()
        self.db.execute("DELETE FROM candidate_facts")
        self.db.commit()
        self.refuse(sheet, "does not hold")

    def test_nothing_here_carries_a_real_contact_detail(self):
        source = Path(__file__).read_text(encoding="utf-8")
        for forbidden in ("@" + "gmail", "sis" + "si", "linkedin.com" + "/in", "21" + "2-"):
            self.assertNotIn(forbidden, source)


class IntakeLoopCliTests(unittest.TestCase):
    """The commands as they are actually run, including the file mode of the worksheet."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db_path = self.root / "library.db"
        db = sqlite3.connect(str(self.db_path))
        db.row_factory = sqlite3.Row
        for module in (ANSWERS, APPLICATIONS, RESUMES, PRE_SUBMIT, CANDIDATES):
            module.initialize(db)
        candidate = {
            "schema_version": "0.2.0", "profile_id": "probe",
            "work_authorization": {"country": "US", "authorized_now": True,
                                   "sponsorship_now": False, "sponsorship_future": False,
                                   "employer_action_required": False, "confirmed": True},
            "search": {},
            "facts": [{"id": "fact-0002", "type": "contact", "value": SYNTHETIC,
                       "status": "confirmed", "locked": False,
                       "evidence_strength": "direct"}]}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        path = self.root / "candidate.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        CANDIDATES.register_snapshot(db, self.root / "snapshots", path, "user", AT)
        db.commit()
        db.close()
        self.worksheet_path = self.root / "worksheet.json"

    def run_command(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "answer_library.py"), "--db", str(self.db_path),
             *arguments], capture_output=True, text=True, cwd=str(ROOT), timeout=60)

    def test_the_worksheet_is_written_private_and_the_output_is_not(self):
        finished = self.run_command(
            "propose-answers", "--intake-plan", str(PLAN_PATH), "--round", "contact",
            "--out", str(self.worksheet_path))
        self.assertEqual(finished.returncode, 0, finished.stderr[-500:])
        self.assertEqual(oct(self.worksheet_path.stat().st_mode)[-3:], "600")
        # The worksheet holds the answers. The terminal holds meanings and counts.
        sheet = json.loads(self.worksheet_path.read_text(encoding="utf-8"))
        self.assertTrue(all(entry["answer"] for entry in sheet["entries"]))
        for piece in ("probe@example.invalid", "555-0100", "example.invalid/in/probe"):
            self.assertNotIn(piece, finished.stdout + finished.stderr)

    def test_the_command_pair_writes_only_what_was_confirmed(self):
        self.run_command("propose-answers", "--intake-plan", str(PLAN_PATH),
                         "--round", "contact", "--out", str(self.worksheet_path))
        sheet = json.loads(self.worksheet_path.read_text(encoding="utf-8"))
        for entry in sheet["entries"]:
            entry["confirmed_by_user"] = entry["canonical_id"] != "contact.phone"
        self.worksheet_path.write_text(json.dumps(sheet), encoding="utf-8")
        finished = self.run_command("confirm-answers", "--intake-plan", str(PLAN_PATH),
                                    "--worksheet", str(self.worksheet_path))
        self.assertEqual(finished.returncode, 0, finished.stderr[-500:])
        result = json.loads(finished.stdout)
        self.assertEqual(result["written"], ["contact.email", "profile.linkedin"])
        self.assertEqual(result["skipped"], ["contact.phone"])
        for piece in ("probe@example.invalid", "example.invalid/in/probe"):
            self.assertNotIn(piece, finished.stdout + finished.stderr)


if __name__ == "__main__":
    unittest.main()
