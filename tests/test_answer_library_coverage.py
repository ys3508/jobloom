"""What the answer library can answer, measured before anything is confirmed into it.

The library is built and tested; what it has never had is content. Zero question forms and
zero answers means every employer question reaching it is `new_question`, and the fill
planner pauses on every one — which is why one application has sat in `ready_to_fill` since
2026-08-28. This measures that, then measures again with the reviewed question forms applied
and still no answers at all.

The gap between those two measurements is the part that can be proved without a single
private value. Nothing here writes, reads, or renders an answer.

**These measurements run against a temporary library, not the private one.** They say what
the forms do when applied; they do not say that anything has been applied to `.jobloom/`.
Importing them there is a command a person runs, and the count that matters afterwards is the
one that command reports.
"""

import importlib.util
import json
import os
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

# The scope the one live application carries. Identifiers, not values: measuring under a
# synthetic context would measure a situation nobody is in. The authorization is
# reconstructed from these in `library()` so `auto_fill_ready` is measured under a standing
# authorization rather than under none.
REAL_CONTEXT = {"country": "US", "application_id": "app-mgb-rq4077023"}
REAL_AUTHORIZATION = "auth-mgb-rq4077023"
AT = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

MAPPED = {
    "prior_employment_at_this_company", "prior_employment_at_an_affiliate",
    "work_authorized_now", "citizenship_status", "permanent_residence_status",
    "current_country_of_residence", "discovery_source",
    "contact.email", "contact.phone", "profile.linkedin",
}


def reviewed_questions():
    """Every distinct employer wording, with the meaning it was reviewed to have.

    A conflict is raised rather than resolved. `setdefault` would keep whichever row was read
    first and drop the other, so one wording reviewed as two different meanings — the exact
    thing `match_answer` reports as `question_mapping_conflict` — would leave no trace here
    at all.
    """
    seen: dict[str, str] = {}
    for entry in APPROVAL["entries"]:
        label, target = entry["recorded_label"], entry["expected_target"]
        if label in seen and seen[label] != target:
            raise AssertionError(
                f"one recorded wording reviewed as two meanings: {label!r} is "
                f"{seen[label]!r} and {target!r}")
        seen[label] = target
    return dict(sorted(seen.items()))


def library():
    """A temporary library holding the live authorization and nothing else."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ANSWERS.initialize(connection)
    ANSWERS.add_authorization(connection, {
        "authorization_id": REAL_AUTHORIZATION, "confirmed_at": AT.isoformat(),
        "expires_at": (AT + timedelta(days=9)).isoformat(), "scope": dict(REAL_CONTEXT)})
    return connection


def measure_controls(connection):
    """One row per recorded control, not per distinct wording.

    Forty-five controls share thirty-five wordings, and the duplicates are the interesting
    ones: the same question reaches the library from three different vendors. Measuring by
    wording would quietly drop that.
    """
    rows = []
    for entry in sorted(APPROVAL["entries"],
                        key=lambda item: (item["source_fixture"], item["kind"])):
        match = ANSWERS.match_answer(connection, entry["recorded_label"], REAL_CONTEXT,
                                     REAL_AUTHORIZATION, AT)
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


def both_measurements():
    """Baseline and after-forms, each from its own library so neither can leak into the other."""
    baseline = measure_controls(library())
    connection = library()
    for form in MANIFEST["forms"]:
        ANSWERS.add_question_form(connection, form["canonical_id"], form["question"],
                                  form["match_level"], form["verified_by_user"])
    after = measure_controls(connection)
    written = connection.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
    return baseline, after, written


def render_artifact(baseline, after, answers_written):
    """The whole document, deterministically, from the measurement itself.

    The earlier version of the check read the committed file and asked whether each reason
    string appeared *somewhere* in it. Reason codes repeat across forty-five rows, so rows
    could be misaligned, missing or stale and every assertion would still pass. Rendering the
    document here and comparing it whole is the only version of this check that can fail.
    """
    labels = {row["recorded_label"] for row in baseline}
    moved = sum(1 for before, now in zip(baseline, after)
                if before["reason"] != now["reason"])
    still_new = sum(1 for row in after if row["reason"] == "new_question")
    lines = [
        "# Answer-library coverage, measured 2026-09-02",
        "",
        "What the answer library can answer, measured twice: against an empty library, and",
        "with the reviewed question forms applied and still no answers written. Rendered and",
        "compared whole by `tests/test_answer_library_coverage.py`, so it cannot drift.",
        "",
        "Measured under the scope the one live application carries —",
        f"`{{country: {REAL_CONTEXT['country']}, application_id: {REAL_CONTEXT['application_id']}}}`,",
        f"authorization `{REAL_AUTHORIZATION}` — because a synthetic context would measure a",
        "situation nobody is in.",
        "",
        "**This measures a temporary library, not the private one.** It says what the forms do",
        "when applied. Applying them to `.jobloom/` is `answer_library.py",
        "import-question-forms`, a command a person runs, and the count that matters afterwards",
        "is the one that command reports.",
        "",
        "**Value-free by construction.** Each row carries source fixture, kind, recorded",
        "employer wording, reviewed disposition, canonical meaning, reason code and",
        "`auto_fill_ready`. No answer, no candidate value, no local path, no token, no database",
        "row.",
        "",
        "## What was measured",
        "",
        f"- {len(baseline)} recorded controls, sharing {len(labels)} distinct wordings. The same",
        "  question reaches the library from three vendors, and measuring by wording would drop",
        "  that.",
        f"- {len(MANIFEST['forms'])} reviewed question forms covering "
        f"{len({form['canonical_id'] for form in MANIFEST['forms']})} canonical meanings.",
        f"- Answers written at any point: **{answers_written}**.",
        "",
        "## Result",
        "",
        "`new_question` — nothing has ever mapped this wording to a meaning; the planner pauses.",
        "`no_applicable_answer` — the meaning is understood and the user has not answered it yet.",
        "The second state is the whole of what this phase can reach without a private value.",
        "",
    ]
    for fixture in sorted({row["source_fixture"] for row in baseline}):
        lines += [
            f"### `{fixture}`",
            "",
            "| kind | recorded wording | disposition | canonical | baseline | after forms "
            "| auto_fill_ready |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for before, now in zip(baseline, after):
            if before["source_fixture"] != fixture:
                continue
            canonical = f"`{now['canonical_id']}`" if now["canonical_id"] else "—"
            lines.append(
                f"| `{before['kind']}` | {before['recorded_label']} | "
                f"`{before['expected_disposition']}` | {canonical} | `{before['reason']}` | "
                f"`{now['reason']}` | {str(now['auto_fill_ready']).lower()} |")
        lines.append("")
    lines += [
        "## What changed, and what did not",
        "",
        f"- {moved} of {len(after)} controls moved from `new_question` to "
        "`no_applicable_answer`.",
        f"- {still_new} still read `new_question`. That is not a backlog: most are reviewed as",
        "  `always_manual` or as facts, and pausing on them is the correct outcome.",
        "- Nothing became fillable. No answer exists.",
        "",
        "## What this does not show",
        "",
        "- It does not show that anything has been applied to the private library. That is a",
        "  separate, explicit command.",
        "- It does not show that any question can be **answered**. That needs values the user",
        "  confirms one at a time, and those live only in `.jobloom/`.",
        "- It does not show that a real ATS page presents this wording. The corpus is a reviewed",
        "  semantic model of real recordings, not current employer DOM.",
        "- It does not show that a production observer can find these fields. There is none.",
        "",
    ]
    return "\n".join(lines) + "\n"


class ReviewedCorpusTests(unittest.TestCase):
    def test_no_wording_is_reviewed_as_two_different_meanings(self):
        questions = reviewed_questions()
        self.assertEqual(len(questions), 35)
        self.assertEqual(len(set(questions)), 35)

    def test_a_conflicting_review_would_be_raised_not_swallowed(self):
        """The guard, exercised. Without it `setdefault` keeps one and drops the other."""
        original = APPROVAL["entries"]
        clash = dict(original[0])
        clash["expected_target"] = "something_else_entirely"
        try:
            APPROVAL["entries"] = original + [clash]
            with self.assertRaisesRegex(AssertionError, "two meanings"):
                reviewed_questions()
        finally:
            APPROVAL["entries"] = original


class ControlLevelMeasurement(unittest.TestCase):
    """All forty-five recorded controls, under the scope that is actually live."""

    def test_all_forty_five_controls_are_measured_not_thirty_five_wordings(self):
        baseline, after, _ = both_measurements()
        self.assertEqual(len(baseline), len(APPROVAL["entries"]))
        self.assertEqual(len(baseline), 45)
        self.assertEqual(len(after), 45)
        self.assertEqual(len({row["recorded_label"] for row in baseline}), 35)

    def test_an_empty_library_answers_nothing_at_all(self):
        baseline, _, _ = both_measurements()
        self.assertEqual({row["reason"] for row in baseline}, {"new_question"})

    def test_nothing_is_auto_fill_ready_before_or_after_the_forms(self):
        """Mapping wording to meaning cannot make a field fillable, and says so on every row."""
        baseline, after, written = both_measurements()
        self.assertEqual([row for row in baseline if row["auto_fill_ready"]], [])
        self.assertEqual([row for row in after if row["auto_fill_ready"]], [])
        self.assertEqual(written, 0)

    def test_the_forms_understand_exactly_the_controls_they_were_written_for(self):
        _, after, _ = both_measurements()
        understood = {row["recorded_label"] for row in after
                      if row["reason"] != "new_question"}
        self.assertEqual(understood, {form["question"] for form in MANIFEST["forms"]})
        for row in after:
            if row["recorded_label"] in understood:
                self.assertEqual(row["reason"], "no_applicable_answer", row["recorded_label"])
                self.assertIn(row["canonical_id"], MAPPED)
            else:
                self.assertEqual(row["canonical_id"], "")

    def test_the_measurement_rows_carry_only_the_seven_allowed_fields(self):
        allowed = {"source_fixture", "kind", "recorded_label", "expected_disposition",
                   "canonical_id", "reason", "auto_fill_ready"}
        baseline, after, _ = both_measurements()
        for row in baseline + after:
            with self.subTest(kind=row["kind"]):
                self.assertEqual(set(row), allowed)
                self.assertIsInstance(row["auto_fill_ready"], bool)


class ArtifactTests(unittest.TestCase):
    """The committed document is the rendered measurement, byte for byte."""

    def test_the_committed_artifact_is_exactly_what_the_measurement_renders(self):
        expected = render_artifact(*both_measurements())
        if os.environ.get("JOBLOOM_REGENERATE"):  # pragma: no cover - maintenance path
            ARTIFACT.write_text(expected, encoding="utf-8")
        self.assertEqual(
            ARTIFACT.read_text(encoding="utf-8"), expected,
            "the artifact is stale; regenerate with JOBLOOM_REGENERATE=1")

    def test_every_wording_appears_once_with_the_reasons_it_was_measured_with(self):
        """Parsed back per row, keyed by wording, so a misaligned row cannot hide.

        Byte equality already catches this. Reading the table back and comparing tuples
        catches the other direction: a document that was regenerated correctly from a
        measurement that had itself gone wrong.
        """
        baseline, after, _ = both_measurements()
        expected = {}
        for before, now in zip(baseline, after):
            key = before["recorded_label"]
            value = (now["canonical_id"], before["reason"], now["reason"])
            if key in expected:
                self.assertEqual(expected[key], value, key)
            expected[key] = value
        parsed = {}
        for line in ARTIFACT.read_text(encoding="utf-8").splitlines():
            if not line.startswith("| `") or line.startswith("| --- "):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            _, label, _, canonical, before_reason, after_reason, _ = cells
            key = label
            value = ("" if canonical == "—" else canonical.strip("`"),
                     before_reason.strip("`"), after_reason.strip("`"))
            if key in parsed:
                self.assertEqual(parsed[key], value, key)
            parsed[key] = value
        self.assertEqual(parsed, expected)
        self.assertEqual(len(parsed), 35)

    def test_the_artifact_records_no_answer_value(self):
        text = ARTIFACT.read_text(encoding="utf-8").lower()
        for forbidden in ("@", "sissi", "linkedin.com/in", "212-", "/users/"):
            self.assertNotIn(forbidden, text)


class ManifestTests(unittest.TestCase):
    """The forms file is reviewed employer wording, and nothing else."""

    def test_every_question_is_wording_an_employer_actually_shipped(self):
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
        ids = {form["canonical_id"] for form in MANIFEST["forms"]}
        for broad in ("sponsorship_now", "sponsorship_future", "employer_action_required"):
            self.assertNotIn(broad, ids)
        self.assertIn("work_authorized_now", ids)


if __name__ == "__main__":
    unittest.main()
