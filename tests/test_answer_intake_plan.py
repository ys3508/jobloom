"""The reviewed shape of the first ten answers, checked while every value is still absent.

Phase 2 fills copies of these entries in `.jobloom/` once the user has confirmed each one.
What can be held to account before that happens is the shape: which meanings are in scope,
what each one is allowed to be, and — the part worth the most — that no path through this
task can set `auto_submit_allowed`. A constraint written down after the values arrive is a
constraint that was never tested against the code that writes them.

Every value here is invented. Nothing reads `.jobloom/`, and nothing prints an answer.
"""

import importlib.util
import json
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "jobloom" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(f"intake_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


ANSWERS = load("answer_library")
POLICY = load("field_policy")
FILL = load("fill_core")
PLAN = json.loads((ROOT / "skills" / "jobloom" / "assets" / "answer-intake-plan.json")
                  .read_text(encoding="utf-8"))
MANIFEST = json.loads((ROOT / "skills" / "jobloom" / "assets" / "question-forms.json")
                      .read_text(encoding="utf-8"))
FORMS = {form["canonical_id"]: form["question"] for form in MANIFEST["forms"]}
APPLICATION = "app-mgb-rq4077023"
AT = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
# The four legal-status meanings the corpus keeps separate and never merges.
LEGAL_STATUS = {"work_authorized_now", "citizenship_status",
                "permanent_residence_status", "current_country_of_residence"}


def entry_for(canonical_id, answer="a placeholder that means nothing", **updates):
    """One plan entry turned into something `add_answer` will take. Invented value."""
    planned = next(item for item in PLAN["entries"]
                   if item["canonical_id"] == canonical_id)
    built = {
        "answer_id": f"answer-{canonical_id}",
        "canonical_id": planned["canonical_id"],
        "canonical_meaning": planned["canonical_meaning"],
        "answer": answer,
        "answer_type": planned["answer_type"],
        "source_type": planned["source_type"],
        "confirmation_status": "confirmed",
        "confirmed_at": AT.isoformat(),
        "validity_class": planned["validity_class"],
        "scope": dict(planned["scope"]),
        "auto_fill_allowed": planned["auto_fill_allowed"],
        "auto_submit_allowed": planned["auto_submit_allowed"],
    }
    built.update(updates)
    return built


class PlanShapeTests(unittest.TestCase):
    def test_the_plan_covers_the_ten_mapped_meanings_and_no_others(self):
        self.assertEqual({item["canonical_id"] for item in PLAN["entries"]}, set(FORMS))
        self.assertEqual(len(PLAN["entries"]), 10)

    def test_no_entry_may_ever_enable_automatic_submission(self):
        """The one line in this file that is not a preference.

        Task 14 has no case for it, and an entry that could opt in would be an entry someone
        could opt in later without anything noticing.
        """
        for item in PLAN["entries"]:
            with self.subTest(canonical_id=item["canonical_id"]):
                self.assertIs(item["auto_submit_allowed"], False)
                self.assertIs(item["auto_fill_allowed"], True)
        self.assertNotIn("true", json.dumps(
            [item["auto_submit_allowed"] for item in PLAN["entries"]]).lower())

    def test_the_plan_holds_no_answer(self):
        for item in PLAN["entries"]:
            with self.subTest(canonical_id=item["canonical_id"]):
                self.assertIsNone(item["answer"])
        rendered = json.dumps(PLAN, ensure_ascii=False).lower()
        for forbidden in ("@", "sissi", "linkedin.com/in", "212-", "http"):
            self.assertNotIn(forbidden, rendered)

    def test_the_four_legal_status_meanings_are_bound_to_this_application(self):
        for item in PLAN["entries"]:
            if item["canonical_id"] not in LEGAL_STATUS:
                continue
            with self.subTest(canonical_id=item["canonical_id"]):
                self.assertEqual(item["validity_class"], "per_application")
                self.assertEqual(item["scope"]["country"], "US")
                self.assertEqual(item["scope"]["application_id"], APPLICATION)
                self.assertEqual(item["source_type"], "user_confirmed")

    def test_the_plan_records_which_recheck_the_engine_actually_enforces(self):
        """Three of the four are per-application by policy, not by enforcement.

        Only `work_authorized_now` is in `answer_library.IMMIGRATION_CANONICAL_IDS`, so only
        it is force-rechecked at match time. Citizenship, permanent residence and country of
        residence carry the same discipline because this plan says so, and nothing in the
        engine would notice if a later entry dropped it. Recorded rather than smoothed over.
        """
        for item in PLAN["entries"]:
            with self.subTest(canonical_id=item["canonical_id"]):
                self.assertEqual(
                    item["engine_enforced_recheck"],
                    item["canonical_id"] in ANSWERS.IMMIGRATION_CANONICAL_IDS)
        enforced = {item["canonical_id"] for item in PLAN["entries"]
                    if item["engine_enforced_recheck"]}
        self.assertEqual(enforced, {"work_authorized_now"})

    def test_prior_employment_is_two_separate_answers_bound_to_one_application(self):
        """Neither may carry to a second employer.

        `normalized_employer` is a deduplication key and not an approved legal entity, so
        there is nothing here that could safely generalise "I worked here" to another
        company. Scoping to the application is what makes that impossible rather than
        discouraged.
        """
        pair = [item for item in PLAN["entries"]
                if item["canonical_id"].startswith("prior_employment_at_")]
        self.assertEqual(len(pair), 2)
        self.assertEqual({item["canonical_id"] for item in pair},
                         {"prior_employment_at_this_company",
                          "prior_employment_at_an_affiliate"})
        for item in pair:
            with self.subTest(canonical_id=item["canonical_id"]):
                self.assertEqual(item["scope"]["application_id"], APPLICATION)
                self.assertEqual(item["validity_class"], "per_application")


class PlanBehaviourTests(unittest.TestCase):
    """The plan's entries, run through the engine that will receive them."""

    def db(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        ANSWERS.initialize(connection)
        ANSWERS.add_authorization(connection, {
            "authorization_id": "auth-mgb-rq4077023", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=9)).isoformat(),
            "scope": {"country": "US", "application_id": APPLICATION}})
        self.addCleanup(connection.close)
        return connection

    def test_every_planned_entry_is_one_the_library_accepts(self):
        for item in PLAN["entries"]:
            with self.subTest(canonical_id=item["canonical_id"]):
                connection = self.db()
                ANSWERS.add_answer(connection, entry_for(item["canonical_id"]))
                ANSWERS.add_question_form(connection, item["canonical_id"],
                                          FORMS[item["canonical_id"]])
                match = ANSWERS.match_answer(
                    connection, FORMS[item["canonical_id"]],
                    {"country": "US", "application_id": APPLICATION},
                    "auth-mgb-rq4077023", AT)
                self.assertTrue(match["auto_fill_ready"], match)
                self.assertFalse(match["auto_submit_ready"])

    def test_a_legal_status_answer_scoped_to_another_application_is_not_reused(self):
        """The scope is what stops the answer travelling, for all four meanings."""
        for canonical_id in sorted(LEGAL_STATUS):
            with self.subTest(canonical_id=canonical_id):
                connection = self.db()
                ANSWERS.add_answer(connection, entry_for(canonical_id))
                ANSWERS.add_question_form(connection, canonical_id, FORMS[canonical_id])
                match = ANSWERS.match_answer(
                    connection, FORMS[canonical_id],
                    {"country": "US", "application_id": "app-somewhere-else"},
                    "auth-mgb-rq4077023", AT)
                self.assertFalse(match["auto_fill_ready"])
                self.assertIn(match["reason"],
                              {"answer_scope_mismatch", "immigration_recheck_required"})

    def test_work_authorization_broadened_to_a_country_still_demands_a_recheck(self):
        """The engine-enforced half, exercised rather than described.

        Widening the scope to the whole country is exactly the shortcut that would make an
        immigration answer travel between applications. `match_answer` refuses it by name.
        """
        connection = self.db()
        ANSWERS.add_answer(connection, entry_for(
            "work_authorized_now", validity_class="stable", scope={"country": "US"}))
        ANSWERS.add_question_form(connection, "work_authorized_now",
                                  FORMS["work_authorized_now"])
        match = ANSWERS.match_answer(
            connection, FORMS["work_authorized_now"],
            {"country": "US", "application_id": APPLICATION}, "auth-mgb-rq4077023", AT)
        self.assertEqual(match["reason"], "immigration_recheck_required")
        self.assertFalse(match["auto_fill_ready"])

    def test_discovery_source_is_refused_unless_the_user_stated_it(self):
        """How Jobloom found the opening is a fact about Jobloom, not about the user.

        `fill_core._discovery_answer_allowed` is the gate, and it wants both halves: an
        answer type that is specific to this application or conditional, and a source that is
        the user's own confirmation. A derived answer of the right type still fails.
        """
        cases = {
            "as planned": ({}, True),
            "conditional instead": ({"answer_type": "conditional_preference"}, True),
            "a stable fact": ({"answer_type": "stable_fact"}, False),
            "derived rather than stated": (
                {"source_type": "deterministic_derivation"}, False),
            "read off an approved resume": ({"source_type": "approved_resume"}, False),
        }
        for label, (updates, allowed) in cases.items():
            with self.subTest(case=label):
                connection = self.db()
                ANSWERS.add_answer(connection, entry_for("discovery_source", **updates))
                self.assertEqual(
                    FILL._discovery_answer_allowed(connection, "answer-discovery_source"),
                    allowed)

    def test_the_engine_refuses_an_entry_that_tries_to_enable_submission(self):
        """Not only the plan says no; the two entry types that matter cannot opt in at all."""
        connection = self.db()
        for canonical_id in ("discovery_source", "work_authorized_now"):
            with self.subTest(canonical_id=canonical_id):
                entry = entry_for(canonical_id, answer_type="legal_commitment",
                                  auto_submit_allowed=True)
                with self.assertRaises(ValueError):
                    ANSWERS.add_answer(connection, entry)


if __name__ == "__main__":
    unittest.main()
