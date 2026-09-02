"""Applying a reviewed question-form manifest to a real library.

A manifest in the repository is a configuration nobody has connected until something reads
it. Before this existed, the only reader was a test: it applied the forms to a temporary
database, measured the improvement, and reported it — while the private library still held
zero forms and the fill planner still answered `new_question` to every question. The
measurement was true about a library nobody uses.

So the import is a command, and these are its terms. Wholly or not at all; idempotent, so a
manifest that grew by one form can be re-applied; strict about shape, provenance and
conflicts, because a half-applied vocabulary is worse than none; and silent about wording in
everything it returns or records, because a vocabulary is only value-free while nobody
copies it somewhere new.
"""

import copy
import importlib.util
import json
import sqlite3
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "jobloom" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("import_answer_library",
                                              SCRIPTS / "answer_library.py")
ANSWERS = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(ANSWERS)

MANIFEST = json.loads((ROOT / "skills" / "jobloom" / "assets" / "question-forms.json")
                      .read_text(encoding="utf-8"))
APPROVAL = json.loads(
    (ROOT / "tests" / "fixtures" / "ats-semantic" / "FIELD-DISPOSITION-APPROVAL.json")
    .read_text(encoding="utf-8"))


class QuestionFormImportTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        ANSWERS.initialize(self.db)
        self.addCleanup(self.db.close)

    def forms(self):
        return self.db.execute("SELECT COUNT(*) FROM question_forms").fetchone()[0]

    def broken(self, **changes):
        manifest = copy.deepcopy(MANIFEST)
        manifest.update(changes)
        return manifest

    def broken_form(self, index=0, **changes):
        manifest = copy.deepcopy(MANIFEST)
        manifest["forms"][index].update(changes)
        return manifest

    # ---- what it does ---------------------------------------------------------

    def test_importing_a_manifest_makes_its_questions_understood(self):
        before = ANSWERS.match_answer(self.db, MANIFEST["forms"][0]["question"], {})
        self.assertEqual(before["reason"], "new_question")
        result = ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL)
        self.assertEqual(result["imported"], len(MANIFEST["forms"]))
        self.assertEqual(result["already_present"], 0)
        after = ANSWERS.match_answer(self.db, MANIFEST["forms"][0]["question"], {})
        self.assertEqual(after["reason"], "no_applicable_answer")
        self.assertEqual(self.forms(), len(MANIFEST["forms"]))
        # A vocabulary, not an answer.
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 0)

    def test_a_second_import_changes_nothing_and_does_not_fail(self):
        """A manifest that grew by one form has to be re-appliable.

        Refusing the whole thing because nine of ten rows are already there would mean the
        only way to add a form is by hand, which is the state this replaces.
        """
        ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL)
        result = ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["already_present"], len(MANIFEST["forms"]))
        self.assertEqual(self.forms(), len(MANIFEST["forms"]))

    def test_a_manifest_that_grew_imports_only_the_new_form(self):
        partial = copy.deepcopy(MANIFEST)
        removed = partial["forms"].pop()
        ANSWERS.import_question_forms(self.db, partial, APPROVAL)
        self.assertEqual(self.forms(), len(MANIFEST["forms"]) - 1)
        result = ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["already_present"], len(MANIFEST["forms"]) - 1)
        self.assertEqual(
            ANSWERS.match_answer(self.db, removed["question"], {})["reason"],
            "no_applicable_answer")

    # ---- wholly or not at all -------------------------------------------------

    def test_a_failure_partway_leaves_the_library_untouched(self):
        """Half a vocabulary is worse than none: it pauses on some questions and not others.

        The failure is injected after several rows have been written, which is exactly where
        a non-transactional import would leave them.
        """
        calls = {"n": 0}
        real_audit = ANSWERS.audit

        def failing_audit(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] > 4:
                raise RuntimeError("interrupted midway")
            return real_audit(*args, **kwargs)

        with unittest.mock.patch.object(ANSWERS, "audit", failing_audit):
            with self.assertRaises(RuntimeError):
                ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL)
        self.assertEqual(self.forms(), 0)

    # ---- shape ----------------------------------------------------------------

    def test_a_manifest_of_an_unexpected_shape_is_refused_whole(self):
        cases = {
            "extra top-level key": self.broken(extra="x"),
            "missing forms": {k: v for k, v in MANIFEST.items() if k != "forms"},
            "no forms at all": self.broken(forms=[]),
            "forms is not a list": self.broken(forms={}),
            "extra form key": self.broken_form(extra="x"),
            "blank question": self.broken_form(question="   "),
            "blank canonical id": self.broken_form(canonical_id=""),
            "non-string meaning": self.broken_form(canonical_meaning=17),
            "no provenance named": self.broken_form(recorded_in=[]),
            "provenance not a list": self.broken_form(recorded_in="ashby"),
        }
        for label, manifest in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    ANSWERS.import_question_forms(self.db, manifest)
                self.assertEqual(self.forms(), 0)

    def test_a_semantic_equivalent_cannot_arrive_in_bulk(self):
        """That claim is one a person makes about one paraphrase at a time."""
        with self.assertRaisesRegex(ValueError, "only exact"):
            ANSWERS.import_question_forms(
                self.db, self.broken_form(match_level="semantic_equivalent"))
        with self.assertRaisesRegex(ValueError, "user-verified"):
            ANSWERS.import_question_forms(self.db, self.broken_form(verified_by_user=False))
        self.assertEqual(self.forms(), 0)

    # ---- conflicts ------------------------------------------------------------

    def test_one_question_mapped_to_two_meanings_is_refused(self):
        manifest = copy.deepcopy(MANIFEST)
        clash = copy.deepcopy(manifest["forms"][0])
        clash["canonical_id"] = "something_else"
        manifest["forms"].append(clash)
        with self.assertRaisesRegex(ValueError, "two meanings"):
            ANSWERS.import_question_forms(self.db, manifest)
        self.assertEqual(self.forms(), 0)

    def test_the_same_form_twice_in_one_manifest_is_refused(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["forms"].append(copy.deepcopy(manifest["forms"][0]))
        with self.assertRaisesRegex(ValueError, "repeated"):
            ANSWERS.import_question_forms(self.db, manifest)
        self.assertEqual(self.forms(), 0)

    def test_a_library_that_already_maps_the_question_elsewhere_is_not_overwritten(self):
        ANSWERS.add_question_form(self.db, "somebody_elses_meaning",
                                  MANIFEST["forms"][0]["question"])
        with self.assertRaisesRegex(ValueError, "already maps"):
            ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL)
        self.assertEqual(self.forms(), 1)

    def test_a_widened_match_level_already_in_the_library_is_a_conflict(self):
        form = MANIFEST["forms"][0]
        ANSWERS.add_question_form(self.db, form["canonical_id"], form["question"],
                                  "semantic_equivalent", True)
        with self.assertRaisesRegex(ValueError, "different form"):
            ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL)
        self.assertEqual(self.forms(), 1)

    # ---- provenance -----------------------------------------------------------

    def test_wording_that_no_recording_contains_is_refused(self):
        """A form no employer ships matches nothing, so importing one is worse than useless."""
        with self.assertRaisesRegex(ValueError, "not recorded employer wording"):
            ANSWERS.import_question_forms(
                self.db, self.broken_form(question="Are you a self-starter?"), APPROVAL)
        self.assertEqual(self.forms(), 0)

    def test_naming_the_wrong_recording_is_refused(self):
        with self.assertRaisesRegex(ValueError, "wrong recording provenance"):
            ANSWERS.import_question_forms(
                self.db, self.broken_form(recorded_in=["a-fixture-that-did-not-record-it"]),
                APPROVAL)
        self.assertEqual(self.forms(), 0)

    def test_provenance_is_optional_so_a_library_can_be_seeded_without_the_corpus(self):
        result = ANSWERS.import_question_forms(self.db, MANIFEST)
        self.assertEqual(result["imported"], len(MANIFEST["forms"]))

    # ---- nothing is written by accident, and nothing is echoed ----------------

    def test_connecting_to_a_library_imports_nothing(self):
        """A file in a checkout is not a person deciding to trust it."""
        fresh = sqlite3.connect(":memory:")
        fresh.row_factory = sqlite3.Row
        ANSWERS.initialize(fresh)
        self.addCleanup(fresh.close)
        self.assertEqual(
            fresh.execute("SELECT COUNT(*) FROM question_forms").fetchone()[0], 0)

    def test_the_result_names_meanings_and_counts_but_never_wording(self):
        result = ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL)
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertEqual(set(result), {"imported", "already_present", "canonical_ids"})
        for form in MANIFEST["forms"]:
            self.assertNotIn(form["question"], rendered)
        self.assertEqual(set(result["canonical_ids"]),
                         {form["canonical_id"] for form in MANIFEST["forms"]})

    def test_the_audit_log_hashes_the_question_rather_than_keeping_it(self):
        """An audit log is not a place to accumulate a second copy of the vocabulary."""
        ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL)
        logged = "\n".join(
            row["metadata_json"] for row in
            self.db.execute("SELECT metadata_json FROM audit_events"))
        for form in MANIFEST["forms"]:
            self.assertNotIn(form["question"], logged)
        self.assertIn("question_hash", logged)


if __name__ == "__main__":
    unittest.main()
