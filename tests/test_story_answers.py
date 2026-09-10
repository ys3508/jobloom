"""What a story is allowed to answer, and everything it has to get past first.

The two reference implementations both stop short here: neither has a Legal / Immigration /
work-authorization / salary category on the question classifier, and neither has a scope or an
expiry on the answer it saves. So most of these tests are about the gates, in the order
consequence demands, and only two of them are about a story actually becoming an answer.
"""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"story_answers_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


PROPOSE = load_script("story_answers")
STORIES = load_script("story_core")
ANSWERS = load_script("answer_library")
CANDIDATES = load_script("candidate_core")
RESUMES = load_script("resume_core")
APPLICATIONS = load_script("application_core")
PRE_SUBMIT = load_script("pre_submit_core")
EVIDENCE = load_script("evidence_units")
APPLICATION_CORE = load_script("application_core")

AT = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
LATER = AT + timedelta(days=400)

QUESTION = "Tell us about a time you ran user research."
CANONICAL = "experience.user_research_story"
COMPETENCY = "cap.survey-design"

ACTION = "I ran 2 focus groups."
RESULT = "Sales rose 17 percent."
SITUATION = "INNSCI needed a read on three products."
TASK = "I was asked to run the qualitative work."


class ProposeFixture(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        for module in (RESUMES, ANSWERS, APPLICATIONS, PRE_SUBMIT, CANDIDATES, PROPOSE):
            module.initialize(self.db)
        self.snapshot = self.register()
        self.register_form(QUESTION, CANONICAL)
        PROPOSE.record_question_competency(self.db, CANONICAL, COMPETENCY, "user", AT)
        self.an_application("app-1", "Example Corp")
        self.story = self.approve_story()

    def an_application(self, application_id, employer):
        job_id = f"job-{application_id}"
        self.db.execute(
            "INSERT OR REPLACE INTO jobs (job_id, canonical_url, original_url, employer, "
            "title, location, normalized_employer, normalized_title, normalized_location, "
            "description_sha256, job_card_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'Analyst', 'Boston', ?, 'analyst', 'boston', 'x', '{}', "
            "'open', ?, ?)",
            (job_id, f"https://example.invalid/{job_id}", f"https://example.invalid/{job_id}",
             employer, APPLICATION_CORE.normalize_text(employer), AT.isoformat(),
             AT.isoformat()))
        self.db.execute(
            "INSERT OR REPLACE INTO applications (application_id, job_id, state, category, "
            "submission_policy, attempts, max_attempts, pre_submit_check_passed, created_at, "
            "updated_at) VALUES (?, ?, 'ready_to_fill', 'review', 'stop_before_submit', 0, 3, "
            "0, ?, ?)",
            (application_id, job_id, AT.isoformat(), AT.isoformat()))
        self.db.commit()
        return application_id

    def register(self, name="Verified Candidate", strength="direct"):
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": False, "employer_action_required": False,
                "confirmed": True},
            "search": {},
            "facts": [
                {"id": "fact-name", "type": "identity", "value": name, "status": "locked",
                 "locked": True, "evidence_strength": "direct"},
                {"id": "fact-focus", "type": "experience_claim",
                 "value": "Ran 2 focus groups", "status": "confirmed", "locked": False,
                 "evidence_strength": strength},
                {"id": "fact-sales", "type": "experience_claim",
                 "value": "Sales increased 17%", "status": "confirmed", "locked": False,
                 "evidence_strength": strength}]}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        path = self.root / f"candidate-{candidate['content_sha256'][:12]}.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        CANDIDATES.register_snapshot(self.db, self.root / "store", path, "user", AT)
        return candidate["content_sha256"]

    def register_form(self, question, canonical_id):
        self.db.execute(
            "INSERT OR REPLACE INTO question_forms (normalized_question, canonical_id, "
            "match_level, verified_by_user, created_at) VALUES (?, ?, 'exact', 1, ?)",
            (ANSWERS.normalize_question(question), canonical_id, AT.isoformat()))
        self.db.commit()

    def approve_story(self, evidence_class="direct", **kwargs):
        content = {
            "title": "INNSCI focus groups",
            "star": {"situation": SITUATION, "task": TASK, "action": ACTION, "result": RESULT},
            "primary_capability": {"capability_id": COMPETENCY, "claim_ids": ["c1", "c2"]},
            "secondary_capabilities": [],
            "domains": ["cap.domain.pharma-insights"],
            "framing_spans": [SITUATION, TASK],
            "claims": [
                {"claim_id": "c1", "text": ACTION,
                 "evidence_refs": [EVIDENCE.unit_id(self.snapshot, "fact-focus")],
                 "evidence_class": evidence_class},
                {"claim_id": "c2", "text": RESULT,
                 "evidence_refs": [EVIDENCE.unit_id(self.snapshot, "fact-sales")],
                 "evidence_class": evidence_class}]}
        drafted = STORIES.draft_version(self.db, content, at=AT, **kwargs)
        STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"],
                                "user", AT)
        return drafted

    def ask(self, **overrides):
        call = {"application_id": "app-1", "field_id": "q_story", "question": QUESTION,
                "control": "textarea", "at": AT}
        call.update(overrides)
        return PROPOSE.propose(self.db, **call)


class GateOrderTests(ProposeFixture):

    def test_a_stop_boundary_item_stops_the_page_before_anything_else(self):
        """T7. Composed from `pre_submit_core.MANDATORY_PAUSES`, not restated here."""
        outcome = self.ask(legal_items=["arbitration"])
        self.assertEqual((outcome["decision"], outcome["reason"]), ("pause", "stop_boundary"))
        self.assertEqual(outcome["stop_items"], ["arbitration"])
        self.assertEqual(self.drafts(), 0)

    def test_a_stop_control_stops_the_page(self):
        outcome = self.ask(control="captcha")
        self.assertEqual(outcome["reason"], "stop_boundary")

    def test_voluntary_eeo_is_handed_back_and_no_story_is_offered(self):
        """T7. A protected characteristic never reaches story material."""
        outcome = self.ask(field_id="eeo_race", question="Please select your race/ethnicity.")
        self.assertEqual((outcome["decision"], outcome["reason"]),
                         ("manual", "always_manual"))
        self.assertEqual(outcome["domain"], "voluntary_eeo")
        self.assertEqual(self.drafts(), 0)

    def test_salary_expectation_is_always_manual(self):
        outcome = self.ask(field_id="comp", question="What is your expected base salary?")
        self.assertEqual(outcome["reason"], "always_manual")
        self.assertEqual(outcome["domain"], "compensation")

    def test_a_sponsorship_question_never_reaches_a_story(self):
        outcome = self.ask(field_id="visa",
                           question="Will you now or in the future require sponsorship?")
        self.assertEqual(outcome["reason"], "always_manual")
        self.assertEqual(outcome["domain"], "sponsorship")
        self.assertEqual(self.drafts(), 0)

    def test_an_unknown_question_pauses_rather_than_being_classified(self):
        outcome = self.ask(question="What is your favourite deployment strategy?")
        self.assertEqual((outcome["decision"], outcome["reason"]),
                         ("pause", "unknown_question_form"))

    def test_a_file_control_is_material_not_narrative(self):
        outcome = self.ask(control="file")
        self.assertEqual((outcome["decision"], outcome["reason"]),
                         ("manual", "material_field"))

    def test_a_supported_but_sensitive_meaning_takes_exact_reuse_or_nothing(self):
        """`discovery_source` routes to `answer`, so it is supported — and still not composed."""
        question = "How did you hear about us?"
        self.register_form(question, "discovery.source")
        outcome = self.ask(field_id="source", question=question)
        self.assertEqual((outcome["decision"], outcome["reason"]),
                         ("pause", "sensitive_requires_exact_answer"))
        self.assertEqual(outcome["domain"], "discovery_source")
        self.assertEqual(self.drafts(), 0)

    def test_a_question_with_no_reviewed_competency_pauses(self):
        self.db.execute("DELETE FROM question_form_competencies WHERE canonical_id=?",
                        (CANONICAL,))
        self.db.commit()
        outcome = self.ask()
        self.assertEqual((outcome["decision"], outcome["reason"]),
                         ("pause", "competency_not_mapped"))

    def test_an_unknown_application_is_not_answered_for(self):
        outcome = self.ask(application_id="app-missing")
        self.assertEqual((outcome["decision"], outcome["reason"]),
                         ("pause", "application_unknown"))

    def drafts(self):
        return self.db.execute("SELECT COUNT(*) FROM story_answer_drafts").fetchone()[0]


class ReuseTests(ProposeFixture):

    def store_answer(self, auto_fill=True, expires_at=None):
        ANSWERS.add_answer(self.db, {
            "answer_id": "ans-1", "canonical_id": CANONICAL, "canonical_meaning": CANONICAL,
            "answer": "The stored answer.", "answer_type": "open_text_template",
            "source_type": "user_confirmed", "confirmation_status": "confirmed",
            "confirmed_at": AT.isoformat(), "validity_class": "stable",
            "expires_at": expires_at, "scope": {},
            "auto_fill_allowed": auto_fill, "auto_submit_allowed": False})

    def authorize(self):
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-1", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=7)).isoformat(),
            "scope": {"company": "Example Corp"}})
        return "auth-1"

    def test_an_exact_stored_answer_is_reused_and_no_story_is_touched(self):
        """T8. The cheap rung stays cheap: no story retrieval, no draft row."""
        self.store_answer()
        outcome = self.ask(authorization_id=self.authorize())
        self.assertEqual(outcome["decision"], "reuse")
        self.assertEqual(outcome["answer"], "The stored answer.")
        self.assertTrue(outcome["auto_fill_ready"])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM story_answer_drafts").fetchone()[0], 0)

    def test_authorization_does_not_revive_an_expired_answer(self):
        """T4. Two channels: the grant is current and the answer is still expired."""
        self.store_answer(expires_at=(AT + timedelta(days=1)).isoformat())
        outcome = PROPOSE.propose(
            self.db, application_id="app-1", field_id="q_story", question=QUESTION,
            control="textarea", authorization_id=self.authorize(), at=LATER)
        self.assertNotEqual(outcome["decision"], "reuse")

    def test_a_stale_answer_falls_through_to_the_story_choice(self):
        self.store_answer(expires_at=(AT + timedelta(days=1)).isoformat())
        outcome = PROPOSE.propose(
            self.db, application_id="app-1", field_id="q_story", question=QUESTION,
            control="textarea", at=LATER)
        self.assertEqual(outcome["decision"], "choose")


class ChoiceTests(ProposeFixture):

    def test_the_user_chooses_and_none_of_these_is_offered(self):
        outcome = self.ask()
        self.assertEqual(outcome["decision"], "choose")
        self.assertEqual(outcome["allow_none"], "none_of_these")
        self.assertEqual(len(outcome["options"]), 1)
        self.assertEqual(outcome["options"][0]["version_id"], self.story["version_id"])
        self.assertEqual(outcome["options"][0]["fit"], "strong")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM story_answer_drafts").fetchone()[0], 0)

    def test_choosing_none_is_a_gap_and_writes_nothing(self):
        outcome = self.ask(chosen_version_id="none_of_these")
        self.assertEqual((outcome["decision"], outcome["reason"]), ("gap", "user_chose_none"))
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM story_answer_drafts").fetchone()[0], 0)

    def test_a_competency_no_story_covers_is_a_gap(self):
        self.db.execute("DELETE FROM question_form_competencies WHERE canonical_id=?",
                        (CANONICAL,))
        self.db.commit()
        PROPOSE.record_question_competency(self.db, CANONICAL,
                                           "cap.clinical-study-operations", "user", AT)
        outcome = self.ask()
        self.assertEqual((outcome["decision"], outcome["reason"]),
                         ("gap", "no_story_covers_this"))

    def test_a_version_that_was_not_offered_cannot_be_chosen(self):
        outcome = self.ask(chosen_version_id="SV-somewhere-else")
        self.assertEqual((outcome["decision"], outcome["reason"]),
                         ("pause", "choice_not_offered"))

    def test_a_confidential_story_is_not_offered_to_another_employer(self):
        """T3, reaching the answer path, with the employer read from the application."""
        confidential = self.approve_story(confidentiality="employer_confidential",
                                          confidential_employer="Other Corp")
        offered = self.ask()["options"]
        self.assertNotIn(confidential["version_id"],
                         [option["version_id"] for option in offered])
        # And it is offered to the employer it belongs to, so the gate is a gate and not a
        # blanket refusal.
        self.an_application("app-other", "Other Corp")
        offered_there = self.ask(application_id="app-other")["options"]
        self.assertIn(confidential["version_id"],
                      [option["version_id"] for option in offered_there])


class DraftTests(ProposeFixture):

    def drafted(self, **overrides):
        return self.ask(chosen_version_id=self.story["version_id"], **overrides)

    def test_a_draft_is_assembled_from_the_approved_version_verbatim(self):
        outcome = self.drafted()
        self.assertEqual(outcome["decision"], "drafted")
        for span in (SITUATION, TASK, ACTION, RESULT):
            self.assertIn(span, outcome["answer_text"])
        self.assertFalse(outcome["auto_fill_ready"])

    def test_a_draft_records_what_it_would_need_to_be_judged_by(self):
        outcome = self.drafted()
        self.assertEqual(outcome["candidate_snapshot_sha256"], self.snapshot)
        self.assertEqual(outcome["dependent_fact_ids"], ["fact-focus", "fact-sales"])
        self.assertEqual(sorted(outcome["evidence_refs"]),
                         sorted([EVIDENCE.unit_id(self.snapshot, "fact-focus"),
                                 EVIDENCE.unit_id(self.snapshot, "fact-sales")]))
        row = self.db.execute("SELECT * FROM story_answer_drafts").fetchone()
        self.assertEqual(row["application_id"], "app-1")
        self.assertEqual(row["employer"], "Example Corp")
        self.assertEqual(row["canonical_id"], CANONICAL)

    def test_a_draft_is_not_an_answer(self):
        self.drafted()
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM answers WHERE canonical_id=?", (CANONICAL,)).fetchone()[0], 0)

    def test_transferable_evidence_is_named_and_never_auto_fill(self):
        """T2 at the answer boundary."""
        self.snapshot = self.register(strength="transferable")
        self.story = self.approve_story(evidence_class="transferable")
        outcome = self.drafted()
        self.assertEqual(outcome["evidence_class"], "transferable")
        self.assertIn("adjacent experience", outcome["bridge"])
        self.assertFalse(outcome["auto_fill_ready"])


class ApprovalTests(ProposeFixture):

    def draft(self):
        return self.ask(chosen_version_id=self.story["version_id"])

    def approve(self, drafted, **overrides):
        call = {"scope": {"company": "Example Corp"}, "validity_class": "stable",
                "at": AT}
        call.update(overrides)
        return PROPOSE.approve_draft(self.db, drafted["draft_id"],
                                     drafted["content_sha256"], **call)

    def test_approval_writes_one_answer_into_the_existing_library(self):
        """T9. And it is the AnswerLibrary, not a second store."""
        approved = self.approve(self.draft())
        row = self.db.execute("SELECT * FROM answers WHERE answer_id=?",
                              (approved["answer_id"],)).fetchone()
        self.assertEqual(row["canonical_id"], CANONICAL)
        self.assertEqual(row["source_type"], "user_confirmed")
        self.assertEqual(json.loads(row["scope_json"]), {"company": "Example Corp"})
        self.assertEqual(json.loads(row["dependent_fact_ids_json"]),
                         ["fact-focus", "fact-sales"])
        self.assertEqual(row["auto_submit_allowed"], 0)

    def test_approval_authorizes_reuse_and_says_what_it_does_not(self):
        approved = self.approve(self.draft())
        self.assertEqual(approved["authorizes"], "reuse_of_this_answer_only")
        self.assertIn("submit", approved["does_not_authorize"])
        self.assertFalse(approved["auto_submit_allowed"])

    def test_approval_names_the_exact_draft_it_approves(self):
        drafted = self.draft()
        with self.assertRaises(ValueError) as caught:
            PROPOSE.approve_draft(self.db, drafted["draft_id"], "0" * 64,
                                  scope={}, validity_class="stable", at=AT)
        self.assertIn("content hash does not match", str(caught.exception))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 0)

    def test_a_draft_whose_story_stopped_being_usable_cannot_be_approved(self):
        """T10 reaching the answer path: the profile moved between drafting and approving."""
        drafted = self.draft()
        self.register(name="Renamed Candidate")
        with self.assertRaises(ValueError) as caught:
            self.approve(drafted)
        self.assertIn("no longer usable", str(caught.exception))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 0)

    def test_transferable_evidence_cannot_be_approved_as_auto_fill(self):
        self.snapshot = self.register(strength="transferable")
        self.story = self.approve_story(evidence_class="transferable")
        drafted = self.draft()
        with self.assertRaises(ValueError):
            self.approve(drafted, auto_fill_allowed=True)

    def test_an_approved_answer_is_then_the_path_next_time_not_a_second_draft(self):
        """Approved without auto-fill, so it comes back for review rather than autofilling —
        and either way a story does not compose a second text for the same meaning."""
        approved = self.approve(self.draft())
        again = self.ask()
        self.assertEqual(again["decision"], "review_existing_answer")
        self.assertEqual(again["answer_id"], approved["answer_id"])
        self.assertFalse(again["auto_fill_ready"])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM story_answer_drafts").fetchone()[0], 1)

    def test_an_answer_approved_for_auto_fill_is_reused_outright(self):
        drafted = self.draft()
        approved = self.approve(drafted, auto_fill_allowed=True)
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-2", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=7)).isoformat(),
            "scope": {"company": "Example Corp"}})
        again = self.ask(authorization_id="auth-2")
        self.assertEqual(again["decision"], "reuse")
        self.assertEqual(again["answer_id"], approved["answer_id"])
        self.assertTrue(again["auto_fill_ready"])

    def test_a_draft_is_approved_once(self):
        drafted = self.draft()
        self.approve(drafted)
        with self.assertRaises(ValueError):
            self.approve(drafted)


class PrivacyTests(ProposeFixture):

    def test_no_answer_text_reaches_the_event_log(self):
        """T12. The draft row is the review surface; the log holds identifiers."""
        drafted = self.ask(chosen_version_id=self.story["version_id"])
        PROPOSE.approve_draft(self.db, drafted["draft_id"], drafted["content_sha256"],
                              scope={}, validity_class="stable", at=AT)
        logged = " ".join(
            f"{row['event_type']} {row['reason_code']} {row['metadata_json']}"
            for row in self.db.execute("SELECT * FROM story_events"))
        for value in (SITUATION, TASK, ACTION, RESULT):
            self.assertNotIn(value, logged)


if __name__ == "__main__":
    unittest.main()


class NoWidestPathTests(ProposeFixture):
    """Three ways a caller could have reached further than it was entitled to.

    Each was a parameter the service trusted. The fix in every case is the same shape: the
    caller names the application, and the service reads the rest out of rows somebody
    registered — so the tests are that the parameter is gone and that the resolved value is
    what decides.
    """

    def test_the_caller_cannot_name_the_employer(self):
        with self.assertRaises(TypeError):
            self.ask(employer="Other Corp")

    def test_naming_an_application_at_another_employer_does_not_reach_its_stories(self):
        confidential = self.approve_story(confidentiality="employer_confidential",
                                          confidential_employer="Other Corp")
        self.an_application("app-other", "Other Corp")
        # The story is reachable from its own employer's application...
        self.assertIn(confidential["version_id"],
                      [row["version_id"] for row in self.ask(application_id="app-other")
                       ["options"]])
        # ...and not from this one, whatever the caller would like to claim.
        self.assertNotIn(confidential["version_id"],
                         [row["version_id"] for row in self.ask()["options"]])

    def test_the_caller_cannot_name_the_competency(self):
        with self.assertRaises(TypeError):
            self.ask(competency="cap.statistical-programming")

    def test_a_competency_reaches_only_what_the_review_mapped(self):
        """A second capability the story has, which this question was never mapped to."""
        self.db.execute("DELETE FROM question_form_competencies")
        self.db.commit()
        outcome = self.ask()
        self.assertEqual(outcome["reason"], "competency_not_mapped")
        PROPOSE.record_question_competency(self.db, CANONICAL, COMPETENCY, "user", AT)
        self.assertEqual(self.ask()["decision"], "choose")

    def test_a_mapping_names_a_reviewed_capability(self):
        with self.assertRaises(ValueError):
            PROPOSE.record_question_competency(self.db, CANONICAL, "cap.invented", "user", AT)


class QuestionFormLockTests(ProposeFixture):
    """The draft records which mapping said what the question means, not just the meaning."""

    def draft(self):
        return self.ask(chosen_version_id=self.story["version_id"])

    def test_a_draft_records_the_form_that_authorized_its_meaning(self):
        drafted = self.draft()
        self.assertEqual(drafted["question_form_sha256"],
                         PROPOSE.question_form_digest(self.db, QUESTION))
        row = self.db.execute("SELECT question_form_sha256 FROM story_answer_drafts").fetchone()
        self.assertEqual(row["question_form_sha256"], drafted["question_form_sha256"])

    def test_a_remapped_question_cannot_be_approved_under_the_old_meaning(self):
        drafted = self.draft()
        self.db.execute(
            "UPDATE question_forms SET canonical_id=? WHERE normalized_question=?",
            ("experience.something_else", ANSWERS.normalize_question(QUESTION)))
        self.db.commit()
        with self.assertRaises(ValueError) as caught:
            PROPOSE.approve_draft(self.db, drafted["draft_id"], drafted["content_sha256"],
                                  scope={}, validity_class="stable", at=AT)
        self.assertIn("question form changed", str(caught.exception))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 0)

    def test_a_second_canonical_id_joining_the_question_invalidates_the_draft(self):
        drafted = self.draft()
        self.register_form(QUESTION, "experience.second_meaning")
        with self.assertRaises(ValueError):
            PROPOSE.approve_draft(self.db, drafted["draft_id"], drafted["content_sha256"],
                                  scope={}, validity_class="stable", at=AT)

    def test_an_unchanged_form_still_approves(self):
        drafted = self.draft()
        approved = PROPOSE.approve_draft(self.db, drafted["draft_id"],
                                         drafted["content_sha256"], scope={},
                                         validity_class="stable", at=AT)
        self.assertEqual(approved["status"], "approved")


class BoundEvidenceAtTheAnswerTests(ProposeFixture):
    """The draft cites the evidence behind the capability retrieved, not the whole story."""

    def test_a_draft_cites_only_the_claims_the_binding_names(self):
        content = {
            "title": "INNSCI focus groups",
            "star": {"situation": SITUATION, "task": TASK, "action": ACTION, "result": RESULT},
            "primary_capability": {"capability_id": COMPETENCY, "claim_ids": ["c1"]},
            "secondary_capabilities": [],
            "domains": ["cap.domain.pharma-insights"],
            "framing_spans": [SITUATION, TASK],
            "claims": [
                {"claim_id": "c1", "text": ACTION,
                 "evidence_refs": [EVIDENCE.unit_id(self.snapshot, "fact-focus")],
                 "evidence_class": "direct"},
                {"claim_id": "c2", "text": RESULT,
                 "evidence_refs": [EVIDENCE.unit_id(self.snapshot, "fact-sales")],
                 "evidence_class": "direct"}]}
        drafted_story = STORIES.draft_version(self.db, content, at=AT)
        STORIES.approve_version(self.db, drafted_story["version_id"],
                                drafted_story["content_sha256"], "user", AT)
        outcome = self.ask(chosen_version_id=drafted_story["version_id"])
        # The binding is what the capability was retrieved on...
        self.assertEqual(outcome["binding_claim_ids"], ["c1"])
        # ...and the provenance covers the whole text, because the whole text is the answer.
        # This assertion was the other way round once, which left the rendered answer
        # asserting `c2` with no reference to the evidence behind it.
        self.assertEqual(outcome["dependent_fact_ids"], ["fact-focus", "fact-sales"])
        self.assertEqual(sorted(outcome["evidence_refs"]),
                         sorted([EVIDENCE.unit_id(self.snapshot, "fact-focus"),
                                 EVIDENCE.unit_id(self.snapshot, "fact-sales")]))


class EveryAssertionInTheAnswerIsCoveredTests(ProposeFixture):
    """The answer is the whole story, so provenance has to be the whole story too.

    The defect this closes: `evidence_refs` and `dependent_fact_ids` were narrowed to the
    claims the capability binding named, while the rendered text stayed the entire version.
    An answer therefore asserted things whose evidence was not recorded — and, worse, whose
    loss would not have invalidated the answer, because the fact was not among its
    dependencies. Two questions had been collapsed into one: what evidences this capability
    (binding-scoped) and what does this text assert (text-scoped).
    """

    UNRELATED = "I ran 2 focus groups."
    TARGET = "Sales rose 17 percent."

    def mixed_story(self):
        """The target capability rests on `c2` (transferable); `c1` is direct and unrelated."""
        self.snapshot = self.register_mixed()
        content = {
            "title": "INNSCI focus groups",
            "star": {"situation": SITUATION, "task": TASK, "action": ACTION, "result": RESULT},
            "primary_capability": {"capability_id": COMPETENCY, "claim_ids": ["c2"]},
            "secondary_capabilities": [],
            "domains": ["cap.domain.pharma-insights"],
            "framing_spans": [SITUATION, TASK],
            "claims": [
                {"claim_id": "c1", "text": self.UNRELATED,
                 "evidence_refs": [EVIDENCE.unit_id(self.snapshot, "fact-focus")],
                 "evidence_class": "direct"},
                {"claim_id": "c2", "text": self.TARGET,
                 "evidence_refs": [EVIDENCE.unit_id(self.snapshot, "fact-sales")],
                 "evidence_class": "transferable"}]}
        drafted = STORIES.draft_version(self.db, content, at=AT)
        STORIES.approve_version(self.db, drafted["version_id"], drafted["content_sha256"],
                                "user", AT)
        return drafted

    def register_mixed(self):
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
                 "evidence_strength": "transferable"}]}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        path = self.root / f"candidate-{candidate['content_sha256'][:12]}.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        CANDIDATES.register_snapshot(self.db, self.root / "store", path, "user", AT)
        return candidate["content_sha256"]

    def test_every_claim_rendered_into_the_answer_carries_its_own_evidence(self):
        story = self.mixed_story()
        outcome = self.ask(chosen_version_id=story["version_id"])
        rendered = outcome["answer_text"]
        claims = self.db.execute(
            "SELECT claim_id, claim_text, evidence_refs_json FROM story_claims "
            "WHERE version_id=?", (story["version_id"],)).fetchall()
        cited = set(outcome["evidence_refs"])
        for claim in claims:
            with self.subTest(claim=claim["claim_id"]):
                # If its text is in the answer, its evidence must be in the answer's refs.
                self.assertIn(claim["claim_text"], rendered)
                for ref in json.loads(claim["evidence_refs_json"]):
                    self.assertIn(ref, cited)

    def test_the_unrelated_direct_claim_is_a_dependency_of_the_answer(self):
        story = self.mixed_story()
        outcome = self.ask(chosen_version_id=story["version_id"])
        self.assertIn(self.UNRELATED, outcome["answer_text"])
        self.assertIn("fact-focus", outcome["dependent_fact_ids"])
        self.assertIn("fact-sales", outcome["dependent_fact_ids"])
        self.assertEqual(outcome["binding_claim_ids"], ["c2"])

    def test_losing_the_unrelated_fact_invalidates_the_approved_answer(self):
        """The point of recording it: the dependency has to reach the library's machinery."""
        story = self.mixed_story()
        drafted = self.ask(chosen_version_id=story["version_id"])
        approved = PROPOSE.approve_draft(self.db, drafted["draft_id"],
                                         drafted["content_sha256"], scope={},
                                         validity_class="stable", at=AT)
        row = self.db.execute("SELECT dependent_fact_ids_json FROM answers WHERE answer_id=?",
                              (approved["answer_id"],)).fetchone()
        self.assertIn("fact-focus", json.loads(row["dependent_fact_ids_json"]))

    def test_the_capability_is_still_read_at_its_own_evidence(self):
        """Widening provenance must not widen the class back out again."""
        story = self.mixed_story()
        outcome = self.ask(chosen_version_id=story["version_id"])
        self.assertEqual(outcome["evidence_class"], "transferable")
        mapped = STORIES.map_stories(self.db, [COMPETENCY], application_id="app-1")
        self.assertEqual(mapped[COMPETENCY]["fit"], "transferable")


class TheQualifierSurvivesReuseTests(EveryAssertionInTheAnswerIsCoveredTests):
    """`transferable never upgrades` has to hold on the *second* form, not just the first.

    The defect this closes: the adjacent-evidence note lived in the draft's `bridge` column,
    beside the answer rather than in it. Only `answer_text` was written to the AnswerLibrary,
    so the approved answer came back on the next form reading exactly like one drawn from
    direct evidence. The rule was intact and the reuse path walked around it.
    """

    def approved_answer(self):
        story = self.mixed_story()
        drafted = self.ask(chosen_version_id=story["version_id"])
        approved = PROPOSE.approve_draft(self.db, drafted["draft_id"],
                                         drafted["content_sha256"], scope={},
                                         validity_class="stable", at=AT)
        return drafted, approved

    def test_the_drafted_text_already_carries_the_qualification(self):
        story = self.mixed_story()
        drafted = self.ask(chosen_version_id=story["version_id"])
        self.assertTrue(drafted["qualified"])
        self.assertIn("Adjacent experience", drafted["answer_text"])
        self.assertIn("transferable rather than direct", drafted["answer_text"])
        # It is in what the user approves, so approval covers it.
        self.assertIn("answer_text", drafted)

    def test_the_stored_answer_text_carries_it(self):
        _, approved = self.approved_answer()
        stored = json.loads(self.db.execute(
            "SELECT answer_json FROM answers WHERE answer_id=?",
            (approved["answer_id"],)).fetchone()["answer_json"])
        self.assertIn("Adjacent experience", stored)
        self.assertIn("transferable rather than direct", stored)

    def test_the_text_handed_back_on_reuse_carries_it(self):
        """The one that matters: a later form gets the qualification, not a bare story."""
        _, approved = self.approved_answer()
        self.db.execute("UPDATE answers SET auto_fill_allowed=1 WHERE answer_id=?",
                        (approved["answer_id"],))
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-reuse", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=7)).isoformat(),
            "scope": {"company": "Example Corp"}})
        self.db.commit()
        again = self.ask(authorization_id="auth-reuse")
        self.assertEqual(again["decision"], "reuse")
        self.assertIn("Adjacent experience", again["answer"])
        self.assertIn(COMPETENCY, again["answer"])

    def test_a_wholly_direct_answer_is_not_qualified(self):
        """The qualifier is a statement about the evidence, not decoration on every answer."""
        drafted = self.ask(chosen_version_id=self.story["version_id"])
        self.assertFalse(drafted["qualified"])
        self.assertIsNone(drafted["bridge"])
        self.assertNotIn("Adjacent experience", drafted["answer_text"])

    def test_a_qualified_answer_cannot_be_approved_as_auto_fill(self):
        story = self.mixed_story()
        drafted = self.ask(chosen_version_id=story["version_id"])
        with self.assertRaises(ValueError):
            PROPOSE.approve_draft(self.db, drafted["draft_id"], drafted["content_sha256"],
                                  scope={}, validity_class="stable",
                                  auto_fill_allowed=True, at=AT)

    def test_a_weak_claim_anywhere_in_the_text_qualifies_it(self):
        """Even when the capability's own binding is direct, the text still says the weak part."""
        self.snapshot = self.register_mixed()
        content = {
            "title": "INNSCI focus groups",
            "star": {"situation": SITUATION, "task": TASK, "action": ACTION, "result": RESULT},
            # Bound to the direct claim only — the retrieval band is `strong`...
            "primary_capability": {"capability_id": COMPETENCY, "claim_ids": ["c1"]},
            "secondary_capabilities": [],
            "domains": ["cap.domain.pharma-insights"],
            "framing_spans": [SITUATION, TASK],
            "claims": [
                {"claim_id": "c1", "text": self.UNRELATED,
                 "evidence_refs": [EVIDENCE.unit_id(self.snapshot, "fact-focus")],
                 "evidence_class": "direct"},
                {"claim_id": "c2", "text": self.TARGET,
                 "evidence_refs": [EVIDENCE.unit_id(self.snapshot, "fact-sales")],
                 "evidence_class": "transferable"}]}
        story = STORIES.draft_version(self.db, content, at=AT)
        STORIES.approve_version(self.db, story["version_id"], story["content_sha256"],
                                "user", AT)
        drafted = self.ask(chosen_version_id=story["version_id"])
        self.assertEqual(drafted["evidence_class"], "direct")
        # ...and the rendered text still asserts something transferable, so it is qualified.
        self.assertEqual(drafted["rendered_evidence_floor"], "transferable")
        self.assertTrue(drafted["qualified"])
        self.assertIn("Adjacent experience", drafted["answer_text"])
