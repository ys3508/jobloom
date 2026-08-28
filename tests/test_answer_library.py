import importlib.util
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "answer_library.py"
SPEC = importlib.util.spec_from_file_location("answer_library", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)
AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


def connection():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    MODULE.initialize(db)
    return db


def entry(answer_id="answer-1", canonical_id="work_authorized_now", answer=True, **updates):
    value = {
        "answer_id": answer_id,
        "canonical_id": canonical_id,
        "canonical_meaning": "Whether the candidate is currently authorized to work",
        "answer": answer,
        "answer_type": "time_sensitive_fact",
        "source_type": "user_confirmed",
        "source_ref": "candidate.json#work_authorization",
        "confirmation_status": "confirmed",
        "confirmed_at": "2026-08-20T12:00:00Z",
        "effective_from": "2026-08-20T12:00:00Z",
        "expires_at": "2027-01-01T00:00:00Z",
        "review_after": None,
        "validity_class": "event_driven",
        "scope": {"country": "US", "application_id": "app-1"},
        "preconditions": {},
        "exclusions": {},
        "auto_fill_allowed": True,
        "auto_submit_allowed": True,
        "sensitivity": "high",
        "invalidation_triggers": ["immigration_status_changed"],
        "dependent_fact_ids": ["fact-work-auth"],
        "status": "active",
    }
    value.update(updates)
    return value


def authorization(authorization_id="auth-1", **updates):
    value = {
        "authorization_id": authorization_id,
        "confirmed_at": "2026-08-20T12:00:00Z",
        "expires_at": "2026-09-01T12:00:00Z",
        "scope": {"country": "US", "queue_id": "queue-1"},
    }
    value.update(updates)
    return value


CONTEXT = {"country": "US", "queue_id": "queue-1", "company": "Example", "application_id": "app-1"}


class AnswerLibraryTests(unittest.TestCase):
    def db(self):
        db = connection()
        self.addCleanup(db.close)
        return db

    def test_exact_answer_matches_without_model(self):
        db = self.db()
        MODULE.add_answer(db, entry())
        MODULE.add_question_form(db, "work_authorized_now", "Are you legally authorized to work in the United States?")
        result = MODULE.match_answer(db, "  Are you legally authorized to work in the United States?! ", CONTEXT, at=AT)
        self.assertEqual(result["decision"], "use")
        self.assertTrue(result["answer"])
        self.assertFalse(result["auto_fill_ready"])
        self.assertTrue(result["per_application_recheck_required"])

    def test_current_authorization_enables_auto_fill(self):
        db = self.db()
        MODULE.add_answer(db, entry())
        MODULE.add_question_form(db, "work_authorized_now", "Authorized to work now?")
        MODULE.add_authorization(db, authorization())
        result = MODULE.match_answer(db, "Authorized to work now?", CONTEXT, "auth-1", AT)
        self.assertTrue(result["channel_a_current"])
        self.assertTrue(result["channel_b_fresh"])
        self.assertTrue(result["auto_fill_ready"])

    def test_current_authorization_never_overrides_expired_answer(self):
        db = self.db()
        MODULE.add_answer(db, entry(expires_at="2026-08-24T00:00:00Z"))
        MODULE.add_question_form(db, "work_authorized_now", "Authorized to work now?")
        MODULE.add_authorization(db, authorization())
        result = MODULE.match_answer(db, "Authorized to work now?", CONTEXT, "auth-1", AT)
        self.assertEqual(result["decision"], "ask")
        self.assertEqual(result["reason"], "answer_expired")

    def test_scope_mismatch_does_not_reuse_answer(self):
        db = self.db()
        MODULE.add_answer(db, entry(scope={"country": "CA", "application_id": "app-1"}))
        MODULE.add_question_form(db, "work_authorized_now", "Authorized to work now?")
        result = MODULE.match_answer(db, "Authorized to work now?", CONTEXT, at=AT)
        self.assertEqual(result["reason"], "answer_scope_mismatch")

    def test_equally_specific_conflicting_answers_stop(self):
        db = self.db()
        MODULE.add_answer(db, entry())
        MODULE.add_answer(db, entry(answer_id="answer-2", answer=False, confirmed_at="2026-08-21T12:00:00Z"))
        MODULE.add_question_form(db, "work_authorized_now", "Authorized to work now?")
        result = MODULE.match_answer(db, "Authorized to work now?", CONTEXT, at=AT)
        self.assertEqual(result["decision"], "conflict")

    def test_immigration_meanings_are_not_substituted(self):
        db = self.db()
        MODULE.add_answer(db, entry(canonical_id="sponsorship_future", canonical_meaning="Future sponsorship", answer=True))
        MODULE.add_question_form(db, "sponsorship_future", "Will you require sponsorship in the future?")
        result = MODULE.match_answer(db, "Are you authorized to work now?", CONTEXT, at=AT)
        self.assertEqual(result["reason"], "new_question")

    def test_unverified_semantic_equivalent_is_rejected(self):
        db = self.db()
        with self.assertRaisesRegex(ValueError, "user-verified"):
            MODULE.add_question_form(db, "work_authorized_now", "Can you legally work here?", "semantic_equivalent", False)

    def test_invalidation_trigger_marks_dependent_answer_stale(self):
        db = self.db()
        MODULE.add_answer(db, entry())
        affected = MODULE.invalidate_by_trigger(db, "immigration_status_changed")
        self.assertEqual(affected, ["answer-1"])
        self.assertEqual(db.execute("SELECT status FROM answers").fetchone()[0], "stale")

    def test_standing_authorization_cannot_exceed_fourteen_days(self):
        db = self.db()
        with self.assertRaisesRegex(ValueError, "fourteen days"):
            MODULE.add_authorization(db, authorization(expires_at="2026-09-10T12:00:00Z"))

    def test_standing_authorization_requires_concrete_scope(self):
        db = self.db()
        with self.assertRaisesRegex(ValueError, "non-empty scope"):
            MODULE.add_authorization(db, authorization(scope={}))

    def test_legal_commitment_cannot_auto_submit(self):
        db = self.db()
        legal = entry(answer_type="legal_commitment", auto_submit_allowed=True)
        with self.assertRaisesRegex(ValueError, "cannot enable"):
            MODULE.add_answer(db, legal)

    def test_per_application_answer_requires_application_scope(self):
        db = self.db()
        with self.assertRaisesRegex(ValueError, "scope.application_id"):
            MODULE.add_answer(db, entry(validity_class="per_application", scope={"country": "US"}))

    def test_per_application_answer_does_not_match_another_application(self):
        db = self.db()
        MODULE.add_answer(db, entry(
            validity_class="per_application", scope={"country": "US", "application_id": "app-1"}
        ))
        MODULE.add_question_form(db, "work_authorized_now", "Authorized to work now?")
        result = MODULE.match_answer(
            db, "Authorized to work now?", dict(CONTEXT, application_id="app-2"), at=AT
        )
        self.assertEqual(result["decision"], "ask")
        self.assertEqual(result["reason"], "answer_scope_mismatch")

    def test_immigration_answer_without_application_scope_is_never_auto_filled(self):
        db = self.db()
        MODULE.add_answer(db, entry(scope={"country": "US"}))
        MODULE.add_question_form(db, "work_authorized_now", "Authorized to work now?")
        MODULE.add_authorization(db, authorization())
        result = MODULE.match_answer(db, "Authorized to work now?", CONTEXT, "auth-1", AT)
        self.assertEqual(result["decision"], "ask")
        self.assertEqual(result["reason"], "immigration_recheck_required")
        self.assertFalse(result["auto_fill_ready"])

    def test_immigration_answer_requires_an_application_in_context(self):
        db = self.db()
        MODULE.add_answer(db, entry(scope={"country": "US"}))
        MODULE.add_question_form(db, "work_authorized_now", "Authorized to work now?")
        context = {key: value for key, value in CONTEXT.items() if key != "application_id"}
        result = MODULE.match_answer(db, "Authorized to work now?", context, at=AT)
        self.assertEqual(result["reason"], "immigration_recheck_required")

    def test_non_immigration_answer_needs_no_application_scope(self):
        db = self.db()
        MODULE.add_answer(db, entry(
            answer_id="answer-relocate", canonical_id="relocation_willingness",
            answer_type="stable_fact", validity_class="stable", scope={"country": "US"},
        ))
        MODULE.add_question_form(db, "relocation_willingness", "Are you willing to relocate?")
        MODULE.add_authorization(db, authorization())
        result = MODULE.match_answer(db, "Are you willing to relocate?", CONTEXT, "auth-1", AT)
        self.assertEqual(result["decision"], "use")
        self.assertTrue(result["auto_fill_ready"])

    def test_audit_log_never_contains_answer_value(self):
        db = self.db()
        secret_value = "sensitive-answer-value"
        MODULE.add_answer(db, entry(answer=secret_value))
        logs = " ".join(row[0] for row in db.execute("SELECT metadata_json FROM audit_events"))
        self.assertNotIn(secret_value, logs)


if __name__ == "__main__":
    unittest.main()
