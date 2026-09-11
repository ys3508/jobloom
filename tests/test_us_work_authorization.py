"""One immigration answer, confirmed for a country and re-confirmed for each application.

The rule under test is the one that changed: an immigration answer used to be fillable only
when its own scope named the application, and now a country-scoped answer plus an
authorization bound to *this application, this answer and this exact value* also counts. The
guarantee is unchanged — a person affirms, per application, close in time — so most of these
tests are about the ways that affirmation stops being true.
"""

import importlib.util
import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"us_work_auth_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


WORK_AUTH = load_script("us_work_authorization")
ANSWERS = load_script("answer_library")
ASSIST = load_script("apply_assist")
POLICY = load_script("field_policy")

AT = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
APP = "app-1"
VALUE = "Yes, I am authorized to work in the US"


class WorkAuthFixture(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        ANSWERS.initialize(self.db)
        self.addCleanup(self.db.close)
        ANSWERS.add_question_form(self.db, WORK_AUTH.CANONICAL_ID, WORK_AUTH.QUESTION,
                                  verified_by_user=True)
        self.db.commit()

    def state(self, at=AT):
        return WORK_AUTH.state(self.db, APP, at=at)

    def save(self, value=VALUE, at=AT):
        return WORK_AUTH.save_answer(self.db, value, at=at)

    def authorize(self, at=AT, answer_id=None, digest=None):
        report = self.state(at)
        return WORK_AUTH.authorize(
            self.db, APP, answer_id or report["answer_id"],
            digest if digest is not None else report["answer_value_sha256"], at=at)


class TheVersionTest(WorkAuthFixture):
    def test_task_fourteen_is_untouched(self):
        """A reviewed round bound to one application must not change meaning under its name."""
        self.assertEqual(ANSWERS.TASK14_APPLICATION, "app-mgb-rq4077023")
        shape = ANSWERS.TASK14_INTAKE_SHAPE["work_authorized_now"]
        self.assertEqual(shape["validity_class"], "per_application")
        self.assertEqual(shape["scope"], {"country": "US", "application_id": "app-mgb-rq4077023"})
        self.assertIs(shape["engine_enforced_recheck"], True)

    def test_every_value_comes_from_a_register_that_already_exists(self):
        shape = WORK_AUTH.ANSWER_SHAPE
        self.assertIn(shape["validity_class"], ANSWERS.VALIDITY_CLASSES)
        self.assertIn(shape["answer_type"], ANSWERS.ANSWER_TYPES)
        self.assertIn(shape["source_type"], ANSWERS.SOURCE_TYPES)
        self.assertLessEqual(set(shape["scope"]), ANSWERS.SCOPE_FIELDS)

    def test_the_answer_is_country_scoped_and_event_driven(self):
        self.assertEqual(WORK_AUTH.ANSWER_SHAPE["scope"], {"country": "US"})
        self.assertEqual(WORK_AUTH.ANSWER_SHAPE["validity_class"], "event_driven")
        self.assertNotIn("application_id", WORK_AUTH.ANSWER_SHAPE["scope"])

    def test_no_trigger_is_declared(self):
        """Empty on purpose: `invalidate_by_trigger` has no production caller, so a trigger
        named here would be a declaration nobody ever raises. The binding is the channel."""
        self.assertEqual(WORK_AUTH.ANSWER_SHAPE["invalidation_triggers"], [])

    def test_it_covers_exactly_one_question(self):
        self.assertEqual(WORK_AUTH.CANONICAL_ID, "work_authorized_now")
        self.assertNotIn("sponsorship", json.dumps(WORK_AUTH.ANSWER_SHAPE))

    def test_it_never_permits_submission(self):
        self.assertIs(WORK_AUTH.ANSWER_SHAPE["auto_submit_allowed"], False)


class TheSequenceTest(WorkAuthFixture):
    """The states the user was told to expect, in order."""

    def test_nothing_saved(self):
        report = self.state()
        self.assertFalse(report["answer_exists"])
        self.assertEqual(report["detail"], "no_confirmed_answer")
        self.assertFalse(report["authorized"])

    def test_saving_the_answer_does_not_authorise_it(self):
        self.save()
        report = self.state()
        self.assertTrue(report["answer_exists"])
        self.assertFalse(report["authorized"])
        self.assertEqual(report["detail"], "application_authorization_missing")

    def test_authorising_this_application_makes_it_fillable(self):
        self.save()
        self.authorize()
        report = self.state()
        self.assertTrue(report["authorized"])
        inspection = ANSWERS.inspect_answer(
            self.db, WORK_AUTH.QUESTION, {"application_id": APP, "country": "US"}, AT)
        self.assertTrue(inspection["auto_fill_ready"])
        self.assertEqual(inspection["reason"], "verified_answer_match")

    def test_the_lane_says_the_answer_exists_and_needs_authorisation(self):
        """Not `you_answer`: sending the user to write an answer they already gave is wrong."""
        self.save()
        lane = ASSIST.classify_question(
            self.db, WORK_AUTH.QUESTION, snapshot_sha256=None,
            context={"application_id": APP, "country": "US"}, facts=[], at=AT)
        self.assertEqual(lane["lane"], "answer_needs_authorization")
        self.assertTrue(lane["answer_exists"])
        self.assertEqual(lane["reason"], "immigration_recheck_required")
        self.assertEqual(lane["authorization_reason"], "application_authorization_missing")


class TheAffirmationStopsBeingTrueTest(WorkAuthFixture):
    def test_another_application_is_not_covered(self):
        self.save()
        self.authorize()
        other = WORK_AUTH.state(self.db, "app-2", at=AT)
        self.assertFalse(other["authorized"])
        self.assertEqual(other["detail"], "application_authorization_missing")

    def test_an_expired_authorisation_stops_covering_it(self):
        self.save()
        self.authorize()
        later = AT + WORK_AUTH.AUTHORIZATION_TTL + timedelta(minutes=1)
        self.assertEqual(self.state(later)["detail"], "application_authorization_expired")

    def test_a_revoked_authorisation_stops_covering_it(self):
        self.save()
        result = self.authorize()
        row = self.db.execute(
            "SELECT authorization_id FROM authorizations WHERE answer_id=?",
            (self.state()["answer_id"],)).fetchone()
        ANSWERS.revoke_authorization(self.db, row["authorization_id"])
        self.assertEqual(self.state()["detail"], "application_authorization_revoked")
        self.assertEqual(result["application_id"], APP)

    def test_changing_the_answer_drops_the_authorisation_with_no_trigger(self):
        """The binding names an answer and a value digest, so a new answer stops matching."""
        self.save()
        self.authorize()
        self.assertTrue(self.state()["authorized"])
        self.save("No, I am not", at=AT + timedelta(minutes=1))
        report = self.state()
        self.assertFalse(report["authorized"])
        self.assertEqual(report["detail"], "application_authorization_answer_changed")

    def test_an_authorisation_may_not_run_longer_than_fourteen_days(self):
        self.save()
        report = self.state()
        with self.assertRaises(ValueError):
            ANSWERS.add_answer_authorization(self.db, {
                "authorization_id": "auth-long", "confirmed_at": AT.isoformat(),
                "expires_at": (AT + timedelta(days=15)).isoformat(),
                "scope": {"application_id": APP, "country": "US"},
                "canonical_id": WORK_AUTH.CANONICAL_ID, "answer_id": report["answer_id"],
                "answer_value_sha256": report["answer_value_sha256"], "actor": "user"})


class WhatDoesNotCountTest(WorkAuthFixture):
    def test_a_broad_standing_authorisation_does_not_count(self):
        """"You may fill things for this application" is a permission. What this rule wants
        re-confirmed is a fact, and a permission is not one."""
        self.save()
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-broad", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=7)).isoformat(),
            "scope": {"application_id": APP, "country": "US"}})
        self.assertFalse(self.state()["authorized"])
        self.assertEqual(self.state()["detail"], "application_authorization_missing")

    def test_a_system_actor_may_not_authorise_it(self):
        self.save()
        report = self.state()
        with self.assertRaises(ValueError):
            ANSWERS.add_answer_authorization(self.db, {
                "authorization_id": "auth-sys", "confirmed_at": AT.isoformat(),
                "expires_at": (AT + timedelta(days=1)).isoformat(),
                "scope": {"application_id": APP, "country": "US"},
                "canonical_id": WORK_AUTH.CANONICAL_ID, "answer_id": report["answer_id"],
                "answer_value_sha256": report["answer_value_sha256"], "actor": "system"})

    def test_authorising_a_value_that_is_not_the_one_on_file_is_refused(self):
        """A screen showing a stale value must not be able to authorise the current one."""
        self.save()
        report = self.state()
        with self.assertRaises(ValueError):
            self.authorize(digest="0" * 64)
        self.assertFalse(self.state()["authorized"])
        self.assertEqual(report["answer_value_sha256"],
                         ANSWERS.answer_value_sha256(self.db, report["answer_id"]))

    def test_an_authorisation_without_an_application_is_refused(self):
        self.save()
        report = self.state()
        with self.assertRaises(ValueError):
            ANSWERS.add_answer_authorization(self.db, {
                "authorization_id": "auth-noapp", "confirmed_at": AT.isoformat(),
                "expires_at": (AT + timedelta(days=1)).isoformat(),
                "scope": {"country": "US"},
                "canonical_id": WORK_AUTH.CANONICAL_ID, "answer_id": report["answer_id"],
                "answer_value_sha256": report["answer_value_sha256"], "actor": "user"})

    def test_a_blank_or_oversized_answer_is_refused_without_echoing_it(self):
        with self.assertRaises(ValueError):
            WORK_AUTH.check_answer("   ")
        secret = "Y" * (WORK_AUTH.MAX_ANSWER + 1)
        with self.assertRaises(ValueError) as caught:
            WORK_AUTH.check_answer(secret)
        self.assertNotIn("Y", str(caught.exception))


class SponsorshipIsUnaffectedTest(WorkAuthFixture):
    def test_the_sponsorship_question_is_still_always_manual(self):
        disposition, domain, _ = POLICY.disposition(
            field_id="sponsorship_now",
            question="Will you require work sponsorship now or in the future?",
            control="radio", source_kind=None)
        self.assertEqual((disposition, domain), ("always_manual", "sponsorship"))

    def test_a_sponsorship_question_never_reaches_the_answer_path(self):
        self.save()
        self.authorize()
        lane = ASSIST.classify_question(
            self.db, "Will you require work sponsorship now or in the future?",
            snapshot_sha256=None, context={"application_id": APP, "country": "US"},
            facts=[], at=AT)
        self.assertEqual(lane["lane"], "manual_only")
        self.assertFalse(lane["answer_exists"])

    def test_authorising_work_authorisation_stores_no_sponsorship_answer(self):
        self.save()
        self.authorize()
        rows = self.db.execute(
            "SELECT canonical_id FROM answers UNION ALL SELECT canonical_id FROM authorizations"
        ).fetchall()
        for row in rows:
            self.assertNotIn("sponsorship", str(row["canonical_id"]))


class TheValueStaysInTheWindowTest(WorkAuthFixture):
    def test_saving_does_not_return_the_value(self):
        result = self.save()
        self.assertNotIn(VALUE, json.dumps(result))

    def test_authorising_does_not_return_the_value(self):
        self.save()
        self.assertNotIn(VALUE, json.dumps(self.authorize()))

    def test_the_state_carries_it_because_the_user_has_to_see_what_they_authorise(self):
        """"Allow reuse" with nothing on screen is not a re-confirmation of this answer."""
        self.save()
        self.assertEqual(self.state()["answer"], VALUE)


if __name__ == "__main__":
    unittest.main()
