"""The worker, driven against the real replay with a real browser.

Skipped when Playwright is unavailable, because the browser is an optional local tool rather
than something the rest of the suite depends on. Everything here runs headless; the worker
itself is headed by default, since a run the user cannot see is a run they cannot stop.
"""

import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.request
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


WORKER = load_script("fill_worker")
POLICY = load_script("field_policy")
PROTOCOL = load_script("worker_protocol")

from tests.fixtures.ats_replay_server import ReplayServer
from tests.pdf_fixture import synthetic_pdf

AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)

def _browser_available() -> bool:
    """Playwright importable *and* a Chromium actually installed.

    Importing the package says nothing about whether a browser was downloaded, and a merge
    gate that accepts "it imported" would let the acceptance tests skip in CI.
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
    # eleven acceptance tests are the point of the run.
    raise RuntimeError(
        "JOBLOOM_REQUIRE_BROWSER is set but no Chromium is available: "
        "run `python -m playwright install chromium`")


class PackageAcceptanceTests(unittest.TestCase):
    """What the worker refuses before a browser is ever launched."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, package, mode=0o600, name="package.json"):
        path = self.root / name
        path.write_text(json.dumps(package), encoding="utf-8")
        path.chmod(mode)
        return path

    def package(self, **updates):
        value = {
            "schema_version": "0.1.0", "mode": "fill_only", "session_id": "session-1",
            "application_id": "app-1", "page_id": "page-1",
            "page_url": "http://127.0.0.1:8931/lever/0",
            "surface": {"origin": "http://127.0.0.1:8931", "renderer_version": "1.0.0",
                        "page_path": "/lever/0", "page_sha256": "a" * 64,
                        "expires_at": (AT + timedelta(hours=1)).isoformat()},
            "actions": [{"step_id": "s-1", "field_id": "f-1", "selector": "#f",
                         "control": "text", "operation": "fill", "value": "x",
                         "expected_sha256": "b" * 64}],
            "stop_before_submit": True, "submission_action": None,
        }
        value.update(updates)
        return value

    def refuses(self, code, package, mode=0o600):
        with self.assertRaises(WORKER.WorkerRefusal) as caught:
            WORKER.load_package(self.write(package, mode))
        self.assertEqual(str(caught.exception), code)

    def test_a_valid_package_is_accepted(self):
        self.assertEqual(
            WORKER.load_package(self.write(self.package()))["page_id"], "page-1")

    def test_a_world_readable_package_is_refused(self):
        self.refuses("package_permissions", self.package(), mode=0o644)

    def test_a_package_without_an_attested_surface_is_refused(self):
        self.refuses("no_attested_surface", self.package(surface=None))
        surface = dict(self.package()["surface"])
        del surface["page_sha256"]
        self.refuses("incomplete_surface_attestation", self.package(surface=surface))

    def test_a_surface_off_loopback_or_from_another_renderer_is_refused(self):
        for origin, code in (("https://jobs.lever.co", "surface_outside_loopback"),
                             ("http://localhost:8931", "surface_outside_loopback"),
                             ("http://10.0.0.5:8931", "surface_outside_loopback")):
            with self.subTest(origin=origin):
                surface = {**self.package()["surface"], "origin": origin}
                self.refuses(code, self.package(surface=surface))
        surface = {**self.package()["surface"], "renderer_version": "0.9.0"}
        self.refuses("renderer_version_mismatch", self.package(surface=surface))

    def test_a_submission_shaped_package_is_refused(self):
        self.refuses("stop_before_submit_not_asserted", self.package(stop_before_submit=False))
        self.refuses("submission_action_present", self.package(submission_action="#submit"))
        for operation in ("click", "submit", "navigate", "press", "evaluate", "download"):
            with self.subTest(operation=operation):
                actions = [{**self.package()["actions"][0], "operation": operation}]
                self.refuses("unsupported_operation", self.package(actions=actions))

    def test_a_package_can_only_be_consumed_once(self):
        path = self.write(self.package())
        WORKER.consume(path)
        with self.assertRaises(WORKER.WorkerRefusal) as caught:
            WORKER.consume(path)
        self.assertEqual(str(caught.exception), "package_already_consumed")
        # The package survives for audit; only a second run is refused.
        self.assertTrue(path.is_file())


@unittest.skipUnless(PLAYWRIGHT, "playwright is not installed")
class BrowserExecutionTests(unittest.TestCase):
    """The worker against a real Chromium and the real replay server."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        POLICY.initialize(self.db)
        self.addCleanup(self.db.close)
        self.now = datetime.now(timezone.utc)

    def package_for(self, server, path, actions, **updates):
        attestation = POLICY.surface_attestation(
            self.db, f"{server.origin}{path}", self.now)
        self.assertIsNotNone(attestation, "the running server should be an attested surface")
        package = {
            "schema_version": "0.1.0", "mode": "fill_only", "session_id": "session-1",
            "application_id": "app-1", "page_id": "page-1",
            "page_url": f"{server.origin}{path}", "surface": attestation,
            "actions": actions, "stop_before_submit": True, "submission_action": None,
        }
        package.update(updates)
        file = self.root / f"package-{len(list(self.root.glob('package-*')))}.json"
        file.write_text(json.dumps(package), encoding="utf-8")
        file.chmod(0o600)
        return file

    def action(self, field_id, control, operation, value):
        return {"step_id": f"step-{field_id}", "field_id": field_id, "selector": "",
                "control": control, "operation": operation, "value": value,
                "expected_sha256": "0" * 64}

    def grant_for(self, package, ttl_seconds=900, at=None):
        """A grant the way `fill_core` issues one, without needing a whole fill session."""
        import worker_protocol as protocol_module  # the same module the worker verifies with
        moment = at or self.now
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        secret = "s" * 64
        grant = {"grant_id": "grant-1", "session_id": "session-1", "page_id": "page-1",
                 "package_sha256": digest, "surface_origin": "http://127.0.0.1:0",
                 "renderer_version": "1.0.0", "issued_at": moment.isoformat(),
                 "expires_at": (moment + timedelta(seconds=ttl_seconds)).isoformat()}
        grant["signature"] = PROTOCOL.grant_signature(secret, digest, grant)
        path = package.with_suffix(".grant.json")
        path.write_text(json.dumps({"grant": grant, "secret": secret}), encoding="utf-8")
        path.chmod(0o600)
        return path

    def execute(self, package, name="result.json", grant=None, oracle=None, **updates):
        output = self.root / name
        WORKER.run(package, output, grant or self.grant_for(package), headed=False,
                   oracle_url=oracle, at=self.now, **updates)
        return json.loads(output.read_text(encoding="utf-8")), output

    def final_actions(self, server):
        return json.load(urllib.request.urlopen(f"{server.origin}/__state", timeout=5))[
            "final_action_activations"]

    def test_it_fills_selects_and_checks_and_never_activates_the_final_action(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            self.assertEqual(self.final_actions(server), 0)
            package = self.package_for(server, "/lever/0", [
                self.action("lever-0-1", "text", "fill", "Verified Candidate"),
                self.action("lever-0-4", "select", "select",
                            POLICY.replay_option_value(
                                "Phoenix, Arizona, United States", server.nonce)),
            ])
            envelope, output = self.execute(
                package, oracle=f"{server.origin}/__state")
            self.assertEqual([entry["outcome"] for entry in envelope["results"]],
                             ["verified", "verified"])
            # A zero because the target was counting, not because the field was hardcoded.
            self.assertEqual(envelope["final_action_activations"], 0)
            # The oracle that can actually fail: the server's own counter.
            self.assertEqual(self.final_actions(server), 0)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)

    def test_the_result_carries_hashes_and_nothing_else(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package = self.package_for(server, "/lever/0", [
                self.action("lever-0-1", "text", "fill", "Verified Candidate")])
            envelope, output = self.execute(package, oracle=f"{server.origin}/__state")
            raw = output.read_text(encoding="utf-8")
            for leak in ("Verified Candidate", server.nonce, str(self.root)):
                self.assertNotIn(leak, raw)
            PROTOCOL.validate_result(
                envelope, expected_session="session-1", expected_page="page-1",
                expected_package_sha256=envelope["package_sha256"],
                expected_action_ids=["step-lever-0-1"], at=self.now)

    def test_hidden_disabled_duplicated_and_missing_controls_fail_closed(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package = self.package_for(server, "/lever/0", [
                self.action("hidden_control", "text", "fill", "x"),
                self.action("disabled_control", "text", "fill", "x"),
                self.action("duplicate_control", "text", "fill", "x"),
                self.action("does_not_exist", "text", "fill", "x"),
                self.action("lever-0-1", "select", "fill", "x"),
            ])
            envelope, _ = self.execute(package)
            self.assertEqual(
                [entry["error_code"] for entry in envelope["results"]],
                ["control_hidden", "control_disabled", "selector_ambiguous",
                 "selector_not_found", "control_type_mismatch"])
            self.assertTrue(all(entry["outcome"] == "not_actionable"
                                for entry in envelope["results"]))
            self.assertEqual(self.final_actions(server), 0)

    def test_a_pdf_uploads_and_anything_else_is_refused(self):
        good = self.root / "resume.pdf"
        good.write_bytes(synthetic_pdf(["Verified Candidate"]))
        renamed = self.root / "renamed.pdf"
        renamed.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package = self.package_for(server, "/lever/0", [
                self.action("lever-0-0", "file", "upload", str(good)),
                self.action("identity_document", "file", "upload", str(renamed)),
            ])
            envelope, _ = self.execute(package)
            self.assertEqual(envelope["results"][0]["outcome"], "verified")
            self.assertEqual(envelope["results"][0]["observed_sha256"],
                             hashlib.sha256(good.read_bytes()).hexdigest())
            self.assertEqual(envelope["results"][1]["error_code"], "upload_type_not_pdf")
            self.assertEqual(self.final_actions(server), 0)

    def test_a_page_that_is_not_the_attested_one_is_never_touched(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            attestation = POLICY.surface_attestation(
                self.db, f"{server.origin}/lever/0", self.now)
            tampered = {**attestation, "page_sha256": "c" * 64}
            package = self.package_for(
                server, "/lever/0",
                [self.action("lever-0-1", "text", "fill", "Verified Candidate")],
                surface=tampered)
            envelope, _ = self.execute(package, oracle=f"{server.origin}/__state")
            self.assertEqual(envelope["results"][0]["outcome"], "not_attempted")
            self.assertEqual(envelope["results"][0]["error_code"],
                             "control_changed_since_observation")
            self.assertEqual(self.final_actions(server), 0)

    def test_an_unregistered_loopback_server_is_never_attested(self):
        # A second server Jobloom did not register: same host, same markup, no surface row.
        with ReplayServer(connection=self.db, clock=lambda: self.now) as registered:
            with ReplayServer() as rogue:
                self.assertIsNone(POLICY.surface_attestation(
                    self.db, f"{rogue.origin}/lever/0", self.now))
                self.assertIsNotNone(POLICY.surface_attestation(
                    self.db, f"{registered.origin}/lever/0", self.now))

    def test_an_expired_or_revoked_surface_yields_no_attestation(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now,
                          lifetime_hours=1) as server:
            self.assertIsNotNone(POLICY.surface_attestation(
                self.db, f"{server.origin}/lever/0", self.now))
            self.assertIsNone(POLICY.surface_attestation(
                self.db, f"{server.origin}/lever/0", self.now + timedelta(hours=2)))
            POLICY.revoke_replay_surface(self.db, server.surface_id, self.now)
            self.assertIsNone(POLICY.surface_attestation(
                self.db, f"{server.origin}/lever/0", self.now))

    def test_the_worker_cannot_reach_a_second_page_or_another_origin(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            attestation = POLICY.surface_attestation(
                self.db, f"{server.origin}/lever/0", self.now)
            package = self.package_for(
                server, "/lever/0",
                [self.action("lever-0-1", "text", "fill", "x")], surface=attestation)
            with self.assertRaises(WORKER.WorkerRefusal) as caught:
                WORKER.run(package, self.root / "elsewhere.json", self.grant_for(package),
                           headed=False, page_url=f"{server.origin}/lever/1", at=self.now)
            self.assertEqual(str(caught.exception), "page_outside_attested_surface")

    def test_the_scoped_guard_blocks_a_submit_the_page_itself_attempts(self):
        """The worker has no click operation, so this is depth: a page that submits itself.

        Run both ways on purpose. Without the guard the counter reaches one, which is what
        makes the zero elsewhere in this file worth reading — an oracle that cannot fail is
        not an oracle. With the guard installed the same click leaves it where it was.
        """
        from playwright.sync_api import sync_playwright
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    unguarded = browser.new_context().new_page()
                    unguarded.goto(f"{server.origin}/lever/1", wait_until="domcontentloaded")
                    unguarded.click("#final-action")
                    unguarded.wait_for_timeout(300)
                    self.assertEqual(self.final_actions(server), 1)

                    guarded = browser.new_context().new_page()
                    with WORKER.PageGuards(guarded, server.origin,
                                           f"{server.origin}/lever/1") as guards:
                        guarded.goto(f"{server.origin}/lever/1",
                                     wait_until="domcontentloaded")
                        guarded.click("#final-action")
                        guarded.wait_for_timeout(300)
                        self.assertEqual(guards.violations, ["submit_attempted"])
                    self.assertEqual(self.final_actions(server), 1)
                    # Aborting the submit leaves the tab on Chromium's error page, which is
                    # the visible shape of the refusal.
                    self.assertTrue(guarded.url.startswith("chrome-error://"))
                    # The guard is scoped to the run: ordinary browsing works again, because
                    # a guard left installed would change how the user's own browser behaves.
                    guarded.goto(f"{server.origin}/lever/0", wait_until="domcontentloaded")
                    self.assertEqual(guarded.url, f"{server.origin}/lever/0")
                finally:
                    browser.close()

    def test_an_upload_post_is_allowed_while_a_submit_post_is_not(self):
        # "Block every POST" would refuse uploads and call itself submit protection.
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            upload_url = f"{server.origin}/__upload"
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_context().new_page()
                    guards = WORKER.PageGuards(
                        page, server.origin, f"{server.origin}/lever/0",
                        allowed_upload_urls=(upload_url,))
                    seen = []

                    class Request:
                        def __init__(self, url, method, resource_type="xhr"):
                            self.url, self.method = url, method
                            self.resource_type = resource_type

                    class Route:
                        def continue_(self):
                            seen.append("continued")

                        def abort(self):
                            seen.append("aborted")

                    guards._route(Route(), Request(upload_url, "POST"))
                    guards._route(Route(), Request(f"{server.origin}/__final_action", "POST"))
                    self.assertEqual(seen, ["continued", "aborted"])
                    self.assertEqual(guards.violations, ["submit_attempted"])
                finally:
                    browser.close()

    # ---- the five review findings ---------------------------------------

    def test_a_same_origin_get_submit_is_stopped_and_never_reported_as_zero(self):
        """The hole: `same origin and GET` was allowed unconditionally.

        A form with `method="GET"` submits by navigating, so filling one field reached the
        application endpoint, the page left, and the run still reported no final action.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package = self.package_for(server, "/hazard/get-submit", [
                self.action("h-1", "text", "fill", "Verified Candidate")])
            envelope, _ = self.execute(package, oracle=f"{server.origin}/__state")
            self.assertEqual(envelope["results"][0]["outcome"], "refused")
            # Named for what the guard saw, not for what the page said it was: a GET form
            # submit is a document navigation, and calling it a refused final action would
            # claim knowledge of the form's intent that only the page could supply.
            self.assertEqual(envelope["results"][0]["error_code"], "navigation_attempted")
            # The target's own counter is the proof, and it did not move.
            self.assertEqual(self.final_actions(server), 0)
            self.assertEqual(envelope["final_action_activations"], 0)

    def test_a_same_origin_location_change_is_stopped(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package = self.package_for(server, "/hazard/location", [
                self.action("h-1", "text", "fill", "Verified Candidate")])
            envelope, _ = self.execute(package, oracle=f"{server.origin}/__state")
            self.assertEqual(envelope["results"][0]["outcome"], "refused")
            self.assertEqual(envelope["results"][0]["error_code"], "navigation_attempted")
            self.assertEqual(self.final_actions(server), 0)

    def test_the_first_violation_stops_the_page_and_the_rest_is_not_attempted(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package = self.package_for(server, "/hazard/get-submit", [
                self.action("h-1", "text", "fill", "first"),
                self.action("h-1", "text", "fill", "second"),
                self.action("h-1", "text", "fill", "third")])
            envelope, _ = self.execute(package, oracle=f"{server.origin}/__state")
            outcomes = [entry["outcome"] for entry in envelope["results"]]
            self.assertEqual(outcomes, ["refused", "not_attempted", "not_attempted"])
            # Not all three disguised as "refused": which one caused it stays legible.
            self.assertEqual(self.final_actions(server), 0)

    def test_a_forged_package_from_a_rogue_local_server_has_no_grant(self):
        """0600 and loopback are not provenance: any local process can produce both."""
        with ReplayServer() as rogue:
            forged = self.root / "forged.json"
            forged.write_text(json.dumps({
                "schema_version": "0.1.0", "mode": "fill_only", "session_id": "session-1",
                "application_id": "app-1", "page_id": "page-1",
                "page_url": f"{rogue.origin}/lever/0",
                "surface": {"origin": rogue.origin, "renderer_version": "1.0.0",
                            "page_path": "/lever/0",
                            "page_sha256": rogue.page_digests()["/lever/0"],
                            "expires_at": (self.now + timedelta(hours=1)).isoformat()},
                "actions": [self.action("lever-0-1", "text", "fill", "x")],
                "stop_before_submit": True, "submission_action": None,
            }), encoding="utf-8")
            forged.chmod(0o600)
            # Every check the previous version made still passes on this package.
            self.assertEqual(WORKER.load_package(forged)["page_id"], "page-1")
            # A grant issued for different bytes does not carry it.
            other = self.root / "other.json"
            other.write_text("{}", encoding="utf-8")
            other.chmod(0o600)
            with self.assertRaises(WORKER.WorkerRefusal) as caught:
                WORKER.run(forged, self.root / "forged-result.json",
                           self.grant_for(other), headed=False, at=self.now)
            self.assertEqual(str(caught.exception), "grant_package_mismatch")

    def test_an_expired_grant_or_surface_refuses_before_the_browser_starts(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package = self.package_for(server, "/lever/0", [
                self.action("lever-0-1", "text", "fill", "x")])
            with self.assertRaises(WORKER.WorkerRefusal) as caught:
                WORKER.run(package, self.root / "late.json",
                           self.grant_for(package, ttl_seconds=60), headed=False,
                           at=self.now + timedelta(minutes=5))
            self.assertEqual(str(caught.exception), "grant_expired")
            # And a surface that lapsed after export, with the same bytes still served.
            stale = self.package_for(
                server, "/lever/0", [self.action("lever-0-1", "text", "fill", "x")],
                surface={**POLICY.surface_attestation(
                    self.db, f"{server.origin}/lever/0", self.now),
                    "expires_at": (self.now - timedelta(minutes=1)).isoformat()})
            with self.assertRaises(WORKER.WorkerRefusal) as caught:
                WORKER.run(stale, self.root / "stale.json", self.grant_for(stale),
                           headed=False, at=self.now)
            self.assertEqual(str(caught.exception), "surface_expired")
            self.assertEqual(self.final_actions(server), 0)

    def test_a_revoked_grant_refuses_even_though_the_page_is_unchanged(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package = self.package_for(server, "/lever/0", [
                self.action("lever-0-1", "text", "fill", "x")])
            grant = self.grant_for(package)
            grant.with_suffix(grant.suffix + ".revoked").write_text("", encoding="utf-8")
            with self.assertRaises(WORKER.WorkerRefusal) as caught:
                WORKER.run(package, self.root / "revoked.json", grant, headed=False,
                           at=self.now)
            self.assertEqual(str(caught.exception), "grant_revoked")

    def test_an_unobservable_target_reports_no_count_rather_than_zero(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package = self.package_for(server, "/lever/0", [
                self.action("lever-0-1", "text", "fill", "Verified Candidate")])
            envelope, _ = self.execute(package)  # no oracle supplied
            self.assertIsNone(envelope["final_action_activations"])
            # And `fill_core` cannot import an unprovable run as a safe one.
            with self.assertRaises(PROTOCOL.ProtocolError) as caught:
                PROTOCOL.validate_result(
                    envelope, expected_session="session-1", expected_page="page-1",
                    expected_package_sha256=envelope["package_sha256"],
                    expected_action_ids=["step-lever-0-1"], at=self.now)
            self.assertEqual(str(caught.exception), "final_action_count_unproven")

    def test_a_consumed_package_cannot_be_replayed(self):
        with ReplayServer(connection=self.db, clock=lambda: self.now) as server:
            package = self.package_for(server, "/lever/0", [
                self.action("lever-0-1", "text", "fill", "Verified Candidate")])
            self.execute(package, "first.json")
            with self.assertRaises(WORKER.WorkerRefusal) as caught:
                WORKER.run(package, self.root / "second.json", self.grant_for(package),
                           headed=False, at=self.now)
            self.assertEqual(str(caught.exception), "package_already_consumed")
            self.assertEqual(self.final_actions(server), 0)


if __name__ == "__main__":
    unittest.main()
