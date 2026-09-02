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
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "ats-semantic" / "upstream"
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
        result = ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, FIXTURE_ROOT)
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
        ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, FIXTURE_ROOT)
        result = ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, FIXTURE_ROOT)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["already_present"], len(MANIFEST["forms"]))
        self.assertEqual(self.forms(), len(MANIFEST["forms"]))

    def test_a_manifest_that_grew_imports_only_the_new_form(self):
        partial = copy.deepcopy(MANIFEST)
        removed = partial["forms"].pop()
        ANSWERS.import_question_forms(self.db, partial, APPROVAL, FIXTURE_ROOT)
        self.assertEqual(self.forms(), len(MANIFEST["forms"]) - 1)
        result = ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, FIXTURE_ROOT)
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
                ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, FIXTURE_ROOT)
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
                    ANSWERS.import_question_forms(self.db, manifest, APPROVAL, FIXTURE_ROOT)
                self.assertEqual(self.forms(), 0)

    def test_a_semantic_equivalent_cannot_arrive_in_bulk(self):
        """That claim is one a person makes about one paraphrase at a time."""
        with self.assertRaisesRegex(ValueError, "only exact"):
            ANSWERS.import_question_forms(
                self.db, self.broken_form(match_level="semantic_equivalent"),
                APPROVAL, FIXTURE_ROOT)
        with self.assertRaisesRegex(ValueError, "user-verified"):
            ANSWERS.import_question_forms(
                self.db, self.broken_form(verified_by_user=False), APPROVAL, FIXTURE_ROOT)
        self.assertEqual(self.forms(), 0)

    # ---- conflicts ------------------------------------------------------------

    def test_one_question_mapped_to_two_meanings_is_refused(self):
        manifest = copy.deepcopy(MANIFEST)
        clash = copy.deepcopy(manifest["forms"][0])
        clash["canonical_id"] = "something_else"
        manifest["forms"].append(clash)
        with self.assertRaisesRegex(ValueError, "two meanings"):
            ANSWERS.import_question_forms(self.db, manifest, APPROVAL, FIXTURE_ROOT)
        self.assertEqual(self.forms(), 0)

    def test_the_same_form_twice_in_one_manifest_is_refused(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["forms"].append(copy.deepcopy(manifest["forms"][0]))
        with self.assertRaisesRegex(ValueError, "repeated"):
            ANSWERS.import_question_forms(self.db, manifest, APPROVAL, FIXTURE_ROOT)
        self.assertEqual(self.forms(), 0)

    def test_a_library_that_already_maps_the_question_elsewhere_is_not_overwritten(self):
        ANSWERS.add_question_form(self.db, "somebody_elses_meaning",
                                  MANIFEST["forms"][0]["question"])
        with self.assertRaisesRegex(ValueError, "already maps"):
            ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, FIXTURE_ROOT)
        self.assertEqual(self.forms(), 1)

    def test_a_widened_match_level_already_in_the_library_is_a_conflict(self):
        form = MANIFEST["forms"][0]
        ANSWERS.add_question_form(self.db, form["canonical_id"], form["question"],
                                  "semantic_equivalent", True)
        with self.assertRaisesRegex(ValueError, "different form"):
            ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, FIXTURE_ROOT)
        self.assertEqual(self.forms(), 1)

    # ---- provenance -----------------------------------------------------------

    def test_wording_that_no_recording_contains_is_refused(self):
        """A form no employer ships matches nothing, so importing one is worse than useless."""
        with self.assertRaisesRegex(ValueError, "not recorded employer wording"):
            ANSWERS.import_question_forms(
                self.db, self.broken_form(question="Are you a self-starter?"),
                APPROVAL, FIXTURE_ROOT)
        self.assertEqual(self.forms(), 0)

    def test_naming_the_wrong_recording_is_refused(self):
        with self.assertRaisesRegex(ValueError, "reviewed provenance"):
            ANSWERS.import_question_forms(
                self.db, self.broken_form(recorded_in=["a-fixture-that-did-not-record-it"]),
                APPROVAL, FIXTURE_ROOT)
        self.assertEqual(self.forms(), 0)

    def test_provenance_cannot_be_omitted(self):
        """It was optional, which made the whole check decorative.

        Any file in a checkout that called itself `exact` and `verified_by_user` could write
        arbitrary mappings in bulk, and a manifest saying so is not a person confirming it.
        Omitting it is now a `TypeError` at the call rather than a quiet permission.
        """
        with self.assertRaises(TypeError):
            ANSWERS.import_question_forms(self.db, MANIFEST)
        self.assertEqual(self.forms(), 0)

    def test_the_right_wording_under_the_wrong_meaning_is_refused(self):
        """The check that was missing entirely.

        Provenance compared the label and the set of fixtures that recorded it, which proves
        the wording is real and proves nothing about what it means. `Email address` mapped to
        `work_authorized_now` passed every check: the label existed, the fixtures matched,
        and the mapping was nonsense — and the library would have gone on to answer an
        employer's email field with a work-authorization answer.
        """
        manifest = copy.deepcopy(MANIFEST)
        stolen = next(form for form in manifest["forms"]
                      if form["canonical_id"] == "contact.email")
        stolen["canonical_id"] = "work_authorized_now"
        manifest["forms"] = [
            form for form in manifest["forms"]
            if not (form["canonical_id"] == "work_authorized_now"
                    and form["question"] == "Authorized to work in the United States?")]
        with self.assertRaisesRegex(ValueError, "reviewed provenance"):
            ANSWERS.import_question_forms(self.db, manifest, APPROVAL, FIXTURE_ROOT)
        self.assertEqual(self.forms(), 0)

    def test_provenance_of_an_unexpected_shape_is_refused_whole(self):
        """Read as strictly as the manifest it vouches for.

        An oracle skimmed with `.get("entries", [])` is an oracle that can be replaced by an
        empty object, at which point every question is unrecorded and nothing notices.
        """
        def broken_entry(**changes):
            provenance = copy.deepcopy(APPROVAL)
            provenance["entries"][0].update(changes)
            return provenance

        cases = {
            "not a mapping": [],
            "an empty object": {},
            "no entries key": {k: v for k, v in APPROVAL.items() if k != "entries"},
            "an extra top-level key": {**APPROVAL, "extra": "x"},
            "no entries at all": {**APPROVAL, "entries": []},
            "entries not a list": {**APPROVAL, "entries": {}},
            "an entry that is not a mapping": {**APPROVAL, "entries": ["x"]},
            "an entry missing a field": {
                **APPROVAL,
                "entries": [{k: v for k, v in APPROVAL["entries"][0].items()
                             if k != "expected_target"}]},
            "an entry with an unknown field": broken_entry(invented="x"),
            "a blank recorded label": broken_entry(recorded_label="  "),
            "a non-string target": broken_entry(expected_target=17),
        }
        for label, provenance in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    ANSWERS.import_question_forms(self.db, MANIFEST, provenance, FIXTURE_ROOT)
                self.assertEqual(self.forms(), 0)

    def test_provenance_that_contradicts_itself_is_refused(self):
        """A file settling what wording means may not review one wording as two meanings."""
        provenance = copy.deepcopy(APPROVAL)
        clash = copy.deepcopy(provenance["entries"][0])
        clash["kind"] = "invented.kind"
        clash["expected_target"] = "something_else_entirely"
        provenance["entries"].append(clash)
        with self.assertRaisesRegex(ValueError, "two meanings"):
            ANSWERS.import_question_forms(self.db, MANIFEST, provenance, FIXTURE_ROOT)
        self.assertEqual(self.forms(), 0)

    def test_provenance_that_reviews_one_control_twice_is_refused(self):
        provenance = copy.deepcopy(APPROVAL)
        provenance["entries"].append(copy.deepcopy(provenance["entries"][0]))
        with self.assertRaisesRegex(ValueError, "one control twice"):
            ANSWERS.import_question_forms(self.db, MANIFEST, provenance, FIXTURE_ROOT)
        self.assertEqual(self.forms(), 0)

    def test_every_rejection_leaves_the_library_exactly_as_it_was(self):
        """Transactional in the sense that matters: a refused import is a no-op.

        Checked from a library that already holds forms, because "nothing was written" is
        easy to satisfy from empty and is not what a real refusal has to preserve.
        """
        ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, FIXTURE_ROOT)
        before = self.db.execute(
            "SELECT normalized_question, canonical_id, match_level FROM question_forms "
            "ORDER BY normalized_question").fetchall()
        grown = copy.deepcopy(MANIFEST)
        grown["forms"].append({
            "canonical_id": "work_authorized_now", "canonical_meaning": "m",
            "question": "Do you like puppies?", "match_level": "exact",
            "verified_by_user": True, "recorded_in": ["invented-fixture"]})
        with self.assertRaises(ValueError):
            ANSWERS.import_question_forms(self.db, grown, APPROVAL, FIXTURE_ROOT)
        after = self.db.execute(
            "SELECT normalized_question, canonical_id, match_level FROM question_forms "
            "ORDER BY normalized_question").fetchall()
        self.assertEqual([tuple(row) for row in before], [tuple(row) for row in after])

    def test_a_rejection_names_no_wording_and_no_value(self):
        """A refusal is part of the API too."""
        for manifest, provenance in ((self.broken_form(question="Are you a self-starter?"),
                                      APPROVAL),
                                     (MANIFEST, {**APPROVAL, "entries": []})):
            with self.subTest():
                with self.assertRaises(ValueError) as caught:
                    ANSWERS.import_question_forms(self.db, manifest, provenance, FIXTURE_ROOT)
                message = str(caught.exception)
                for form in MANIFEST["forms"]:
                    self.assertNotIn(form["question"], message)
                self.assertNotIn("self-starter", message)

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
        result = ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, FIXTURE_ROOT)
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertEqual(set(result), {"imported", "already_present", "canonical_ids"})
        for form in MANIFEST["forms"]:
            self.assertNotIn(form["question"], rendered)
        self.assertEqual(set(result["canonical_ids"]),
                         {form["canonical_id"] for form in MANIFEST["forms"]})

    def test_the_audit_log_hashes_the_question_rather_than_keeping_it(self):
        """An audit log is not a place to accumulate a second copy of the vocabulary."""
        ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, FIXTURE_ROOT)
        logged = "\n".join(
            row["metadata_json"] for row in
            self.db.execute("SELECT metadata_json FROM audit_events"))
        for form in MANIFEST["forms"]:
            self.assertNotIn(form["question"], logged)
        self.assertIn("question_hash", logged)


if __name__ == "__main__":
    unittest.main()


class ProvenanceIntegrityTests(unittest.TestCase):
    """The approval has to be about the corpus it names, and about answerable questions."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        ANSWERS.initialize(self.db)
        self.addCleanup(self.db.close)

    def forms(self):
        return self.db.execute("SELECT COUNT(*) FROM question_forms").fetchone()[0]

    # A sentinel rather than None: `None` is itself one of the malformed provenances under
    # test, and a default that swallowed it would have quietly imported the real manifest.
    UNSET = object()

    def refuse(self, manifest=UNSET, provenance=UNSET, fixture_root=UNSET, pattern=""):
        with self.assertRaisesRegex(ValueError, pattern):
            ANSWERS.import_question_forms(
                self.db,
                MANIFEST if manifest is self.UNSET else manifest,
                APPROVAL if provenance is self.UNSET else provenance,
                FIXTURE_ROOT if fixture_root is self.UNSET else fixture_root)
        self.assertEqual(self.forms(), 0)

    def test_the_reviewed_disposition_vocabulary_has_not_drifted(self):
        """Declared locally so the answer library need not import the policy module."""
        policy = importlib.util.spec_from_file_location(
            "intake_field_policy", SCRIPTS / "field_policy.py")
        module = importlib.util.module_from_spec(policy)
        policy.loader.exec_module(module)
        self.assertEqual(ANSWERS.REVIEWED_DISPOSITIONS, module.DISPOSITIONS)
        self.assertTrue(ANSWERS.FORMABLE_DISPOSITIONS < ANSWERS.REVIEWED_DISPOSITIONS)

    def test_a_control_reviewed_as_manual_can_never_become_a_question_form(self):
        """The disposition whose entire purpose is that the question reaches the user.

        Nothing stops a manifest naming an `always_manual` control's wording and its reason
        code as though they were a question and an answerable meaning. What stops it is that
        such a control is not indexed as provenance for a form at all.
        """
        for disposition in ("always_manual", "material"):
            with self.subTest(disposition=disposition):
                entry = next(item for item in APPROVAL["entries"]
                             if item["expected_disposition"] == disposition)
                manifest = copy.deepcopy(MANIFEST)
                manifest["forms"].append({
                    "canonical_id": entry["expected_target"], "canonical_meaning": "m",
                    "question": entry["recorded_label"], "match_level": "exact",
                    "verified_by_user": True,
                    "recorded_in": [entry["source_fixture"]]})
                self.refuse(manifest=manifest, pattern="not recorded employer wording")

    def test_an_approval_that_describes_other_bytes_is_refused(self):
        """The digest is a claim about a recording, so it is checked against the recording."""
        tampered = copy.deepcopy(APPROVAL)
        tampered["entries"][0]["source_fixture_sha256"] = "0" * 64
        self.refuse(provenance=tampered, pattern="different bytes")

    def test_a_digest_that_is_not_a_digest_is_refused(self):
        for bad in ("", "not-a-digest", "A" * 64, "0" * 63, "0" * 65, "g" * 64):
            with self.subTest(digest=bad or "empty"):
                tampered = copy.deepcopy(APPROVAL)
                tampered["entries"][0]["source_fixture_sha256"] = bad
                self.refuse(provenance=tampered, pattern="sha256 digest")

    def test_an_unknown_disposition_is_refused(self):
        tampered = copy.deepcopy(APPROVAL)
        tampered["entries"][0]["expected_disposition"] = "probably_fine"
        self.refuse(provenance=tampered, pattern="unknown disposition")

    def test_an_approval_naming_a_fixture_that_is_not_there_is_refused(self):
        tampered = copy.deepcopy(APPROVAL)
        tampered["entries"][0]["source_fixture"] = "a-corpus-nobody-shipped"
        self.refuse(provenance=tampered, pattern="not present")

    def test_fixtures_that_cannot_be_read_stop_the_import(self):
        """Not an optional check that quietly turns itself off when the corpus is missing."""
        self.refuse(fixture_root=FIXTURE_ROOT / "nowhere", pattern="not readable")
        self.refuse(fixture_root="a string, not a path", pattern="not readable")

    def test_recorded_in_must_be_the_exact_set_that_recorded_the_wording(self):
        """One fewer, one more, or one wrong — each is a different lie about provenance."""
        every = {entry["source_fixture"] for entry in APPROVAL["entries"]}
        # More than one fixture, so "one fewer" is meaningful, and fewer than all, so there
        # is a real fixture left over that did not record it.
        form = next(item for item in MANIFEST["forms"]
                    if 1 < len(item["recorded_in"]) < len(every))
        # A real fixture that did not record this wording, so the extra entry passes the
        # "is this corpus present" check and fails only on the set it claims.
        innocent = sorted(every - set(form["recorded_in"]))[0]
        cases = {
            "one fewer": form["recorded_in"][:-1],
            "one more": form["recorded_in"] + [innocent],
            "one wrong": form["recorded_in"][:-1] + ["a-corpus-nobody-shipped"],
        }
        for label, recorded_in in cases.items():
            with self.subTest(case=label):
                manifest = copy.deepcopy(MANIFEST)
                target = next(item for item in manifest["forms"]
                              if item["question"] == form["question"])
                target["recorded_in"] = recorded_in
                self.refuse(manifest=manifest, pattern="reviewed provenance")

    def test_a_malformed_approval_file_never_reaches_the_library(self):
        """What the CLI hands over when the file on disk is not what it should be."""
        for provenance in (None, [], "", 17, {"entries": None}):
            with self.subTest(provenance=type(provenance).__name__):
                self.refuse(provenance=provenance, pattern="unexpected shape|no reviewed")
