"""A story may be told only out of evidence that is still there.

The reference implementation this is adapted from keeps stories as markdown, where the number
in the Result line is attached to nothing and the coach's own improve step invites the user to
supply one — "even rough ones". These tests are the difference: a version whose narrative is
not completely accounted for cannot be used, a claim cannot be evidenced better than what it
cites, and neither an approval nor a change of profile can quietly move either of those.
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
    spec = importlib.util.spec_from_file_location(f"story_core_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


STORIES = load_script("story_core")
CANDIDATES = load_script("candidate_core")
RESUMES = load_script("resume_core")
EVIDENCE = load_script("evidence_units")
# Registering a snapshot re-checks the tables that answers and applications live in, because
# a profile change invalidates rows in both. They are initialised here for that reason only.
ANSWERS = load_script("answer_library")
APPLICATIONS = load_script("application_core")
PRE_SUBMIT = load_script("pre_submit_core")

AT = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)

SITUATION = "INNSCI needed a read on three products."
TASK = "I was asked to run the qualitative work."
ACTION = "I ran 2 focus groups."
RESULT = "Sales rose 17 percent."
FRAMING = "It is the piece of work I would show first."


def story_content(**overrides):
    content = {
        "title": "INNSCI focus groups",
        "star": {"situation": SITUATION, "task": TASK, "action": ACTION, "result": RESULT},
        "primary_capability": "cap.survey-design",
        "secondary_capabilities": [],
        "domains": ["cap.domain.pharma-insights"],
        "earned_secret": FRAMING,
        "framing_spans": [SITUATION, TASK, FRAMING],
        "claims": [
            {"claim_id": "c1", "text": ACTION, "evidence_refs": ["@focus"],
             "evidence_class": "direct"},
            {"claim_id": "c2", "text": RESULT, "evidence_refs": ["@sales"],
             "evidence_class": "direct"},
        ],
    }
    content.update(overrides)
    return content


class StoryFixture(unittest.TestCase):
    """A registered profile with two usable facts, and a helper to move it."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.store = self.root / "candidates"
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        for module in (RESUMES, ANSWERS, APPLICATIONS, PRE_SUBMIT, CANDIDATES, STORIES):
            module.initialize(self.db)
        self.snapshot = self.register(self.facts())

    def facts(self, sales_strength="direct", drop_sales=False):
        facts = [
            {"id": "fact-name", "type": "identity", "value": "Verified Candidate",
             "status": "locked", "locked": True, "evidence_strength": "direct"},
            {"id": "fact-focus", "type": "experience_claim", "value": "Ran 2 focus groups",
             "status": "confirmed", "locked": False, "evidence_strength": "direct"},
        ]
        if not drop_sales:
            facts.append({"id": "fact-sales", "type": "experience_claim",
                          "value": "Sales increased 17%", "status": "confirmed",
                          "locked": False, "evidence_strength": sales_strength})
        return facts

    def register(self, facts, name="Verified Candidate"):
        # The name is what makes one registration a different snapshot from the last: the
        # snapshot is identified by its content, so re-registering identical content is not a
        # profile change and correctly does nothing.
        facts = [dict(fact, value=name) if fact["id"] == "fact-name" else fact
                 for fact in facts]
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": False, "employer_action_required": False,
                "confirmed": True},
            "search": {}, "facts": facts}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        path = self.root / f"candidate-{candidate['content_sha256'][:12]}.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        CANDIDATES.register_snapshot(self.db, self.store, path, "user", AT)
        return candidate["content_sha256"]

    def refs(self, snapshot=None):
        snapshot = snapshot or self.snapshot
        return {"@focus": EVIDENCE.unit_id(snapshot, "fact-focus"),
                "@sales": EVIDENCE.unit_id(snapshot, "fact-sales")}

    def bind(self, content, snapshot=None):
        """Replace the readable placeholders with the EvidenceUnit ids they stand for."""
        table = self.refs(snapshot)
        for claim in content["claims"]:
            claim["evidence_refs"] = [table.get(ref, ref) for ref in claim["evidence_refs"]]
        return content

    def drafted(self, **overrides):
        return STORIES.draft_version(self.db, self.bind(story_content(**overrides)), at=AT)

    def approved(self, **overrides):
        drafted = self.drafted(**overrides)
        STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"],
                                "user", AT)
        return drafted


class BindingTests(StoryFixture):

    def test_a_story_is_usable_when_every_span_is_accounted_for(self):
        drafted = self.approved()
        self.assertEqual(drafted["unbound_spans"], [])
        self.assertTrue(STORIES.selectable(self.db, drafted["version_id"])["selectable"])

    def test_narrative_bound_to_nothing_makes_the_version_unusable(self):
        """T1. Not only numbers: a qualitative responsibility nobody evidenced counts too."""
        drafted = self.drafted(framing_spans=[SITUATION, FRAMING])
        self.assertEqual(drafted["unbound_spans"], [TASK])
        self.assertFalse(STORIES.selectable(self.db, drafted["version_id"])["selectable"])
        self.assertIn("narrative_not_fully_bound",
                      STORIES.selectable(self.db, drafted["version_id"])["reasons"])

    def test_unaccounted_narrative_cannot_be_approved(self):
        drafted = self.drafted(framing_spans=[SITUATION, FRAMING])
        with self.assertRaises(ValueError) as caught:
            STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"])
        self.assertIn("bound to nothing", str(caught.exception))

    def test_an_unsupported_claim_may_be_drafted_and_never_approved(self):
        """Writing down "this part has nothing behind it" is the point of the class."""
        content = story_content()
        content["claims"][1]["evidence_class"] = "unsupported"
        drafted = STORIES.draft_version(self.db, self.bind(content), at=AT)
        with self.assertRaises(ValueError) as caught:
            STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"])
        self.assertIn("supported by nothing", str(caught.exception))

    def test_a_claim_must_quote_the_story_it_belongs_to(self):
        content = story_content()
        content["claims"][1]["text"] = "Sales rose 40 percent."
        with self.assertRaises(ValueError) as caught:
            STORIES.draft_version(self.db, self.bind(content), at=AT)
        self.assertIn("does not appear in the narrative", str(caught.exception))

    def test_a_claim_citing_evidence_the_profile_does_not_have_is_refused(self):
        content = story_content()
        content["claims"][0]["evidence_refs"] = ["eu-0000000000000000"]
        with self.assertRaises(ValueError) as caught:
            STORIES.draft_version(self.db, self.bind(content), at=AT)
        self.assertIn("does not have", str(caught.exception))

    def test_a_fact_that_stops_being_usable_leaves_the_index(self):
        """A registered snapshot cannot hold a proposed fact at all — `_validate_candidate`
        refuses one — so the index's status filter is about a fact that stops being usable
        after the story was written, which is the case that has to fail closed."""
        unit = EVIDENCE.unit_id(self.snapshot, "fact-focus")
        self.assertIn(unit, STORIES.evidence_index(self.db, self.snapshot))
        self.db.execute("UPDATE candidate_facts SET status='proposed' WHERE fact_id=?",
                        ("fact-focus",))
        self.assertNotIn(unit, STORIES.evidence_index(self.db, self.snapshot))

    def test_an_approved_story_stops_being_selectable_when_its_evidence_does(self):
        drafted = self.approved()
        self.db.execute("UPDATE candidate_facts SET status='proposed' WHERE fact_id=?",
                        ("fact-sales",))
        check = STORIES.selectable(self.db, drafted["version_id"])
        self.assertFalse(check["selectable"])
        self.assertIn("evidence_no_longer_valid", check["reasons"])


class EvidenceClassTests(StoryFixture):

    def test_a_claim_cannot_be_evidenced_better_than_what_it_cites(self):
        """G2, at draft time. The rule resume claims already live under."""
        self.snapshot = self.register(self.facts(sales_strength="transferable"))
        content = story_content()
        with self.assertRaises(ValueError) as caught:
            STORIES.draft_version(self.db, self.bind(content), at=AT)
        self.assertIn("inflates its supporting evidence", str(caught.exception))

    def test_transferable_stays_transferable_through_approval_and_retrieval(self):
        """T2. The class travels with the claim; nothing along the way rounds it up."""
        self.snapshot = self.register(self.facts(sales_strength="transferable"))
        content = story_content()
        content["claims"][1]["evidence_class"] = "transferable"
        drafted = STORIES.draft_version(self.db, self.bind(content), at=AT)
        STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"])
        check = STORIES.selectable(self.db, drafted["version_id"])
        self.assertTrue(check["selectable"])
        self.assertEqual(check["evidence_classes"], {"c1": "direct", "c2": "transferable"})


class ApprovalTests(StoryFixture):

    def test_approval_names_the_exact_content_it_approves(self):
        drafted = self.drafted()
        with self.assertRaises(ValueError) as caught:
            STORIES.approve_version(self.db, drafted["version_id"], "0" * 64)
        self.assertIn("content hash does not match", str(caught.exception))
        self.assertFalse(STORIES.selectable(self.db, drafted["version_id"])["selectable"])

    def test_one_changed_character_is_a_new_version_and_a_new_approval(self):
        """T11."""
        first = self.approved()
        second = STORIES.draft_version(
            self.db, self.bind(story_content(title="INNSCI focus group")),
            story_id=first["story_id"], at=AT)
        self.assertNotEqual(second["content_sha256"], first["content_sha256"])
        self.assertNotEqual(second["version_id"], first["version_id"])
        self.assertFalse(STORIES.selectable(self.db, second["version_id"])["selectable"])
        # The approved one is still the story's current version until the successor is approved.
        self.assertTrue(STORIES.selectable(self.db, first["version_id"])["selectable"])

    def test_approving_the_successor_supersedes_the_version_it_replaces(self):
        first = self.approved()
        second = STORIES.draft_version(
            self.db, self.bind(story_content(title="INNSCI focus group")),
            story_id=first["story_id"], at=AT)
        STORIES.approve_version(self.db, second["version_id"], second["content_sha256"])
        self.assertIn("superseded_version",
                      STORIES.selectable(self.db, first["version_id"])["reasons"])

    def test_a_revoked_story_is_not_selectable_and_takes_no_new_versions(self):
        drafted = self.approved()
        STORIES.revoke(self.db, drafted["story_id"], "no_longer_true", "user", AT)
        self.assertIn("story_revoked",
                      STORIES.selectable(self.db, drafted["version_id"])["reasons"])
        with self.assertRaises(ValueError):
            STORIES.draft_version(self.db, self.bind(story_content()),
                                  story_id=drafted["story_id"], at=AT)


class SnapshotTests(StoryFixture):

    def test_a_changed_profile_makes_the_approved_version_unusable(self):
        """T10. Approval does not follow the profile it was given against."""
        drafted = self.approved()
        self.register(self.facts(), "Renamed Candidate")
        check = STORIES.selectable(self.db, drafted["version_id"])
        self.assertFalse(check["selectable"])
        self.assertIn("candidate_snapshot_changed", check["reasons"])

    def test_a_stranded_story_is_listed_with_what_moved_under_it(self):
        drafted = self.approved()
        moved = self.register(self.facts(), "Renamed Candidate")
        stranded = STORIES.stranded(self.db)
        self.assertEqual(len(stranded), 1)
        self.assertEqual(stranded[0]["story_id"], drafted["story_id"])
        self.assertTrue(stranded[0]["changes"]["identical"])
        self.assertEqual(stranded[0]["changes"]["target_snapshot_sha256"], moved)

    def test_carrying_a_story_rebinds_it_and_still_asks(self):
        drafted = self.approved()
        moved = self.register(self.facts(), "Renamed Candidate")
        successor = STORIES.prepare_successor(self.db, drafted["version_id"], at=AT)
        self.assertFalse(successor["approved"])
        self.assertEqual(successor["candidate_snapshot_sha256"], moved)
        self.assertEqual(successor["claims"][0]["evidence_refs"],
                         [EVIDENCE.unit_id(moved, "fact-focus")])
        self.assertFalse(STORIES.selectable(self.db, successor["version_id"])["selectable"])
        STORIES.approve_version(self.db, successor["version_id"], successor["content_sha256"])
        self.assertTrue(STORIES.selectable(self.db, successor["version_id"])["selectable"])

    def test_a_carry_reports_evidence_that_got_weaker_and_carries_it_weaker(self):
        drafted = self.approved()
        self.register(self.facts(sales_strength="transferable"), "Renamed Candidate")
        successor = STORIES.prepare_successor(self.db, drafted["version_id"], at=AT)
        weakened = [claim for claim in successor["restated"]["claims"]
                    if claim["claim_id"] == "c2"][0]
        self.assertEqual(weakened["status"], "evidence_weakened")
        self.assertEqual(weakened["available_class"], "transferable")
        carried = [claim for claim in successor["claims"] if claim["claim_id"] == "c2"][0]
        self.assertEqual(carried["evidence_class"], "transferable")

    def test_a_carry_stops_when_the_new_profile_lost_a_cited_fact(self):
        drafted = self.approved()
        self.register(self.facts(drop_sales=True), "Renamed Candidate")
        with self.assertRaises(ValueError) as caught:
            STORIES.prepare_successor(self.db, drafted["version_id"], at=AT)
        self.assertIn("no longer supports every claim", str(caught.exception))

    def test_a_version_written_against_a_superseded_profile_cannot_be_approved(self):
        drafted = self.drafted()
        self.register(self.facts(), "Renamed Candidate")
        with self.assertRaises(ValueError) as caught:
            STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"])
        self.assertIn("no longer active", str(caught.exception))


class UsageTests(StoryFixture):

    def test_use_count_is_read_from_events_not_set_by_a_caller(self):
        drafted = self.approved()
        self.assertEqual(STORIES.usage(self.db, drafted["story_id"])["use_count"], 0)
        STORIES.record_use(self.db, drafted["version_id"], "behavioral",
                           application_id="app-1", at=AT)
        STORIES.record_use(self.db, drafted["version_id"], "behavioral",
                           interview_id="int-1", at=AT)
        counted = STORIES.usage(self.db, drafted["story_id"])
        self.assertEqual(counted["use_count"], 2)
        self.assertEqual(counted["last_used"], AT.isoformat())

    def test_the_columns_hold_no_counter_to_disagree_with(self):
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(story_versions)")}
        self.assertNotIn("use_count", columns)
        self.assertNotIn("last_used", columns)


class ConfidentialityTests(StoryFixture):

    def test_a_confidential_story_must_name_what_it_is_confidential_to(self):
        with self.assertRaises(ValueError):
            STORIES.draft_version(self.db, self.bind(story_content()),
                                  confidentiality="employer_confidential", at=AT)
        with self.assertRaises(ValueError):
            STORIES.draft_version(self.db, self.bind(story_content()),
                                  confidentiality="application_confidential", at=AT)

    def test_confidentiality_travels_with_the_selectability_answer(self):
        drafted = STORIES.draft_version(
            self.db, self.bind(story_content()), confidentiality="employer_confidential",
            confidential_employer="Example Corp", at=AT)
        STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"])
        check = STORIES.selectable(self.db, drafted["version_id"])
        self.assertEqual(check["confidentiality"], "employer_confidential")
        self.assertEqual(check["confidential_employer"], "Example Corp")


class PrivacyTests(StoryFixture):

    def test_no_story_text_reaches_the_event_log(self):
        """T12. Events say what happened, never what the story says."""
        self.approved()
        logged = " ".join(
            f"{row['event_type']} {row['reason_code']} {row['metadata_json']}"
            for row in self.db.execute("SELECT * FROM story_events"))
        for value in (SITUATION, TASK, ACTION, RESULT, FRAMING, "INNSCI focus groups"):
            self.assertNotIn(value, logged)


class AccountingTests(unittest.TestCase):
    """The span accounting on its own, where its edges are easiest to state."""

    def test_punctuation_left_over_is_not_unbound_material(self):
        self.assertEqual(STORIES.account_for("I ran 2 focus groups. ", ["I ran 2 focus groups"]),
                         [])

    def test_a_repeated_sentence_is_not_covered_twice_by_one_span(self):
        residue = STORIES.account_for("It doubled. It doubled.", ["It doubled."])
        self.assertEqual(residue, ["It doubled."])

    def test_uncovered_text_comes_back_in_readable_pieces(self):
        self.assertEqual(
            STORIES.account_for("A happened. B happened. C happened.", ["B happened."]),
            ["A happened.", "C happened."])


if __name__ == "__main__":
    unittest.main()
