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
import shutil
import json
import sqlite3
import subprocess
import sys
import tempfile
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
CORPUS_ROOT = ROOT / "tests" / "fixtures" / "ats-semantic" / "upstream"
PLAN = json.loads((ROOT / "skills" / "jobloom" / "assets" / "answer-intake-plan.json")
                  .read_text(encoding="utf-8"))
# The permission, taken from the value-free intake plan rather than from a disposition.
PERMITTED = {entry["canonical_id"] for entry in PLAN["entries"]}
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
        result = ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, CORPUS_ROOT, PERMITTED)
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
        ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, CORPUS_ROOT, PERMITTED)
        result = ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, CORPUS_ROOT, PERMITTED)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["already_present"], len(MANIFEST["forms"]))
        self.assertEqual(self.forms(), len(MANIFEST["forms"]))

    def test_a_manifest_that_grew_imports_only_the_new_form(self):
        partial = copy.deepcopy(MANIFEST)
        removed = partial["forms"].pop()
        ANSWERS.import_question_forms(self.db, partial, APPROVAL, CORPUS_ROOT, PERMITTED)
        self.assertEqual(self.forms(), len(MANIFEST["forms"]) - 1)
        result = ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, CORPUS_ROOT, PERMITTED)
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
                ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, CORPUS_ROOT, PERMITTED)
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
                    ANSWERS.import_question_forms(self.db, manifest, APPROVAL, CORPUS_ROOT, PERMITTED)
                self.assertEqual(self.forms(), 0)

    def test_a_semantic_equivalent_cannot_arrive_in_bulk(self):
        """That claim is one a person makes about one paraphrase at a time."""
        with self.assertRaisesRegex(ValueError, "only exact"):
            ANSWERS.import_question_forms(
                self.db, self.broken_form(match_level="semantic_equivalent"),
                APPROVAL, CORPUS_ROOT, PERMITTED)
        with self.assertRaisesRegex(ValueError, "user-verified"):
            ANSWERS.import_question_forms(
                self.db, self.broken_form(verified_by_user=False), APPROVAL, CORPUS_ROOT, PERMITTED)
        self.assertEqual(self.forms(), 0)

    # ---- conflicts ------------------------------------------------------------

    def test_one_question_mapped_to_two_meanings_is_refused(self):
        manifest = copy.deepcopy(MANIFEST)
        clash = copy.deepcopy(manifest["forms"][0])
        clash["canonical_id"] = "something_else"
        manifest["forms"].append(clash)
        with self.assertRaisesRegex(ValueError, "two meanings"):
            ANSWERS.import_question_forms(self.db, manifest, APPROVAL, CORPUS_ROOT, PERMITTED)
        self.assertEqual(self.forms(), 0)

    def test_the_same_form_twice_in_one_manifest_is_refused(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["forms"].append(copy.deepcopy(manifest["forms"][0]))
        with self.assertRaisesRegex(ValueError, "repeated"):
            ANSWERS.import_question_forms(self.db, manifest, APPROVAL, CORPUS_ROOT, PERMITTED)
        self.assertEqual(self.forms(), 0)

    def test_a_library_that_already_maps_the_question_elsewhere_is_not_overwritten(self):
        ANSWERS.add_question_form(self.db, "somebody_elses_meaning",
                                  MANIFEST["forms"][0]["question"])
        with self.assertRaisesRegex(ValueError, "already maps"):
            ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, CORPUS_ROOT, PERMITTED)
        self.assertEqual(self.forms(), 1)

    def test_a_widened_match_level_already_in_the_library_is_a_conflict(self):
        form = MANIFEST["forms"][0]
        ANSWERS.add_question_form(self.db, form["canonical_id"], form["question"],
                                  "semantic_equivalent", True)
        with self.assertRaisesRegex(ValueError, "different form"):
            ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, CORPUS_ROOT, PERMITTED)
        self.assertEqual(self.forms(), 1)

    # ---- provenance -----------------------------------------------------------

    def test_wording_that_no_recording_contains_is_refused(self):
        """A form no employer ships matches nothing, so importing one is worse than useless."""
        with self.assertRaisesRegex(ValueError, "not recorded employer wording"):
            ANSWERS.import_question_forms(
                self.db, self.broken_form(question="Are you a self-starter?"),
                APPROVAL, CORPUS_ROOT, PERMITTED)
        self.assertEqual(self.forms(), 0)

    def test_naming_the_wrong_recording_is_refused(self):
        with self.assertRaisesRegex(ValueError, "reviewed provenance"):
            ANSWERS.import_question_forms(
                self.db, self.broken_form(recorded_in=["a-fixture-that-did-not-record-it"]),
                APPROVAL, CORPUS_ROOT, PERMITTED)
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
            ANSWERS.import_question_forms(self.db, manifest, APPROVAL, CORPUS_ROOT, PERMITTED)
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
                    ANSWERS.import_question_forms(self.db, MANIFEST, provenance, CORPUS_ROOT, PERMITTED)
                self.assertEqual(self.forms(), 0)

    def test_provenance_that_contradicts_itself_is_refused(self):
        """A file settling what wording means may not review one wording as two meanings.

        Reading the corpus now catches this first — an invented control is not recorded
        anywhere — so the two-meanings guard has become defence in depth rather than the
        thing standing between a contradiction and the library. What matters here is that
        the contradiction does not get in, not which guard turns it away.
        """
        provenance = copy.deepcopy(APPROVAL)
        clash = copy.deepcopy(provenance["entries"][0])
        clash["kind"] = "invented.kind"
        clash["expected_target"] = "something_else_entirely"
        provenance["entries"].append(clash)
        with self.assertRaises(ValueError):
            ANSWERS.import_question_forms(self.db, MANIFEST, provenance, CORPUS_ROOT,
                                          PERMITTED)
        self.assertEqual(self.forms(), 0)

    def test_provenance_that_reviews_one_control_twice_is_refused(self):
        provenance = copy.deepcopy(APPROVAL)
        provenance["entries"].append(copy.deepcopy(provenance["entries"][0]))
        with self.assertRaisesRegex(ValueError, "one control twice"):
            ANSWERS.import_question_forms(self.db, MANIFEST, provenance, CORPUS_ROOT, PERMITTED)
        self.assertEqual(self.forms(), 0)

    def test_every_rejection_leaves_the_library_exactly_as_it_was(self):
        """Transactional in the sense that matters: a refused import is a no-op.

        Checked from a library that already holds forms, because "nothing was written" is
        easy to satisfy from empty and is not what a real refusal has to preserve.
        """
        ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, CORPUS_ROOT, PERMITTED)
        before = self.db.execute(
            "SELECT normalized_question, canonical_id, match_level FROM question_forms "
            "ORDER BY normalized_question").fetchall()
        grown = copy.deepcopy(MANIFEST)
        grown["forms"].append({
            "canonical_id": "work_authorized_now", "canonical_meaning": "m",
            "question": "Do you like puppies?", "match_level": "exact",
            "verified_by_user": True, "recorded_in": ["invented-fixture"]})
        with self.assertRaises(ValueError):
            ANSWERS.import_question_forms(self.db, grown, APPROVAL, CORPUS_ROOT, PERMITTED)
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
                    ANSWERS.import_question_forms(self.db, manifest, provenance, CORPUS_ROOT, PERMITTED)
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
        result = ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, CORPUS_ROOT, PERMITTED)
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertEqual(set(result), {"imported", "already_present", "canonical_ids"})
        for form in MANIFEST["forms"]:
            self.assertNotIn(form["question"], rendered)
        self.assertEqual(set(result["canonical_ids"]),
                         {form["canonical_id"] for form in MANIFEST["forms"]})

    def test_the_audit_log_hashes_the_question_rather_than_keeping_it(self):
        """An audit log is not a place to accumulate a second copy of the vocabulary."""
        ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, CORPUS_ROOT, PERMITTED)
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

    def refuse(self, manifest=UNSET, provenance=UNSET, corpus_root=UNSET,
               permitted=UNSET, pattern=""):
        with self.assertRaisesRegex(ValueError, pattern):
            ANSWERS.import_question_forms(
                self.db,
                MANIFEST if manifest is self.UNSET else manifest,
                APPROVAL if provenance is self.UNSET else provenance,
                CORPUS_ROOT if corpus_root is self.UNSET else corpus_root,
                PERMITTED if permitted is self.UNSET else permitted)
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
                # Refused, and by the permission — but that is not the check under test.
                self.refuse(manifest=manifest, pattern="nobody permitted")
                # The floor beneath it, exercised where the permission cannot shadow it:
                # even told this target is allowed, the index refuses to speak for a control
                # reviewed as one that must pause.
                indexed = ANSWERS._provenance_index(
                    APPROVAL, CORPUS_ROOT,
                    set(PERMITTED) | {entry["expected_target"]})
                self.assertNotIn(entry["recorded_label"], indexed)

    def test_an_approval_that_describes_other_bytes_is_refused(self):
        """The digest is a claim about a recording, so it is checked against the recording."""
        tampered = copy.deepcopy(APPROVAL)
        tampered["entries"][0]["source_fixture_sha256"] = "0" * 64
        self.refuse(provenance=tampered, pattern="not match the recorded control")

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
        self.refuse(provenance=tampered, pattern="does not record|not match the recorded")

    def test_fixtures_that_cannot_be_read_stop_the_import(self):
        """Not an optional check that quietly turns itself off when the corpus is missing."""
        self.refuse(corpus_root=CORPUS_ROOT / "nowhere", pattern="not readable")
        self.refuse(corpus_root="a string, not a path", pattern="not readable")

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


class CorpusIdentityTests(unittest.TestCase):
    """The approval has to describe the controls the pinned corpus actually records.

    A digest says a file exists and has not changed. It says nothing about whether the
    approval describes what is inside it — so with every hash correct, an approval could
    rename a control's wording and its role, a manifest could use the renamed wording, and
    the two files would agree with each other about a form no employer ships. Every test
    here keeps the digests honest and moves something else.
    """

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        ANSWERS.initialize(self.db)
        self.addCleanup(self.db.close)
        # A library that already holds the forms, so "unchanged" means what it should.
        ANSWERS.import_question_forms(self.db, MANIFEST, APPROVAL, CORPUS_ROOT, PERMITTED)
        self.before = self.snapshot()

    def snapshot(self):
        return (
            [tuple(row) for row in self.db.execute(
                "SELECT normalized_question, canonical_id, match_level FROM question_forms "
                "ORDER BY normalized_question")],
            [tuple(row) for row in self.db.execute(
                "SELECT event_type, entity_id, metadata_json FROM audit_events "
                "ORDER BY event_id")],
        )

    def refuse(self, provenance=None, manifest=None, corpus_root=None, permitted=None):
        with self.assertRaises(ValueError) as caught:
            ANSWERS.import_question_forms(
                self.db, MANIFEST if manifest is None else manifest,
                APPROVAL if provenance is None else provenance,
                CORPUS_ROOT if corpus_root is None else corpus_root,
                PERMITTED if permitted is None else permitted)
        # Nothing moved: not the forms, and not the audit log either.
        self.assertEqual(self.snapshot(), self.before)
        message = str(caught.exception)
        for form in MANIFEST["forms"]:
            self.assertNotIn(form["question"], message)
        return message

    def entry_for(self, target):
        return next(item for item in APPROVAL["entries"]
                    if item["expected_target"] == target)

    def test_a_rewritten_label_is_refused_though_every_digest_is_correct(self):
        """The hole this class exists for. Nothing else had to change for it to work."""
        provenance = copy.deepcopy(APPROVAL)
        entry = next(item for item in provenance["entries"]
                     if item["expected_target"] == "contact.email")
        entry["recorded_label"] = "Your electronic mail"
        manifest = copy.deepcopy(MANIFEST)
        form = next(item for item in manifest["forms"]
                    if item["canonical_id"] == "contact.email")
        form["question"] = "Your electronic mail"
        form["recorded_in"] = [entry["source_fixture"]]
        self.assertIn("recorded control",
                      self.refuse(provenance=provenance, manifest=manifest))

    def test_a_rewritten_role_is_refused(self):
        provenance = copy.deepcopy(APPROVAL)
        entry = provenance["entries"][0]
        entry["role"] = "combobox" if entry["role"] != "combobox" else "textbox"
        self.assertIn("recorded control", self.refuse(provenance=provenance))

    def test_a_rewritten_kind_is_refused(self):
        provenance = copy.deepcopy(APPROVAL)
        provenance["entries"][0]["kind"] = "contact.invented"
        self.assertIn("does not record", self.refuse(provenance=provenance))

    def test_an_approval_missing_a_recorded_control_is_refused(self):
        """Walking the approval can never notice a control nobody reviewed.

        The same hole the disposition approval itself once had, closed the same way: the
        comparison runs corpus → approval as well as approval → corpus.
        """
        provenance = copy.deepcopy(APPROVAL)
        provenance["entries"].pop()
        self.assertIn("never reviewed", self.refuse(provenance=provenance))

    def test_an_approval_reviewing_a_control_nobody_recorded_is_refused(self):
        provenance = copy.deepcopy(APPROVAL)
        invented = copy.deepcopy(provenance["entries"][0])
        invented["kind"] = "contact.invented"
        provenance["entries"].append(invented)
        self.assertIn("does not record", self.refuse(provenance=provenance))

    def test_one_changed_byte_in_a_recorded_fixture_is_refused(self):
        """The digest still has to match, and now it is compared per control."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "upstream"
            shutil.copytree(CORPUS_ROOT, root)
            victim = next(root.iterdir()) / "fixture.json"
            # A byte inside a recorded label: the document stays valid JSON, so nothing but
            # the comparison against the approval can notice.
            raw = victim.read_bytes()
            victim.write_bytes(raw.replace(b'"Resume"', b'"Resum3"', 1))
            self.assertNotEqual(raw, victim.read_bytes(), "the corpus was not modified")
            self.assertIn("recorded control", self.refuse(corpus_root=root))

    def test_a_fact_nobody_permitted_cannot_become_a_question_form(self):
        """A disposition is a floor, not a permission.

        `answer` and `fact` between them cover every contact fact, every profile URL and
        every employment fact in the corpus. Gating on disposition alone let all of these in
        beside the three contact meanings that were actually reviewed for this task.
        """
        for target in ("contact.full_name", "profile.website", "profile.github",
                       "profile.portfolio", "employment.current_company",
                       "contact.location"):
            with self.subTest(target=target):
                entry = self.entry_for(target)
                manifest = copy.deepcopy(MANIFEST)
                manifest["forms"].append({
                    "canonical_id": target, "canonical_meaning": "m",
                    "question": entry["recorded_label"], "match_level": "exact",
                    "verified_by_user": True,
                    "recorded_in": sorted({
                        item["source_fixture"] for item in APPROVAL["entries"]
                        if item["recorded_label"] == entry["recorded_label"]})})
                self.assertIn("nobody permitted", self.refuse(manifest=manifest))

    def test_the_permitted_set_is_exactly_the_reviewed_intake_plan(self):
        self.assertEqual(PERMITTED, {form["canonical_id"] for form in MANIFEST["forms"]})
        self.assertEqual(len(PERMITTED), 10)
        for absent in ("profile.website", "profile.github", "profile.portfolio",
                       "contact.full_name", "employment.current_company"):
            self.assertNotIn(absent, PERMITTED)

    def test_an_empty_permission_imports_nothing(self):
        self.refuse(permitted=set())

    def test_a_missing_domain_family_or_review_reason_is_refused(self):
        cases = {
            "no domain key": ("expected_domain", None, "pop"),
            "no family key": ("expected_family", None, "pop"),
            "domain without family": ("expected_family", None, "set"),
            "blank domain": ("expected_domain", "  ", "set"),
        }
        for label, (field, value, how) in cases.items():
            with self.subTest(case=label):
                provenance = copy.deepcopy(APPROVAL)
                entry = next(item for item in provenance["entries"]
                             if item["expected_domain"] is not None)
                if how == "pop":
                    entry.pop(field)
                else:
                    entry[field] = value
                self.refuse(provenance=provenance)

    def test_review_reason_belongs_to_exactly_the_entries_that_pause(self):
        dropped = copy.deepcopy(APPROVAL)
        manual = next(item for item in dropped["entries"]
                      if item["expected_disposition"] == "always_manual")
        manual.pop("review_reason")
        self.refuse(provenance=dropped)

        blank = copy.deepcopy(APPROVAL)
        next(item for item in blank["entries"]
             if item["expected_disposition"] == "always_manual")["review_reason"] = "  "
        self.refuse(provenance=blank)

        spurious = copy.deepcopy(APPROVAL)
        next(item for item in spurious["entries"]
             if item["expected_disposition"] != "always_manual")["review_reason"] = "why"
        self.refuse(provenance=spurious)

    def test_an_unrecorded_role_is_refused(self):
        provenance = copy.deepcopy(APPROVAL)
        provenance["entries"][0]["role"] = "slider"
        self.assertIn("unrecorded control role", self.refuse(provenance=provenance))

    def test_blank_top_level_metadata_is_refused(self):
        for field in ("reviewed_at", "reviewed_by", "note"):
            with self.subTest(field=field):
                provenance = copy.deepcopy(APPROVAL)
                provenance[field] = "   "
                self.refuse(provenance=provenance)

    def test_the_recorded_role_vocabulary_has_not_drifted(self):
        replay = importlib.util.spec_from_file_location(
            "import_semantic_replay", SCRIPTS / "semantic_replay.py")
        module = importlib.util.module_from_spec(replay)
        replay.loader.exec_module(module)
        self.assertEqual(ANSWERS.RECORDED_ROLES, set(module.ROLE_CONTROLS))


class _ImportCli:
    """Harness for running the command as it is actually run: real parsing, real files.

    The permission lived in the intake plan for one commit, and the tests of that commit
    checked the committed plan — which of course named the reviewed ten. What they never did
    was hand the command a different plan. Copying the plan, adding `profile.website`, and
    adding the matching reviewed form to the manifest imported eleven forms past every other
    check, because every other check was satisfied: the wording was recorded, the corpus
    matched, the disposition was `fact`. So these run the script.
    """

    SCRIPT = SCRIPTS / "answer_library.py"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db_path = self.root / "library.db"
        self.manifest_path = self.root / "manifest.json"
        self.plan_path = self.root / "plan.json"
        self.write(self.manifest_path, MANIFEST)
        self.write(self.plan_path, PLAN)
        self.approval_path = (ROOT / "tests" / "fixtures" / "ats-semantic"
                              / "FIELD-DISPOSITION-APPROVAL.json")

    def write(self, path, document):
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    def run_import(self):
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), "--db", str(self.db_path),
             "import-question-forms", "--manifest", str(self.manifest_path),
             "--provenance", str(self.approval_path),
             "--intake-plan", str(self.plan_path)],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60)

    def library(self):
        if not self.db_path.exists():
            return [], []
        connection = sqlite3.connect(str(self.db_path))
        try:
            return (connection.execute(
                        "SELECT normalized_question, canonical_id FROM question_forms "
                        "ORDER BY normalized_question").fetchall(),
                    connection.execute(
                        "SELECT event_type, entity_id FROM audit_events "
                        "ORDER BY event_id").fetchall())
        finally:
            connection.close()

    def seed(self):
        finished = self.run_import()
        self.assertEqual(finished.returncode, 0, finished.stderr[-800:])
        return self.library()

    def assert_refused(self, before):
        finished = self.run_import()
        self.assertNotEqual(finished.returncode, 0, finished.stdout)
        self.assertEqual(self.library(), before, "the refusal changed the library")
        rendered = finished.stdout + finished.stderr
        for form in MANIFEST["forms"]:
            self.assertNotIn(form["question"], rendered)
        return rendered

    def reviewed_form(self, target):
        entry = next(item for item in APPROVAL["entries"]
                     if item["expected_target"] == target)
        return {
            "canonical_id": target, "canonical_meaning": "m",
            "question": entry["recorded_label"], "match_level": "exact",
            "verified_by_user": True,
            "recorded_in": sorted({item["source_fixture"] for item in APPROVAL["entries"]
                                   if item["recorded_label"] == entry["recorded_label"]}),
        }

    def test_the_reviewed_artifacts_import_and_then_do_nothing(self):
        before = self.seed()
        self.assertEqual(len(before[0]), 10)
        finished = self.run_import()
        self.assertEqual(finished.returncode, 0, finished.stderr[-800:])
        result = json.loads(finished.stdout)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["already_present"], 10)
        self.assertEqual(self.library(), before)

    def test_a_plan_that_grants_itself_another_fact_is_refused(self):
        """The bypass, run through the command rather than described."""
        for target in ("profile.website", "profile.github", "profile.portfolio",
                       "contact.full_name", "employment.current_company"):
            with self.subTest(target=target):
                self.setUp()
                before = self.seed()
                plan = copy.deepcopy(PLAN)
                plan["entries"].append({**plan["entries"][-1], "canonical_id": target,
                                        "canonical_meaning": "m"})
                manifest = copy.deepcopy(MANIFEST)
                manifest["forms"].append(self.reviewed_form(target))
                self.write(self.plan_path, plan)
                self.write(self.manifest_path, manifest)
                self.assertRegex(self.assert_refused(before),
                                 "reviewed meanings|meaning nobody reviewed")

    def test_a_plan_that_drops_a_meaning_does_not_quietly_import_the_rest(self):
        before = self.seed()
        plan = copy.deepcopy(PLAN)
        dropped = plan["entries"].pop()
        manifest = copy.deepcopy(MANIFEST)
        manifest["forms"] = [form for form in manifest["forms"]
                             if form["canonical_id"] != dropped["canonical_id"]]
        self.write(self.plan_path, plan)
        self.write(self.manifest_path, manifest)
        self.assertRegex(self.assert_refused(before),
                                 "reviewed meanings|meaning nobody reviewed")

    def test_a_plan_carrying_an_answer_or_enabling_submission_is_refused(self):
        cases = {
            "an answer": {"answer": "something the user never said"},
            "automatic submission": {"auto_submit_allowed": True},
            "filling switched off": {"auto_fill_allowed": False},
            "a derived source": {"source_type": "deterministic_derivation"},
        }
        for label, change in cases.items():
            with self.subTest(case=label):
                self.setUp()
                before = self.seed()
                plan = copy.deepcopy(PLAN)
                plan["entries"][0].update(change)
                self.write(self.plan_path, plan)
                self.assert_refused(before)

    def test_a_plan_that_loosens_a_scope_is_refused(self):
        cases = {
            "no application scope": {"scope": {"country": "US"}},
            "another application": {"scope": {"country": "US",
                                              "application_id": "app-somewhere-else"}},
            "not per application": {"validity_class": "stable"},
        }
        for label, change in cases.items():
            with self.subTest(case=label):
                self.setUp()
                before = self.seed()
                plan = copy.deepcopy(PLAN)
                entry = next(item for item in plan["entries"]
                             if item["canonical_id"] == "work_authorized_now")
                entry.update(change)
                self.write(self.plan_path, plan)
                self.assert_refused(before)

    def test_a_discovery_source_that_could_be_derived_is_refused(self):
        before = self.seed()
        plan = copy.deepcopy(PLAN)
        entry = next(item for item in plan["entries"]
                     if item["canonical_id"] == "discovery_source")
        entry["answer_type"] = "stable_fact"
        self.write(self.plan_path, plan)
        self.assert_refused(before)

    def test_a_malformed_plan_is_refused(self):
        cases = {
            "an extra top-level key": {**PLAN, "extra": "x"},
            "no entries key": {k: v for k, v in PLAN.items() if k != "entries"},
            "entries not a list": {**PLAN, "entries": {}},
            "an entry with an extra key": None,
            "a repeated meaning": None,
            "blank application id": {**PLAN, "application_id": "  "},
        }
        cases["an entry with an extra key"] = copy.deepcopy(PLAN)
        cases["an entry with an extra key"]["entries"][0]["invented"] = "x"
        cases["a repeated meaning"] = copy.deepcopy(PLAN)
        cases["a repeated meaning"]["entries"].append(
            copy.deepcopy(cases["a repeated meaning"]["entries"][0]))
        for label, plan in cases.items():
            with self.subTest(case=label):
                self.setUp()
                before = self.seed()
                self.write(self.plan_path, plan)
                self.assert_refused(before)


class PinnedTargetTests(unittest.TestCase):
    def test_the_pinned_set_is_the_ten_that_were_reviewed(self):
        self.assertEqual(set(ANSWERS.TASK14_QUESTION_FORM_TARGETS), {
            "work_authorized_now", "citizenship_status", "permanent_residence_status",
            "current_country_of_residence", "prior_employment_at_this_company",
            "prior_employment_at_an_affiliate", "discovery_source",
            "contact.email", "contact.phone", "profile.linkedin"})

    def test_the_committed_plan_and_manifest_agree_with_the_pinned_set(self):
        self.assertEqual({entry["canonical_id"] for entry in PLAN["entries"]},
                         set(ANSWERS.TASK14_QUESTION_FORM_TARGETS))
        self.assertEqual({form["canonical_id"] for form in MANIFEST["forms"]},
                         set(ANSWERS.TASK14_QUESTION_FORM_TARGETS))

    def test_a_permission_that_is_not_the_reviewed_set_is_refused(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        ANSWERS.initialize(connection)
        self.addCleanup(connection.close)
        widened = set(ANSWERS.TASK14_QUESTION_FORM_TARGETS) | {"profile.website"}
        narrowed = set(ANSWERS.TASK14_QUESTION_FORM_TARGETS) - {"contact.email"}
        for label, permitted in (("widened", widened), ("narrowed", narrowed)):
            with self.subTest(case=label):
                with self.assertRaisesRegex(ValueError, "not the reviewed set"):
                    ANSWERS.import_question_forms(connection, MANIFEST, APPROVAL,
                                                  CORPUS_ROOT, permitted)

    def test_a_permission_that_is_not_a_set_is_a_value_error_not_a_surprise(self):
        """`set("profile.website")` is thirteen letters, which permits nothing very quietly."""
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        ANSWERS.initialize(connection)
        self.addCleanup(connection.close)
        for permitted in ("profile.website", None, 17, ["contact.email"], ()):
            with self.subTest(permitted=type(permitted).__name__):
                with self.assertRaisesRegex(ValueError, "must be a set"):
                    ANSWERS.import_question_forms(connection, MANIFEST, APPROVAL,
                                                  CORPUS_ROOT, permitted)


class ProductionCliTests(_ImportCli, unittest.TestCase):
    """Whether the command can be talked into permitting a meaning nobody reviewed."""


class IntakeShapeCliTests(_ImportCli, unittest.TestCase):
    """The reviewed arrangement, checked through the command that reads it.

    The rules were patched one at a time and the gaps between them were the holes. A plan
    self-consistently moved to another application passed every one of them: the entries
    agreed with the top-level id, which was the only thing anything compared them to. Nothing
    said which application had been reviewed.
    """

    def entry(self, plan, target):
        return next(item for item in plan["entries"]
                    if item["canonical_id"] == target)

    def refuse_plan(self, mutate, expected=""):
        before = self.seed()
        plan = copy.deepcopy(PLAN)
        mutate(plan)
        self.write(self.plan_path, plan)
        rendered = self.assert_refused(before)
        if expected:
            self.assertIn(expected, rendered)

    def test_a_plan_moved_wholesale_to_another_application_is_refused(self):
        """The one that passed. Self-consistency is not the test."""
        def move(plan):
            plan["application_id"] = "app-other"
            for item in plan["entries"]:
                if item["scope"].get("application_id"):
                    item["scope"]["application_id"] = "app-other"
        self.refuse_plan(move, "reviewed application")

    def test_moving_only_the_top_level_application_is_refused(self):
        self.refuse_plan(lambda plan: plan.update({"application_id": "app-other"}),
                         "reviewed application")

    def test_moving_only_one_entry_scope_is_refused(self):
        self.refuse_plan(
            lambda plan: self.entry(plan, "work_authorized_now")["scope"].update(
                {"application_id": "app-other"}),
            "reviewed scope")

    def test_a_scope_carrying_a_key_nobody_reviewed_is_refused(self):
        """Compared as a whole, so an extra key is a difference and not an extra."""
        for target, extra in (("work_authorized_now", {"queue_id": "anything"}),
                              ("contact.email", {"application_id": ANSWERS.TASK14_APPLICATION}),
                              ("discovery_source", {"company": "Example Corp"})):
            with self.subTest(target=target):
                self.setUp()
                self.refuse_plan(
                    lambda plan, t=target, e=extra: self.entry(plan, t)["scope"].update(e),
                    "reviewed scope")

    def test_a_changed_answer_type_is_refused(self):
        cases = {
            "work_authorized_now": "open_text_template",
            "prior_employment_at_this_company": "stable_fact",
            "prior_employment_at_an_affiliate": "stable_fact",
            "contact.phone": "derived_answer",
            "profile.linkedin": "company_specific",
            "discovery_source": "stable_fact",
        }
        for target, answer_type in cases.items():
            with self.subTest(target=target):
                self.setUp()
                self.refuse_plan(
                    lambda plan, t=target, a=answer_type: self.entry(plan, t).update(
                        {"answer_type": a}),
                    "reviewed answer type")

    def test_a_discovery_source_may_be_either_reviewed_type_and_nothing_else(self):
        """The one field with more than one right answer, and still not any answer."""
        before = self.seed()
        for answer_type in ("application_specific", "conditional_preference"):
            with self.subTest(answer_type=answer_type):
                plan = copy.deepcopy(PLAN)
                self.entry(plan, "discovery_source")["answer_type"] = answer_type
                self.write(self.plan_path, plan)
                finished = self.run_import()
                self.assertEqual(finished.returncode, 0, finished.stderr[-400:])
                self.assertEqual(self.library(), before)

    def test_a_loosened_validity_class_is_refused(self):
        for target in ("work_authorized_now", "prior_employment_at_an_affiliate",
                       "discovery_source"):
            with self.subTest(target=target):
                self.setUp()
                self.refuse_plan(
                    lambda plan, t=target: self.entry(plan, t).update(
                        {"validity_class": "stable"}),
                    "reviewed plan: validity_class")
        self.setUp()
        self.refuse_plan(
            lambda plan: self.entry(plan, "contact.email").update(
                {"validity_class": "per_application"}),
            "reviewed plan: validity_class")

    def test_the_recheck_the_engine_enforces_must_be_stated_plainly(self):
        """Only `work_authorized_now` is force-rechecked, and the plan has to say so.

        Claiming it of all ten would read as a stronger guarantee than the engine gives, and
        claiming it of none would lose the one place it is real.
        """
        self.refuse_plan(
            lambda plan: [item.update({"engine_enforced_recheck": True})
                          for item in plan["entries"]],
            "engine_enforced_recheck")
        self.setUp()
        self.refuse_plan(
            lambda plan: self.entry(plan, "work_authorized_now").update(
                {"engine_enforced_recheck": False}),
            "engine_enforced_recheck")
        self.setUp()
        self.refuse_plan(
            lambda plan: self.entry(plan, "citizenship_status").update(
                {"engine_enforced_recheck": 1}))

    def test_blank_prose_is_refused(self):
        for field in ("canonical_meaning", "confirmation"):
            with self.subTest(field=field):
                self.setUp()
                self.refuse_plan(
                    lambda plan, name=field: plan["entries"][0].update({name: "   "}),
                    f"non-empty string: {field}")

    def test_an_unknown_schema_version_is_refused(self):
        self.refuse_plan(lambda plan: plan.update({"schema_version": "9.9.9"}),
                         "version this understands")

    def test_the_committed_plan_is_exactly_the_reviewed_shape(self):
        """If this fails, the plan changed and nobody reviewed the change."""
        self.assertEqual({entry["canonical_id"] for entry in PLAN["entries"]},
                         set(ANSWERS.TASK14_INTAKE_SHAPE))
        for entry in PLAN["entries"]:
            expected = ANSWERS.TASK14_INTAKE_SHAPE[entry["canonical_id"]]
            with self.subTest(target=entry["canonical_id"]):
                self.assertIn(entry["answer_type"], expected["answer_type"])
                self.assertEqual(entry["scope"], expected["scope"])
                for field in ("answer", "source_type", "validity_class",
                              "auto_fill_allowed", "auto_submit_allowed",
                              "engine_enforced_recheck"):
                    self.assertEqual(entry[field], expected[field], field)
