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


from tests.fixtures.completed_page import complete_page_as_if_imported

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
                 "source_kind": "fact", "canonical_id": "contact.full_name"},
                *({"field_id": extra, "question": "Full name", "selector": f"#{extra}",
                   "control": "text", "required": True, "sensitivity": "normal",
                   "source_kind": "fact", "canonical_id": "contact.full_name"}
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

    def test_an_expired_reservation_cannot_be_consumed(self):
        # Both clocks are conditions of the same statement. The old version checked only the
        # reservation string, so a hold taken at T0 could still be spent long after its own
        # window closed.
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            grant = self.issue(package)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            held = FILL.reserve_execution_grant(
                self.db, grant["grant_id"], digest, self.now, reservation_seconds=120)
            late = self.now + timedelta(minutes=5)
            outcome = FILL.consume_execution_grant(
                self.db, grant["grant_id"], held["reservation"], late)
            self.assertEqual(outcome, {"consumed": False, "reason": "reservation_expired"})
            # Nothing was spent, so a fresh reservation still works.
            again = FILL.reserve_execution_grant(
                self.db, grant["grant_id"], digest, late)
            self.assertTrue(again["authorised"])
            self.assertTrue(FILL.consume_execution_grant(
                self.db, grant["grant_id"], again["reservation"], late)["consumed"])

    def test_an_expired_grant_cannot_be_consumed_even_with_a_live_reservation(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            # A grant that outlives its own window before the hold does.
            grant = self.issue(package, ttl_seconds=60)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            held = FILL.reserve_execution_grant(
                self.db, grant["grant_id"], digest, self.now, reservation_seconds=3600)
            self.assertTrue(held["authorised"])
            late = self.now + timedelta(minutes=10)
            outcome = FILL.consume_execution_grant(
                self.db, grant["grant_id"], held["reservation"], late)
            self.assertEqual(outcome, {"consumed": False, "reason": "grant_expired"})

    def test_two_authorities_racing_for_one_grant_produce_exactly_one_winner(self):
        # Separate connections, so each authority's own lock cannot serialise them: the
        # decision has to come from the conditional update, not from Python.
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            grant = self.issue(package)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            shared = self.root / "shared.db"
            file_db = sqlite3.connect(shared, check_same_thread=False)
            file_db.row_factory = sqlite3.Row
            self.addCleanup(file_db.close)
            for line in self.db.iterdump():
                try:
                    file_db.execute(line)
                except sqlite3.Error:
                    pass
            file_db.commit()
            second = sqlite3.connect(shared, check_same_thread=False)
            second.row_factory = sqlite3.Row
            self.addCleanup(second.close)
            outcomes = [
                FILL.reserve_execution_grant(file_db, grant["grant_id"], digest, self.now),
                FILL.reserve_execution_grant(second, grant["grant_id"], digest, self.now),
            ]
            self.assertEqual([outcome["authorised"] for outcome in outcomes], [True, False])
            self.assertEqual(outcomes[1]["reason"], "grant_already_reserved")

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
            fresh = self.issue(package)
            # Thirty minutes: past the grant's fifteen-minute life, inside the surface's
            # hour, so the refusal names the grant rather than the surface.
            half_hour = AUTHORITY.ExecutionAuthority(
                self.db, FILL.reserve_execution_grant,
                consume=FILL.consume_execution_grant,
                clock=lambda: self.now + timedelta(minutes=30))
            with half_hour as authority:
                self.assertEqual(
                    self.redeem(authority, fresh["grant_id"], digest)[1]["reason"],
                    "grant_expired")
            # And past the surface's life the surface is what has gone.
            two_hours = AUTHORITY.ExecutionAuthority(
                self.db, FILL.reserve_execution_grant,
                consume=FILL.consume_execution_grant,
                clock=lambda: self.now + timedelta(hours=2))
            with two_hours as authority:
                self.assertEqual(
                    self.redeem(authority, self.issue(package)["grant_id"], digest)[1]["reason"],
                    "surface_expired")

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
class ResultImportTests(_SessionBase):
    """The whole chain, and every way a result must fail to change anything."""

    def setUp(self):
        self.now = AT
        super().setUp()

    def final_actions(self, server):
        return json.load(urllib.request.urlopen(f"{server.origin}/__state", timeout=5))[
            "final_action_activations"]

    def add_rival(self, answer_id, canonical_id, value, scope=None):
        """A competing answer under an existing question form, not a second form."""
        ANSWERS.add_answer(self.db, {
            "answer_id": answer_id, "canonical_id": canonical_id,
            "canonical_meaning": "Current company", "answer": value,
            "answer_type": "stable_fact", "source_type": "user_confirmed",
            "confirmation_status": "confirmed", "confirmed_at": AT.isoformat(),
            "validity_class": "stable",
            "scope": scope if scope is not None else {"country": "US",
                                                      "application_id": "app-1"},
            "auto_fill_allowed": True, "auto_submit_allowed": True,
        })

    def run_worker(self, server, name="result.json", extra_field_ids=()):
        """Real session, export, grant, Chromium, result — nothing fabricated."""
        self.replay_session(server, extra_field_ids=extra_field_ids)
        package = self.export(f"{name}.package.json")
        issued = self.issue(package)
        output = self.root / name
        with self.authority() as service:
            WORKER.run(package, output, service.url, service.token, issued["grant_id"],
                       headed=False, at=self.now)
        return package, issued, output

    def counts(self):
        return {
            "fields": self.db.execute("SELECT COUNT(*) FROM application_fields").fetchone()[0],
            "markers": self.db.execute(
                "SELECT COUNT(*) FROM nondisclosure_handling").fetchone()[0],
            "completed": self.db.execute(
                "SELECT COUNT(*) FROM fill_steps WHERE status='completed'").fetchone()[0],
            "events": self.db.execute("SELECT COUNT(*) FROM fill_events").fetchone()[0],
            "imports": self.db.execute("SELECT COUNT(*) FROM imported_results").fetchone()[0],
        }

    def rewrite(self, output, mutate):
        envelope = json.loads(output.read_text(encoding="utf-8"))
        mutate(envelope)
        path = output.with_suffix(f".{abs(hash(json.dumps(envelope, sort_keys=True)))}.json")
        path.write_text(json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def refuses(self, reason, session_id="session-1", page_id="page-1", **kwargs):
        before = self.counts()
        with self.assertRaises(FILL.ImportRefused) as caught:
            FILL.import_result(self.db, session_id, page_id, "worker-1",
                               kwargs["result"], kwargs["grant_id"], self.now)
        self.assertTrue(str(caught.exception).startswith(reason), str(caught.exception))
        self.assertEqual(self.counts(), before, "a refused import must change nothing")

    # ---- the whole chain --------------------------------------------------

    def test_a_real_run_imports_verifies_every_step_and_permits_checkpoint(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package, issued, output = self.run_worker(
                server, extra_field_ids=("lever-0-2", "lever-0-3"))
            outcome = FILL.import_result(self.db, "session-1", "page-1", "worker-1",
                                         output, issued["grant_id"], self.now)
            self.assertEqual(outcome["status"], "verified")
            self.assertEqual(outcome["verified_action_count"], 3)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM fill_steps WHERE status='completed'").fetchone()[0], 3)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM application_fields").fetchone()[0], 3)
            FILL.checkpoint_page(self.db, "session-1", "worker-1", "page-1", "cp-1", self.now)
            self.assertEqual(self.final_actions(server), 0)

    def test_the_expected_package_hash_comes_from_the_authority_not_the_envelope(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package, issued, output = self.run_worker(server)
            # Computed here, independently, and matched against what the grant recorded.
            external = hashlib.sha256(package.read_bytes()).hexdigest()
            recorded = self.db.execute(
                "SELECT package_sha256 FROM execution_grants WHERE grant_id=?",
                (issued["grant_id"],)).fetchone()["package_sha256"]
            self.assertEqual(recorded, external)
            # An envelope claiming a different package is refused even though it is
            # internally consistent — the expectation does not come from it.
            forged = self.rewrite(output, lambda e: e.update({"package_sha256": "0" * 64}))
            self.refuses("package_hash_mismatch", result=forged,
                         grant_id=issued["grant_id"])
            FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                               issued["grant_id"], self.now)

    # ---- batch atomicity --------------------------------------------------

    def test_a_bad_hash_records_nothing_and_hands_the_page_to_the_user(self):
        """Different in kind from a refusal: the worker acted and the page disagrees.

        Nothing successful is written — no step, field, marker or verified import row — and
        then the session goes to takeover, because what is on the form in front of the user
        is not what was planned.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(
                server, extra_field_ids=("lever-0-2", "lever-0-3"))

            def break_last(envelope):
                envelope["results"][-1]["observed_sha256"] = "f" * 64

            broken = self.rewrite(output, break_last)
            outcome = FILL.import_result(self.db, "session-1", "page-1", "worker-1", broken,
                                         issued["grant_id"], self.now)
            self.assertEqual(outcome["status"], "rejected")
            self.assertEqual(outcome["reason"], "observed_hash_mismatch")
            self.assertEqual(outcome["state"], "waiting_for_user_takeover")
            self.assertEqual(self.db.execute(
                "SELECT state FROM applications WHERE application_id='app-1'"
            ).fetchone()["state"], "waiting_for_user_takeover")
            # No successful writing of any kind.
            for table, column in (("fill_steps", "status='completed'"),
                                  ("application_fields", "1=1"),
                                  ("nondisclosure_handling", "1=1"),
                                  ("imported_results", "status='verified'")):
                self.assertEqual(self.db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column}").fetchone()[0], 0, table)
            # The failure record says nothing about the value.
            events = " ".join(str(part) for row in self.db.execute(
                "SELECT * FROM fill_events") for part in row)
            self.assertIn("incorrect_autofill", events)
            self.assertNotIn("f" * 64, events)
            self.assertNotIn("Verified Candidate", events)
            for row in self.db.execute("SELECT expected_sha256 FROM fill_steps"):
                self.assertNotIn(row["expected_sha256"], events)
            # Replaying the same rejection does not pause or record a second time.
            before = self.db.execute("SELECT COUNT(*) FROM fill_events").fetchone()[0]
            repeat = FILL.import_result(self.db, "session-1", "page-1", "worker-1", broken,
                                        issued["grant_id"], self.now)
            self.assertEqual(repeat["status"], "already_rejected")
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM fill_events").fetchone()[0], before)
            # The session is paused for the user, so checkpointing is refused there first;
            # the page has nothing verified behind it either way.
            with self.assertRaisesRegex(ValueError, "active fill session not found"):
                FILL.checkpoint_page(self.db, "session-1", "worker-1", "page-1", "cp-1",
                                     self.now)

    def test_completed_steps_without_an_import_still_cannot_checkpoint(self):
        # The second half of the gate: a page sealed on steps marked complete by some other
        # route would be a checkpoint with no verified result behind it.
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server)
            session = self.db.execute(
                "SELECT * FROM fill_sessions WHERE session_id='session-1'").fetchone()
            for step in self.db.execute("SELECT * FROM fill_steps").fetchall():
                FILL._apply_step(self.db, session, "worker-1", step, self.now)
            self.db.commit()
            with self.assertRaisesRegex(ValueError, "without a verified result import"):
                FILL.checkpoint_page(self.db, "session-1", "worker-1", "page-1", "cp-1",
                                     self.now)

    def test_every_non_verified_outcome_fails_the_whole_batch(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server, extra_field_ids=("lever-0-2",))
            for outcome in ("refused", "not_attempted", "not_actionable", "error",
                            "mismatch"):
                with self.subTest(outcome=outcome):
                    def spoil(envelope, outcome=outcome):
                        envelope["results"][0]["outcome"] = outcome
                        envelope["results"][0].pop("observed_sha256", None)
                    self.refuses(f"action_not_verified:{outcome}",
                                 result=self.rewrite(output, spoil),
                                 grant_id=issued["grant_id"])

    def test_missing_extra_duplicated_and_reordered_actions_are_refused(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server, extra_field_ids=("lever-0-2",))
            cases = {
                "result_order_or_completeness_mismatch":
                    lambda e: e["results"].pop(),
                "duplicate_result":
                    lambda e: e["results"].__setitem__(1, dict(e["results"][0])),
                "unexpected_action_id":
                    lambda e: e["results"].append(
                        {"action_id": "invented", "outcome": "verified",
                         "observed_sha256": "1" * 64}),
            }
            for reason, mutate in cases.items():
                with self.subTest(reason=reason):
                    self.refuses(reason, result=self.rewrite(output, mutate),
                                 grant_id=issued["grant_id"])
            reordered = self.rewrite(output, lambda e: e["results"].reverse())
            self.refuses("result_order_or_completeness_mismatch", result=reordered,
                         grant_id=issued["grant_id"])

    def test_an_unproven_or_moved_final_action_count_is_refused(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server)
            self.refuses("final_action_count_unproven",
                         result=self.rewrite(
                             output, lambda e: e.update({"final_action_activations": None})),
                         grant_id=issued["grant_id"])
            self.refuses("final_action_activated",
                         result=self.rewrite(
                             output, lambda e: e.update({"final_action_activations": 1})),
                         grant_id=issued["grant_id"])
            self.refuses("side_effects_unattributed",
                         result=self.rewrite(
                             output,
                             lambda e: e.update({"side_effect_attribution": "unproven"})),
                         grant_id=issued["grant_id"])


    # ---- everything the plan rested on, re-read ---------------------------

    def test_state_that_changed_since_planning_refuses_the_import(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server)
            arguments = {"result": output, "grant_id": issued["grant_id"]}

            # A lease that has run out is not this worker's lease any more.
            self.db.execute(
                "UPDATE applications SET lease_expires_at=? WHERE application_id='app-1'",
                ((self.now - timedelta(minutes=1)).isoformat(),))
            with self.assertRaises(ValueError):
                FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)
            self.db.execute(
                "UPDATE applications SET lease_expires_at=? WHERE application_id='app-1'",
                ((self.now + timedelta(minutes=5)).isoformat(),))

            # Authorisation withdrawn between planning and import.
            self.db.execute("UPDATE authorizations SET status='revoked' "
                            "WHERE authorization_id='auth-1'")
            self.refuses("standing_authorization", **arguments)
            self.db.execute("UPDATE authorizations SET status='active' "
                            "WHERE authorization_id='auth-1'")

            # The snapshot the plan was built from is no longer the active one.
            self.db.execute("UPDATE candidate_snapshots SET status='superseded'")
            self.refuses("candidate_snapshot_changed", **arguments)
            self.db.execute("UPDATE candidate_snapshots SET status='active'")

            # The material lock is gone.
            self.db.execute("UPDATE material_locks SET invalidated_at=?, "
                            "invalidation_reason='resume_rebound' WHERE application_id='app-1'",
                            (self.now.isoformat(),))
            with self.assertRaises(ValueError):
                FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)
            self.db.execute("UPDATE material_locks SET invalidated_at=NULL, "
                            "invalidation_reason=NULL WHERE application_id='app-1'")

            # With everything restored the same result imports.
            self.assertEqual(
                FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)["status"], "verified")

    def test_a_grant_that_is_unconsumed_revoked_or_for_another_page_is_refused(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package, issued, output = self.run_worker(server)
            self.refuses("grant_unknown", result=output, grant_id="grant-invented")
            # A second grant for the same package that was never run.
            fresh = self.issue(package)
            self.refuses("grant_not_consumed", result=output, grant_id=fresh["grant_id"])
            FILL.revoke_execution_grant(self.db, fresh["grant_id"], self.now)
            self.refuses("grant_revoked", result=output, grant_id=fresh["grant_id"])
            # And a grant whose page is not the one being imported.
            self.refuses("grant_belongs_to_another_page", page_id="page-2",
                         result=output, grant_id=issued["grant_id"])

    # ---- replay and conflict ----------------------------------------------

    def test_importing_the_same_result_twice_is_a_no_op(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server, extra_field_ids=("lever-0-2",))
            first = FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                                       issued["grant_id"], self.now)
            self.assertEqual(first["status"], "verified")
            after_first = self.counts()
            second = FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                                        issued["grant_id"], self.now)
            self.assertEqual(second["status"], "already_imported")
            # No duplicated fields, markers or events.
            self.assertEqual(self.counts(), after_first)

    def test_a_different_result_for_the_same_grant_is_a_conflict(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server)
            FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                               issued["grant_id"], self.now)
            recorded = self.counts()
            # Same grant, different bytes: the first record is never overwritten.
            other = self.rewrite(output, lambda e: e.update({"page_id": "page-1"}))
            other.write_text(output.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaises(FILL.ImportRefused) as caught:
                FILL.import_result(self.db, "session-1", "page-1", "worker-1", other,
                                   issued["grant_id"], self.now)
            self.assertEqual(str(caught.exception), "result_conflicts_with_recorded_import")
            self.assertEqual(self.counts(), recorded)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM imported_results").fetchone()[0], 1)

    def test_the_result_file_is_kept_for_audit(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server)
            FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                               issued["grant_id"], self.now)
            self.assertTrue(output.is_file())

    # ---- transaction boundary ---------------------------------------------

    def test_a_failure_midway_through_applying_leaves_nothing_behind(self):
        """A Python exception does not roll SQLite back on its own.

        Without an explicit savepoint the first step's writes would sit uncommitted in the
        connection, and the next `commit()` anyone made would make a failed batch permanent.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(
                server, extra_field_ids=("lever-0-2", "lever-0-3"))
            before = self.counts()
            original = FILL._apply_step
            calls = []

            def fail_on_second(connection, session, worker_id, step, at):
                calls.append(step["step_id"])
                if len(calls) == 2:
                    raise RuntimeError("source vanished mid-batch")
                return original(connection, session, worker_id, step, at)

            FILL._apply_step = fail_on_second
            self.addCleanup(setattr, FILL, "_apply_step", original)
            with self.assertRaises(RuntimeError):
                FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)
            FILL._apply_step = original
            # Someone else's commit must not be able to make the partial batch permanent.
            self.db.commit()
            self.assertEqual(self.counts(), before)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM fill_steps WHERE status='pending'").fetchone()[0], 3)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM imported_results").fetchone()[0], 0)

    # ---- per-step source freshness -----------------------------------------

    def test_an_answer_that_expired_or_moved_since_planning_refuses_the_import(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.add_answer("answer-company", "current_company", "Current company",
                            "Example Corp")
            self.start(form_url=f"{server.origin}/lever/0", at=self.now)
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path, {
                "page_id": "page-1", "page_index": 0,
                "page_url": f"{server.origin}/lever/0",
                "fields": [
                    # A textbox: a radiogroup renders as a fieldset, which the worker
                    # names as unsupported rather than acting on.
                    {"field_id": "lever-0-5", "question": "Current company",
                     "selector": "#c", "control": "text", "required": False,
                     "sensitivity": "normal", "source_kind": "answer"},
                    {"field_id": "final-action", "question": "Submit application",
                     "selector": "#s", "control": "submit", "required": True,
                     "sensitivity": "normal"},
                ],
                "legal_items": [], "restricted_requests": [], "final_page": True,
            }, self.now)
            package = self.export("answer.package.json")
            issued = self.issue(package)
            output = self.root / "answer.json"
            with self.authority() as service:
                WORKER.run(package, output, service.url, service.token, issued["grant_id"],
                           headed=False, at=self.now)
            arguments = {"result": output, "grant_id": issued["grant_id"]}

            # `record_field` is satisfied by "active and the same value", so each of these
            # would previously have imported cleanly.
            for column, value, reason in (
                ("expires_at", (self.now - timedelta(days=1)).isoformat(), "answer_expired"),
                ("review_after", (self.now - timedelta(days=1)).isoformat(),
                 "answer_review_due"),
                # The planner filters unconfirmed answers out of the candidate set, so what
                # comes back is "nothing applies" rather than a fact about that row.
                ("confirmation_status", "provisional", "no_applicable_answer"),
                ("scope_json", json.dumps({"country": "DE"}), "answer_scope_mismatch"),
                ("preconditions_json", json.dumps({"country": "DE"}),
                 "answer_precondition_failed"),
                ("auto_fill_allowed", 0, "automatic_fill_not_allowed"),
                ("answer_json", json.dumps("changed"), "answer_value_changed"),
            ):
                with self.subTest(column=column):
                    keep = self.db.execute(
                        f"SELECT {column} AS value FROM answers WHERE answer_id='answer-company'"
                    ).fetchone()["value"]
                    self.db.execute(
                        f"UPDATE answers SET {column}=? WHERE answer_id='answer-company'",
                        (value,))
                    self.refuses(reason, **arguments)
                    self.db.execute(
                        f"UPDATE answers SET {column}=? WHERE answer_id='answer-company'",
                        (keep,))
            self.assertEqual(
                FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)["status"], "verified")

    def test_a_competing_answer_added_after_planning_refuses_the_import(self):
        """`answer_issue` on the original row cannot see any of these.

        Each one changes what re-planning would choose, which is the decision the page was
        filled from — so each has to be re-made, not merely re-checked.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.add_answer("answer-company", "current_company", "Current company",
                            "Example Corp")
            self.start(form_url=f"{server.origin}/lever/0", at=self.now)
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path, {
                "page_id": "page-1", "page_index": 0,
                "page_url": f"{server.origin}/lever/0",
                "fields": [
                    {"field_id": "lever-0-5", "question": "Current company",
                     "selector": "#c", "control": "text", "required": False,
                     "sensitivity": "normal", "source_kind": "answer"},
                    {"field_id": "final-action", "question": "Submit application",
                     "selector": "#s", "control": "submit", "required": True,
                     "sensitivity": "normal"},
                ],
                "legal_items": [], "restricted_requests": [], "final_page": True,
            }, self.now)
            package = self.export("compete.package.json")
            issued = self.issue(package)
            output = self.root / "compete.json"
            with self.authority() as service:
                WORKER.run(package, output, service.url, service.token, issued["grant_id"],
                           headed=False, at=self.now)
            arguments = {"result": output, "grant_id": issued["grant_id"]}

            # Equal specificity, different value: re-planning is a conflict, and the old row
            # on its own still looks perfectly usable.
            self.add_rival("answer-company-2", "current_company", "Another Corp")
            self.refuses("conflicting_active_answers", **arguments)
            self.db.execute("UPDATE answers SET status='revoked' "
                            "WHERE answer_id='answer-company-2'")

            # A more specific answer that now wins: the page holds the value of one this
            # system would no longer choose.
            self.add_rival("answer-company-3", "current_company", "Specific Corp",
                           scope={"country": "US", "application_id": "app-1",
                                  "company": "Example Corp"})
            self.refuses("answer_selection_changed", **arguments)
            self.db.execute("UPDATE answers SET status='revoked' "
                            "WHERE answer_id='answer-company-3'")

            # The question form now maps to two canonical meanings.
            ANSWERS.add_question_form(self.db, "another_meaning", "Current company")
            self.refuses("question_mapping_conflict", **arguments)
            self.db.execute("DELETE FROM question_forms WHERE canonical_id='another_meaning'")

            self.assertEqual(
                FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)["status"], "verified")

    def test_a_rejection_and_its_handover_are_one_transaction(self):
        """A rejected row without a pause would eat the handover signal permanently.

        Replay answers `already_rejected`, so the takeover would never be attempted again.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server)
            broken = self.rewrite(
                output, lambda e: e["results"][0].update({"observed_sha256": "f" * 64}))
            before_state = self.db.execute(
                "SELECT state FROM applications WHERE application_id='app-1'"
            ).fetchone()["state"]
            before_events = self.db.execute(
                "SELECT COUNT(*) FROM fill_events").fetchone()[0]

            # The savepoint path uses the uncommitted primitive, which is the point of
            # having a separately named one.
            original = FILL.application_core._release_lease_uncommitted

            def fail_after_the_row(*arguments, **keywords):
                raise RuntimeError("lease race during handover")

            FILL.application_core._release_lease_uncommitted = fail_after_the_row
            self.addCleanup(setattr, FILL.application_core,
                            "_release_lease_uncommitted", original)
            with self.assertRaises(RuntimeError):
                FILL.import_result(self.db, "session-1", "page-1", "worker-1", broken,
                                   issued["grant_id"], self.now)
            FILL.application_core._release_lease_uncommitted = original
            # A commit by anyone else must not make the half-written handover permanent.
            self.db.commit()
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM imported_results").fetchone()[0], 0)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM fill_events").fetchone()[0], before_events)
            self.assertEqual(self.db.execute(
                "SELECT status FROM fill_sessions WHERE session_id='session-1'"
            ).fetchone()["status"], "active")
            self.assertEqual(self.db.execute(
                "SELECT status FROM fill_pages WHERE page_id='page-1'"
            ).fetchone()["status"], "active")
            self.assertEqual(self.db.execute(
                "SELECT state FROM applications WHERE application_id='app-1'"
            ).fetchone()["state"], before_state)

            # With the handover working, all four land together.
            outcome = FILL.import_result(self.db, "session-1", "page-1", "worker-1", broken,
                                         issued["grant_id"], self.now)
            self.assertEqual(outcome["status"], "rejected")
            self.assertEqual(self.db.execute(
                "SELECT status FROM imported_results").fetchone()["status"], "rejected")
            self.assertEqual(self.db.execute(
                "SELECT status FROM fill_sessions WHERE session_id='session-1'"
            ).fetchone()["status"], "paused")
            self.assertEqual(self.db.execute(
                "SELECT state FROM applications WHERE application_id='app-1'"
            ).fetchone()["state"], "waiting_for_user_takeover")
            reasons = [row["reason_code"] for row in self.db.execute(
                "SELECT reason_code FROM fill_events")]
            self.assertIn("observed_hash_mismatch", reasons)

    def test_the_rejection_event_carries_a_hashed_step_id_and_no_hashes_of_values(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server)
            expected = self.db.execute(
                "SELECT step_id, expected_sha256 FROM fill_steps").fetchone()
            broken = self.rewrite(
                output, lambda e: e["results"][0].update({"observed_sha256": "f" * 64}))
            FILL.import_result(self.db, "session-1", "page-1", "worker-1", broken,
                               issued["grant_id"], self.now)
            row = self.db.execute(
                "SELECT metadata_json FROM fill_events WHERE event_type='result_rejected'"
            ).fetchone()
            metadata = json.loads(row["metadata_json"])
            # Persisted, not merely returned: the docstring's claim and the record agree.
            self.assertEqual(set(metadata), {"page_id", "step_id_sha256"})
            self.assertNotEqual(metadata["step_id_sha256"], expected["step_id"])
            events = " ".join(str(part) for line in self.db.execute(
                "SELECT * FROM fill_events") for part in line)
            self.assertNotIn(expected["expected_sha256"], events)
            self.assertNotIn("f" * 64, events)
            self.assertNotIn("Verified Candidate", events)

    def test_an_immigration_answer_rescoped_after_planning_refuses_the_import(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            # A text value, so a correct fill hashes to the planned digest: a boolean would
            # come back as "True" and be a hash mismatch, which is a different path.
            self.db.execute("UPDATE answers SET answer_json=? WHERE answer_id='answer-auth'",
                            (json.dumps("Yes"),))
            self.start(form_url=f"{server.origin}/lever/0", at=self.now)
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path, {
                "page_id": "page-1", "page_index": 0,
                "page_url": f"{server.origin}/lever/0",
                "fields": [
                    {"field_id": "lever-0-5", "question": "Are you authorized to work?",
                     "selector": "#a", "control": "text", "required": True,
                     "sensitivity": "normal", "source_kind": "answer"},
                    {"field_id": "final-action", "question": "Submit application",
                     "selector": "#s", "control": "submit", "required": True,
                     "sensitivity": "normal"},
                ],
                "legal_items": [], "restricted_requests": [], "final_page": True,
            }, self.now)
            package = self.export("immigration.package.json")
            issued = self.issue(package)
            output = self.root / "immigration.json"
            with self.authority() as service:
                WORKER.run(package, output, service.url, service.token, issued["grant_id"],
                           headed=False, at=self.now)
            # One of the four immigration meanings, re-scoped to another application after
            # the page was planned. `answer_issue` alone would not object.
            self.db.execute("UPDATE answers SET scope_json=? WHERE answer_id='answer-auth'",
                            (json.dumps({"country": "US", "application_id": "app-2"}),))
            self.refuses("answer_scope_mismatch", result=output,
                         grant_id=issued["grant_id"])
            self.db.execute("UPDATE answers SET scope_json=? WHERE answer_id='answer-auth'",
                            (json.dumps({"country": "US", "application_id": "app-1"}),))
            self.assertEqual(
                FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)["status"], "verified")

    def test_a_discovery_source_answer_that_loses_its_permission_refuses_the_import(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            ANSWERS.add_answer(self.db, {
                "answer_id": "answer-source", "canonical_id": "discovery_source",
                "canonical_meaning": "How did you hear about us?", "answer": "Job board",
                "answer_type": "application_specific", "source_type": "user_confirmed",
                "confirmation_status": "confirmed", "confirmed_at": AT.isoformat(),
                "validity_class": "per_application",
                "scope": {"country": "US", "application_id": "app-1"},
                "auto_fill_allowed": True, "auto_submit_allowed": False,
            })
            ANSWERS.add_question_form(self.db, "discovery_source",
                                      "How did you hear about us?")
            self.start(form_url=f"{server.origin}/lever/0", at=self.now)
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path, {
                "page_id": "page-1", "page_index": 0,
                "page_url": f"{server.origin}/lever/0",
                "fields": [
                    {"field_id": "lever-0-5", "question": "How did you hear about us?",
                     "selector": "#d", "control": "text", "required": False,
                     "sensitivity": "normal", "source_kind": "answer"},
                    {"field_id": "final-action", "question": "Submit application",
                     "selector": "#s", "control": "submit", "required": True,
                     "sensitivity": "normal"},
                ],
                "legal_items": [], "restricted_requests": [], "final_page": True,
            }, self.now)
            package = self.export("discovery.package.json")
            issued = self.issue(package)
            output = self.root / "discovery.json"
            with self.authority() as service:
                WORKER.run(package, output, service.url, service.token, issued["grant_id"],
                           headed=False, at=self.now)
            # How the user heard about a role is their statement. A source type that is no
            # longer a user confirmation loses that standing after planning.
            self.db.execute("UPDATE answers SET source_type='deterministic_derivation' "
                            "WHERE answer_id='answer-source'")
            self.refuses("discovery_source_not_user_confirmed", result=output,
                         grant_id=issued["grant_id"])
            self.db.execute("UPDATE answers SET source_type='user_confirmed' "
                            "WHERE answer_id='answer-source'")
            self.assertEqual(
                FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)["status"], "verified")

    def test_a_fact_that_changed_or_unlocked_since_planning_refuses_the_import(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server)
            arguments = {"result": output, "grant_id": issued["grant_id"]}
            self.db.execute("UPDATE candidate_facts SET locked=0 WHERE fact_id='fact-name'")
            self.refuses("candidate_fact_not_locked", **arguments)
            self.db.execute("UPDATE candidate_facts SET locked=1 WHERE fact_id='fact-name'")
            self.db.execute("UPDATE candidate_facts SET value_json=? WHERE fact_id='fact-name'",
                            (json.dumps("Someone Else"),))
            self.refuses("candidate_fact_changed", **arguments)

    def test_a_policy_that_expired_or_narrowed_since_planning_refuses_the_import(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start(form_url=f"{server.origin}/lever/0", at=self.now)
            POLICY.register_policy(
                self.db, policy_id="policy-eeo_gender", question_family="eeo_gender",
                locale="en-US", option_tokens=["decline_to_answer"], confirmed_by="user",
                confirmed_at=self.now, scope={})
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path, {
                "page_id": "page-1", "page_index": 0,
                "page_url": f"{server.origin}/lever/0", "locale": "en-US",
                "fields": [
                    {"field_id": "lever-0-23", "question": "Gender", "selector": "#g",
                     "control": "select", "required": False, "sensitivity": "normal",
                     "options": [{"label": label,
                                  "value": POLICY.replay_option_value(label, server.nonce)}
                                 for label in ("Male", "Female", "Decline to answer")]},
                    {"field_id": "final-action", "question": "Submit application",
                     "selector": "#s", "control": "submit", "required": True,
                     "sensitivity": "normal"},
                ],
                "legal_items": [], "restricted_requests": [], "final_page": True,
            }, self.now)
            package = self.export("policy.package.json")
            issued = self.issue(package)
            output = self.root / "policy.json"
            with self.authority() as service:
                WORKER.run(package, output, service.url, service.token, issued["grant_id"],
                           headed=False, at=self.now)
            arguments = {"result": output, "grant_id": issued["grant_id"]}

            # `record_handling` never re-read the policy at all.
            POLICY.revoke_policy(self.db, "policy-eeo_gender", "user_withdrew", self.now)
            self.refuses("nondisclosure_policy_revoked", **arguments)
            self.db.execute("UPDATE nondisclosure_policies SET revoked_at=NULL")
            self.db.execute("UPDATE nondisclosure_policies SET expires_at=? ",
                            ((self.now - timedelta(days=1)).isoformat(),))
            self.refuses("nondisclosure_policy_expired", **arguments)
            self.db.execute("UPDATE nondisclosure_policies SET expires_at=NULL")
            self.db.execute("UPDATE nondisclosure_policies SET scope_json=?",
                            (json.dumps({"country": "DE"}),))
            self.refuses("nondisclosure_policy_scope_mismatch", **arguments)
            self.db.execute("UPDATE nondisclosure_policies SET scope_json='{}'")
            # Narrowed to a vocabulary that no longer produces the option that was planned.
            self.db.execute("UPDATE nondisclosure_policies SET option_tokens_json=?",
                            (json.dumps(["prefer_not_to_answer"]),))
            self.refuses("nondisclosure_option_no_longer_reviewed", **arguments)
            self.db.execute("UPDATE nondisclosure_policies SET option_tokens_json=?",
                            (json.dumps(["decline_to_answer"]),))
            self.assertEqual(
                FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)["status"], "verified")

    def test_idempotence_never_returns_success_for_the_wrong_page(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server)
            FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                               issued["grant_id"], self.now)
            # Same grant, same bytes, a page the caller named wrongly.
            with self.assertRaises(FILL.ImportRefused) as caught:
                FILL.import_result(self.db, "session-1", "page-9", "worker-1", output,
                                   issued["grant_id"], self.now)
            # The recorded import is what belongs elsewhere, and idempotence does not get to
            # skip that check: the old branch returned success with the caller's wrong ids
            # echoed back.
            self.assertEqual(str(caught.exception), "import_belongs_to_another_page")

    # ---- two connections racing for one grant ------------------------------

    def shared_database(self):
        """The same state on disk, reachable from two independent connections."""
        path = self.root / "race.db"
        first = sqlite3.connect(path, check_same_thread=False)
        first.row_factory = sqlite3.Row
        self.addCleanup(first.close)
        for line in self.db.iterdump():
            try:
                first.execute(line)
            except sqlite3.Error:
                pass
        first.commit()
        second = sqlite3.connect(path, check_same_thread=False)
        second.row_factory = sqlite3.Row
        self.addCleanup(second.close)
        return first, second

    def test_a_verified_import_winning_the_race_is_not_undone_by_a_mismatch(self):
        """The bug `INSERT OR IGNORE` created: a losing racer paused a finished page.

        The conflict was swallowed, so the mismatch path carried on and moved an application
        whose page another connection had already imported cleanly.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server)
            broken = self.rewrite(
                output, lambda e: e["results"][0].update({"observed_sha256": "f" * 64}))
            winner, loser = self.shared_database()

            self.assertEqual(
                FILL.import_result(winner, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)["status"], "verified")
            # A different result for a grant already imported is a conflict, not a pause.
            with self.assertRaises(FILL.ImportRefused) as caught:
                FILL.import_result(loser, "session-1", "page-1", "worker-1", broken,
                                   issued["grant_id"], self.now)
            self.assertEqual(str(caught.exception),
                             "result_conflicts_with_recorded_import")
        rows = winner.execute("SELECT status FROM imported_results").fetchall()
        self.assertEqual([row["status"] for row in rows], ["verified"])
        self.assertNotEqual(
            winner.execute("SELECT state FROM applications WHERE application_id='app-1'"
                           ).fetchone()["state"], "waiting_for_user_takeover")

    def test_a_rejection_winning_the_race_leaves_the_verified_path_writing_nothing(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server)
            broken = self.rewrite(
                output, lambda e: e["results"][0].update({"observed_sha256": "f" * 64}))
            winner, loser = self.shared_database()

            self.assertEqual(
                FILL.import_result(winner, "session-1", "page-1", "worker-1", broken,
                                   issued["grant_id"], self.now)["status"], "rejected")
            with self.assertRaises(FILL.ImportRefused) as caught:
                FILL.import_result(loser, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)
            self.assertEqual(str(caught.exception),
                             "result_conflicts_with_recorded_import")
        rows = winner.execute("SELECT status FROM imported_results").fetchall()
        self.assertEqual([row["status"] for row in rows], ["rejected"])
        # The losing verified path applied nothing.
        for table in ("application_fields", "nondisclosure_handling"):
            self.assertEqual(
                winner.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
        self.assertEqual(winner.execute(
            "SELECT COUNT(*) FROM fill_steps WHERE status='completed'").fetchone()[0], 0)
        # State and events agree with the row that won.
        self.assertEqual(winner.execute(
            "SELECT state FROM applications WHERE application_id='app-1'"
        ).fetchone()["state"], "waiting_for_user_takeover")
        self.assertEqual(winner.execute(
            "SELECT COUNT(*) FROM fill_events WHERE event_type='result_rejected'"
        ).fetchone()[0], 1)

    def test_the_same_result_racing_itself_is_idempotent_on_both_connections(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            _, issued, output = self.run_worker(server)
            winner, loser = self.shared_database()
            self.assertEqual(
                FILL.import_result(winner, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)["status"], "verified")
            self.assertEqual(
                FILL.import_result(loser, "session-1", "page-1", "worker-1", output,
                                   issued["grant_id"], self.now)["status"],
                "already_imported")
        self.assertEqual(winner.execute(
            "SELECT COUNT(*) FROM imported_results").fetchone()[0], 1)

    # ---- transaction ownership is not a public boolean ---------------------

    def test_the_public_transition_api_always_persists(self):
        self.assertNotIn("commit",
                         inspect.signature(APPLICATIONS.transition).parameters)
        self.assertNotIn("commit",
                         inspect.signature(APPLICATIONS.release_lease).parameters)
        self.assertTrue(hasattr(APPLICATIONS, "_transition_uncommitted"))
        self.assertTrue(hasattr(APPLICATIONS, "_release_lease_uncommitted"))
        first, second = self.shared_database()
        # Public: visible from another connection immediately.
        APPLICATIONS.transition(first, "app-1", "waiting_for_user_takeover", "worker-1",
                                "fill_paused_for_takeover", at=self.now)
        self.assertEqual(second.execute(
            "SELECT state FROM applications WHERE application_id='app-1'"
        ).fetchone()["state"], "waiting_for_user_takeover")
        # Private: nothing is durable until the caller that owns the transaction commits.
        APPLICATIONS._transition_uncommitted(first, "app-1", "ready_to_fill", "user",
                                             "user_resolved", at=self.now)
        self.assertEqual(second.execute(
            "SELECT state FROM applications WHERE application_id='app-1'"
        ).fetchone()["state"], "waiting_for_user_takeover")
        first.commit()
        self.assertEqual(second.execute(
            "SELECT state FROM applications WHERE application_id='app-1'"
        ).fetchone()["state"], "ready_to_fill")

    # ---- value isolation ---------------------------------------------------

    def test_no_value_token_or_path_reaches_events_or_the_import_record(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.replay_session(server)
            package = self.export()
            issued = self.issue(package)
            output = self.root / "isolated.json"
            with self.authority() as service:
                token, reservation = service.token, None
                capability = service.write_capability(self.root / "cap.json")
                summary = WORKER.run(package, output, service.url, service.token,
                                     issued["grant_id"], headed=False, at=self.now)
            FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                               issued["grant_id"], self.now)
            stored = " ".join(str(part) for row in self.db.execute(
                "SELECT * FROM fill_events") for part in row)
            stored += " ".join(str(part) for row in self.db.execute(
                "SELECT * FROM imported_results") for part in row)
            for leak in ("Verified Candidate", token, server.nonce, str(capability),
                         str(self.root)):
                self.assertNotIn(leak, stored)
            # And the summary the CLI would print carries counts, not values.
            self.assertNotIn("Verified Candidate", json.dumps(summary))
            self.assertNotIn(token, json.dumps(summary))

    def test_a_nondisclosure_step_imports_as_a_marker_and_never_a_field(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.start(form_url=f"{server.origin}/lever/0", at=self.now)
            POLICY.register_policy(
                self.db, policy_id="policy-eeo_gender", question_family="eeo_gender",
                locale="en-US", option_tokens=["decline_to_answer"], confirmed_by="user",
                confirmed_at=self.now, scope={})
            observation = {
                "page_id": "page-1", "page_index": 0,
                "page_url": f"{server.origin}/lever/0", "locale": "en-US",
                "fields": [
                    # `lever-0-23` is the fixture's Gender control, a `<select>`.
                    {"field_id": "lever-0-23", "question": "Gender",
                     "selector": "#g", "control": "select", "required": False,
                     "sensitivity": "normal",
                     "options": [{"label": label,
                                  "value": POLICY.replay_option_value(label, server.nonce)}
                                 for label in ("Male", "Female", "Decline to answer")]},
                    {"field_id": "final-action", "question": "Submit application",
                     "selector": "#s", "control": "submit", "required": True,
                     "sensitivity": "normal"},
                ],
                "legal_items": [], "restricted_requests": [], "final_page": True,
            }
            FILL.observe_page(self.db, "session-1", "worker-1", self.candidate_path,
                              observation, self.now)
            package = self.export("eeo.package.json")
            issued = self.issue(package)
            output = self.root / "eeo.json"
            with self.authority() as service:
                WORKER.run(package, output, service.url, service.token, issued["grant_id"],
                           headed=False, at=self.now)
            FILL.import_result(self.db, "session-1", "page-1", "worker-1", output,
                               issued["grant_id"], self.now)
            self.assertEqual(POLICY.handling_summary(self.db, "app-1"),
                             {"status": "recorded", "markers": {"lever-0-23": "policy_declined"}})
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM application_fields WHERE field_id='lever-0-23'"
            ).fetchone()[0], 0)
            dump = "\n".join(self.db.iterdump())
            for value in ("Male", "Female"):
                self.assertNotIn(value, dump)


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

    def test_the_authority_url_must_be_the_exact_loopback_reserve_endpoint(self):
        """A capability file names where the token goes. Anything else is a network egress.

        Without a shape check a misconfigured or hostile file would POST the token, the grant
        id and the package digest anywhere, then act on whatever came back as authorisation.
        """
        for bad in ("http://localhost:8931/reserve",
                    "https://127.0.0.1:8931/reserve",
                    "http://127.0.0.1:8931/consume",
                    "http://127.0.0.1:8931/reserve?x=1",
                    "http://127.0.0.1:8931/reserve#fragment",
                    "http://user:pass@127.0.0.1:8931/reserve",
                    "http://127.0.0.1:99999/reserve",
                    "http://127.0.0.1:0/reserve",
                    "http://evil.example.com/reserve",
                    "http://127.0.0.1.evil.com:8931/reserve"):
            with self.subTest(url=bad):
                capability = self.root / f"cap-{abs(hash(bad))}.json"
                capability.write_text(
                    json.dumps({"authority_url": bad, "token": "t"}), encoding="utf-8")
                capability.chmod(0o600)
                with self.assertRaises(WORKER.WorkerRefusal) as caught:
                    WORKER.read_capability(capability)
                self.assertEqual(str(caught.exception), "authority_url_not_loopback")
        good = self.root / "cap-good.json"
        good.write_text(json.dumps({"authority_url": "http://127.0.0.1:8931/reserve",
                                    "token": "t"}), encoding="utf-8")
        good.chmod(0o600)
        self.assertEqual(WORKER.read_capability(good)[0], "http://127.0.0.1:8931/reserve")

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
