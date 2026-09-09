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
APPLICATION_CORE = load_script("application_core")
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
        "primary_capability": {"capability_id": "cap.survey-design",
                               "claim_ids": ["c1", "c2"]},
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

    def an_application(self, application_id, employer):
        """A real application row, because that is the only thing identity is read from."""
        job_id = f"job-{application_id}"
        self.db.execute(
            "INSERT INTO jobs (job_id, canonical_url, original_url, employer, title, "
            "location, normalized_employer, normalized_title, normalized_location, "
            "description_sha256, job_card_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'Analyst', 'Boston', ?, 'analyst', 'boston', 'x', '{}', "
            "'open', ?, ?)",
            (job_id, f"https://example.invalid/{job_id}", f"https://example.invalid/{job_id}",
             employer, APPLICATION_CORE.normalize_text(employer), AT.isoformat(),
             AT.isoformat()))
        self.db.execute(
            "INSERT INTO applications (application_id, job_id, state, category, "
            "submission_policy, attempts, max_attempts, pre_submit_check_passed, created_at, "
            "updated_at) VALUES (?, ?, 'ready_to_fill', 'review', 'stop_before_submit', 0, 3, "
            "0, ?, ?)",
            (application_id, job_id, AT.isoformat(), AT.isoformat()))
        self.db.commit()
        return application_id

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


class MappingFixture(StoryFixture):
    """Two approved stories with different capabilities, to have something to choose between."""

    SECOND_ACTION = "I wrote the SAS analysis plan."
    SECOND_RESULT = "It cut the reporting cycle."

    def second_story(self, primary="cap.statistical-programming", secondary=(),
                     evidence_class="direct", confidentiality="reusable", **kwargs):
        content = {
            "title": "Analysis plan",
            "star": {"situation": "The team had no plan.", "task": "I owned it.",
                     "action": self.SECOND_ACTION, "result": self.SECOND_RESULT},
            "primary_capability": {"capability_id": primary, "claim_ids": ["d1"]},
            "secondary_capabilities": [{"capability_id": item, "claim_ids": ["d1"]}
                                       for item in secondary],
            "domains": ["cap.domain.statistical-analytics"],
            "framing_spans": ["The team had no plan.", "I owned it.", self.SECOND_RESULT],
            "claims": [{"claim_id": "d1", "text": self.SECOND_ACTION,
                        "evidence_refs": ["@focus"], "evidence_class": evidence_class}],
        }
        drafted = STORIES.draft_version(self.db, self.bind(content),
                                        confidentiality=confidentiality, at=AT, **kwargs)
        STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"],
                                "user", AT)
        return drafted


class StoryMappingTests(MappingFixture):

    def test_the_capability_a_story_is_about_is_a_strong_fit(self):
        drafted = self.approved()
        mapped = STORIES.map_stories(self.db, ["cap.survey-design"])
        self.assertEqual(mapped["cap.survey-design"]["fit"], "strong")
        self.assertEqual(mapped["cap.survey-design"]["stories"][0]["version_id"],
                         drafted["version_id"])
        self.assertEqual(mapped["cap.survey-design"]["stories"][0]["why"], "primary_capability")

    def test_a_secondary_capability_is_workable_not_strong(self):
        self.approved(secondary_capabilities=[
            {"capability_id": "cap.stakeholder-reporting", "claim_ids": ["c1"]}])
        mapped = STORIES.map_stories(self.db, ["cap.stakeholder-reporting"])
        self.assertEqual(mapped["cap.stakeholder-reporting"]["fit"], "workable")

    def test_a_competency_no_story_covers_is_a_gap_not_the_nearest_thing(self):
        self.approved()
        mapped = STORIES.map_stories(self.db, ["cap.clinical-study-operations"])
        self.assertEqual(mapped["cap.clinical-study-operations"]["fit"], "gap")
        self.assertEqual(mapped["cap.clinical-study-operations"]["stories"], [])

    def test_transferable_evidence_stays_a_transferable_fit(self):
        """T2 at retrieval: the band is named after the evidence, not rounded up to it."""
        self.snapshot = self.register(self.facts(sales_strength="transferable"))
        content = story_content()
        content["claims"][0]["evidence_class"] = "transferable"
        content["claims"][1]["evidence_class"] = "transferable"
        drafted = STORIES.draft_version(self.db, self.bind(content), at=AT)
        STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"])
        mapped = STORIES.map_stories(self.db, ["cap.survey-design"])
        self.assertEqual(mapped["cap.survey-design"]["fit"], "transferable")
        self.assertEqual(mapped["cap.survey-design"]["stories"][0]["evidence_class"],
                         "transferable")

    def test_a_reviewed_mapping_is_what_reaches_beyond_the_capabilities(self):
        drafted = self.approved()
        self.assertEqual(
            STORIES.map_stories(self.db, ["cap.research-design"])["cap.research-design"]["fit"],
            "gap")
        STORIES.record_mapping(self.db, drafted["version_id"], "cap.research-design",
                               ["c1"], "answers_with_limitation",
                               "the design was qualitative only", "user", AT)
        mapped = STORIES.map_stories(self.db, ["cap.research-design"])["cap.research-design"]
        self.assertEqual(mapped["fit"], "workable")
        self.assertEqual(mapped["stories"][0]["why"], "reviewed_mapping")
        self.assertEqual(mapped["stories"][0]["limitation"], "the design was qualitative only")

    def test_a_limitation_mapping_must_say_what_the_limitation_is(self):
        drafted = self.approved()
        with self.assertRaises(ValueError):
            STORIES.record_mapping(self.db, drafted["version_id"], "cap.research-design",
                                   ["c1"], "answers_with_limitation", "  ", "user", AT)

    def test_a_mapping_names_a_reviewed_capability(self):
        drafted = self.approved()
        with self.assertRaises(ValueError):
            STORIES.record_mapping(self.db, drafted["version_id"], "cap.made-up", ["c1"],
                                   "answers", None, "user", AT)


class RetrievalGateTests(MappingFixture):

    def test_an_unapproved_story_is_never_retrieved(self):
        self.drafted()
        self.assertEqual(STORIES.map_stories(self.db, ["cap.survey-design"])
                         ["cap.survey-design"]["fit"], "gap")

    def test_a_story_stranded_by_a_profile_change_is_never_retrieved(self):
        self.approved()
        self.register(self.facts(), "Renamed Candidate")
        self.assertEqual(STORIES.map_stories(self.db, ["cap.survey-design"])
                         ["cap.survey-design"]["fit"], "gap")

    def test_an_employer_confidential_story_never_surfaces_for_another_employer(self):
        """T3, with the employer read from the application rather than offered."""
        self.second_story(confidentiality="employer_confidential",
                          confidential_employer="Employer A")
        self.an_application("app-a", "Employer A")
        self.an_application("app-b", "Employer B")
        asked = ["cap.statistical-programming"]
        self.assertEqual(STORIES.map_stories(self.db, asked, application_id="app-a")
                         ["cap.statistical-programming"]["fit"], "strong")
        for application_id in ("app-b", None):
            self.assertEqual(STORIES.map_stories(self.db, asked, application_id=application_id)
                             ["cap.statistical-programming"]["fit"], "gap")

    def test_the_employer_cannot_be_named_by_the_caller_at_all(self):
        """The escalation this closes: naming the employer would be naming the password."""
        self.second_story(confidentiality="employer_confidential",
                          confidential_employer="Employer A")
        with self.assertRaises(TypeError):
            STORIES.map_stories(self.db, ["cap.statistical-programming"],
                                employer="Employer A")

    def test_an_unknown_application_carries_no_employer_identity(self):
        self.second_story(confidentiality="employer_confidential",
                          confidential_employer="Employer A")
        self.assertIsNone(STORIES.application_identity(self.db, "app-missing"))
        self.assertEqual(
            STORIES.map_stories(self.db, ["cap.statistical-programming"],
                                application_id="app-missing")
            ["cap.statistical-programming"]["fit"], "gap")

    def test_employer_identity_is_matched_on_the_normalized_name(self):
        """"Employer A, Inc." typed one way and stored another is still one employer."""
        self.second_story(confidentiality="employer_confidential",
                          confidential_employer="Employer  A")
        self.an_application("app-a", "employer a")
        self.assertEqual(
            STORIES.map_stories(self.db, ["cap.statistical-programming"],
                                application_id="app-a")
            ["cap.statistical-programming"]["fit"], "strong")

    def test_an_application_confidential_story_requires_that_exact_application(self):
        """T3."""
        self.second_story(confidentiality="application_confidential",
                          confidential_application_id="app-a")
        self.an_application("app-a", "Employer A")
        self.an_application("app-b", "Employer B")
        asked = ["cap.statistical-programming"]
        self.assertEqual(STORIES.map_stories(self.db, asked, application_id="app-a")
                         ["cap.statistical-programming"]["fit"], "strong")
        self.assertEqual(STORIES.map_stories(self.db, asked, application_id="app-b")
                         ["cap.statistical-programming"]["fit"], "gap")


class AdvisoryRankingTests(MappingFixture):
    """A model may reorder what the deterministic layer returned. That is all it may do."""

    def advisory(self, version_id, score):
        return [{"version_id": version_id, "score": score, "confidence": 0.4,
                 "provenance": {"source": "model", "model": "test-model",
                                "at": AT.isoformat()}}]

    def test_an_advisory_signal_cannot_promote_a_gap(self):
        self.approved()
        mapped = STORIES.map_stories(self.db, ["cap.clinical-study-operations"],
                                     advisory=self.advisory("SV-anything", 99.0))
        self.assertEqual(mapped["cap.clinical-study-operations"]["fit"], "gap")

    def test_an_advisory_signal_cannot_change_a_fit_or_a_class(self):
        first = self.approved()
        self.snapshot = self.snapshot  # unchanged; the second story shares the profile
        second = self.second_story(secondary=("cap.survey-design",))
        plain = STORIES.map_stories(self.db, ["cap.survey-design"])["cap.survey-design"]
        boosted = STORIES.map_stories(
            self.db, ["cap.survey-design"],
            advisory=self.advisory(second["version_id"], 99.0))["cap.survey-design"]
        self.assertEqual([row["version_id"] for row in plain["stories"]],
                         [first["version_id"], second["version_id"]])
        # The boosted story is workable and stays below the strong one however high it scores.
        self.assertEqual([(row["version_id"], row["fit"]) for row in boosted["stories"]],
                         [(first["version_id"], "strong"), (second["version_id"], "workable")])

    def test_an_advisory_signal_orders_within_a_band(self):
        first = self.approved()
        second = self.second_story(primary="cap.survey-design")
        default = STORIES.map_stories(self.db, ["cap.survey-design"])["cap.survey-design"]
        self.assertEqual({row["fit"] for row in default["stories"]}, {"strong"})
        boosted = STORIES.map_stories(
            self.db, ["cap.survey-design"],
            advisory=self.advisory(second["version_id"], 5.0))["cap.survey-design"]
        self.assertEqual(boosted["stories"][0]["version_id"], second["version_id"])
        self.assertEqual({row["version_id"] for row in boosted["stories"]},
                         {first["version_id"], second["version_id"]})

    def test_an_advisory_signal_without_provenance_is_refused(self):
        self.approved()
        for broken in ({"version_id": "SV-1", "score": 1.0},
                       {"version_id": "SV-1", "score": 1.0,
                        "provenance": {"source": "model", "model": "m"}},
                       {"version_id": "SV-1",
                        "provenance": {"source": "model", "model": "m", "at": "now"}}):
            with self.assertRaises(ValueError):
                STORIES.map_stories(self.db, ["cap.survey-design"], advisory=[broken])


class BoundEvidenceTests(StoryFixture):
    """A capability is retrieved at the class of *its own* claims.

    The hole this closes: the class used to be the strongest anywhere in the version, so a
    capability supported only by transferable evidence was retrieved as `strong` whenever
    some unrelated claim in the same story happened to be direct. That is G2 defeated by
    arithmetic over the wrong set — no rule was missing, the rule was reading the wrong rows.
    """

    def mixed_story(self):
        """`c1` direct, `c2` transferable, and the capability bound only to `c2`."""
        self.snapshot = self.register([
            {"id": "fact-name", "type": "identity", "value": "Verified Candidate",
             "status": "locked", "locked": True, "evidence_strength": "direct"},
            {"id": "fact-focus", "type": "experience_claim", "value": "Ran 2 focus groups",
             "status": "confirmed", "locked": False, "evidence_strength": "direct"},
            {"id": "fact-sales", "type": "experience_claim", "value": "Sales increased 17%",
             "status": "confirmed", "locked": False, "evidence_strength": "transferable"}])
        content = story_content()
        content["claims"][1]["evidence_class"] = "transferable"
        content["primary_capability"] = {"capability_id": "cap.survey-design",
                                         "claim_ids": ["c2"]}
        drafted = STORIES.draft_version(self.db, self.bind(content), at=AT)
        STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"])
        return drafted

    def test_a_transferable_binding_is_not_promoted_by_a_direct_claim_elsewhere(self):
        drafted = self.mixed_story()
        mapped = STORIES.map_stories(self.db, ["cap.survey-design"])["cap.survey-design"]
        self.assertEqual(mapped["fit"], "transferable")
        self.assertEqual(mapped["stories"][0]["evidence_class"], "transferable")
        self.assertEqual(mapped["stories"][0]["claim_ids"], ["c2"])
        # The version's strongest claim is still direct; it just does not evidence this.
        self.assertEqual(
            STORIES.selectable(self.db, drafted["version_id"])["evidence_classes"]["c1"],
            "direct")

    def test_a_binding_reports_the_strongest_of_its_own_claims(self):
        content = story_content()
        content["primary_capability"] = {"capability_id": "cap.survey-design",
                                         "claim_ids": ["c1"]}
        drafted = STORIES.draft_version(self.db, self.bind(content), at=AT)
        STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"])
        mapped = STORIES.map_stories(self.db, ["cap.survey-design"])["cap.survey-design"]
        self.assertEqual(mapped["fit"], "strong")
        self.assertEqual(mapped["stories"][0]["claim_ids"], ["c1"])

    def test_a_capability_must_name_the_claims_that_evidence_it(self):
        content = story_content()
        content["primary_capability"] = "cap.survey-design"
        with self.assertRaises(ValueError) as caught:
            STORIES.draft_version(self.db, self.bind(content), at=AT)
        self.assertIn("must name the claims", str(caught.exception))

    def test_a_capability_cannot_be_bound_to_a_claim_that_is_not_there(self):
        content = story_content()
        content["primary_capability"] = {"capability_id": "cap.survey-design",
                                         "claim_ids": ["c9"]}
        with self.assertRaises(ValueError) as caught:
            STORIES.draft_version(self.db, self.bind(content), at=AT)
        self.assertIn("does not have", str(caught.exception))

    def test_a_reviewed_mapping_is_read_at_its_own_claims_too(self):
        drafted = self.mixed_story()
        STORIES.record_mapping(self.db, drafted["version_id"], "cap.research-design",
                               ["c2"], "answers", None, "user", AT)
        mapped = STORIES.map_stories(self.db, ["cap.research-design"])["cap.research-design"]
        self.assertEqual(mapped["fit"], "transferable")

    def test_a_mapping_cannot_name_a_claim_the_version_does_not_have(self):
        drafted = self.approved()
        with self.assertRaises(ValueError):
            STORIES.record_mapping(self.db, drafted["version_id"], "cap.research-design",
                                   ["c9"], "answers", None, "user", AT)
