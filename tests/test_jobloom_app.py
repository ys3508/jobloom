"""The window a person actually uses, driven the way the page drives it.

Over HTTP rather than by calling the functions, because what is under test is the surface: the
token, the origin check, what crosses the boundary, and whether the wizard's sequence of calls
ends with a registered profile. The rules themselves are `candidate_profile`'s and are tested
there; if this file could make one of them pass differently, that would be the bug.

Every value here is visibly synthetic and every path is a temporary directory.
"""

import contextlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "jobloom" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(f"app_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


APP = load("jobloom_app")
PROFILE = load("candidate_profile")
CANDIDATES = load("candidate_core")
RESUMES = load("resume_core")
APPLICATIONS = load("application_core")
PRE_SUBMIT = load("pre_submit_core")
ANSWERS = load("answer_library")

AT = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
COMPOSITE = ("probe@example.invalid ǁ 555-0100 ǁ "
             "LinkedIn: https://example.invalid/in/probe")
PIECES = ("probe@example.invalid", "555-0100", "example.invalid/in/probe")
NINE = ("contact.email", "contact.first_name", "contact.full_name", "contact.last_name",
        "contact.location", "contact.location_city", "contact.phone", "contact.phone_country",
        "profile.linkedin")
TYPED = {
    "contact.first_name": "Probe",
    "contact.last_name": "Example",
    "contact.full_name": "Probe Q. Example",
    "contact.phone_country": "+1",
    "contact.location_city": "Testville",
    "contact.location": "Testville, Nowhere",
}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        self.store = self.root / "candidates"
        self.db_path = self.root / "app.db"
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        for module in (RESUMES, APPLICATIONS, PRE_SUBMIT, ANSWERS, PROFILE):
            module.initialize(self.db)
        self.base = self.register_snapshot()
        self.bind_a_resume(self.base)
        self.db.commit()
        self.db.close()

        with contextlib.redirect_stdout(io.StringIO()):
            self.server = APP.serve(self.db_path, self.private, self.store,
                                    port=0, open_browser=False)
        self.origin = APP.Handler.origin
        self.token = APP.Handler.token
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        # LIFO, so this pair runs as shutdown() then server_close(): stop serving, then let
        # go of the socket. Closing first leaves the serving thread on a dead listener.
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    # ---- fixtures --------------------------------------------------------------

    def register_snapshot(self, extra=()):
        facts = [{"id": "fact-0002", "type": "contact", "value": COMPOSITE,
                  "status": "confirmed", "locked": False, "evidence_strength": "direct"}]
        facts.extend(extra)
        candidate = {
            "schema_version": "0.2.0", "profile_id": "probe",
            "work_authorization": {"country": "US", "authorized_now": True,
                                   "sponsorship_now": False, "sponsorship_future": False,
                                   "employer_action_required": False, "confirmed": True},
            "search": {}, "facts": facts}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        path = self.root / f"candidate-{candidate['content_sha256'][:12]}.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        CANDIDATES.register_snapshot(connection, self.store, path, "user", AT)
        connection.close()
        return candidate["content_sha256"]

    def bind_a_resume(self, snapshot_sha256):
        self.db.execute(
            "INSERT INTO resume_versions (version_id, kind, direction, status, snapshot_path, "
            "file_sha256, file_size, file_format, candidate_profile_sha256, created_at, "
            "source_mode) VALUES ('resume-a', 'direction', 'probe', 'approved', ?, 'f', 1, "
            "'pdf', ?, ?, 'user_provided')",
            (str(self.root / "resume-a.pdf"), snapshot_sha256, AT.isoformat()))
        self.db.execute(
            "INSERT INTO material_locks (lock_id, application_id, resume_version_id, "
            "resume_file_sha256, locked_at) VALUES ('lock-1', 'app-1', 'resume-a', 'f', ?)",
            (AT.isoformat(),))

    # ---- talking to it ---------------------------------------------------------

    def call(self, path, body=None, token=None, origin=None, raw=False):
        request = urllib.request.Request(self.origin + path)
        request.add_header("X-Jobloom-Token", self.token if token is None else token)
        if origin:
            request.add_header("Origin", origin)
        if body is not None:
            request.data = json.dumps(body).encode("utf-8")
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read()
        return payload if raw else json.loads(payload)

    def refused(self, path, body=None, **kwargs):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call(path, body, **kwargs)
        return caught.exception.code, json.loads(caught.exception.read())

    def walk_the_wizard(self, autofill=NINE, confirm=NINE):
        fields = self.call("/api/round", {})["fields"]
        answers = {}
        for field in fields:
            canonical_id = field["canonical_id"]
            answers[canonical_id] = {
                "value": field["value"] or TYPED.get(canonical_id, ""),
                "confirmed": canonical_id in confirm,
                "autofill": canonical_id in confirm and canonical_id in autofill,
            }
        self.call("/api/answers", {"answers": answers})
        return self.call("/api/draft", {})

    # ---- the surface -----------------------------------------------------------

    def test_the_page_is_served_without_a_token_and_forbids_outside_resources(self):
        """The HTML holds nothing. The token guards the data, not the markup."""
        request = urllib.request.Request(self.origin + "/")
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
            policy = response.headers["Content-Security-Policy"]
        self.assertIn("<title>Jobloom</title>", body)
        self.assertIn("default-src 'none'", policy)
        self.assertIn("connect-src 'self'", policy)
        for piece in PIECES:
            self.assertNotIn(piece, body)

    def test_every_data_route_needs_the_token(self):
        for path, body in (("/api/state", None), ("/api/round", {}), ("/api/draft", {}),
                           ("/api/answers", {"answers": {}}), ("/api/register", {}),
                           ("/api/check", {"canonical_id": "contact.email", "value": "x"})):
            status, payload = self.refused(path, body, token="wrong")
            self.assertEqual((status, payload["error"]), (403, "bad_token"), path)

    def test_a_request_from_another_origin_is_refused(self):
        """A site the user has open must not reach this, even knowing the port."""
        status, payload = self.refused("/api/state", None, origin="https://example.invalid")
        self.assertEqual((status, payload["error"]), (403, "bad_token"))
        self.assertEqual(self.call("/api/state", origin=self.origin)["round"], "required-v1")

    def test_an_unknown_endpoint_is_a_404(self):
        self.assertEqual(self.refused("/api/everything", {})[0], 404)

    # ---- the wizard ------------------------------------------------------------

    def test_state_says_what_is_missing_before_anything_is_asked(self):
        state = self.call("/api/state")
        self.assertTrue(state["has_profile"])
        self.assertEqual(state["fields_in_round"], sorted(NINE))
        self.assertEqual(state["resolvable"], [])
        self.assertEqual(state["unresolved"]["contact.email"], "profile_fact_missing")
        self.assertIsNone(state["open_round"])

    def test_a_round_is_opened_once_and_then_resumed(self):
        """Reopening the window is not a decision to start over."""
        first = self.call("/api/round", {})
        self.assertFalse(first["resumed"])
        self.assertEqual(len(first["fields"]), 9)
        self.assertEqual(sum(1 for f in first["fields"] if f["proposed"]), 3)
        second = self.call("/api/round", {})
        self.assertTrue(second["resumed"])
        connection = sqlite3.connect(str(self.db_path))
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM profile_proposals").fetchone()[0], 1)
        connection.close()

    def test_checking_a_value_complains_and_offers_but_never_rewrites(self):
        bad = self.call("/api/check", {"canonical_id": "contact.email", "value": "nope"})
        self.assertEqual(bad["value"], "nope")
        self.assertIn("one local part", bad["complaint"])
        offered = self.call("/api/check", {"canonical_id": "contact.phone_country",
                                           "value": " 1 "})
        self.assertEqual(offered["value"], "1")
        self.assertEqual(offered["suggestion"], "+1")
        self.assertIsNone(offered["complaint"])

    def test_a_field_outside_the_profile_cannot_be_checked(self):
        self.assertEqual(self.refused(
            "/api/check", {"canonical_id": "eeo.race", "value": "x"})[1]["error"],
            "unknown_field")

    def test_the_wizard_reaches_a_registered_profile(self):
        draft = self.walk_the_wizard()
        self.assertFalse(draft["registered"])
        self.assertEqual(self.call("/api/state")["resolvable"], [])
        done = self.call("/api/register", {"draft_sha256": draft["draft_sha256"]})
        self.assertEqual(done["observed_impact"]["meanings_now_resolvable"], sorted(NINE))
        self.assertEqual(self.call("/api/state")["resolvable"], sorted(NINE))

    def test_the_impact_is_shown_before_anything_is_activated(self):
        draft = self.walk_the_wizard()
        impact = draft["impact_if_registered"]
        self.assertEqual(impact["material_locks_invalidated"], 1)
        self.assertEqual(impact["resume_versions_needing_rebinding"], ["resume-a"])
        self.assertEqual(impact["answers_going_stale"], [])
        connection = sqlite3.connect(str(self.db_path))
        self.assertEqual(connection.execute(
            "SELECT COUNT(*) FROM material_locks WHERE invalidated_at IS NOT NULL"
        ).fetchone()[0], 0)
        connection.close()

    def test_the_second_gate_is_carried_through_to_the_facts(self):
        draft = self.walk_the_wizard(autofill=("contact.email",))
        self.assertEqual(draft["facts_locked"], ["contact.email"])
        self.assertEqual(draft["facts_recorded_only"],
                         sorted(set(NINE) - {"contact.email"}))
        self.call("/api/register", {"draft_sha256": draft["draft_sha256"]})
        self.assertEqual(self.call("/api/state")["resolvable"], ["contact.email"])

    def test_registering_needs_the_draft_that_was_shown(self):
        self.walk_the_wizard()
        self.assertEqual(self.refused("/api/register", {})[1]["error"], "draft_required")
        status, payload = self.refused("/api/register", {"draft_sha256": "0" * 64})
        self.assertEqual((status, payload["error"]), (409, "refused"))
        self.assertIn("no such draft", payload["detail"])

    def test_a_refusal_reaches_the_page_as_words_and_never_as_a_value(self):
        fields = self.call("/api/round", {})["fields"]
        answers = {f["canonical_id"]: {"value": f["value"] or TYPED.get(f["canonical_id"], ""),
                                       "confirmed": False, "autofill": True} for f in fields}
        status, payload = self.refused("/api/answers", {"answers": answers})
        self.assertEqual(status, 409)
        self.assertIn("cannot be authorised", payload["detail"])
        for piece in PIECES:
            self.assertNotIn(piece, json.dumps(payload))

    def test_the_draft_the_page_receives_carries_no_value(self):
        rendered = json.dumps(self.walk_the_wizard(), ensure_ascii=False)
        for piece in PIECES + tuple(TYPED.values()):
            self.assertNotIn(piece, rendered)

    def test_a_worksheet_left_from_a_previous_profile_is_set_aside(self):
        """A round proposed against a profile that has since moved cannot be confirmed."""
        self.call("/api/round", {})
        self.register_snapshot(extra=[{"id": "fact-0009", "type": "skill", "value": "R",
                                       "status": "confirmed", "locked": False,
                                       "evidence_strength": "direct"}])
        self.assertIsNone(self.call("/api/state")["open_round"])
        fresh = self.call("/api/round", {})
        self.assertFalse(fresh["resumed"])
        self.assertEqual(len(list(self.private.glob("*.superseded-*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
