"""The worker, its authority, and the attacks both have to survive.

Three layers, because they fail differently. `IssuanceTests` covers what the authority will
authorise — an earlier version signed whatever bytes the caller handed it. `AuthorityTests`
covers redemption, the only place a grant's existence can be decided; a previous attempt put
an HMAC key in the same file as the signature it verified, which authenticates nothing.
`BrowserExecutionTests` drives a real Chromium against the real replay, through a real
session, export, grant and redemption.

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
    spec = importlib.util.spec_from_file_location(f"worker_test_{name}", path)
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
PROTOCOL = load_script("worker_protocol")
WORKER = load_script("fill_worker")
AUTHORITY = load_script("execution_authority")
from tests.pdf_fixture import synthetic_pdf

import hashlib
import inspect
import os
import urllib.error
import urllib.request

from tests.fixtures.ats_replay_server import ReplayServer


def _browser_available() -> bool:
    """Playwright importable *and* a Chromium actually installed.

    Importing the package says nothing about whether a browser was downloaded, and a merge
    gate that accepted "it imported" would let the acceptance tests skip in CI.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).exists()
    except Exception:  # noqa: BLE001 - absence is the answer, not an error to raise
        return False


PLAYWRIGHT = _browser_available()

if os.environ.get("JOBLOOM_REQUIRE_BROWSER") and not PLAYWRIGHT:
    # A merge gate sets this. Skipping is correct on a laptop and unacceptable where the
    # browser acceptance tests are the point of the run.
    raise RuntimeError(
        "JOBLOOM_REQUIRE_BROWSER is set but no Chromium is available: "
        "run `python -m playwright install chromium`")


AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class _SessionBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        # The authority answers on its own thread, so the connection has to be usable there.
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
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

    def authority(self, reserve=None):
        """The real service, backed by the real redemption logic."""
        return AUTHORITY.ExecutionAuthority(
            self.db, reserve or FILL.reserve_execution_grant,
            consume=FILL.consume_execution_grant, clock=lambda: self.now)

    def reacquire(self, worker_id="worker-2"):
        APPLICATIONS.transition(self.db, "app-1", "ready_to_fill", "system", "user_resolved", at=AT)
        return APPLICATIONS.acquire_next(self.db, worker_id, at=AT)


    # ---- a session whose page is the running replay ----------------------

    def replay_session(self, server, path="/lever/0", field_id="lever-0-1",
                       extra_field_ids=()):
        """A real session against a real surface, so nothing here is fabricated."""
        self.start(form_url=f"{server.origin}{path}", at=self.now)
        observation = {
            "page_id": "page-1", "page_index": 0,
            "page_url": f"{server.origin}{path}",
            "fields": [
                {"field_id": field_id, "question": "Full name", "selector": "#n",
                 "control": "text", "required": True, "sensitivity": "normal",
                 "source_kind": "fact", "source_id": "fact-name"},
                *({"field_id": extra, "question": "Full name", "selector": f"#{extra}",
                   "control": "text", "required": True, "sensitivity": "normal",
                   "source_kind": "fact", "source_id": "fact-name"}
                  for extra in extra_field_ids),
                {"field_id": "final-action", "question": "Submit application",
                 "selector": "#s", "control": "submit", "required": True,
                 "sensitivity": "normal"},
            ],
            "legal_items": [], "restricted_requests": [], "final_page": True,
        }
        FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                          observation, self.now)
        return observation

    def export(self, name="package.json"):
        output = self.root / "private" / name
        FILL.export_page(self.db, "session-1", "worker-1", "page-1", output, self.now)
        return output

    def issue(self, package, **updates):
        return FILL.issue_execution_grant(
            self.db, "session-1", "page-1", package, self.now, **updates)


class IssuanceTests(_SessionBase):
    """What the authority will and will not authorise. No browser needed."""

    def setUp(self):
        # One injected clock for the whole run — the session, the surface and the grant all
        # read it — rather than mixing a fixed fixture time with wall-clock expiry.
        self.now = AT
        super().setUp()

    def test_a_package_this_authority_exported_is_authorised(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            grant = self.issue(self.export())
            self.assertTrue(grant["grant_id"].startswith("grant-"))

    def test_bytes_this_authority_never_exported_are_refused(self):
        # The hole: issuance checked only that the session and page existed, then signed
        # whatever file it was pointed at.
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            self.export()
            forged = self.root / "private" / "forged.json"
            forged.write_text('{"mode": "fill_only"}', encoding="utf-8")
            forged.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "was not exported by this authority"):
                self.issue(forged)

    def test_an_edited_or_added_action_is_refused_even_with_a_real_session_id(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            original = json.loads(package.read_text(encoding="utf-8"))
            for label, mutate in (
                ("value", lambda p: p["actions"][0].update({"value": "Someone Else"})),
                ("extra", lambda p: p["actions"].append(dict(p["actions"][0]))),
                ("hash", lambda p: p["actions"][0].update({"expected_sha256": "0" * 64})),
                ("identity", lambda p: p.update({"application_id": "app-2"})),
                ("surface", lambda p: p["surface"].update({"page_sha256": "0" * 64})),
            ):
                with self.subTest(mutation=label):
                    tampered = json.loads(json.dumps(original))
                    mutate(tampered)
                    path = self.root / "private" / f"tampered-{label}.json"
                    path.write_text(json.dumps(tampered, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
                    path.chmod(0o600)
                    with self.assertRaises(ValueError):
                        self.issue(path)


class AuthorityTests(_SessionBase):
    """Redemption: the only place a grant's existence is decided."""

    def setUp(self):
        # One injected clock for the whole run — the session, the surface and the grant all
        # read it — rather than mixing a fixed fixture time with wall-clock expiry.
        self.now = AT
        super().setUp()

    def redeem(self, authority, grant_id, digest, token=None):
        """One reservation attempt, which is what "is this authorised" now means."""
        request = urllib.request.Request(
            authority.url,
            data=json.dumps({"grant_id": grant_id, "package_sha256": digest}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token or authority.token}"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as refusal:
            try:
                return refusal.code, json.load(refusal)
            except Exception:  # noqa: BLE001
                return refusal.code, {}

    def test_an_attacker_cannot_mint_a_grant_for_a_package_of_their_own(self):
        """The attack the HMAC design could not stop.

        The old grant file carried the signature and the key that verified it, so an attacker
        never needed a real grant: choose a secret, sign a forged package, present both. Here
        there is nothing to sign. Authorisation is a row in the authority's database, and a
        package it never exported has none.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now):
            forged = self.root / "attacker.json"
            forged.write_text('{"mode": "fill_only", "actions": []}', encoding="utf-8")
            digest = hashlib.sha256(forged.read_bytes()).hexdigest()
            with self.authority() as authority:
                status, answer = self.redeem(authority, "grant-invented", digest)
                self.assertEqual(status, 403)
                self.assertEqual(answer["reason"], "grant_unknown")

    def test_a_real_grant_cannot_be_moved_onto_other_bytes(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            grant = self.issue(self.export())
            with self.authority() as authority:
                status, answer = self.redeem(authority, grant["grant_id"], "0" * 64)
                self.assertEqual((status, answer["reason"]), (403, "grant_package_mismatch"))

    def test_consumption_is_single_use_and_survives_copying_the_files(self):
        # Single-use is a row in the authority's state, not a marker beside a path, so
        # copying the package elsewhere buys nothing.
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            grant = self.issue(package)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            copied = self.root / "private" / "copy.json"
            copied.write_bytes(package.read_bytes())
            status, answer = 0, {}
            with self.authority() as authority:
                status, answer = self.redeem(authority, grant["grant_id"], digest)
                self.assertEqual(status, 200)
                FILL.consume_execution_grant(
                    self.db, grant["grant_id"], answer["reservation"], self.now)
                self.assertEqual(
                    self.redeem(authority, grant["grant_id"], digest)[1]["reason"],
                    "grant_already_consumed")
            self.assertEqual(
                hashlib.sha256(copied.read_bytes()).hexdigest(), digest,
                "the copy is byte-identical and still cannot run")

    def test_an_unconsumed_reservation_lapses_and_the_grant_can_run_later(self):
        # The point of splitting the phases: a run refused before it touched anything must
        # not have burned the grant.
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            grant = self.issue(package)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            with self.authority() as authority:
                self.assertEqual(self.redeem(authority, grant["grant_id"], digest)[0], 200)
                # Held, so a second attempt right away is refused rather than racing.
                self.assertEqual(
                    self.redeem(authority, grant["grant_id"], digest)[1]["reason"],
                    "grant_already_reserved")
            later = AUTHORITY.ExecutionAuthority(
                self.db, FILL.reserve_execution_grant, consume=FILL.consume_execution_grant,
                clock=lambda: self.now + timedelta(minutes=5))
            with later as authority:
                # The reservation lapsed; the grant was never consumed, so it still works.
                self.assertEqual(self.redeem(authority, grant["grant_id"], digest)[0], 200)

    def test_consuming_needs_the_reservation_that_was_issued(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            grant = self.issue(self.export())
            outcome = FILL.consume_execution_grant(
                self.db, grant["grant_id"], "not-the-reservation", self.now)
            self.assertEqual(outcome, {"consumed": False, "reason": "reservation_mismatch"})

    def test_a_revoked_or_expired_grant_is_refused(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            revoked = self.issue(package)
            FILL.revoke_execution_grant(self.db, revoked["grant_id"], self.now)
            with self.authority() as authority:
                self.assertEqual(
                    self.redeem(authority, revoked["grant_id"], digest)[1]["reason"],
                    "grant_revoked")
            later = AUTHORITY.ExecutionAuthority(
                self.db, FILL.reserve_execution_grant,
                clock=lambda: self.now + timedelta(hours=2))
            fresh = self.issue(package)
            with later as authority:
                self.assertEqual(
                    self.redeem(authority, fresh["grant_id"], digest)[1]["reason"],
                    "grant_expired")

    def test_another_local_process_cannot_spend_a_grant(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            grant = self.issue(package)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            with self.authority() as authority:
                self.assertEqual(
                    self.redeem(authority, grant["grant_id"], digest, token="guessed")[0], 403)
                # Unspent, so the legitimate run still works.
                self.assertEqual(self.redeem(authority, grant["grant_id"], digest)[0], 200)

    def test_the_oracle_is_a_capability_of_the_attested_surface(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            grant = self.issue(package)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            with self.authority() as authority:
                _, answer = self.redeem(authority, grant["grant_id"], digest)
            # Named by the authority from the surface, never by the caller: a caller-supplied
            # URL could be any service returning a constant zero.
            self.assertEqual(answer["oracle_url"], f"{server.origin}/__state")
            self.assertTrue(answer["target"].startswith(server.origin))

    def test_a_worker_refuses_an_oracle_that_is_not_on_the_surface(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            grant = self.issue(package)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()

            def lying_reserve(connection, grant_id, package_sha256, at):
                answer = FILL.reserve_execution_grant(connection, grant_id, package_sha256, at)
                if answer.get("authorised"):
                    answer["oracle_url"] = "http://127.0.0.1:9/always-zero"
                return answer

            with self.authority(reserve=lying_reserve) as authority:
                with self.assertRaises(WORKER.WorkerRefusal) as caught:
                    WORKER.reserve(authority.url, authority.token, grant["grant_id"], digest)
                self.assertEqual(str(caught.exception), "oracle_outside_surface")



@unittest.skipUnless(PLAYWRIGHT, "playwright is not installed")
class BrowserExecutionTests(_SessionBase):
    """Real Chromium, real replay, real session, real grant, real redemption."""

    def setUp(self):
        self.now = AT
        super().setUp()

    def final_actions(self, server):
        return json.load(urllib.request.urlopen(f"{server.origin}/__state", timeout=5))[
            "final_action_activations"]

    def execute(self, server, name="result.json", grant=None, authority=None):
        package = self.export(f"{name}.package.json")
        issued = grant or self.issue(package)
        output = self.root / name
        if authority is None:
            with self.authority() as service:
                summary = WORKER.run(package, output, service.url, service.token,
                                     issued["grant_id"], headed=False, at=self.now)
        else:
            summary = WORKER.run(package, output, authority.url, authority.token,
                                 issued["grant_id"], headed=False, at=self.now)
        return json.loads(output.read_text(encoding="utf-8")), output, summary

    def test_a_real_chain_fills_the_page_and_proves_the_counter_never_moved(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            self.assertEqual(self.final_actions(server), 0)
            envelope, output, _ = self.execute(server)
            self.assertEqual([entry["outcome"] for entry in envelope["results"]], ["verified"])
            # A zero from the target's own counter, reached through the oracle the authority
            # named — not a field the worker wrote.
            self.assertEqual(envelope["final_action_activations"], 0)
            self.assertEqual(self.final_actions(server), 0)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            # The expected digest is computed here, from the package bytes, before the
            # envelope is consulted. Using `envelope["package_sha256"]` on both sides is what
            # hid a bug where the field carried the last action's observed hash instead —
            # every real import would have failed on `package_hash_mismatch`.
            expected = hashlib.sha256(
                (self.root / "private" / "result.json.package.json").read_bytes()).hexdigest()
            self.assertEqual(envelope["package_sha256"], expected)
            observed = {entry.get("observed_sha256") for entry in envelope["results"]}
            self.assertNotIn(expected, observed)
            PROTOCOL.validate_result(
                envelope, expected_session="session-1", expected_page="page-1",
                expected_package_sha256=expected,
                expected_action_ids=["session-1:page-1:lever-0-1"], at=self.now)

    def test_the_package_digest_is_not_the_last_field_hash(self):
        # A multi-action run, because the shadowed variable took the value of whichever
        # action ran last: with one action the two could still have looked plausible.
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server, extra_field_ids=("lever-0-2", "lever-0-3"))
            package = self.export("multi.package.json")
            expected = hashlib.sha256(package.read_bytes()).hexdigest()
            issued = self.issue(package)
            output = self.root / "multi.json"
            with self.authority() as service:
                WORKER.run(package, output, service.url, service.token, issued["grant_id"],
                           headed=False, at=self.now)
            envelope = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(envelope["package_sha256"], expected)
            self.assertEqual(len(envelope["results"]), 3)
            for entry in envelope["results"]:
                self.assertNotEqual(entry.get("observed_sha256"), expected)
            PROTOCOL.validate_result(
                envelope, expected_session="session-1", expected_page="page-1",
                expected_package_sha256=expected,
                expected_action_ids=[entry["action_id"] for entry in envelope["results"]],
                at=self.now)

    def test_the_result_carries_no_value_nonce_or_local_path(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            envelope, output, _ = self.execute(server)
            raw = output.read_text(encoding="utf-8")
            for leak in ("Verified Candidate", server.nonce, str(self.root)):
                self.assertNotIn(leak, raw)

    def test_a_grant_cannot_be_spent_twice_even_from_a_copied_package(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            issued = self.issue(package)
            copied = self.root / "private" / "copied.json"
            copied.write_bytes(package.read_bytes())
            copied.chmod(0o600)
            with self.authority() as service:
                WORKER.run(package, self.root / "first.json", service.url, service.token,
                           issued["grant_id"], headed=False, at=self.now)
                with self.assertRaises(WORKER.WorkerRefusal) as caught:
                    WORKER.run(copied, self.root / "second.json", service.url,
                               service.token, issued["grant_id"], headed=False, at=self.now)
            self.assertEqual(str(caught.exception), "grant_already_consumed")
            self.assertEqual(self.final_actions(server), 0)

    def test_a_forged_package_from_a_rogue_server_gets_no_authorisation(self):
        """The full attack: attacker-chosen bytes, attacker-run server, attacker's own files.

        Nothing the attacker controls can produce an authorisation, because authorisation is
        a row in the authority's database rather than a signature they could compute.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as legitimate:
            self.replay_session(legitimate)
            with ReplayServer() as rogue:
                (self.root / "private").mkdir(parents=True, exist_ok=True)
                forged = self.root / "private" / "forged.json"
                forged.write_text(json.dumps({
                    "schema_version": "0.1.0", "mode": "fill_only",
                    "session_id": "session-1", "application_id": "app-1",
                    "page_id": "page-1", "page_url": f"{rogue.origin}/lever/0",
                    "surface": {"origin": rogue.origin, "renderer_version": "1.0.0",
                                "page_path": "/lever/0",
                                "page_sha256": rogue.page_digests()["/lever/0"],
                                "expires_at": (self.now + timedelta(hours=1)).isoformat()},
                    "actions": [], "stop_before_submit": True, "submission_action": None,
                }), encoding="utf-8")
                forged.chmod(0o600)
                # It passes every structural check the worker makes on the file itself.
                self.assertEqual(WORKER.load_package(forged)["page_id"], "page-1")
                # The authority will not issue for it.
                with self.assertRaisesRegex(ValueError, "was not exported"):
                    self.issue(forged)
                # And an invented grant id is not a grant.
                with self.authority() as service:
                    with self.assertRaises(WORKER.WorkerRefusal) as caught:
                        WORKER.run(forged, self.root / "forged.result.json", service.url,
                                   service.token, "grant-invented", headed=False,
                                   at=self.now)
                self.assertEqual(str(caught.exception), "grant_unknown")

    def test_a_same_origin_get_submit_is_stopped_and_the_counter_stays_zero(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server, "/hazard/get-submit", field_id="h-1")
            envelope, _, _ = self.execute(server)
            self.assertEqual(envelope["results"][0]["outcome"], "refused")
            self.assertEqual(envelope["results"][0]["error_code"], "navigation_attempted")
            self.assertEqual(self.final_actions(server), 0)
            self.assertEqual(envelope["final_action_activations"], 0)

    def test_the_first_violation_stops_the_page(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server, "/hazard/get-submit", field_id="h-1",
                                extra_field_ids=("h-2", "h-3"))
            envelope, _, _ = self.execute(server)
            self.assertEqual([entry["outcome"] for entry in envelope["results"]],
                             ["refused", "not_attempted", "not_attempted"])
            self.assertEqual(self.final_actions(server), 0)

    def test_an_unreachable_authority_authorises_nothing(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            issued = self.issue(package)
            with self.assertRaises(WORKER.WorkerRefusal) as caught:
                WORKER.run(package, self.root / "unreachable.json",
                           "http://127.0.0.1:9/redeem", "token", issued["grant_id"],
                           headed=False, at=self.now)
            self.assertEqual(str(caught.exception), "authority_unreachable")
            self.assertEqual(self.final_actions(server), 0)




class RevocationTests(_SessionBase):
    """Revoking says which of four things happened, and never invents a success."""

    def setUp(self):
        self.now = AT
        super().setUp()

    def test_revoking_a_grant_that_does_not_exist_is_not_a_success(self):
        # The old version ran the UPDATE without reading rowcount and returned revoked=True,
        # turning an absence into a fact.
        outcome = FILL.revoke_execution_grant(self.db, "grant-never-existed", self.now)
        self.assertEqual(outcome, {"grant_id": "grant-never-existed", "revoked": False,
                                   "status": "unknown"})

    def test_the_four_states_are_distinguished(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            first = self.issue(package)
            self.assertEqual(
                FILL.revoke_execution_grant(self.db, first["grant_id"], self.now)["status"],
                "revoked")
            self.assertEqual(
                FILL.revoke_execution_grant(self.db, first["grant_id"], self.now)["status"],
                "already_revoked")
            second = self.issue(package)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            reserved = FILL.reserve_execution_grant(
                self.db, second["grant_id"], digest, self.now)
            FILL.consume_execution_grant(
                self.db, second["grant_id"], reserved["reservation"], self.now)
            outcome = FILL.revoke_execution_grant(self.db, second["grant_id"], self.now)
            self.assertEqual(outcome["status"], "already_consumed")
            self.assertFalse(outcome["revoked"])


@unittest.skipUnless(PLAYWRIGHT, "playwright is not installed")
class TimingBoundaryTests(_SessionBase):
    """A static wait is not a completion boundary; the guard outliving the context is."""

    def setUp(self):
        self.now = AT
        super().setUp()

    def final_actions(self, server):
        return json.load(urllib.request.urlopen(f"{server.origin}/__state", timeout=5))[
            "final_action_activations"]

    def execute(self, server, name="result.json"):
        package = self.export(f"{name}.package.json")
        issued = self.issue(package)
        output = self.root / name
        with self.authority() as service:
            summary = WORKER.run(package, output, service.url, service.token,
                                 issued["grant_id"], headed=False, at=self.now)
        return json.loads(output.read_text(encoding="utf-8")), summary

    def unguarded_control(self, server, path, field_id="h-1"):
        """What the same page does with no guard and no teardown: the counter moves.

        Without this the zeros below prove nothing — a submit that never fires and a submit
        that was stopped look identical from the outside.
        """
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_context().new_page()
                page.goto(f"{server.origin}{path}", wait_until="domcontentloaded")
                page.locator(f'[data-test-id="{field_id}"]').fill("Verified Candidate")
                page.wait_for_timeout(1500)  # far past the page's own 400ms timer
            finally:
                browser.close()
        return self.final_actions(server)

    def test_a_submit_scheduled_past_any_static_wait_never_reaches_the_target(self):
        """The 150ms pause is not what stops this, and the test says so.

        The page schedules its submit 400ms after the input event — past any number a worker
        could pick. The control run proves the timer really fires and really reaches the
        server. The worker's run leaves the counter untouched, because the context is
        destroyed unconditionally rather than after a wait that could be outlasted.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.assertEqual(self.unguarded_control(server, "/hazard/delayed-submit"), 1)
            self.replay_session(server, "/hazard/delayed-submit", field_id="h-1")
            envelope, _ = self.execute(server)
            # Still one, from the control run: the worker's run added nothing.
            self.assertEqual(self.final_actions(server), 1)
            self.assertEqual(envelope["final_action_activations"], 0)

    def test_a_delayed_get_navigation_never_reaches_the_target_either(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.assertEqual(self.unguarded_control(server, "/hazard/delayed-get"), 1)
            self.replay_session(server, "/hazard/delayed-get", field_id="h-1")
            envelope, _ = self.execute(server)
            self.assertEqual(self.final_actions(server), 1)
            self.assertEqual(envelope["final_action_activations"], 0)

    def test_a_late_violation_makes_the_run_unattributable_rather_than_clean(self):
        # Reached directly, because whether a page's own timer fires before teardown is not
        # something a test should race on: what matters is what the envelope says when one
        # does.
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            issued = self.issue(package)
            original = WORKER.PageGuards.seal

            def seal_then_trip(self):
                original(self)
                self.violations.append("submit_attempted")

            WORKER.PageGuards.seal = seal_then_trip
            self.addCleanup(setattr, WORKER.PageGuards, "seal", original)
            with self.authority() as service:
                WORKER.run(package, self.root / "late.json", service.url, service.token,
                           issued["grant_id"], headed=False, at=self.now)
            envelope = json.loads((self.root / "late.json").read_text(encoding="utf-8"))
            self.assertEqual(envelope["side_effect_attribution"], "unproven")
            with self.assertRaises(PROTOCOL.ProtocolError) as caught:
                PROTOCOL.validate_result(
                    envelope, expected_session="session-1", expected_page="page-1",
                    expected_package_sha256=envelope["package_sha256"],
                    expected_action_ids=[entry["action_id"] for entry in envelope["results"]],
                    at=self.now)
            self.assertEqual(str(caught.exception), "side_effects_unattributed")

    def test_the_guard_is_never_removed_while_the_page_is_alive(self):
        # There is nothing to unroute: destroying the context is the removal.
        body = "\n".join(line for line in
                          inspect.getsource(WORKER.PageGuards.__exit__).split("\n")
                          if not line.strip().startswith("#"))
        self.assertNotIn("unroute", body)
        self.assertNotIn("remove_listener", body)
        run_source = inspect.getsource(WORKER.run)
        self.assertIn("context.close()", run_source)
        self.assertLess(run_source.index("guards.seal()"), run_source.index("context.close()"))
        # And the oracle is read after the context is gone, not before.
        self.assertLess(run_source.index("context.close()"),
                        run_source.rindex("_read_oracle(oracle_url)"))

    def test_an_unreadable_oracle_stops_before_a_field_is_touched(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            issued = self.issue(package)

            def blind_reserve(connection, grant_id, package_sha256, at):
                answer = FILL.reserve_execution_grant(connection, grant_id, package_sha256, at)
                if answer.get("authorised"):
                    # Same surface, an oracle path that is not being served.
                    answer["oracle_url"] = answer["origin"] + "/__state-missing"
                return answer

            with self.authority(reserve=blind_reserve) as service:
                with self.assertRaises(WORKER.WorkerRefusal) as caught:
                    WORKER.run(package, self.root / "blind.json", service.url, service.token,
                               issued["grant_id"], headed=False, at=self.now)
            self.assertEqual(str(caught.exception), "oracle_unavailable")
            # Nothing was opened and nothing was typed: the page still serves its blank form.
            page = urllib.request.urlopen(f"{server.origin}/lever/0", timeout=5).read().decode()
            self.assertNotIn("Verified Candidate", page)
            self.assertEqual(self.final_actions(server), 0)


class CapabilityTests(_SessionBase):
    """The token stays out of `argv`. That is the claim, and it is the whole claim.

    A 0600 file excludes other Unix users and keeps the value out of the process list. It
    does not exclude a hostile process running as this same user — that one can read the
    file — so nothing here tests for a property the mechanism does not have.
    """

    def setUp(self):
        self.now = AT
        super().setUp()

    def test_the_documented_limit_is_the_one_the_mechanism_has(self):
        # A same-UID reader is not excluded, and the docstrings say so rather than implying
        # a guarantee file permissions cannot give.
        def flat(text):
            return " ".join(text.split())

        for text in (inspect.getdoc(WORKER.read_capability),
                     inspect.getdoc(AUTHORITY.ExecutionAuthority.write_capability)):
            self.assertIn("same user", flat(text))
        self.assertIn("does not exclude", flat(inspect.getdoc(WORKER.read_capability)))

    def test_the_token_is_read_from_a_private_file_and_stays_out_of_argv(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            self.issue(package)
            with self.authority() as service:
                capability = service.write_capability(self.root / "capability.json")
                self.assertEqual(os.stat(capability).st_mode & 0o777, 0o600)
                url, token = WORKER.read_capability(capability)
                self.assertEqual((url, token), (service.url, service.token))
                # Not in the command line the worker exposes.
                parser_source = inspect.getsource(WORKER.main)
                self.assertNotIn("--authority-token", parser_source)
                self.assertIn("--capability", parser_source)
                # Not in the package, the events, or the exported action file.
                self.assertNotIn(service.token, package.read_text(encoding="utf-8"))
                events = " ".join(row[0] for row in self.db.execute(
                    "SELECT metadata_json FROM fill_events"))
                self.assertNotIn(service.token, events)

    def test_a_world_readable_capability_file_is_refused(self):
        capability = self.root / "loose.json"
        capability.write_text(json.dumps({"authority_url": "http://127.0.0.1:1/redeem",
                                          "token": "t"}), encoding="utf-8")
        capability.chmod(0o644)
        with self.assertRaises(WORKER.WorkerRefusal) as caught:
            WORKER.read_capability(capability)
        self.assertEqual(str(caught.exception), "capability_permissions")


if __name__ == "__main__":
    unittest.main()
