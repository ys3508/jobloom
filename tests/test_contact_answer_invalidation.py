"""What happens to the three contact answers when the profile behind them changes.

They are the only answers Task 14 lets travel between applications, so they are the only ones
whose staleness is not handled by re-confirming per application. The chain that handles it
runs through `candidate_core.register_snapshot`, which compares the union of the old and new
fact ids and marks any active answer depending on a changed one stale. Naming the dependency
is what connects an answer to it — an answer that names nothing is never invalidated by
anything.

Every check here runs the production chain rather than reading a field back. Asserting that
`dependent_fact_ids` contains `fact-0002` would pass just as happily if nothing consumed it,
which is the failure this file exists to rule out: a dependency that looks handled and is
not is worse than one that plainly is not.

Every value below is obviously synthetic. Nothing reads `.jobloom/`, and no contact detail of
the user's appears in this file, in its output, or in anything it writes.
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


def load(name):
    spec = importlib.util.spec_from_file_location(f"invalidation_{name}",
                                                  SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


ANSWERS = load("answer_library")
CANDIDATES = load("candidate_core")
RESUMES = load("resume_core")
APPLICATIONS = load("application_core")
PRE_SUBMIT = load("pre_submit_core")

AT = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
CONTACT_TARGETS = ("contact.email", "contact.phone", "profile.linkedin")
# Invented, and invented visibly. A fixture that reached for realism would put a real contact
# detail in the repository to make a test read better.
SYNTHETIC_CONTACT = "probe@example.invalid | 555-0100 | https://example.invalid/in/probe"
SYNTHETIC_REPLACEMENT = "moved@example.invalid | 555-0199 | https://example.invalid/in/moved"


class ContactAnswerInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        for module in (ANSWERS, APPLICATIONS, RESUMES, PRE_SUBMIT, CANDIDATES):
            module.initialize(self.db)
        self.addCleanup(self.db.close)
        # Channel A, so `auto_fill_ready` can be true before anything is changed. Without a
        # standing authorization every answer reads not-ready and the "before" half of each
        # assertion would pass for the wrong reason.
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-probe", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=9)).isoformat(),
            "scope": {"country": "US"}})
        self.register(self.facts())
        self.confirm_contact_answers()

    # ---- the production chain, driven exactly as production drives it ----------

    def facts(self, contact=SYNTHETIC_CONTACT, other="Somewhere, ST",
              drop_contact=False, contact_id="fact-0002"):
        facts = [
            {"id": "fact-0001", "type": "identity", "value": "Probe Person",
             "status": "confirmed", "locked": False, "evidence_strength": "direct"},
            {"id": "fact-0003", "type": "location", "value": other,
             "status": "confirmed", "locked": False, "evidence_strength": "direct"},
        ]
        if not drop_contact:
            facts.insert(1, {"id": contact_id, "type": "contact", "value": contact,
                             "status": "confirmed", "locked": False,
                             "evidence_strength": "direct"})
        return facts

    def register(self, facts):
        candidate = {
            "schema_version": "0.2.0", "profile_id": "probe",
            "work_authorization": {"country": "US", "authorized_now": True,
                                   "sponsorship_now": False, "sponsorship_future": False,
                                   "employer_action_required": False, "confirmed": True},
            "search": {}, "facts": facts}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        path = self.root / f"candidate-{candidate['content_sha256'][:12]}.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        return CANDIDATES.register_snapshot(self.db, self.root / "snapshots", path,
                                            "user", AT)

    def confirm_contact_answers(self):
        """Written exactly as the reviewed shape says they must be."""
        for target in CONTACT_TARGETS:
            shape = ANSWERS.TASK14_INTAKE_SHAPE[target]
            ANSWERS.require_dependent_facts(self.db, shape["dependent_fact_ids"])
            ANSWERS.add_answer(self.db, {
                "answer_id": f"answer-{target}", "canonical_id": target,
                "canonical_meaning": target, "answer": "a synthetic placeholder",
                "answer_type": "stable_fact", "source_type": "user_confirmed",
                "confirmation_status": "confirmed", "confirmed_at": AT.isoformat(),
                "validity_class": "stable", "scope": {},
                "auto_fill_allowed": True, "auto_submit_allowed": False,
                "dependent_fact_ids": list(shape["dependent_fact_ids"]),
                "invalidation_triggers": list(shape["invalidation_triggers"])})
            ANSWERS.add_question_form(self.db, target, f"Reviewed wording for {target}")

    def reasons(self):
        return {target: ANSWERS.match_answer(
            self.db, f"Reviewed wording for {target}", {"country": "US"}, "auth-probe", AT)
            for target in CONTACT_TARGETS}

    def statuses(self):
        return {row["answer_id"]: row["status"] for row in self.db.execute(
            "SELECT answer_id, status FROM answers ORDER BY answer_id")}

    def assert_all_ready(self):
        for target, result in self.reasons().items():
            with self.subTest(target=target):
                self.assertTrue(result["auto_fill_ready"], result)
                self.assertEqual(result["reason"], "verified_answer_match")
        self.assertEqual(set(self.statuses().values()), {"active"})

    def assert_all_stale(self):
        for target, result in self.reasons().items():
            with self.subTest(target=target):
                self.assertEqual(result["reason"], "answer_stale")
                self.assertFalse(result["auto_fill_ready"])
        self.assertEqual(set(self.statuses().values()), {"stale"})

    # ---- the chain itself -----------------------------------------------------

    def test_the_three_answers_start_ready_and_go_stale_when_the_fact_changes(self):
        """The whole point, end to end. A field check would pass with nothing wired."""
        self.assert_all_ready()
        self.register(self.facts(contact=SYNTHETIC_REPLACEMENT))
        self.assert_all_stale()

    def test_a_stale_answer_cannot_be_planned_into_a_fill_step(self):
        """Staleness has to reach the planner, not only the row.

        `answer_issue` turns a non-active status into `answer_stale`, so the planner never
        sees an answer to plan. This is the assertion that would catch a status nothing reads.
        """
        self.register(self.facts(contact=SYNTHETIC_REPLACEMENT))
        for target in CONTACT_TARGETS:
            with self.subTest(target=target):
                match = ANSWERS.match_answer(
                    self.db, f"Reviewed wording for {target}", {"country": "US"},
                    "auth-probe", AT)
                self.assertFalse(match["auto_fill_ready"])
                self.assertNotIn("answer", match)

    def test_changing_an_unrelated_fact_leaves_them_alone(self):
        """The invalidation is per-fact, and this is what says so.

        If this ever fails, the emitter has become snapshot-level: every answer with any
        dependency dying on every edit. That is over-invalidation — safe, and worth
        recording rather than fixing by narrowing what counts as a change.
        """
        self.assert_all_ready()
        self.register(self.facts(other="Elsewhere, ZZ"))
        self.assert_all_ready()

    def test_registering_the_same_snapshot_again_invalidates_nothing(self):
        self.assert_all_ready()
        self.register(self.facts())
        self.assert_all_ready()

    def test_deleting_the_fact_they_depend_on_makes_them_stale(self):
        """Absence is a change, because the walk compares the union of old and new ids.

        Iterating only the facts that are present would leave the dependency dangling and the
        old value filling forever, which is the original bug wearing a different hat.
        """
        self.assert_all_ready()
        self.register(self.facts(drop_contact=True))
        self.assert_all_stale()

    def test_renaming_the_fact_they_depend_on_makes_them_stale(self):
        self.assert_all_ready()
        self.register(self.facts(contact_id="fact-0099"))
        self.assert_all_stale()

    def test_one_changed_contact_detail_stales_all_three(self):
        """Recorded rather than fixed. The fact is composite, so the three move together.

        Splitting it would need a new CandidateSnapshot and the re-approval of every resume
        version behind it. Over-invalidating costs a re-confirmation; under-invalidating fills
        a form with a value the user has replaced.
        """
        self.assert_all_ready()
        only_the_phone = SYNTHETIC_CONTACT.replace("555-0100", "555-0177")
        self.assertNotEqual(only_the_phone, SYNTHETIC_CONTACT)
        self.register(self.facts(contact=only_the_phone))
        self.assert_all_stale()

    # ---- naming a dependency nothing holds ------------------------------------

    def test_an_answer_may_not_depend_on_a_fact_the_snapshot_does_not_hold(self):
        """A list naming nothing real intersects nothing, forever."""
        with self.assertRaisesRegex(ValueError, "does not hold"):
            ANSWERS.require_dependent_facts(self.db, ["fact-nobody-has"])
        with self.assertRaisesRegex(ValueError, "must be a list"):
            ANSWERS.require_dependent_facts(self.db, "fact-0002")
        # An empty list is not a claim about anything, so it needs no snapshot.
        ANSWERS.require_dependent_facts(self.db, [])

    def test_the_reviewed_shape_names_the_dependency_on_exactly_the_three(self):
        for target, shape in ANSWERS.TASK14_INTAKE_SHAPE.items():
            with self.subTest(target=target):
                expected = ([ANSWERS.TASK14_CONTACT_FACT]
                            if target in ANSWERS.TASK14_CONTACT_TARGETS else [])
                self.assertEqual(shape["dependent_fact_ids"], expected)
                # Empty everywhere: `invalidate_by_trigger` has no production caller, so a
                # trigger named here would be a declaration nobody raises.
                self.assertEqual(shape["invalidation_triggers"], [])

    def test_nothing_here_carries_a_real_contact_detail(self):
        """A test that scans itself has to avoid writing the needles it scans for.

        Assembled from fragments so this file never contains one whole — the first version
        failed against its own guard, which is the honest outcome of a check that works.
        """
        source = Path(__file__).read_text(encoding="utf-8")
        needles = ["@" + "gmail", "sis" + "si", "linkedin.com" + "/in", "21" + "2-"]
        for forbidden in needles:
            with self.subTest(needle=len(forbidden)):
                self.assertNotIn(forbidden, source)
        self.assertIn("example.invalid", source)


if __name__ == "__main__":
    unittest.main()
