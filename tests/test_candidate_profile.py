"""The reviewed Candidate Profile shape, and how a form field reaches a fact.

Two things are being pinned. A field reaches a fact by **meaning**, because a real employer
page has never heard of `fact-0001` and the only mapping in the repository was a three-entry
constant in a test fixture. And **accurate** and **usable** are two different confirmations,
because a profile of `confirmed` facts fills nothing: the planner refuses anything that is not
locked.

Every value here is visibly synthetic.
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
    spec = importlib.util.spec_from_file_location(f"profile_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


PROFILE = load("candidate_profile")
CANDIDATES = load("candidate_core")
RESUMES = load("resume_core")
APPROVAL = json.loads(
    (ROOT / "tests" / "fixtures" / "ats-semantic" / "FIELD-DISPOSITION-APPROVAL.json")
    .read_text(encoding="utf-8"))
AT = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


class ShapeTests(unittest.TestCase):
    def test_every_meaning_the_corpus_asks_a_profile_for_is_covered_or_deferred(self):
        """The corpus decides what the profile owes, not a list written from imagination."""
        demanded = {entry["expected_target"] for entry in APPROVAL["entries"]
                    if entry["expected_disposition"] == "fact"}
        covered = set(PROFILE.PROFILE_V1) | set(PROFILE.PROFILE_V1_DEFERRED)
        self.assertEqual(sorted(demanded - covered), [])
        # And every deferred one says why it is deferred rather than dropped.
        for canonical_id, reason in PROFILE.PROFILE_V1_DEFERRED.items():
            with self.subTest(canonical_id=canonical_id):
                self.assertIn(canonical_id, demanded)
                self.assertTrue(reason.strip())

    def test_a_field_says_whether_a_recording_asked_for_it(self):
        """An address is in the schema because the user needs one, not because a form asked.

        Labelling the two apart is what keeps a field set from growing on the strength of
        looking reasonable.
        """
        demanded = {entry["expected_target"] for entry in APPROVAL["entries"]
                    if entry["expected_disposition"] == "fact"}
        for canonical_id, field in PROFILE.PROFILE_V1.items():
            with self.subTest(canonical_id=canonical_id):
                self.assertIn(field.demand, (PROFILE.DEMAND_CORPUS, PROFILE.DEMAND_USER))
                self.assertEqual(field.demand == PROFILE.DEMAND_CORPUS,
                                 canonical_id in demanded)
                self.assertIn(field.group, PROFILE.GROUPS)
                self.assertTrue(field.note.strip())

    def test_required_where_present_matches_the_recordings(self):
        """Measured from the fixtures rather than assumed from the field's name."""
        required = {}
        upstream = ROOT / "tests" / "fixtures" / "ats-semantic" / "upstream"
        kinds = {entry["kind"]: entry["expected_target"] for entry in APPROVAL["entries"]}
        for directory in sorted(path for path in upstream.iterdir() if path.is_dir()):
            fixture = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))
            for step in fixture["steps"]:
                for control in step["controls"]:
                    target = kinds.get(control["kind"])
                    if target in PROFILE.PROFILE_V1:
                        required.setdefault(target, []).append(bool(control.get("required")))
        for target, flags in required.items():
            with self.subTest(canonical_id=target):
                self.assertEqual(PROFILE.PROFILE_V1[target].required_where_present,
                                 all(flags))

    def test_nothing_that_belongs_elsewhere_can_be_a_profile_field(self):
        """Secrets, identity documents, EEO, salary and per-employer answers.

        Each belongs to a secret store, a pause, or the AnswerLibrary. A profile that learned
        to supply one would be answering a question whose whole disposition is that it reaches
        the user.
        """
        self.assertEqual(set(PROFILE.PROFILE_V1) & PROFILE.FORBIDDEN_MEANINGS, set())
        manual = {entry["expected_target"] for entry in APPROVAL["entries"]
                  if entry["expected_disposition"] == "always_manual"}
        self.assertEqual(manual & set(PROFILE.PROFILE_V1), set())

    def test_the_name_is_five_confirmations_and_no_derivation(self):
        """`full_name` is not first + last.

        Name order is not universally western, middle names and compound surnames break the
        concatenation, and a derivation would turn "technically joinable" into "the display
        name the user accepts".
        """
        names = {canonical_id for canonical_id, field in PROFILE.PROFILE_V1.items()
                 if field.group == "name"}
        self.assertEqual(names, {"contact.first_name", "contact.middle_name",
                                 "contact.last_name", "contact.preferred_name",
                                 "contact.full_name"})
        source = (SCRIPTS / "candidate_profile.py").read_text(encoding="utf-8")
        code = "\n".join(line.split("#")[0] for line in source.splitlines())
        for joining in ("first_name'] + ", '+ " " +', ".join([first"):
            self.assertNotIn(joining, code)

    def test_a_mailing_address_is_separate_from_where_someone_lives(self):
        groups = {canonical_id: field.group for canonical_id, field in PROFILE.PROFILE_V1.items()}
        self.assertEqual(groups["contact.address.line1"], "mailing_address")
        self.assertEqual(groups["contact.postal_code"], "mailing_address")
        self.assertEqual(groups["contact.location_city"], "current_location")
        self.assertEqual(groups["contact.country"], "current_location")


class ConfirmationGateTests(unittest.TestCase):
    """Accurate and usable are different questions."""

    def test_only_both_answers_together_produce_a_fact_the_planner_may_fill(self):
        self.assertEqual(PROFILE.gate_status(True, True), ("locked", True))
        self.assertEqual(PROFILE.gate_status(True, False), ("confirmed", False))
        self.assertEqual(PROFILE.gate_status(False, False), ("unconfirmed", False))

    def test_filling_cannot_be_authorised_before_the_value_is_confirmed(self):
        with self.assertRaisesRegex(ValueError, "before it is confirmed"):
            PROFILE.gate_status(False, True)

    def test_both_answers_must_be_stated_as_booleans(self):
        for confirmed, allowed in ((1, True), (True, 1), ("yes", "yes"), (None, None)):
            with self.subTest(confirmed=repr(confirmed)):
                with self.assertRaisesRegex(ValueError, "stated as booleans"):
                    PROFILE.gate_status(confirmed, allowed)


class CanonicalResolutionTests(unittest.TestCase):
    """A field reaches a fact by meaning, and fails closed in every direction."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        for module in (load("application_core"), RESUMES, load("pre_submit_core"), CANDIDATES):
            module.initialize(self.db)
        self.addCleanup(self.db.close)
        self.snapshot = "s" * 64

    def add(self, fact_id, canonical_id, status="locked", locked=True, expires=None):
        self.db.execute(
            "INSERT INTO candidate_facts (content_sha256, fact_id, fact_type, value_json, "
            "status, locked, evidence_strength, expires_at, source_json, keywords_json, "
            "confirmed_at, invalidation_triggers_json, canonical_id, fact_sha256) "
            "VALUES (?, ?, 'contact', ?, ?, ?, 'direct', ?, '{}', '[]', NULL, '[]', ?, ?)",
            (self.snapshot, fact_id, json.dumps("a synthetic placeholder"), status,
             int(locked), expires, canonical_id, fact_id))
        self.db.commit()

    def resolve(self, canonical_id, at=AT):
        return PROFILE.resolve_canonical_fact(self.db, canonical_id, self.snapshot, at)

    def test_one_locked_fact_resolves(self):
        self.add("fact-email", "contact.email")
        fact, reason = self.resolve("contact.email")
        self.assertIsNone(reason)
        self.assertEqual(fact["fact_id"], "fact-email")

    def test_two_facts_claiming_one_meaning_is_a_pause_and_not_a_coin_toss(self):
        """Picking either would fill a form from a value nobody arbitrated."""
        self.add("fact-email-a", "contact.email")
        self.add("fact-email-b", "contact.email")
        fact, reason = self.resolve("contact.email")
        self.assertIsNone(fact)
        self.assertEqual(reason, PROFILE.PROFILE_FACT_AMBIGUOUS)

    def test_a_fact_that_is_only_confirmed_does_not_resolve(self):
        """The reason a profile of confirmed facts fills nothing."""
        self.add("fact-email", "contact.email", status="confirmed", locked=False)
        fact, reason = self.resolve("contact.email")
        self.assertIsNone(fact)
        self.assertEqual(reason, PROFILE.PROFILE_FACT_NOT_LOCKED)

    def test_a_meaning_with_no_fact_does_not_resolve(self):
        fact, reason = self.resolve("contact.email")
        self.assertIsNone(fact)
        self.assertEqual(reason, PROFILE.PROFILE_FACT_MISSING)

    def test_an_expired_fact_does_not_resolve(self):
        self.add("fact-email", "contact.email",
                 expires=(AT - timedelta(days=1)).isoformat())
        fact, reason = self.resolve("contact.email")
        self.assertIsNone(fact)
        self.assertEqual(reason, PROFILE.PROFILE_FACT_EXPIRED)

    def test_a_meaning_nobody_reviewed_does_not_resolve(self):
        self.add("fact-invented", "contact.invented")
        fact, reason = self.resolve("contact.invented")
        self.assertIsNone(fact)
        self.assertEqual(reason, PROFILE.PROFILE_FIELD_UNKNOWN)

    def test_a_meaning_that_is_not_profile_data_is_refused_by_name(self):
        """Even with a locked fact sitting there claiming it."""
        self.add("fact-eeo", "eeo.race")
        fact, reason = self.resolve("eeo.race")
        self.assertIsNone(fact)
        self.assertEqual(reason, PROFILE.PROFILE_FACT_FORBIDDEN)

    def test_a_confirmed_duplicate_beside_a_locked_one_still_resolves(self):
        """One authorised value and one merely recorded is not an ambiguity."""
        self.add("fact-email", "contact.email")
        self.add("fact-email-old", "contact.email", status="confirmed", locked=False)
        fact, reason = self.resolve("contact.email")
        self.assertIsNone(reason)
        self.assertEqual(fact["fact_id"], "fact-email")

    def test_facts_written_before_the_profile_existed_carry_no_meaning(self):
        """The column is nullable on purpose: inventing meanings for old facts is guessing."""
        self.db.execute(
            "INSERT INTO candidate_facts (content_sha256, fact_id, fact_type, value_json, "
            "status, locked, evidence_strength, source_json, keywords_json, "
            "invalidation_triggers_json, fact_sha256) "
            "VALUES (?, 'fact-0002', 'contact', ?, 'confirmed', 0, 'direct', '{}', '[]', "
            "'[]', 'h')", (self.snapshot, json.dumps("composite placeholder")))
        self.db.commit()
        fact, reason = self.resolve("contact.email")
        self.assertEqual(reason, PROFILE.PROFILE_FACT_MISSING)


if __name__ == "__main__":
    unittest.main()
