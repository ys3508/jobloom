"""The worker protocol, and the page chain that makes form coverage a claim.

`finish_session` used to prove that every page already in the database had been checkpointed
and that some page had shown a submit control. A single page observed at index 49 satisfies
both, so what it actually proved was coverage of what was seen. `not_present` — the statement
that this form had no voluntary-disclosure control — cannot rest on that.

The setup mirrors `tests/test_fill_core.py` rather than importing it: importing one test
module from another made results depend on which invocation ran.
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
    spec = importlib.util.spec_from_file_location(f"protocol_test_{name}", path)
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
PROTOCOL = load_script("worker_protocol")
POLICY = load_script("field_policy")
from tests.pdf_fixture import synthetic_pdf

AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class PageChainTests(unittest.TestCase):
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
            {"id": "fact-name", "type": "identity", "value": "Verified Candidate",
             "status": "locked", "locked": True, "evidence_strength": "direct"},
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
            "form_url": "https://apply.example.com/jobs/1/apply",
            "observed_employer": "Example Corp", "observed_role": "Backend Engineer",
            "known_form": True, "authorization_id": "auth-1",
            "authorization_context": {"country": "wrong", "application_id": "wrong"}, "at": AT,
        }
        value.update(updates)
        return FILL.start_session(self.db, **value)

    def page(self, fields=None, **updates):
        value = {
            "page_id": "page-1", "page_index": 0,
            "page_url": "https://apply.example.com/jobs/1/apply",
            "fields": fields if fields is not None else self.standard_fields(),
            "legal_items": [], "restricted_requests": [], "final_page": True,
        }
        value.update(updates)
        return value

    def standard_fields(self):
        return [
            {"field_id": "candidate_name", "question": "Full name", "selector": "#name",
             "control": "text", "required": True, "sensitivity": "normal",
             "source_kind": "fact", "source_id": "fact-name"},
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
        rows = self.db.execute(
            "SELECT step_id, expected_sha256 FROM fill_steps WHERE session_id='session-1' AND page_id=? "
            "ORDER BY ordinal", (page_id,),
        ).fetchall()
        for row in rows:
            FILL.complete_step(self.db, "session-1", worker_id, row["step_id"], row["expected_sha256"], AT)
        return FILL.checkpoint_page(
            self.db, "session-1", worker_id, page_id, f"checkpoint-{page_id}", AT
        )

    def reacquire(self, worker_id="worker-2"):
        APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "user_resolved", at=AT)
        return APPLICATIONS.acquire_next(self.db, worker_id, at=AT)


    # ---- helpers -------------------------------------------------------

    def request(self, **updates):
        value = {
            "protocol_version": PROTOCOL.PROTOCOL_VERSION, "session_id": "session-1",
            "page_id": "page-1", "page_index": 0, "package_sha256": "a" * 64,
            "allowed_origin": "http://127.0.0.1:8931",
            "expires_at": (AT + timedelta(minutes=10)).isoformat(),
            "action_ids": ["step-1", "step-2"],
            "operations": {"step-1": "fill", "step-2": "select"},
            "stop_before_submit": True, "submission_action": None,
        }
        value.update(updates)
        return value

    def checked_request(self, request, **updates):
        arguments = {"expected_session": "session-1", "expected_page": "page-1",
                     "expected_package_sha256": "a" * 64,
                     "allowed_origin": "http://127.0.0.1:8931", "at": AT}
        arguments.update(updates)
        return PROTOCOL.validate_request(request, **arguments)

    def result(self, **updates):
        value = {
            "protocol_version": PROTOCOL.PROTOCOL_VERSION, "session_id": "session-1",
            "page_id": "page-1", "package_sha256": "a" * 64, "final_action_activations": 0,
            "results": [{"action_id": "step-1", "outcome": "verified", "observed_sha256": "b" * 64},
                        {"action_id": "step-2", "outcome": "verified", "observed_sha256": "c" * 64}],
        }
        value.update(updates)
        return value

    def checked_result(self, result, **updates):
        arguments = {"expected_session": "session-1", "expected_page": "page-1",
                     "expected_package_sha256": "a" * 64,
                     "expected_action_ids": ["step-1", "step-2"], "at": AT}
        arguments.update(updates)
        return PROTOCOL.validate_result(result, **arguments)

    def refuses(self, code, callable_):
        with self.assertRaises(PROTOCOL.ProtocolError) as caught:
            callable_()
        self.assertTrue(str(caught.exception).startswith(code), str(caught.exception))

    # ---- envelopes ------------------------------------------------------

    def test_a_valid_envelope_round_trips(self):
        checked = self.checked_request(self.request())
        self.assertEqual(checked["action_ids"], ["step-1", "step-2"])
        self.assertEqual(self.checked_result(self.result())["verified"], ["step-1", "step-2"])

    def test_tampered_expired_replayed_and_unversioned_packages_fail_closed(self):
        self.refuses("package_hash_mismatch",
                     lambda: self.checked_request(self.request(package_sha256="d" * 64)))
        self.refuses("malformed_package_hash",
                     lambda: self.checked_request(self.request(package_sha256="not-a-hash")))
        self.refuses("session_mismatch",
                     lambda: self.checked_request(self.request(session_id="session-2")))
        self.refuses("page_mismatch",
                     lambda: self.checked_request(self.request(page_id="page-9")))
        self.refuses("package_expired", lambda: self.checked_request(
            self.request(expires_at=(AT - timedelta(seconds=1)).isoformat())))
        self.refuses("missing_expiry",
                     lambda: self.checked_request(self.request(expires_at=None)))
        self.refuses("unsupported_protocol_version",
                     lambda: self.checked_request(self.request(protocol_version="0.9.0")))

    def test_origin_must_be_the_expected_loopback_port(self):
        self.refuses("unexpected_origin", lambda: self.checked_request(
            self.request(allowed_origin="http://127.0.0.1:9999")))
        self.refuses("origin_outside_loopback", lambda: self.checked_request(
            self.request(allowed_origin="https://apply.example.com"),
            allowed_origin="https://apply.example.com"))
        self.refuses("origin_outside_loopback", lambda: self.checked_request(
            self.request(allowed_origin="http://localhost:8931"),
            allowed_origin="http://localhost:8931"))

    def test_no_submission_or_navigation_operation_survives_the_protocol(self):
        for operation in sorted(PROTOCOL.FORBIDDEN_OPERATIONS):
            with self.subTest(operation=operation):
                self.refuses("unsupported_operation", lambda operation=operation:
                             self.checked_request(self.request(
                                 action_ids=["step-1"],
                                 operations={"step-1": operation})))
        # Naming a control `submit` is a stop boundary, not an instruction: the label is
        # irrelevant because the operation vocabulary has no way to express it.
        self.refuses("stop_before_submit_not_asserted",
                     lambda: self.checked_request(self.request(stop_before_submit=False)))
        self.refuses("submission_action_present",
                     lambda: self.checked_request(self.request(submission_action="#submit")))

    def test_results_must_match_the_actions_that_were_issued(self):
        entries = self.result()["results"]
        self.refuses("unexpected_action_id", lambda: self.checked_result(
            self.result(results=entries + [{"action_id": "step-3", "outcome": "verified",
                                            "observed_sha256": "d" * 64}])))
        self.refuses("duplicate_result", lambda: self.checked_result(
            self.result(results=[entries[0], entries[0]])))
        self.refuses("result_order_or_completeness_mismatch",
                     lambda: self.checked_result(self.result(results=[entries[0]])))
        self.refuses("result_order_or_completeness_mismatch", lambda: self.checked_result(
            self.result(results=list(reversed(entries)))))
        self.refuses("unknown_outcome_code", lambda: self.checked_result(
            self.result(results=[{"action_id": "step-1", "outcome": "submitted"}, entries[1]])))
        self.refuses("final_action_activated",
                     lambda: self.checked_result(self.result(final_action_activations=1)))

    def test_a_result_carrying_a_value_is_refused_at_any_depth(self):
        for key in ("value", "text", "cookie", "token", "file_path", "options", "page_text"):
            with self.subTest(key=key):
                self.refuses("forbidden_result_field", lambda key=key: self.checked_result(
                    self.result(results=[{"action_id": "step-1", "outcome": "verified",
                                          "observed_sha256": "b" * 64, key: "leaked"},
                                         self.result()["results"][1]])))
        # A top-level leak is now refused one step earlier, as an unknown field: the closed
        # field set is the stricter of the two checks and runs first.
        self.refuses("unknown_result_field",
                     lambda: self.checked_result(self.result(cookie="leaked")))
        # Nested is where the forbidden-key scan is the only thing that catches it.
        self.refuses("forbidden_result_field", lambda: self.checked_result(
            self.result(results=[{"action_id": "step-1", "outcome": "verified",
                                  "observed_sha256": "b" * 64,
                                  "control": {"text": "Race / Ethnicity"}},
                                 self.result()["results"][1]])))
        self.refuses("unknown_result_entry_field", lambda: self.checked_result(
            self.result(results=[{"action_id": "step-1", "outcome": "verified",
                                  "observed_sha256": "b" * 64, "selector": "#name"},
                                 self.result()["results"][1]])))

    def test_the_tracked_schemas_describe_this_protocol(self):
        request_schema = PROTOCOL.schema("worker-request")
        self.assertEqual(request_schema["properties"]["protocol_version"]["const"],
                         PROTOCOL.PROTOCOL_VERSION)
        self.assertEqual(
            set(request_schema["properties"]["operations"]["additionalProperties"]["enum"]),
            PROTOCOL.ALLOWED_OPERATIONS)
        result_schema = PROTOCOL.schema("worker-result")
        self.assertEqual(
            set(result_schema["properties"]["results"]["items"]["properties"]["outcome"]["enum"]),
            PROTOCOL.OUTCOME_CODES)
        self.assertEqual(result_schema["properties"]["final_action_activations"]["const"], 0)
        # The two oracles must not be reported as the same evidence.
        oracle = result_schema["properties"]["final_action_activations"]["description"]
        self.assertIn("test oracle", oracle)
        self.assertIn("scoped guard", oracle)
        self.assertIn("upload", oracle.casefold())
        metadata = PROTOCOL.schema("action-package-metadata")
        self.assertEqual(metadata["properties"]["submission_action"]["type"], "null")

    # ---- the page chain --------------------------------------------------

    def test_operations_must_match_the_issued_actions_exactly(self):
        # An operation nobody looks up is an operation nobody checks.
        self.refuses("operations_do_not_match_action_ids", lambda: self.checked_request(
            self.request(operations={"step-1": "fill", "step-2": "select",
                                     "evil": "submit"})))
        self.refuses("operations_do_not_match_action_ids", lambda: self.checked_request(
            self.request(operations={"step-1": "fill"})))
        self.refuses("malformed_operations",
                     lambda: self.checked_request(self.request(operations=None)))

    def test_unknown_top_level_fields_are_refused_by_the_validator(self):
        # The validator is what runs; a schema that drifted from it would be the only thing
        # refusing these.
        self.refuses("unknown_request_field",
                     lambda: self.checked_request({**self.request(), "callback_url": "x"}))
        self.refuses("unknown_result_field",
                     lambda: self.checked_result({**self.result(), "page_html": "x"}))
        self.assertEqual(PROTOCOL.REQUEST_FIELDS,
                         set(PROTOCOL.schema("worker-request")["properties"]))
        self.assertEqual(PROTOCOL.RESULT_FIELDS,
                         set(PROTOCOL.schema("worker-result")["properties"]))

    def test_error_codes_come_from_a_closed_vocabulary(self):
        entries = self.result()["results"]
        for hostile in ("Applicant answered Asian", "value=150000", "x" * 64):
            with self.subTest(code=hostile):
                self.refuses("unknown_error_code", lambda hostile=hostile: self.checked_result(
                    self.result(results=[{"action_id": "step-1", "outcome": "error",
                                          "error_code": hostile}, entries[1]])))
        self.checked_result(self.result(results=[
            {"action_id": "step-1", "outcome": "error", "error_code": "selector_not_found"},
            entries[1]]))
        self.assertEqual(
            set(PROTOCOL.schema("worker-result")["properties"]["results"]["items"]
                ["properties"]["error_code"]["enum"]),
            PROTOCOL.ERROR_CODES)

    def test_declaring_a_page_final_without_a_submit_control_is_a_contradiction(self):
        # Reached through the real data flow, not by hand-building a chain: folding the two
        # observations together made this case unreachable and the assertion meaningless.
        self.start()
        FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                          self.page(fields=[self.standard_fields()[0]], final_page=True), AT)
        self.complete_page()
        with self.assertRaisesRegex(ValueError, "final_page_without_submit_control"):
            FILL.finish_session(self.db, "session-1", "worker-1", "inventory-1", AT)
        row = self.db.execute(
            "SELECT final_page, submit_control_seen FROM fill_pages").fetchone()
        self.assertEqual((row["final_page"], row["submit_control_seen"]), (1, 0))

    def test_seeing_a_submit_control_does_not_make_a_page_final(self):
        self.start()
        FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                          self.page(final_page=False), AT)
        self.complete_page()
        row = self.db.execute(
            "SELECT final_page, submit_control_seen FROM fill_pages").fetchone()
        self.assertEqual((row["final_page"], row["submit_control_seen"]), (0, 1))
        with self.assertRaisesRegex(ValueError, "no_final_page_observed"):
            FILL.finish_session(self.db, "session-1", "worker-1", "inventory-1", AT)

    def test_a_lone_late_page_cannot_pass_for_a_complete_form(self):
        # The gap this closes: one page at index 49 bearing a submit control satisfied every
        # completeness check `finish_session` performed.
        self.assertEqual(PROTOCOL.chain_issue([
            {"page_index": 49, "status": "completed", "final_page": True,
             "submit_control_seen": True, "checkpoint_sha256": "a" * 64}]),
            "chain_does_not_start_at_first_page")

    def test_the_chain_refuses_skips_gaps_and_wrong_predecessors(self):
        def page(index, predecessor=None, final=False, status="completed"):
            return {"page_index": index, "status": status, "final_page": final,
                    "submit_control_seen": final,
                    "predecessor_checkpoint_sha256": predecessor,
                    "checkpoint_sha256": f"{index:064d}"}
        cases = {
            "no_pages_observed": [],
            "page_index_not_consecutive": [page(0), page(2, f"{1:064d}", final=True)],
            "page_not_checkpointed": [page(0, status="active"), page(1, f"{0:064d}", final=True)],
            "first_page_has_a_predecessor": [page(0, "f" * 64, final=True)],
            "page_predecessor_mismatch": [page(0), page(1, "e" * 64, final=True)],
            "no_final_page_observed": [page(0), page(1, f"{0:064d}")],
            "final_page_is_not_the_last_page": [page(0, final=True), page(1, f"{0:064d}")],
            "final_page_without_submit_control": [
                page(0), {"page_index": 1, "status": "completed", "final_page": True,
                          "submit_control_seen": False,
                          "predecessor_checkpoint_sha256": f"{0:064d}",
                          "checkpoint_sha256": f"{1:064d}"}],
        }
        for expected, pages in cases.items():
            with self.subTest(case=expected):
                self.assertEqual(PROTOCOL.chain_issue(pages), expected)
        self.assertIsNone(PROTOCOL.chain_issue([page(0), page(1, f"{0:064d}", final=True)]))

    def test_a_later_page_cannot_be_observed_without_its_predecessor(self):
        self.start()
        with self.assertRaisesRegex(ValueError, "consecutive from the first page"):
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                              self.page(page_id="page-2", page_index=1,
                                        predecessor_checkpoint_sha256="a" * 64), AT)
        with self.assertRaisesRegex(ValueError, "must name the checkpoint"):
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                              self.page(page_id="page-2", page_index=1), AT)
        with self.assertRaisesRegex(ValueError, "cannot name a predecessor"):
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                              self.page(predecessor_checkpoint_sha256="a" * 64), AT)

    def test_a_page_cannot_be_observed_before_the_previous_one_is_checkpointed(self):
        self.start()
        FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                          self.page(fields=[self.standard_fields()[0]], final_page=False), AT)
        with self.assertRaisesRegex(ValueError, "must be checkpointed before"):
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                              self.page(page_id="page-2", page_index=1,
                                        predecessor_checkpoint_sha256="a" * 64), AT)
        checkpoint = self.complete_page()
        with self.assertRaisesRegex(ValueError, "does not match the previous page"):
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                              self.page(page_id="page-2", page_index=1,
                                        predecessor_checkpoint_sha256="f" * 64), AT)
        FILL.observe_page(
            self.db, "session-1", "worker-1", self.candidate_path,
            self.page(page_id="page-2", page_index=1,
                      page_url="https://apply.example.com/jobs/1/apply",
                      predecessor_checkpoint_sha256=checkpoint["checkpoint_sha256"]), AT)

    def test_a_duplicate_index_and_a_page_after_the_final_page_are_refused(self):
        self.start()
        FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path, self.page(), AT)
        with self.assertRaises(sqlite3.IntegrityError):
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                              self.page(page_id="page-1b", page_index=0), AT)
        self.db.rollback()
        checkpoint = self.complete_page()
        with self.assertRaisesRegex(ValueError, "no page follows the final page"):
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                              self.page(page_id="page-2", page_index=1,
                                        predecessor_checkpoint_sha256=checkpoint["checkpoint_sha256"]), AT)

    def test_finishing_refuses_a_form_whose_chain_is_incomplete(self):
        self.start()
        FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                          self.page(fields=[self.standard_fields()[0],
                                            self.standard_fields()[-1]], final_page=False), AT)
        self.complete_page()
        with self.assertRaisesRegex(ValueError, "no_final_page_observed"):
            FILL.finish_session(self.db, "session-1", "worker-1", "inventory-1", AT)
        self.assertEqual(POLICY.handling_summary(self.db, "app-1"),
                         {"status": "unknown", "markers": {}})


if __name__ == "__main__":
    unittest.main()
