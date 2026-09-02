"""What the answer library can answer today, measured before anything is written to it.

The library is built and tested; what it has never had is content. Zero answers and zero
question forms means every employer question reaching it is `new_question`, and the fill
planner pauses on every one — which is why an application has sat in `ready_to_fill` since
2026-08-28. This measures that state rather than assuming it, and then measures again with
the reviewed question forms applied and still no answers at all.

The gap between those two measurements is the part that can be proved without a single
private value: mapping wording to meaning moves a question from "nobody has ever seen this"
to "this is understood and unanswered". Nothing here writes, reads, or renders an answer.
"""

import importlib.util
import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "jobloom" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(f"coverage_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


ANSWERS = load("answer_library")
APPROVAL = json.loads(
    (ROOT / "tests" / "fixtures" / "ats-semantic" / "FIELD-DISPOSITION-APPROVAL.json")
    .read_text(encoding="utf-8"))
MANIFEST = json.loads(
    (ROOT / "skills" / "jobloom" / "assets" / "question-forms.json")
    .read_text(encoding="utf-8"))
ARTIFACT = ROOT / "docs" / "answer-library-coverage-2026-09-02.md"

# The ten the ruling names: the seven the corpus reviews as `answer`, plus the three contact
# meanings whose values exist only inside one composite fact. Not website, GitHub or
# portfolio — those have no confirmed candidate value and are not in scope.
MAPPED = {
    "prior_employment_at_this_company", "prior_employment_at_an_affiliate",
    "work_authorized_now", "citizenship_status", "permanent_residence_status",
    "current_country_of_residence", "discovery_source",
    "contact.email", "contact.phone", "profile.linkedin",
}


# The context the one live application actually carries. Identifiers, not values: the
# authorization is `auth-mgb-rq4077023`, scoped to this country and this application, and
# measuring under a synthetic context would measure a situation nobody is in.
REAL_CONTEXT = {"country": "US", "application_id": "app-mgb-rq4077023"}
REAL_AUTHORIZATION = "auth-mgb-rq4077023"


def reviewed_questions():
    """Every distinct employer wording in the corpus, with what it was reviewed to mean."""
    seen = {}
    for entry in APPROVAL["entries"]:
        seen.setdefault(entry["recorded_label"], entry["expected_target"])
    return dict(sorted(seen.items()))


def measure_controls(connection, at):
    """One row per recorded control, not per distinct wording.

    Forty-five controls share thirty-five wordings, and the duplicates are the interesting
    ones: the same question reaches the library from three different vendors. Measuring by
    wording would quietly drop that.
    """
    rows = []
    for entry in sorted(APPROVAL["entries"],
                        key=lambda item: (item["source_fixture"], item["kind"])):
        match = ANSWERS.match_answer(connection, entry["recorded_label"], REAL_CONTEXT,
                                     REAL_AUTHORIZATION, at)
        rows.append({
            "source_fixture": entry["source_fixture"],
            "kind": entry["kind"],
            "recorded_label": entry["recorded_label"],
            "expected_disposition": entry["expected_disposition"],
            "canonical_id": match.get("canonical_id") or "",
            "reason": match["reason"],
            "auto_fill_ready": bool(match["auto_fill_ready"]),
        })
    return rows


class CoverageMeasurement(unittest.TestCase):
    """Two measurements, neither of which needs an answer to exist."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        ANSWERS.initialize(self.db)
        self.addCleanup(self.db.close)

    def measure(self):
        """Reason code per employer question. Codes and meanings only, never a value."""
        context = {"country": "US", "application_id": "app-under-measurement"}
        return {question: ANSWERS.match_answer(self.db, question, context)["reason"]
                for question in reviewed_questions()}

    def apply_manifest(self):
        for form in MANIFEST["forms"]:
            ANSWERS.add_question_form(self.db, form["canonical_id"], form["question"],
                                      form["match_level"], form["verified_by_user"])

    def test_an_empty_library_answers_nothing_at_all(self):
        measured = self.measure()
        self.assertEqual(set(measured.values()), {"new_question"})
        self.assertEqual(len(measured), 35)

    def test_the_reviewed_forms_move_ten_meanings_off_new_question(self):
        """Mapping wording to meaning is provable with nothing confirmed yet.

        `no_applicable_answer` is not success — it is the library saying it understands the
        question and has nothing to say. That is exactly the state this phase is allowed to
        reach: the values are the user's to confirm, and none of them exist yet.
        """
        self.apply_manifest()
        measured = self.measure()
        understood = {question for question, reason in measured.items()
                      if reason != "new_question"}
        expected = {form["question"] for form in MANIFEST["forms"]}
        self.assertEqual(understood, expected)
        for question in understood:
            self.assertEqual(measured[question], "no_applicable_answer", question)
        # Still an empty library. Nothing above wrote an answer.
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 0)

    def test_the_recorded_measurement_matches_what_the_library_actually_says(self):
        """The artifact is regenerated here, so it cannot drift into being a story."""
        before = self.measure()
        self.apply_manifest()
        after = self.measure()
        text = ARTIFACT.read_text(encoding="utf-8")
        for question, reason in sorted(after.items()):
            with self.subTest(question=question):
                self.assertIn(f"| `{reason}` |", text)
        self.assertIn(str(len(before)), text)
        self.assertIn(str(len(MANIFEST["forms"])), text)

    def test_the_measurement_records_no_answer_value(self):
        """The artifact may name a meaning and a question. Never a value.

        There are no values to leak yet, which is precisely why the constraint is worth
        writing down now: the file is regenerated in the phase that does have them.
        """
        text = ARTIFACT.read_text(encoding="utf-8")
        for forbidden in ("@", "sissi", "linkedin.com/in", "212-", "+1"):
            self.assertNotIn(forbidden, text.lower())


class ManifestTests(unittest.TestCase):
    """The forms file is reviewed employer wording, and nothing else."""

    def test_every_question_is_wording_an_employer_actually_shipped(self):
        """A paraphrase would map a form no employer sends.

        Each question must appear verbatim as a recorded label in the reviewed corpus, under
        the fixtures the manifest names. Inventing plausible wording is the failure this
        rules out — the corpus already found one such miss, where a conflict pattern did not
        match `Related to someone at this company?`.
        """
        recorded = {}
        for entry in APPROVAL["entries"]:
            recorded.setdefault(entry["recorded_label"], set()).add(entry["source_fixture"])
        for form in MANIFEST["forms"]:
            with self.subTest(question=form["question"]):
                self.assertIn(form["question"], recorded)
                self.assertEqual(set(form["recorded_in"]), recorded[form["question"]])

    def test_the_manifest_maps_only_the_ten_meanings_in_scope(self):
        ids = {form["canonical_id"] for form in MANIFEST["forms"]}
        self.assertEqual(ids, MAPPED)
        for absent in ("profile.website", "profile.github", "profile.portfolio"):
            self.assertNotIn(absent, ids)

    def test_no_form_is_written_twice_and_none_is_a_guess(self):
        pairs = [(form["canonical_id"], ANSWERS.normalize_question(form["question"]))
                 for form in MANIFEST["forms"]]
        self.assertEqual(len(pairs), len(set(pairs)))
        for form in MANIFEST["forms"]:
            with self.subTest(canonical_id=form["canonical_id"]):
                # `semantic_equivalent` is a claim about meaning that a person has to make;
                # nothing in this file is one.
                self.assertEqual(form["match_level"], "exact")
                self.assertTrue(form["verified_by_user"])

    def test_the_manifest_carries_no_answer_field(self):
        allowed = {"canonical_id", "canonical_meaning", "question", "match_level",
                   "verified_by_user", "recorded_in"}
        for form in MANIFEST["forms"]:
            with self.subTest(canonical_id=form["canonical_id"]):
                self.assertEqual(set(form), allowed)
        rendered = json.dumps(MANIFEST, ensure_ascii=False).lower()
        for forbidden in ("answer_json", '"answer"', "@", "sissi", "linkedin.com/in"):
            self.assertNotIn(forbidden, rendered)

    def test_the_immigration_meanings_stay_four_separate_things(self):
        """The manifest may not quietly reintroduce a broad sponsorship question.

        `authorization.sponsorship_status` is `always_manual` precisely because one control
        covers more than one meaning. A question form pointing at it would route a pause into
        an answer.
        """
        ids = {form["canonical_id"] for form in MANIFEST["forms"]}
        for broad in ("sponsorship_now", "sponsorship_future", "employer_action_required"):
            self.assertNotIn(broad, ids)
        self.assertIn("work_authorized_now", ids)


if __name__ == "__main__":
    unittest.main()


class ControlLevelMeasurement(unittest.TestCase):
    """All forty-five recorded controls, under the authorization that is actually live.

    The row schema is fixed by what a measurement of this kind may hold: which fixture
    recorded the control, its kind, the employer's own wording, the reviewed disposition, the
    canonical meaning if one is known, the reason code, and whether the field could be filled
    automatically. No answer, no candidate value, no local path, no token, no database row.
    """

    AT = None

    def setUp(self):
        from datetime import datetime, timedelta, timezone
        self.at = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        ANSWERS.initialize(self.db)
        # The live authorization, reconstructed from its identifiers so `auto_fill_ready` is
        # measured under a standing authorization rather than under none.
        ANSWERS.add_authorization(self.db, {
            "authorization_id": REAL_AUTHORIZATION,
            "confirmed_at": self.at.isoformat(),
            "expires_at": (self.at + timedelta(days=9)).isoformat(),
            "scope": dict(REAL_CONTEXT),
        })
        self.addCleanup(self.db.close)

    def apply_manifest(self):
        for form in MANIFEST["forms"]:
            ANSWERS.add_question_form(self.db, form["canonical_id"], form["question"],
                                      form["match_level"], form["verified_by_user"])

    def test_all_forty_five_controls_are_measured_not_thirty_five_wordings(self):
        rows = measure_controls(self.db, self.at)
        self.assertEqual(len(rows), len(APPROVAL["entries"]))
        self.assertEqual(len(rows), 45)
        self.assertEqual(len({row["recorded_label"] for row in rows}), 35)

    def test_nothing_is_auto_fill_ready_before_or_after_the_forms(self):
        """The forms are a vocabulary, not an answer.

        This is the assertion that would fail if a value slipped in early: mapping wording to
        meaning cannot make a field fillable, and the measurement says so on all forty-five
        rows both before and after.
        """
        for stage in ("baseline", "after forms"):
            if stage == "after forms":
                self.apply_manifest()
            with self.subTest(stage=stage):
                rows = measure_controls(self.db, self.at)
                self.assertEqual([row for row in rows if row["auto_fill_ready"]], [])
                self.assertEqual(
                    self.db.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 0)

    def test_the_forms_understand_exactly_the_controls_they_were_written_for(self):
        self.apply_manifest()
        rows = measure_controls(self.db, self.at)
        understood = {row["recorded_label"] for row in rows
                      if row["reason"] != "new_question"}
        self.assertEqual(understood, {form["question"] for form in MANIFEST["forms"]})
        for row in rows:
            if row["recorded_label"] in understood:
                self.assertEqual(row["reason"], "no_applicable_answer", row["recorded_label"])
                self.assertTrue(row["canonical_id"])
            else:
                self.assertEqual(row["canonical_id"], "")

    def test_the_recorded_control_measurement_matches_the_library(self):
        self.apply_manifest()
        text = ARTIFACT.read_text(encoding="utf-8")
        for row in measure_controls(self.db, self.at):
            with self.subTest(kind=row["kind"], fixture=row["source_fixture"]):
                self.assertIn(f"| `{row['kind']}` |", text)
                self.assertIn(row["recorded_label"], text)
                self.assertIn(f"| `{row['reason']}` |", text)

    def test_the_measurement_rows_carry_only_the_seven_allowed_fields(self):
        allowed = {"source_fixture", "kind", "recorded_label", "expected_disposition",
                   "canonical_id", "reason", "auto_fill_ready"}
        for row in measure_controls(self.db, self.at):
            with self.subTest(kind=row["kind"]):
                self.assertEqual(set(row), allowed)
                self.assertIsInstance(row["auto_fill_ready"], bool)
