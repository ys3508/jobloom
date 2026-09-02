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


def reviewed_questions():
    """Every distinct employer wording in the corpus, with what it was reviewed to mean."""
    seen = {}
    for entry in APPROVAL["entries"]:
        seen.setdefault(entry["recorded_label"], entry["expected_target"])
    return dict(sorted(seen.items()))


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

    def test_an_empty_library_answers_nothing_at_all(self):
        """Every employer question the corpus records, and not one of them understood.

        This is the state an application has been sitting in since 2026-08-28: the fill
        planner asks the library, the library has never seen the wording, and the page
        pauses. Measured rather than assumed, because the whole point of measuring first is
        that the gap list is real.
        """
        measured = self.measure()
        self.assertEqual(set(measured.values()), {"new_question"})
        self.assertEqual(len(measured), 35)

    def test_the_recorded_measurement_matches_what_the_library_actually_says(self):
        """The artifact is regenerated here, so it cannot drift into being a story."""
        measured = self.measure()
        text = ARTIFACT.read_text(encoding="utf-8")
        for question, reason in sorted(measured.items()):
            with self.subTest(question=question):
                self.assertIn(f"| `{reason}` |", text)
        self.assertIn(str(len(measured)), text)

    def test_the_measurement_records_no_answer_value(self):
        """The artifact may name a meaning and a question. Never a value.

        There are no values to leak yet, which is precisely why the constraint is worth
        writing down now: this file is regenerated in the phase that does have them.
        """
        text = ARTIFACT.read_text(encoding="utf-8")
        for forbidden in ("@", "sissi", "linkedin.com/in", "212-", "+1"):
            self.assertNotIn(forbidden, text.lower())


if __name__ == "__main__":
    unittest.main()
