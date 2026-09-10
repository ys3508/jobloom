"""A database written by the previous schema, opened by the current code.

`CREATE TABLE IF NOT EXISTS` leaves an existing table exactly as it was, so a database that
already held stories would keep the old columns and every new read would fail on a column
that is not there. `story_core._migrate` adds and backfills them.

The interesting part is what it refuses to backfill. An older version named a capability and
no claims, so nothing on disk says which claims evidenced it. Filling that in with "all of
them" would recreate the promotion the new schema exists to prevent — silently, on rows
nobody would look at again. So the binding is carried with an empty claim list, the version
becomes unselectable with a reason that says exactly that, and `prepare_successor` is the way
back. The story, its text, its claims and its usage history are all preserved; the only thing
withheld is the assertion that somebody reviewed the binding.

`prepare_successor` is **not** that way back, and an earlier version of this docstring said it
was. Carrying re-derives evidence references from the same facts; it cannot supply claim ids
nobody ever recorded, and a binding with an empty claim list is refused by `_validate_bindings`
before it could try. Recovery is a person drafting a successor with real bindings and
approving it — which is what `test_the_migrated_story_comes_back_by_being_re_bound` does.

The old DDL is written out here rather than imported from the old commit, so this test states
the shape it is guarding against instead of depending on history staying reachable.
"""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"story_migration_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


STORIES = load_script("story_core")
PROPOSE = load_script("story_answers")
CANDIDATES = load_script("candidate_core")
RESUMES = load_script("resume_core")
ANSWERS = load_script("answer_library")
APPLICATIONS = load_script("application_core")
PRE_SUBMIT = load_script("pre_submit_core")
EVIDENCE = load_script("evidence_units")

AT = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)

ACTION = "I ran 2 focus groups."
RESULT = "Sales rose 17 percent."
SITUATION = "INNSCI needed a read on three products."
TASK = "I was asked to run the qualitative work."

# The schema as commit 8fdec40 wrote it: `primary_capability` a bare id,
# `secondary_capabilities_json` beside it, no `capability_bindings_json`, no
# `confidential_employer_normalized`, and no `claim_ids_json` on a mapping.
OLD_SCHEMA = """
    CREATE TABLE stories (
        story_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        current_version_id TEXT,
        confidentiality TEXT NOT NULL,
        confidential_employer TEXT,
        confidential_application_id TEXT,
        applicability_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        revoked_at TEXT,
        status_reason TEXT
    );
    CREATE TABLE story_versions (
        version_id TEXT PRIMARY KEY,
        story_id TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        candidate_snapshot_sha256 TEXT NOT NULL,
        authored_by TEXT NOT NULL,
        title TEXT NOT NULL,
        star_json TEXT NOT NULL,
        primary_capability TEXT NOT NULL,
        secondary_capabilities_json TEXT NOT NULL,
        domains_json TEXT NOT NULL,
        earned_secret TEXT,
        reflection TEXT,
        framing_spans_json TEXT NOT NULL,
        unbound_spans_json TEXT NOT NULL,
        supersedes_version_id TEXT,
        created_at TEXT NOT NULL,
        approved_by TEXT,
        approved_at TEXT
    );
    CREATE TABLE story_claims (
        version_id TEXT NOT NULL,
        claim_id TEXT NOT NULL,
        claim_text TEXT NOT NULL,
        evidence_refs_json TEXT NOT NULL,
        evidence_class TEXT NOT NULL,
        PRIMARY KEY (version_id, claim_id)
    );
    CREATE TABLE story_competency_mappings (
        version_id TEXT NOT NULL,
        competency TEXT NOT NULL,
        relation TEXT NOT NULL,
        limitation TEXT,
        reviewed_by TEXT NOT NULL,
        reviewed_at TEXT NOT NULL,
        PRIMARY KEY (version_id, competency)
    );
    CREATE TABLE story_usage_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        story_version_id TEXT NOT NULL,
        application_id TEXT,
        interview_id TEXT,
        round_id TEXT,
        question_type TEXT NOT NULL,
        used_at TEXT NOT NULL,
        outcome_ref TEXT
    );
    CREATE TABLE story_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        story_id TEXT,
        version_id TEXT,
        actor TEXT NOT NULL,
        event_type TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        metadata_json TEXT NOT NULL
    );
"""


class MigrationFixture(unittest.TestCase):
    """An old-schema database holding one approved story. No tests of its own."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        for module in (RESUMES, ANSWERS, APPLICATIONS, PRE_SUBMIT, CANDIDATES):
            module.initialize(self.db)
        self.snapshot = self.register()
        self.db.executescript(OLD_SCHEMA)
        self.write_old_story()

    def register(self):
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": False, "employer_action_required": False,
                "confirmed": True},
            "search": {},
            "facts": [
                {"id": "fact-name", "type": "identity", "value": "Verified Candidate",
                 "status": "locked", "locked": True, "evidence_strength": "direct"},
                {"id": "fact-focus", "type": "experience_claim",
                 "value": "Ran 2 focus groups", "status": "confirmed", "locked": False,
                 "evidence_strength": "direct"},
                {"id": "fact-sales", "type": "experience_claim",
                 "value": "Sales increased 17%", "status": "confirmed", "locked": False,
                 "evidence_strength": "direct"}]}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        path = self.root / "candidate.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        CANDIDATES.register_snapshot(self.db, self.root / "store", path, "user", AT)
        return candidate["content_sha256"]

    def write_old_story(self):
        """One approved story, written the way the previous release wrote one."""
        star = {"situation": SITUATION, "task": TASK, "action": ACTION, "result": RESULT}
        self.db.execute(
            "INSERT INTO stories (story_id, status, current_version_id, confidentiality, "
            "confidential_employer, confidential_application_id, applicability_json, "
            "created_at, status_reason) VALUES ('S-old', 'approved', 'SV-old', "
            "'employer_confidential', 'Example  Corp', NULL, '{}', ?, 'user_approved')",
            (AT.isoformat(),))
        self.db.execute(
            "INSERT INTO story_versions (version_id, story_id, content_sha256, "
            "candidate_snapshot_sha256, authored_by, title, star_json, primary_capability, "
            "secondary_capabilities_json, domains_json, framing_spans_json, "
            "unbound_spans_json, created_at, approved_by, approved_at) "
            "VALUES ('SV-old', 'S-old', 'oldhash', ?, 'user', 'INNSCI focus groups', ?, "
            "'cap.survey-design', ?, ?, ?, '[]', ?, 'user', ?)",
            (self.snapshot, json.dumps(star),
             json.dumps(["cap.stakeholder-reporting"]),
             json.dumps(["cap.domain.pharma-insights"]),
             json.dumps([SITUATION, TASK]), AT.isoformat(), AT.isoformat()))
        for claim_id, text, fact in (("c1", ACTION, "fact-focus"), ("c2", RESULT, "fact-sales")):
            self.db.execute(
                "INSERT INTO story_claims (version_id, claim_id, claim_text, "
                "evidence_refs_json, evidence_class) VALUES ('SV-old', ?, ?, ?, 'direct')",
                (claim_id, text, json.dumps([EVIDENCE.unit_id(self.snapshot, fact)])))
        self.db.execute(
            "INSERT INTO story_competency_mappings (version_id, competency, relation, "
            "limitation, reviewed_by, reviewed_at) VALUES ('SV-old', 'cap.research-design', "
            "'answers', NULL, 'user', ?)", (AT.isoformat(),))
        self.db.execute(
            "INSERT INTO story_usage_events (story_version_id, question_type, used_at) "
            "VALUES ('SV-old', 'behavioral', ?)", (AT.isoformat(),))
        self.db.commit()

    def rebound_story(self):
        """A story with a reviewed binding, so a check other than the binding can be tested."""
        STORIES.initialize(self.db)
        content = {
            "title": "Analysis plan",
            "star": {"situation": "No plan existed.", "task": "I owned it.",
                     "action": ACTION, "result": "It shipped."},
            "primary_capability": {"capability_id": "cap.survey-design", "claim_ids": ["n1"]},
            "secondary_capabilities": [], "domains": ["cap.domain.pharma-insights"],
            "framing_spans": ["No plan existed.", "I owned it.", "It shipped."],
            "claims": [{"claim_id": "n1", "text": ACTION,
                        "evidence_refs": [EVIDENCE.unit_id(self.snapshot, "fact-focus")],
                        "evidence_class": "direct"}]}
        drafted = STORIES.draft_version(self.db, content, at=AT)
        STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"],
                                "user", AT)
        return drafted


class MigrationTests(MigrationFixture):

    def test_initialising_over_the_old_schema_succeeds(self):
        STORIES.initialize(self.db)
        PROPOSE.initialize(self.db)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(story_versions)")}
        self.assertIn("capability_bindings_json", columns)
        self.assertNotIn("secondary_capabilities_json", columns)
        self.assertIn("confidential_employer_normalized",
                      {row[1] for row in self.db.execute("PRAGMA table_info(stories)")})
        self.assertIn("claim_ids_json", {row[1] for row in self.db.execute(
            "PRAGMA table_info(story_competency_mappings)")})

    def test_the_story_and_everything_it_said_is_still_there(self):
        STORIES.initialize(self.db)
        version = self.db.execute(
            "SELECT * FROM story_versions WHERE version_id='SV-old'").fetchone()
        self.assertEqual(version["title"], "INNSCI focus groups")
        self.assertEqual(json.loads(version["star_json"])["action"], ACTION)
        claims = self.db.execute(
            "SELECT claim_id, claim_text FROM story_claims WHERE version_id='SV-old' "
            "ORDER BY claim_id").fetchall()
        self.assertEqual([row["claim_text"] for row in claims], [ACTION, RESULT])
        self.assertEqual(STORIES.usage(self.db, "S-old")["use_count"], 1)

    def test_the_capabilities_are_carried_with_no_claims_and_it_says_so(self):
        STORIES.initialize(self.db)
        bindings = json.loads(self.db.execute(
            "SELECT capability_bindings_json FROM story_versions WHERE version_id='SV-old'"
        ).fetchone()["capability_bindings_json"])
        self.assertEqual(bindings["primary"],
                         {"capability_id": "cap.survey-design", "claim_ids": []})
        self.assertEqual(bindings["secondary"],
                         [{"capability_id": "cap.stakeholder-reporting", "claim_ids": []}])
        self.assertTrue(bindings["migrated_without_claims"])

    def test_an_unreviewed_binding_is_not_selectable_and_names_the_reason(self):
        """Preserved, readable, and deliberately not usable until somebody re-binds it."""
        STORIES.initialize(self.db)
        check = STORIES.selectable(self.db, "SV-old")
        self.assertFalse(check["selectable"])
        self.assertIn("capability_binding_unreviewed", check["reasons"])

    def test_a_migrated_story_is_retrieved_by_nothing(self):
        STORIES.initialize(self.db)
        for competency in ("cap.survey-design", "cap.stakeholder-reporting",
                           "cap.research-design"):
            with self.subTest(competency=competency):
                mapped = STORIES.map_stories(self.db, [competency])
                self.assertEqual(mapped[competency]["fit"], "gap")

    def test_the_confidential_employer_is_normalized_by_the_migration(self):
        """Two spaces in the stored name; the new match is on the normalized identity."""
        STORIES.initialize(self.db)
        self.assertEqual(self.db.execute(
            "SELECT confidential_employer_normalized FROM stories WHERE story_id='S-old'"
        ).fetchone()[0], APPLICATIONS.normalize_text("Example  Corp"))

    def test_running_the_migration_twice_changes_nothing(self):
        STORIES.initialize(self.db)
        first = self.db.execute(
            "SELECT capability_bindings_json FROM story_versions WHERE version_id='SV-old'"
        ).fetchone()[0]
        STORIES.initialize(self.db)
        PROPOSE.initialize(self.db)
        self.assertEqual(self.db.execute(
            "SELECT capability_bindings_json FROM story_versions WHERE version_id='SV-old'"
        ).fetchone()[0], first)

    # ---- and the new interfaces work on the migrated database ------------------------

    def test_a_new_story_can_be_written_alongside_the_migrated_one(self):
        STORIES.initialize(self.db)
        content = {
            "title": "Analysis plan",
            "star": {"situation": "No plan existed.", "task": "I owned it.",
                     "action": ACTION, "result": "It shipped."},
            "primary_capability": {"capability_id": "cap.survey-design",
                                   "claim_ids": ["n1"]},
            "secondary_capabilities": [], "domains": ["cap.domain.pharma-insights"],
            "framing_spans": ["No plan existed.", "I owned it.", "It shipped."],
            "claims": [{"claim_id": "n1", "text": ACTION,
                        "evidence_refs": [EVIDENCE.unit_id(self.snapshot, "fact-focus")],
                        "evidence_class": "direct"}]}
        drafted = STORIES.draft_version(self.db, content, at=AT)
        STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"],
                                "user", AT)
        self.assertTrue(STORIES.selectable(self.db, drafted["version_id"])["selectable"])
        mapped = STORIES.map_stories(self.db, ["cap.survey-design"])["cap.survey-design"]
        self.assertEqual(mapped["fit"], "strong")
        # Only the new one. The migrated version is still sitting there, still unusable.
        self.assertEqual([row["version_id"] for row in mapped["stories"]],
                         [drafted["version_id"]])

    def test_the_migrated_story_comes_back_by_being_re_bound(self):
        """The way out is the successor path, not a backfill nobody reviewed."""
        STORIES.initialize(self.db)
        version = self.db.execute(
            "SELECT * FROM story_versions WHERE version_id='SV-old'").fetchone()
        star = json.loads(version["star_json"])
        content = {
            "title": version["title"], "star": star,
            "primary_capability": {"capability_id": "cap.survey-design",
                                   "claim_ids": ["c1", "c2"]},
            "secondary_capabilities": [],
            "domains": json.loads(version["domains_json"]),
            "framing_spans": json.loads(version["framing_spans_json"]),
            "claims": [{"claim_id": "c1", "text": ACTION,
                        "evidence_refs": [EVIDENCE.unit_id(self.snapshot, "fact-focus")],
                        "evidence_class": "direct"},
                       {"claim_id": "c2", "text": RESULT,
                        "evidence_refs": [EVIDENCE.unit_id(self.snapshot, "fact-sales")],
                        "evidence_class": "direct"}]}
        successor = STORIES.draft_version(self.db, content, story_id="S-old",
                                          supersedes_version_id="SV-old", at=AT)
        STORIES.approve_version(self.db, successor["version_id"],
                                successor["content_sha256"], "user", AT)
        self.assertTrue(STORIES.selectable(self.db, successor["version_id"])["selectable"])
        self.assertIn("capability_binding_unreviewed",
                      STORIES.selectable(self.db, "SV-old")["reasons"])

    def test_a_migrated_mapping_produces_no_fit_until_it_is_recorded_again(self):
        STORIES.initialize(self.db)
        self.assertEqual(json.loads(self.db.execute(
            "SELECT claim_ids_json FROM story_competency_mappings "
            "WHERE version_id='SV-old'").fetchone()[0]), [])

    def test_an_old_answer_draft_table_gains_the_question_form_lock(self):
        """The drafts table predates the column that pins which form authorized a meaning."""
        self.db.executescript("""
            CREATE TABLE story_answer_drafts (
                draft_id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL,
                employer TEXT,
                canonical_id TEXT NOT NULL,
                normalized_question TEXT NOT NULL,
                competency TEXT NOT NULL,
                story_version_id TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                dependent_fact_ids_json TEXT NOT NULL,
                candidate_snapshot_sha256 TEXT NOT NULL,
                evidence_class TEXT NOT NULL,
                answer_text TEXT NOT NULL,
                bridge TEXT,
                content_sha256 TEXT NOT NULL,
                auto_fill_ready INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                approved_at TEXT,
                approved_answer_id TEXT
            );
        """)
        self.db.commit()
        PROPOSE.initialize(self.db)
        self.assertIn("question_form_sha256", {row[1] for row in self.db.execute(
            "PRAGMA table_info(story_answer_drafts)")})


if __name__ == "__main__":
    unittest.main()


class UnlockedDraftTests(MigrationFixture):
    """A draft written before the lock existed cannot be approved into the library.

    Backfilling it with today's digest would be the exact substitution the lock exists to
    prevent: today's answer to "what does this question mean" is not evidence about what it
    meant when the draft was written.
    """

    def test_a_draft_with_no_recorded_form_is_refused(self):
        story = self.rebound_story()
        self.db.executescript("""
            CREATE TABLE story_answer_drafts (
                draft_id TEXT PRIMARY KEY, application_id TEXT NOT NULL, employer TEXT,
                canonical_id TEXT NOT NULL, normalized_question TEXT NOT NULL,
                competency TEXT NOT NULL, story_version_id TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL, dependent_fact_ids_json TEXT NOT NULL,
                candidate_snapshot_sha256 TEXT NOT NULL, evidence_class TEXT NOT NULL,
                answer_text TEXT NOT NULL, bridge TEXT, content_sha256 TEXT NOT NULL,
                auto_fill_ready INTEGER NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, approved_at TEXT, approved_answer_id TEXT
            );
        """)
        self.db.execute(
            "INSERT INTO story_answer_drafts (draft_id, application_id, canonical_id, "
            "normalized_question, competency, story_version_id, evidence_refs_json, "
            "dependent_fact_ids_json, candidate_snapshot_sha256, evidence_class, "
            "answer_text, content_sha256, auto_fill_ready, status, created_at) "
            "VALUES ('AD-old', 'app-1', 'experience.story', 'tell us about a time', "
            "'cap.survey-design', ?, '[]', '[]', ?, 'direct', 'text', 'hash', 0, "
            "'draft', ?)", (story["version_id"], self.snapshot, AT.isoformat()))
        self.db.commit()
        PROPOSE.initialize(self.db)
        with self.assertRaises(ValueError) as caught:
            PROPOSE.approve_draft(self.db, "AD-old", "hash", scope={},
                                  validity_class="stable", at=AT)
        self.assertIn("predates the question-form lock", str(caught.exception))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 0)


class InterruptedMigrationTests(MigrationFixture):
    """A migration that stopped half way is fixed by running it again.

    The version this replaces decided what to do from whether a column existed. Adding a
    column and backfilling it are one change, so a crash between them left every row at the
    `'{}'` default while the column's existence made the next run skip the backfill — a
    database that looked migrated and had lost its capabilities. Each step is a transaction
    now, and what remains to do is read from the data rather than from the schema.
    """

    def bindings(self, version_id="SV-old"):
        return json.loads(self.db.execute(
            "SELECT capability_bindings_json FROM story_versions WHERE version_id=?",
            (version_id,)).fetchone()[0])

    def test_a_run_that_stopped_after_adding_the_column_is_repaired(self):
        self.db.execute(
            "ALTER TABLE story_versions ADD COLUMN capability_bindings_json TEXT "
            "NOT NULL DEFAULT '{}'")
        self.db.commit()
        self.assertEqual(self.bindings(), {})
        STORIES.initialize(self.db)
        self.assertEqual(self.bindings()["primary"],
                         {"capability_id": "cap.survey-design", "claim_ids": []})
        self.assertEqual(self.bindings()["secondary"],
                         [{"capability_id": "cap.stakeholder-reporting", "claim_ids": []}])

    def test_a_run_that_stopped_before_the_drop_completes_the_drop(self):
        STORIES.initialize(self.db)
        first = self.bindings()
        # Re-create the situation: the replaced column back beside its replacement.
        self.db.execute(
            "ALTER TABLE story_versions ADD COLUMN secondary_capabilities_json TEXT "
            "NOT NULL DEFAULT '[]'")
        self.db.commit()
        STORIES.initialize(self.db)
        self.assertNotIn("secondary_capabilities_json",
                         {row[1] for row in self.db.execute(
                             "PRAGMA table_info(story_versions)")})
        self.assertEqual(self.bindings(), first)

    def test_a_row_left_at_the_default_after_the_drop_is_still_repaired(self):
        """The worst ordering: the source column gone and a row never backfilled.

        `primary_capability` is never dropped, which is what makes this recoverable at all.
        """
        STORIES.initialize(self.db)
        self.db.execute(
            "UPDATE story_versions SET capability_bindings_json='{}' WHERE version_id='SV-old'")
        self.db.commit()
        STORIES.initialize(self.db)
        self.assertEqual(self.bindings()["primary"],
                         {"capability_id": "cap.survey-design", "claim_ids": []})
        self.assertEqual(self.bindings()["secondary"], [])

    def test_unreadable_binding_json_counts_as_unmigrated(self):
        STORIES.initialize(self.db)
        self.db.execute(
            "UPDATE story_versions SET capability_bindings_json='not json' "
            "WHERE version_id='SV-old'")
        self.db.commit()
        STORIES.initialize(self.db)
        self.assertEqual(self.bindings()["primary"]["capability_id"], "cap.survey-design")

    def test_a_failure_during_the_backfill_leaves_no_half_written_rows(self):
        """The transaction is the point: rows either all carry a binding or none do."""
        original = STORIES.application_core.normalize_text

        def explode(value):
            raise RuntimeError("interrupted")

        self.db.execute(
            "ALTER TABLE stories ADD COLUMN confidential_employer_normalized TEXT")
        self.db.commit()
        STORIES.application_core.normalize_text = explode
        try:
            with self.assertRaises(RuntimeError):
                STORIES.initialize(self.db)
        finally:
            STORIES.application_core.normalize_text = original
        self.assertIsNone(self.db.execute(
            "SELECT confidential_employer_normalized FROM stories "
            "WHERE story_id='S-old'").fetchone()[0])
        # And running again, uninterrupted, completes it.
        STORIES.initialize(self.db)
        self.assertEqual(self.db.execute(
            "SELECT confidential_employer_normalized FROM stories "
            "WHERE story_id='S-old'").fetchone()[0],
            APPLICATIONS.normalize_text("Example  Corp"))

    def test_migrating_is_safe_to_repeat_any_number_of_times(self):
        for _ in range(4):
            STORIES.initialize(self.db)
            PROPOSE.initialize(self.db)
        self.assertEqual(self.bindings()["primary"],
                         {"capability_id": "cap.survey-design", "claim_ids": []})
        self.assertEqual(STORIES.usage(self.db, "S-old")["use_count"], 1)


class RecoveringAMigratedStoryTests(MigrationFixture):
    """What the way back actually is, since the docstring once named the wrong one."""

    def test_carrying_a_migrated_version_forward_is_refused_by_name(self):
        STORIES.initialize(self.db)
        # Move the profile, so the carry would otherwise be the natural next step.
        candidate = json.loads((self.root / "candidate.json").read_text(encoding="utf-8"))
        candidate["facts"][0]["value"] = "Renamed Candidate"
        # The hash is over the profile without it, so the old one has to come out first.
        candidate.pop("content_sha256")
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        moved = self.root / "candidate-2.json"
        moved.write_text(json.dumps(candidate), encoding="utf-8")
        CANDIDATES.register_snapshot(self.db, self.root / "store", moved, "user", AT)
        with self.assertRaises(ValueError) as caught:
            STORIES.prepare_successor(self.db, "SV-old", at=AT)
        self.assertIn("names no claims", str(caught.exception))
        self.assertIn("draft a successor with real capability bindings",
                      str(caught.exception))

    def test_a_hand_drafted_successor_is_the_way_back(self):
        successor = self.rebound_story()
        self.assertTrue(STORIES.selectable(self.db, successor["version_id"])["selectable"])
        self.assertEqual(
            STORIES.map_stories(self.db, ["cap.survey-design"])["cap.survey-design"]["fit"],
            "strong")


class DamagedBindingTests(MigrationFixture):
    """A database whose binding column holds something unexpected still opens.

    The gap this closes: the migration tolerated *unparseable* JSON but not well-formed JSON
    of the wrong type. A column holding `[]`, `null`, `5` or `"text"` raised `AttributeError`
    inside `initialize`, so the database could not be opened at all — no escalation, but a
    worse failure than the one being guarded, because nothing can be done to a database that
    will not start. `read_bindings` is total: anything unusable reads as no binding, which is
    what the migration repairs and what `selectable` reports.
    """

    UNUSABLE = ("[]", "null", "5", '"text"', "", "not json", "{}", '{"primary": null}',
                '{"primary": "cap.survey-design"}', '{"primary": {"claim_ids": []}}',
                '{"primary": {"capability_id": "  "}}')

    def damage(self, raw):
        STORIES.initialize(self.db)
        self.db.execute(
            "UPDATE story_versions SET capability_bindings_json=? WHERE version_id='SV-old'",
            (raw,))
        self.db.commit()

    def stored(self):
        return self.db.execute(
            "SELECT capability_bindings_json FROM story_versions WHERE version_id='SV-old'"
        ).fetchone()[0]

    def test_the_database_opens_whatever_the_column_holds(self):
        for raw in self.UNUSABLE:
            with self.subTest(raw=raw):
                self.damage(raw)
                STORIES.initialize(self.db)
                PROPOSE.initialize(self.db)

    def test_an_unusable_binding_is_repaired_to_the_unreviewed_form(self):
        for raw in self.UNUSABLE:
            with self.subTest(raw=raw):
                self.damage(raw)
                STORIES.initialize(self.db)
                bindings = json.loads(self.stored())
                self.assertEqual(bindings["primary"],
                                 {"capability_id": "cap.survey-design", "claim_ids": []})
                self.assertTrue(bindings["migrated_without_claims"])

    def test_a_damaged_row_is_refused_rather_than_raising(self):
        for raw in self.UNUSABLE:
            with self.subTest(raw=raw):
                self.damage(raw)
                check = STORIES.selectable(self.db, "SV-old")
                self.assertFalse(check["selectable"])
                self.assertIn("capability_binding_unreviewed", check["reasons"])
                self.assertEqual(
                    STORIES.map_stories(self.db, ["cap.survey-design"])
                    ["cap.survey-design"]["fit"], "gap")

    def test_reading_never_raises_on_any_of_them(self):
        for raw in (*self.UNUSABLE, '{"primary": {"capability_id": "cap.x",'
                    ' "claim_ids": "c1"}}', '{"primary": {"capability_id": "cap.x",'
                    ' "claim_ids": ["c1", 7]}, "secondary": 7}'):
            with self.subTest(raw=raw):
                self.assertIsInstance(STORIES.read_bindings(raw), dict)

    def test_a_claim_ids_that_is_not_a_list_of_strings_becomes_empty(self):
        """Normalized, never guessed at: a string is not silently read as one claim id."""
        self.assertEqual(
            STORIES.read_bindings(
                '{"primary": {"capability_id": "cap.x", "claim_ids": "c1"}}'
            )["primary"]["claim_ids"], [])
        self.assertEqual(
            STORIES.read_bindings(
                '{"primary": {"capability_id": "cap.x", "claim_ids": ["c1", 7, ""]}}'
            )["primary"]["claim_ids"], ["c1"])

    def test_a_malformed_claim_list_is_rewritten_so_disk_matches_behaviour(self):
        self.damage('{"primary": {"capability_id": "cap.survey-design", "claim_ids": "c1"}}')
        STORIES.initialize(self.db)
        self.assertEqual(json.loads(self.stored())["primary"]["claim_ids"], [])
        self.assertIn("capability_binding_unreviewed",
                      STORIES.selectable(self.db, "SV-old")["reasons"])

    def test_a_damaged_secondary_list_does_not_break_retrieval(self):
        self.damage('{"primary": {"capability_id": "cap.survey-design", "claim_ids": ["c1"]},'
                    ' "secondary": 7}')
        STORIES.initialize(self.db)
        self.assertEqual(
            STORIES.map_stories(self.db, ["cap.data-querying"])["cap.data-querying"]["fit"],
            "gap")

    def test_a_legacy_secondary_column_holding_junk_still_migrates(self):
        """The replaced column is read the same way: unusable is nothing, never an error."""
        self.db.execute(
            "UPDATE story_versions SET secondary_capabilities_json='not json' "
            "WHERE version_id='SV-old'")
        self.db.commit()
        STORIES.initialize(self.db)
        self.assertEqual(json.loads(self.stored())["secondary"], [])
        self.assertEqual(json.loads(self.stored())["primary"]["capability_id"],
                         "cap.survey-design")
