"""The domain rules that decide who may answer each class of first-form field.

`docs/lever-first-form-readiness.md` audited the reviewed Lever semantic fixture: of its 27
controls only 2 resolve to career evidence. These are its ten pre-protocol tests, written
before the worker protocol freezes a vocabulary around a form the core cannot safely finish.

The setup mirrors `tests/test_fill_core.py` on purpose rather than importing it: importing one
test module from another made results depend on which invocation ran.
"""

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"field_policy_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


FILL = load_script("fill_core")
CANDIDATES = load_script("candidate_core")
APPLICATIONS = load_script("application_core")
ANSWERS = load_script("answer_library")
RESUMES = load_script("resume_core")
COVERS = load_script("cover_letter_core")
ARCHIVE = load_script("archive_core")
PRE_SUBMIT = load_script("pre_submit_core")
POLICY = load_script("field_policy")
from tests.pdf_fixture import synthetic_pdf

from tests.fixtures.completed_page import complete_page_as_if_imported

AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class FirstFormFieldPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        APPLICATIONS.initialize(self.db)
        ANSWERS.initialize(self.db)
        RESUMES.initialize(self.db)
        COVERS.initialize(self.db)
        ARCHIVE.initialize(self.db)
        PRE_SUBMIT.initialize(self.db)
        FILL.initialize(self.db)
        CANDIDATES.initialize(self.db)
        self.addCleanup(self.db.close)
        self.candidate_path, self.manifest_path = self.make_candidate()
        CANDIDATES.register_snapshot(
            self.db, self.root / "candidates", self.candidate_path, "user", AT
        )
        self.prepare_application()

    def make_candidate(self):
        facts = [
            {"id": "fact-name", "type": "identity", "canonical_id": "contact.full_name",
             "value": "Verified Candidate", "status": "locked", "locked": True,
             "evidence_strength": "direct"},
            {"id": "fact-unlocked", "type": "location", "value": "New York",
             "status": "confirmed", "locked": False, "evidence_strength": "direct"},
        ]
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": False, "employer_action_required": False, "confirmed": True,
            },
            "search": {}, "facts": facts,
        }
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        candidate_path = self.root / "candidate.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        manifest_path = self.root / "claims.json"
        manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-name", "claim_text": "Verified Candidate", "fact_ids": ["fact-name"],
            "evidence_strength": "direct", "exact_locked_value_preserved": True,
        }]}), encoding="utf-8")
        return candidate_path, manifest_path

    def prepare_application(self):
        source = self.root / "resume.pdf"
        source.write_bytes(synthetic_pdf(["Verified Candidate"]))
        RESUMES.register_version(
            self.db, self.root / "resumes", source, "resume-1", "master_source", "general", at=AT
        )
        RESUMES.approve_version(
            self.db, "resume-1", self.candidate_path, self.manifest_path, "user", AT
        )
        card = {
            "job_id": "job-1", "canonical_url": "https://apply.example.com/jobs/1",
            "employer": "Example Corp", "title": "Backend Engineer", "location": "New York, NY",
            "country": "US", "employment_type": "full_time", "status": "open",
        }
        APPLICATIONS.ingest_job(self.db, card, at=AT)
        APPLICATIONS.create_application(self.db, "app-1", "job-1", "precision", "approved_queue", AT)
        APPLICATIONS.transition(self.db, "app-1", "pending_analysis", "system", "analysis", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "precision_recommended", "system", "match", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "approved", "user", "approved", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "materials_in_progress", "system", "materials", at=AT)
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        RESUMES.lock_materials(self.db, "app-1", lock_id="lock-1", at=AT)
        APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "ready", at=AT)
        APPLICATIONS.acquire_next(self.db, "worker-1", at=AT)
        self.add_answer(
            "answer-auth", "work_authorized_now", "Are you authorized to work?", True
        )
        ANSWERS.add_authorization(self.db, {
            "authorization_id": "auth-1", "confirmed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=7)).isoformat(),
            "scope": {"country": "US", "application_id": "app-1"},
        })

    def add_answer(self, answer_id, canonical_id, question, value, scope=None):
        ANSWERS.add_answer(self.db, {
            "answer_id": answer_id, "canonical_id": canonical_id,
            "canonical_meaning": question, "answer": value, "answer_type": "stable_fact",
            "source_type": "user_confirmed", "confirmation_status": "confirmed",
            "confirmed_at": AT.isoformat(), "validity_class": "stable",
            "scope": scope if scope is not None else {"country": "US", "application_id": "app-1"},
            "auto_fill_allowed": True, "auto_submit_allowed": True,
        })
        ANSWERS.add_question_form(self.db, canonical_id, question)

    def start(self, **updates):
        value = {
            "session_id": "session-1", "application_id": "app-1", "worker_id": "worker-1",
            "form_url": "http://127.0.0.1:8931/lever/0",
            "observed_employer": "Example Corp", "observed_role": "Backend Engineer",
            "known_form": True, "authorization_id": "auth-1",
            "authorization_context": {"country": "wrong", "application_id": "wrong"}, "at": AT,
        }
        value.update(updates)
        return FILL.start_session(self.db, **value)

    def page(self, fields=None, **updates):
        value = {
            "page_id": "page-1", "page_index": 0,
            "page_url": "http://127.0.0.1:8931/lever/0",
            "fields": fields if fields is not None else self.standard_fields(),
            "legal_items": [], "restricted_requests": [], "final_page": True,
        }
        value.update(updates)
        return value

    def standard_fields(self):
        return [
            {"field_id": "candidate_name", "question": "Full name", "selector": "#name",
             "control": "text", "required": True, "sensitivity": "normal",
             "source_kind": "fact", "canonical_id": "contact.full_name"},
            {"field_id": "work_auth", "question": "Are you authorized to work?", "selector": "#auth",
             "control": "radio", "required": True, "sensitivity": "normal",
             "source_kind": "answer"},
            {"field_id": "resume_upload", "question": "Resume", "selector": "#resume",
             "control": "file", "required": True, "sensitivity": "normal",
             "upload_kind": "resume"},
            {"field_id": "truth", "question": "I certify this is accurate", "selector": "#truth",
             "control": "standard_attestation", "required": True, "sensitivity": "normal"},
            {"field_id": "submit", "question": "Submit application", "selector": "#submit",
             "control": "submit", "required": True, "sensitivity": "normal"},
        ]

    def complete_page(self, page_id="page-1", worker_id="worker-1"):
        # A test-only shortcut, in `tests/`, because production has no bypass: see
        # `tests/fixtures/completed_page.py`. The real path runs with a browser in
        # `tests/test_fill_worker.py`.
        complete_page_as_if_imported(FILL, self.db, "session-1", worker_id, page_id, AT)
        return FILL.checkpoint_page(
            self.db, "session-1", worker_id, page_id, f"checkpoint-{page_id}", AT)

    def reacquire(self, worker_id="worker-2"):
        APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "user_resolved", at=AT)
        return APPLICATIONS.acquire_next(self.db, worker_id, at=AT)


    # ---- helpers -------------------------------------------------------

    RACE_VALUES = ["Hispanic or Latino", "Black or African American", "Asian"]
    DECLINE = "I do not wish to self-identify"
    OTHER_DECLINE = "Prefer not to answer"
    # A real control's label and its submitted value are different strings.


    NONCE = "n" * 48
    ORIGIN = "http://127.0.0.1:8931"

    def issue_surface(self, origin=None, nonce=None, **updates):
        value = {"surface_id": "surface-1", "origin": origin or self.ORIGIN,
                 "nonce": nonce or self.NONCE, "fixture_sha256": "f" * 64,
                 "renderer_version": "1.0.0", "issued_at": AT,
                 "expires_at": AT + timedelta(hours=1)}
        value.update(updates)
        return POLICY.register_replay_surface(self.db, **value)

    def options(self, labels, nonce=None):
        return [{"label": label,
                 "value": POLICY.replay_option_value(label, nonce or self.NONCE)}
                for label in labels]

    def race_options(self, extra=()):
        return self.options(list(self.RACE_VALUES) + [self.DECLINE] + list(extra))

    def field(self, field_id, question, **updates):
        value = {"field_id": field_id, "question": question, "selector": f"#{field_id}",
                 "control": "radio", "required": True, "sensitivity": "normal"}
        value.update(updates)
        return value

    def observe(self, fields, **page_updates):
        self.start()
        return FILL.observe_page(
            self.db, "session-1", "worker-1", self.candidate_path,
            self.page(fields=fields + [self.field(
                "submit", "Submit application", control="submit")], **page_updates), AT)

    def pause_reasons(self, result):
        return {reason.split(":", 1)[0] for reason in result.get("reasons", [])}

    def add_decline_policy(self, family="eeo_race", locale="en-US", surface=True, **updates):
        if surface and not self.db.execute(
            "SELECT 1 FROM replay_surfaces").fetchone():
            self.issue_surface()
        value = {"policy_id": f"policy-{family}", "question_family": family, "locale": locale,
                 "option_tokens": ["decline_to_self_identify"], "confirmed_by": "user",
                 "confirmed_at": AT, "scope": {"country": "US"}}
        value.update(updates)
        return POLICY.register_policy(self.db, **value)

    def database_dump(self):
        return "\n".join(
            line for line in self.db.iterdump() if not line.startswith("CREATE"))

    # ---- 1-3: employer conflict ----------------------------------------

    def test_conflict_derivation_needs_approved_entity_and_fresh_certification(self):
        # No employer-entity or registry subsystem exists, so the answer is never derivable.
        # The contract that a future one must satisfy is recorded in the function's docstring;
        # this asserts the only behaviour that may exist until then.
        outcome = POLICY.conflict_derivation(
            self.db, "conflict_related_person", {"application_id": "app-1"}, AT)
        self.assertFalse(outcome["derivable"])
        self.assertEqual(outcome["reason"], "employer_entity_not_approved")
        with self.assertRaisesRegex(ValueError, "unknown conflict family"):
            POLICY.conflict_derivation(self.db, "conflict_invented", {}, AT)

    def test_conflict_fields_fail_closed_and_record_nothing(self):
        result = self.observe([
            self.field("conflict_related", "Do you have a relative employed by Example Corp?"),
            self.field("conflict_commercial",
                       "Are you a customer, partner or reseller of Example Corp?"),
        ])
        self.assertEqual(result["status"], "paused")
        self.assertEqual(self.pause_reasons(result), {"employer_entity_not_approved"})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM fill_steps").fetchone()[0], 0)

    def test_the_two_conflict_questions_are_separate_families(self):
        related = POLICY.classify("conflict_a", "Do you have a relative working here?")
        commercial = POLICY.classify("conflict_b", "Are you a customer or reseller?")
        self.assertEqual(related[0], "employer_conflict")
        self.assertEqual(commercial[0], "employer_conflict")
        self.assertNotEqual(related[1], commercial[1])

    # ---- 4-5: compensation ---------------------------------------------

    def test_salary_floor_never_resolves_an_expected_compensation_field(self):
        # A confirmed, in-scope, auto-fillable answer exists and still must not be used: the
        # bracket belongs to the employer, and `salary_floor` is a search filter elsewhere.
        self.add_answer("answer-comp", "expected_compensation",
                        "What is your expected total compensation?", "150000")
        result = self.observe([self.field(
            "comp_range", "What is your expected total compensation?",
            options=self.options(["$100k-$150k", "$150k-$200k"]), source_kind="answer")])
        self.assertEqual(self.pause_reasons(result), {"employer_defined_compensation_manual"})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM fill_steps").fetchone()[0], 0)

    def test_employer_defined_bracket_is_always_manual(self):
        result = self.observe([self.field(
            "comp_range", "Select the salary range you expect",
            options=self.options(["$100k-$150k", "$150k-$200k"]))])
        self.assertEqual(self.pause_reasons(result), {"employer_defined_compensation_manual"})

    # ---- 6: sponsorship -------------------------------------------------

    def test_broad_sponsorship_question_rejects_every_canonical_substitution(self):
        for canonical in ("work_authorized_now", "sponsorship_now", "sponsorship_future",
                          "employer_action_required"):
            with self.subTest(canonical=canonical):
                self.setUp()
                self.add_answer(f"answer-{canonical}", canonical,
                                "Will you require employment visa sponsorship?", True)
                result = self.observe([self.field(
                    "sponsorship", "Will you require employment visa sponsorship?",
                    source_kind="answer")])
                self.assertEqual(self.pause_reasons(result), {"sponsorship_meaning_ambiguous"})

    def test_page_wording_cannot_lower_caution_on_a_sponsorship_question(self):
        # This used to resolve when the wording named one point in time, which let untrusted
        # page text move caution in the one direction it may never move. An employer who
        # writes "now" while meaning "now or in the future" would have had a narrower answer
        # submitted than the question asked for.
        for wording in ("Do you require sponsorship now, at this time?",
                        "Will you ever require sponsorship in the future?",
                        "Will you require employment visa sponsorship?",
                        "Sponsorship?"):
            with self.subTest(wording=wording):
                self.assertTrue(POLICY.sponsorship_is_ambiguous(wording))
        self.setUp()
        self.add_answer("answer-now", "sponsorship_now",
                        "Do you require sponsorship now, at this time?", False)
        result = self.observe([self.field(
            "sponsorship", "Do you require sponsorship now, at this time?",
            source_kind="answer")])
        self.assertEqual(self.pause_reasons(result), {"sponsorship_meaning_ambiguous"})

    def test_a_field_that_declares_no_source_is_answered_from_the_library(self):
        """The route every control on a real employer form takes.

        The replay tags its controls with `data-kind`, so the observation names a fact or a
        material and the planner is told where to look. A real page tags nothing: the field
        arrives with no `source_kind` at all, falls into the `{None, "answer"}` branch, and
        the answer library is the only thing that can answer it. That is why the three
        contact meanings have to live there — the candidate's email, phone and LinkedIn exist
        as one composite fact that cannot fill a single field, and splitting it would need a
        new snapshot and the re-approval of every resume version behind it.

        The wording is the corpus's own recorded employer wording. The values here are
        invented for this test and mean nothing.

        What this proves: the library matches this shape and the planner plans it. What it
        does not prove: that a production observer can find these fields — there is none —
        or that any real ATS would accept them. Nothing here touches one.
        """
        for canonical_id, question, value in (
            ("contact.email", "Email address", "someone@example.invalid"),
            ("contact.phone", "Phone number", "+1 555 0100"),
            ("profile.linkedin", "LinkedIn profile", "https://example.invalid/in/someone"),
        ):
            with self.subTest(canonical_id=canonical_id):
                self.setUp()
                self.add_answer(f"answer-{canonical_id}", canonical_id, question, value)
                field = self.field("contact_field", question)
                self.assertNotIn("source_kind", field)
                result = self.observe([field])
                self.assertNotEqual(result["status"], "paused",
                                    self.pause_reasons(result))
                row = self.db.execute(
                    "SELECT source_kind, source_id FROM fill_steps WHERE field_id=?",
                    ("contact_field",)).fetchone()
                self.assertEqual(row["source_kind"], "answer")
                self.assertEqual(row["source_id"], f"answer-{canonical_id}")

    def test_a_voluntary_eeo_field_is_resolved_by_policy_not_by_a_fixed_reason(self):
        """`voluntary_eeo` names a protected domain, not a pause reason.

        The other always-manual domains really do map to one stable code — compensation to
        `employer_defined_compensation_manual`, a referral to
        `referral_contact_requires_user` — because nothing can ever answer them. This domain
        is different in kind: entering it hands the field to the non-disclosure policy, and
        the answer depends on what the user registered and on what the control is. The three
        outcomes below share no code, and one of them is not a pause at all, which is why the
        deleted `MANUAL_REASONS` could not have described this domain correctly.
        """
        eeo = dict(field_id="eeo_race", question="Race / Ethnicity")
        # Nothing registered: the reason names the absence, not the domain.
        self.assertEqual(
            self.pause_reasons(self.observe([self.field(
                **eeo, control="select", options=self.race_options())], locale="en-US")),
            {"nondisclosure_policy_absent"})
        # Registered, but on a control that cannot carry the reviewed option.
        self.setUp()
        self.add_decline_policy()
        self.assertEqual(
            self.pause_reasons(self.observe([self.field(
                **eeo, control="radio", options=self.race_options())], locale="en-US")),
            {"nondisclosure_control_unsupported"})
        # Registered, on a control that can: planned, and no pause of any kind.
        self.setUp()
        self.add_decline_policy()
        result = self.observe([self.field(
            **eeo, control="select", options=self.race_options())], locale="en-US")
        self.assertNotEqual(result["status"], "paused")
        self.assertEqual(self.pause_reasons(result), set())
        self.assertEqual(self.db.execute(
            "SELECT source_kind FROM fill_steps WHERE field_id='eeo_race'"
        ).fetchone()["source_kind"], "nondisclosure_policy")
        # And no static map may come back to stand in front of that rule.
        self.assertFalse(hasattr(POLICY, "MANUAL_REASONS"))

    def test_a_radiogroup_nondisclosure_control_is_named_unsupported_not_mis_filled(self):
        # A radiogroup's identity is the group; the reviewed option lives on one of its
        # inputs, and nothing addresses that yet. Naming it beats filling the wrong node.
        self.add_decline_policy()
        result = self.observe([self.field(
            "eeo_race", "Race / Ethnicity", control="radio",
            options=self.race_options())], locale="en-US")
        self.assertEqual(self.pause_reasons(result),
                         {"nondisclosure_control_unsupported"})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM fill_steps").fetchone()[0], 0)

    def test_a_page_that_mislabels_its_own_option_cannot_be_trusted(self):
        # The attack the "opaque value" story missed entirely: offer the reviewed label and
        # submit a real category. Not understanding the value is not the value being safe.
        self.add_decline_policy()
        spoofed = [{"label": label, "value": POLICY.replay_option_value(label, self.NONCE)}
                   for label in self.RACE_VALUES]
        spoofed.append({"label": self.DECLINE, "value": "Asian"})
        result = self.observe([self.field(
            "eeo_race", "Race / Ethnicity", control="select", options=spoofed)], locale="en-US")
        self.assertEqual(self.pause_reasons(result), {"option_mapping_unverified"})
        self.assertNotIn("Asian", self.database_dump())

    def test_trust_comes_from_an_issued_surface_not_from_a_loopback_address(self):
        # A loopback address is a network location, not provenance: any local process can
        # listen on 127.0.0.1 and compute a public hash. Without a surface record Jobloom
        # issued, and without the nonce only it and its renderer hold, nothing is trusted.
        pair = {"label": self.DECLINE,
                "value": POLICY.replay_option_value(self.DECLINE, self.NONCE)}
        self.assertFalse(POLICY.option_mapping_trusted(
            self.db, f"{self.ORIGIN}/lever/0", pair, AT))
        self.issue_surface()
        self.assertTrue(POLICY.option_mapping_trusted(
            self.db, f"{self.ORIGIN}/lever/0", pair, AT))
        # A different local server on another port is not this surface.
        self.assertFalse(POLICY.option_mapping_trusted(
            self.db, "http://127.0.0.1:8932/lever/0", pair, AT))
        # A live ATS has no approved mapping at all.
        self.assertFalse(POLICY.option_mapping_trusted(
            self.db, "https://jobs.lever.co/example/1", pair, AT))
        # Expiry and revocation both withdraw it.
        self.assertFalse(POLICY.option_mapping_trusted(
            self.db, f"{self.ORIGIN}/lever/0", pair, AT + timedelta(hours=2)))
        POLICY.revoke_replay_surface(self.db, "surface-1", AT)
        self.assertFalse(POLICY.option_mapping_trusted(
            self.db, f"{self.ORIGIN}/lever/0", pair, AT))

    def test_a_hostile_local_server_cannot_guess_the_surface_nonce(self):
        # The derivation rule is public; the nonce is not. A rogue process on the same host
        # that computes the algorithm without it produces a pair that does not match.
        self.issue_surface()
        rogue = {"label": self.DECLINE,
                 "value": POLICY.replay_option_value(self.DECLINE, "guessed-nonce" * 4)}
        self.assertFalse(POLICY.option_mapping_trusted(
            self.db, f"{self.ORIGIN}/lever/0", rogue, AT))
        with self.assertRaisesRegex(ValueError, "surface nonce"):
            POLICY.replay_option_value(self.DECLINE, "")

    def test_a_surface_record_must_be_a_bare_loopback_origin(self):
        for bad in ("https://127.0.0.1:8931", "http://example.com:80",
                    "http://127.0.0.1:8931/lever"):
            with self.subTest(origin=bad):
                with self.assertRaises(ValueError):
                    self.issue_surface(origin=bad)
        with self.assertRaisesRegex(ValueError, "session nonce"):
            self.issue_surface(nonce="short")

    def test_a_label_match_does_not_settle_what_the_form_would_submit(self):
        self.add_decline_policy()
        result = self.observe([self.field(
            "eeo_race", "Race / Ethnicity", control="select",
            options=self.race_options())], locale="en-US")
        self.assertNotEqual(result["status"], "paused")
        step = self.db.execute(
            "SELECT value_json FROM fill_steps ORDER BY ordinal").fetchall()[-1]
        # The reviewed label chose the option; the page's own opaque value is submitted.
        self.assertEqual(json.loads(step["value_json"]),
                         POLICY.replay_option_value(self.DECLINE, self.NONCE))
        self.assertNotIn(self.DECLINE, step["value_json"])

    def test_one_reviewed_label_mapping_to_two_values_pauses(self):
        self.add_decline_policy()
        duplicated = self.race_options() + [{"label": self.DECLINE, "value": "opt-other"}]  # noqa: E501
        result = self.observe([self.field(
            "eeo_race", "Race / Ethnicity", control="select", options=duplicated)], locale="en-US")
        self.assertEqual(self.pause_reasons(result), {"nondisclosure_option_ambiguous"})

    def test_a_file_control_cannot_outrun_its_domain_classification(self):
        # The upload branch used to `continue` before classification ran, so a control
        # declared `file` with `upload_kind: "resume"` and an identity-document question was
        # planned as a resume upload without ever being classified.
        # An identity document is caught by `archive_core.SENSITIVE_FIELD_PATTERN` even
        # under the old order, so the case that actually got through is a protected
        # characteristic asked for as an upload.
        result = self.observe([self.field(
            "veteran_proof", "Upload documentation of your protected veteran status",
            control="file", upload_kind="resume")])
        self.assertEqual(result["status"], "paused")
        self.assertEqual(self.pause_reasons(result), {"nondisclosure_policy_absent"})
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM fill_steps WHERE operation='upload'").fetchone()[0], 0)
        self.assertEqual(POLICY.disposition(
            "veteran_proof", "Upload documentation of your protected veteran status",
            "file", None)[0], "always_manual")

    # ---- 7-9: voluntary EEO --------------------------------------------

    def test_every_voluntary_eeo_kind_is_manual_without_a_reviewed_option(self):
        for field_id, question in (("eeo_race", "Race / Ethnicity"),
                                   ("eeo_gender", "Gender"),
                                   ("eeo_disability", "Do you have a disability?"),
                                   ("eeo_veteran", "Protected veteran status")):
            with self.subTest(field_id=field_id):
                self.setUp()
                result = self.observe([self.field(
                    field_id, question, control="select",
                    options=self.race_options(), source_kind="answer")], locale="en-US")
                self.assertEqual(result["status"], "paused")
                self.assertEqual(self.pause_reasons(result), {"nondisclosure_policy_absent"})

    def test_an_exact_reviewed_non_disclosure_option_may_be_selected(self):
        self.add_decline_policy()
        result = self.observe([self.field(
            "eeo_race", "Race / Ethnicity", control="select",
            options=self.race_options())], locale="en-US")
        self.assertNotEqual(result["status"], "paused")
        step = self.db.execute(
            "SELECT source_kind, source_id FROM fill_steps ORDER BY ordinal").fetchall()[-1]
        self.assertEqual(step["source_kind"], "nondisclosure_policy")
        self.assertEqual(step["source_id"], "policy-eeo_race")

    def test_expiry_revocation_scope_and_inexact_options_all_pause(self):
        cases = {
            "nondisclosure_policy_expired": lambda: self.add_decline_policy(
                confirmed_at=AT - timedelta(days=3), expires_at=AT - timedelta(days=1)),
            "nondisclosure_policy_scope_mismatch": lambda: self.add_decline_policy(
                scope={"country": "DE"}),
            "nondisclosure_option_ambiguous": lambda: self.add_decline_policy(
                option_tokens=["decline_to_self_identify", "prefer_not_to_answer"]),
            "nondisclosure_option_unavailable": lambda: self.add_decline_policy(),
        }
        for reason, register in cases.items():
            with self.subTest(reason=reason):
                self.setUp()
                register()
                options = None if reason == "nondisclosure_option_unavailable" else (
                    self.race_options(
                        [self.OTHER_DECLINE] if reason == "nondisclosure_option_ambiguous" else []))
                result = self.observe([self.field(
                    "eeo_race", "Race / Ethnicity", control="select", options=options)], locale="en-US")
                self.assertEqual(self.pause_reasons(result), {reason})
        self.setUp()
        self.add_decline_policy()
        POLICY.revoke_policy(self.db, "policy-eeo_race", "user_withdrew", AT)
        result = self.observe([self.field(
            "eeo_race", "Race / Ethnicity", control="select",
            options=self.race_options())], locale="en-US")
        self.assertEqual(self.pause_reasons(result), {"nondisclosure_policy_revoked"})

    def test_no_demographic_value_reaches_any_action_result_or_store(self):
        self.add_decline_policy()
        self.observe([self.field(
            "eeo_race", "Race / Ethnicity", control="select",
            options=self.race_options())], locale="en-US")
        output = self.root / "private" / "page-actions.json"
        FILL.export_page(self.db, "session-1", "worker-1", "page-1", output, AT)
        package = output.read_text(encoding="utf-8")
        dump = self.database_dump()
        for value in self.RACE_VALUES:
            self.assertNotIn(value, dump)
            self.assertNotIn(value, package)
            self.assertNotIn(FILL.canonical_hash(value), dump)
            self.assertNotIn(FILL.canonical_hash(value), package)
        # The page's own option list is never persisted; only how many it held.
        self.assertIn("options_count", dump)
        self.assertNotIn("options", json.loads(self.db.execute(
            "SELECT observation_json FROM fill_pages").fetchone()[0])["fields"][0])

    def test_a_declared_answer_source_cannot_turn_an_eeo_field_into_an_answer(self):
        # Page text may add caution and never remove it: declaring `source_kind: "answer"`
        # on a race question must not route it through the answer library.
        self.add_answer("answer-race", "race", "Race / Ethnicity", "Asian")
        result = self.observe([self.field(
            "eeo_race", "Race / Ethnicity", control="select", source_kind="answer",
            options=self.options(self.RACE_VALUES))], locale="en-US")
        self.assertEqual(result["status"], "paused")
        # The answer exists and is confirmed; what must not exist is a step that used it.
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM fill_steps WHERE source_id='answer-race'").fetchone()[0], 0)
        self.assertEqual(self.pause_reasons(result), {"nondisclosure_policy_absent"})

    # ---- 10: the browser boundary ---------------------------------------

    def test_no_planned_action_can_submit_or_navigate(self):
        self.add_decline_policy()
        self.observe([
            self.field("eeo_race", "Race / Ethnicity", control="select",
                       options=self.race_options()),
            self.field("next_page", "Continue to the next step", control="submit"),
        ], locale="en-US")
        output = self.root / "private" / "page-actions.json"
        result = FILL.export_page(self.db, "session-1", "worker-1", "page-1", output, AT)
        package = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(package["stop_before_submit"])
        self.assertIsNone(package["submission_action"])
        self.assertFalse(result["contains_submission_action"])
        # A reviewed option on a `<select>` is planned as `select`, not typed into it.
        self.assertEqual({action["operation"] for action in package["actions"]}, {"select"})
        self.assertNotIn("next_page", json.dumps(package["actions"]))

    # ---- discovery source -----------------------------------------------

    # ---- lifecycle regressions -------------------------------------------

    def test_policy_action_completes_but_archives_only_a_handling_marker(self):
        # The gap this closes: `record_field` accepts only `fact` or `answer`, so a
        # successful non-disclosure step used to raise on completion. Testing only as far as
        # export never reached it.
        self.add_decline_policy()
        self.observe([self.field(
            "eeo_race", "Race / Ethnicity", control="select",
            options=self.race_options())], locale="en-US")
        output = self.root / "private" / "page-actions.json"
        FILL.export_page(self.db, "session-1", "worker-1", "page-1", output, AT)
        complete_page_as_if_imported(FILL, self.db, "session-1", "worker-1", "page-1", AT)
        self.assertEqual(POLICY.handling_markers(self.db, "app-1"),
                         {"eeo_race": "policy_declined"})
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM application_fields WHERE field_id='eeo_race'").fetchone()[0], 0)
        dump = self.database_dump()
        for value in self.RACE_VALUES:
            self.assertNotIn(value, dump)
            self.assertNotIn(FILL.canonical_hash(value), dump)

    def test_only_reviewed_vocabulary_tokens_can_be_registered(self):
        for tokens in (["Asian"], ["prefer_not_to_say"], []):
            with self.subTest(tokens=tokens):
                with self.assertRaises(ValueError):
                    self.add_decline_policy(option_tokens=tokens)
        with self.assertRaisesRegex(ValueError, "vocabulary version"):
            self.add_decline_policy(vocabulary_version="1999-01-01")
        with self.assertRaisesRegex(ValueError, "language tag"):
            self.add_decline_policy(locale="not a locale")
        dump = self.database_dump()
        self.assertNotIn("Asian", dump)
        self.assertNotIn(self.DECLINE, dump)
        # Even a valid policy stores the token and version, never a page-facing string.
        self.add_decline_policy()
        stored = self.db.execute(
            "SELECT option_tokens_json, vocabulary_version FROM nondisclosure_policies"
        ).fetchone()
        self.assertEqual(json.loads(stored["option_tokens_json"]), ["decline_to_self_identify"])
        self.assertEqual(stored["vocabulary_version"], POLICY.NONDISCLOSURE_VOCABULARY_VERSION)
        self.assertNotIn(self.DECLINE, self.database_dump())

    def test_a_paused_eeo_page_cannot_resume_from_the_sanitized_record(self):
        # Option strings were never stored, so the record cannot say what the control offers.
        # Replanning from it would skip the policy the user just registered.
        self.observe([self.field(
            "eeo_race", "Race / Ethnicity", control="select",
            options=self.race_options())], locale="en-US")
        self.add_decline_policy()
        self.reacquire()
        with self.assertRaisesRegex(ValueError, "new live observation"):
            FILL.resume_session(self.db, "session-1", "worker-2", "auth-1",
                                {"country": "US", "application_id": "app-1"},
                                self.candidate_path, AT)
        fresh = self.page(fields=[
            self.field("eeo_race", "Race / Ethnicity", control="select",
                       options=self.race_options()),
            self.field("submit", "Submit application", control="submit")], locale="en-US")
        result = FILL.resume_session(
            self.db, "session-1", "worker-2", "auth-1",
            {"country": "US", "application_id": "app-1"}, self.candidate_path, AT,
            observation=fresh)
        self.assertNotEqual(result["status"], "paused")
        self.assertEqual(self.db.execute(
            "SELECT source_kind FROM fill_steps ORDER BY ordinal").fetchall()[-1]["source_kind"],
            "nondisclosure_policy")

    def test_a_vanished_control_never_becomes_user_handled(self):
        # A control that stopped appearing may have been completed by the user, or missed by
        # the observer, or re-rendered away. Those are not the same event.
        self.observe([self.field(
            "eeo_race", "Race / Ethnicity", control="select",
            options=self.race_options())], locale="en-US")
        self.reacquire()
        fresh = self.page(fields=[
            self.field("other", "Are you authorized to work?", source_kind="answer"),
            self.field("submit", "Submit application", control="submit")])
        FILL.resume_session(self.db, "session-1", "worker-2", "auth-1",
                            {"country": "US", "application_id": "app-1"},
                            self.candidate_path, AT, observation=fresh)
        self.assertEqual(POLICY.handling_markers(self.db, "app-1"), {})
        self.assertEqual(POLICY.handling_summary(self.db, "app-1"),
                         {"status": "unknown", "markers": {}})

    def test_only_an_explicit_user_confirmation_writes_user_handled(self):
        with self.assertRaisesRegex(ValueError, "only the user"):
            POLICY.confirm_user_handled(self.db, "app-1", "eeo_race", "worker-1", "ref-1", AT)
        self.assertEqual(POLICY.handling_markers(self.db, "app-1"), {})
        POLICY.confirm_user_handled(self.db, "app-1", "eeo_race", "user", "ref-1", AT)
        self.assertEqual(POLICY.handling_summary(self.db, "app-1"),
                         {"status": "recorded", "markers": {"eeo_race": "user_handled"}})
        evidence = self.db.execute(
            "SELECT evidence_kind, evidence_ref FROM nondisclosure_handling").fetchone()
        self.assertEqual(evidence["evidence_kind"], "user_confirmation")
        self.assertEqual(evidence["evidence_ref"], "ref-1")

    def test_an_empty_handling_table_is_unknown_and_never_not_present(self):
        self.assertEqual(POLICY.handling_markers(self.db, "app-1"), {})
        self.assertEqual(POLICY.handling_summary(self.db, "app-1"),
                         {"status": "unknown", "markers": {}})
        with self.assertRaisesRegex(ValueError, "unknown handling marker"):
            POLICY.record_handling(self.db, "app-1", "eeo_race", "Asian", "ref", AT)
        with self.assertRaisesRegex(ValueError, "evidence"):
            POLICY.record_handling(self.db, "app-1", "eeo_race", "user_handled", "", AT)
        self.assertNotIn("Asian", self.database_dump())

    def test_the_surface_nonce_stays_out_of_events_packages_and_archives(self):
        # The plaintext nonce is accepted only for the local replay, and only on the terms
        # that it never leaves the protected store.
        self.issue_surface()
        self.add_decline_policy(surface=False)
        self.observe([self.field(
            "eeo_race", "Race / Ethnicity", control="select",
            options=self.race_options())], locale="en-US")
        output = self.root / "private" / "page-actions.json"
        FILL.export_page(self.db, "session-1", "worker-1", "page-1", output, AT)
        package = output.read_text(encoding="utf-8")
        self.assertNotIn(self.NONCE, package)
        events = " ".join(row[0] for row in self.db.execute(
            "SELECT metadata_json FROM fill_events"))
        self.assertNotIn(self.NONCE, events)
        self.assertNotIn(self.NONCE, " ".join(row[0] for row in self.db.execute(
            "SELECT reason_code FROM fill_events")))
        # It is in exactly one place: the protected surface record it was issued into.
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM replay_surfaces WHERE nonce=?", (self.NONCE,)
        ).fetchone()[0], 1)

    def test_a_retired_not_present_row_is_read_as_unknown(self):
        # A row an earlier version wrote must not be re-interpreted into a claim this
        # version has decided it cannot support.
        self.db.execute(
            "INSERT INTO nondisclosure_handling (application_id, field_id, marker, "
            "evidence_kind, evidence_ref, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("app-1", POLICY.INVENTORY_SCOPE, "not_present", "complete_inventory",
             "legacy", AT.isoformat()))
        self.assertEqual(POLICY.handling_summary(self.db, "app-1"),
                         {"status": "unknown", "markers": {}})
        # And it can no longer be written.
        with self.assertRaisesRegex(ValueError, "unknown handling marker"):
            POLICY.record_handling(self.db, "app-1", "eeo_race", "not_present", "ref", AT)
        self.assertNotIn("not_present", POLICY.HANDLING_MARKERS)

    def test_finalizing_never_writes_not_present_in_v1(self):
        # A self-reported chain cannot establish absence: an observer that never saw a page
        # cannot report that page missing. Naming the evidence honestly did not make it
        # stronger, so nothing writes `not_present` at all.
        summary = POLICY.finalize_handling(self.db, "app-1", [], "inventory-hash", AT)
        self.assertEqual(summary, {"status": "unknown", "markers": {}})
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM nondisclosure_handling").fetchone()[0], 0)
        # An observed control with no marker still cannot be finalized away.
        with self.assertRaisesRegex(ValueError, "without a handling marker"):
            POLICY.finalize_handling(self.db, "app-2", ["eeo_race"], "inventory-hash", AT)

    def test_finishing_a_session_still_leaves_an_unseen_control_unknown(self):
        # The claim "this form had no voluntary-disclosure control" is only made here, where
        # every page is known to have been observed and checkpointed.
        self.start()
        FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path, self.page(), AT)
        self.assertEqual(POLICY.handling_summary(self.db, "app-1"),
                         {"status": "unknown", "markers": {}})
        self.complete_page()
        FILL.finish_session(self.db, "session-1", "worker-1", "inventory-1", AT)
        # Finishing settles nothing about a control nobody saw.
        self.assertEqual(POLICY.handling_summary(self.db, "app-1"),
                         {"status": "unknown", "markers": {}})
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM nondisclosure_handling").fetchone()[0], 0)

    def test_a_policy_that_can_never_apply_is_refused(self):
        with self.assertRaisesRegex(ValueError, "expire after it takes effect"):
            self.add_decline_policy(expires_at=AT - timedelta(days=1))
        with self.assertRaisesRegex(ValueError, "expire after it takes effect"):
            self.add_decline_policy(effective_from=AT + timedelta(days=2),
                                    expires_at=AT + timedelta(days=1))
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM nondisclosure_policies").fetchone()[0], 0)

    def test_locale_uses_one_contract_for_policies_and_observations(self):
        for bad in ("not a locale", "EN_US", "e", "x" * 33):
            with self.subTest(locale=bad):
                with self.assertRaises(ValueError):
                    POLICY.require_locale(bad)
                with self.assertRaises(ValueError):
                    self.observe([self.field("q", "Full name", control="text",
                                             source_kind="fact", source_id="fact-name")],
                                 locale=bad)
                self.setUp()
        self.assertEqual(POLICY.require_locale("en-US"), "en-US")

    def test_dispositions_are_returned_not_merely_declared(self):
        cases = {
            ("eeo_race", "Race / Ethnicity", "radio", "answer"): "always_manual",
            ("comp", "Expected salary range", "radio", "answer"): "always_manual",
            ("conflict", "Do you have a relative here?", "radio", "answer"): "always_manual",
            ("resume", "Resume", "file", None): "material",
            ("name", "Full name", "text", "fact"): "fact",
            ("auth", "Are you authorized to work?", "radio", "answer"): "answer",
            ("weird", "Anything", "text", "invented"): "unsupported",
        }
        for arguments, expected in cases.items():
            with self.subTest(field=arguments[0]):
                self.assertEqual(POLICY.disposition(*arguments)[0], expected)
        self.assertEqual({value[0] for value in
                          (POLICY.disposition(*arguments) for arguments in cases)},
                         POLICY.DISPOSITIONS)

    def test_discovery_source_needs_a_user_confirmed_application_answer(self):
        self.add_answer("answer-source", "discovery_source", "How did you hear about us?",
                        "Job board")
        result = self.observe([self.field(
            "source", "How did you hear about us?", source_kind="answer")])
        self.assertEqual(self.pause_reasons(result), {"discovery_source_not_user_confirmed"})


if __name__ == "__main__":
    unittest.main()
