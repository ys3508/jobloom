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
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "jobloom" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
COVERS = load("cover_letter_core")
MIGRATION = load("resume_migration")

from tests.pdf_fixture import synthetic_pdf  # noqa: E402

AT = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
COMPOSITE = ("probe@example.invalid ǁ 555-0100 ǁ "
             "LinkedIn: https://example.invalid/in/probe")
PIECES = ("probe@example.invalid", "555-0100", "example.invalid/in/probe")
ROUND = "onboarding-v1"
NINE = tuple(sorted(PROFILE.PROFILE_ROUNDS[ROUND]))
TYPED = {
    "contact.first_name": "Probe",
    "contact.last_name": "Example",
    "contact.full_name": "Probe Q. Example",
    "contact.preferred_name": "Probe",
    "contact.phone_country": "+1",
    "contact.phone_extension": "101",
    "contact.location_city": "Testville",
    "contact.location": "Testville, Nowhere",
    "contact.location_region": "Nowhere",
    "contact.address.line1": "1 Probe Lane",
    "contact.address.line2": "Unit 2",
    "contact.postal_code": "00000",
    "contact.country": "United States of America",
    "profile.github": "https://example.invalid/probe",
    "profile.portfolio": "https://example.invalid/portfolio",
    "profile.website": "https://example.invalid/site",
    "employment.current_company": "Probe Corp",
}


class AppFixture(unittest.TestCase):
    """The window, its service and a profile waiting to be filled in. No tests of its own."""

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
        for module in (RESUMES, APPLICATIONS, PRE_SUBMIT, ANSWERS, COVERS,
                       PROFILE, MIGRATION):
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


class AppTests(AppFixture):
    """The surface itself: what it serves, what it refuses, and what the wizard reaches."""

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
        self.assertEqual(self.call("/api/state", origin=self.origin)["round"], ROUND)

    def test_an_unknown_endpoint_is_a_404(self):
        self.assertEqual(self.refused("/api/everything", {})[0], 404)

    # ---- the wizard ------------------------------------------------------------

    def test_state_says_what_is_missing_before_anything_is_asked(self):
        state = self.call("/api/state")
        self.assertTrue(state["has_profile"])
        self.assertEqual(state["fields_in_round"], sorted(NINE))
        # The grouping comes from the service, so the page cannot decide it.
        self.assertEqual([group["name"] for group in state["screens"]],
                         ["name", "address", "reach", "links"])
        self.assertEqual(sorted(f for g in state["screens"] for f in g["fields"]),
                         sorted(NINE))
        self.assertEqual(state["resolvable"], [])
        self.assertEqual(state["unresolved"]["contact.email"], "profile_fact_missing")
        self.assertIsNone(state["open_round"])

    def test_a_round_is_opened_once_and_then_resumed(self):
        """Reopening the window is not a decision to start over."""
        first = self.call("/api/round", {})
        self.assertFalse(first["resumed"])
        self.assertEqual(len(first["fields"]), len(NINE))
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


class MigrationSurfaceTests(AppFixture):
    """Carrying a stranded resume, driven the way the window drives it.

    The page shows and asks; it cannot say a version is migratable, and it cannot name a file.
    These hold that line: no path crosses the boundary in either direction, opening the
    document is not approving it, and nothing advances on a GET.
    """

    def setUp(self):
        super().setUp()
        self.pdf = self.root / "resume-a.pdf"
        self.pdf.write_bytes(synthetic_pdf(["Probe analysis, as approved"]))
        self.approve_a_resume()
        draft = self.walk_the_wizard()
        self.call("/api/register", {"draft_sha256": draft["draft_sha256"]})

    def approve_a_resume(self):
        """A real approved user-provided resume, so the successor path has something to carry.

        The fixture in `AppTests` inserts a row; this one goes through `resume_core`, because
        what is under test here is a migration of a genuinely approved version.
        """
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("DELETE FROM material_locks")
        connection.execute("DELETE FROM resume_versions")
        connection.commit()
        RESUMES.register_version(connection, self.root / "resumes", self.pdf, "resume-a",
                                 "direction", "probe-direction", actor="user", at=AT,
                                 source_mode="user_provided")
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-1", "claim_text": "Probe analysis", "fact_ids": ["fact-0002"],
            "evidence_strength": "direct", "exact_locked_value_preserved": False,
        }]}), encoding="utf-8")
        active = connection.execute(
            "SELECT snapshot_path FROM candidate_snapshots WHERE status='active'").fetchone()[0]
        RESUMES.approve_version(connection, "resume-a", Path(active), manifest, "user", AT)
        APPLICATIONS.ingest_job(connection, {
            "job_id": "job-1", "canonical_url": "https://example.invalid/jobs/1",
            "employer": "Probe Corp", "title": "Analyst", "location": "Testville",
            "country": "US", "employment_type": "full_time", "status": "open"}, at=AT)
        APPLICATIONS.create_application(connection, "app-1", "job-1", "precision",
                                        "approved_queue", AT)
        for state, reason in (("pending_analysis", "analysis"),
                              ("precision_recommended", "match"), ("approved", "approved"),
                              ("materials_in_progress", "materials")):
            APPLICATIONS.transition(connection, "app-1", state,
                                    "user" if state == "approved" else "system", reason, at=AT)
        RESUMES.bind_version(connection, "app-1", "resume-a", at=AT)
        RESUMES.lock_materials(connection, "app-1", lock_id="lock-1", at=AT)
        APPLICATIONS.transition(connection, "app-1", "ready_to_fill", "system", "ready", at=AT)
        connection.commit()
        connection.close()

    def prepared(self):
        return self.call("/api/resume-migrations/prepare",
                         {"predecessor_version_id": "resume-a"})

    def app_state(self):
        connection = sqlite3.connect(str(self.db_path))
        try:
            return connection.execute(
                "SELECT state FROM applications WHERE application_id='app-1'").fetchone()[0]
        finally:
            connection.close()

    # ---- listing ---------------------------------------------------------------

    def test_the_window_creates_every_table_it_can_reach(self):
        """Registering used to be followed by a bare failure code.

        `serve` initialised the profile's tables and stopped, so the screen after registering
        was the first thing to touch `resume_migrations` and found no such table.
        """
        connection = sqlite3.connect(str(self.db_path))
        try:
            for table in ("resume_migrations", "profile_proposals", "profile_drafts"):
                self.assertIsNotNone(connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone(), table)
        finally:
            connection.close()

    def test_the_listing_names_the_stranded_resume_and_migrates_none(self):
        listed = self.call("/api/resume-migrations")
        self.assertEqual([row["version_id"] for row in listed["stranded"]], ["resume-a"])
        self.assertTrue(listed["stranded"][0]["migratable"])
        self.assertTrue(listed["stranded"][0]["lock_lost"])
        self.assertEqual(listed["stranded"][0]["application_id"], "app-1")
        self.assertEqual(listed["carryable"], 1)
        self.assertEqual(self.app_state(), "ready_to_fill")

    def test_no_local_path_crosses_the_boundary(self):
        rendered = json.dumps([self.call("/api/resume-migrations"), self.prepared()],
                              ensure_ascii=False)
        for shape in (str(self.root), "/private/", ".pdf", "manifest.json"):
            self.assertNotIn(shape, rendered)

    # ---- preparing --------------------------------------------------------------

    def test_preparing_returns_the_claims_to_read_and_no_manifest_path(self):
        result = self.prepared()
        self.assertEqual(result["status"], "prepared")
        self.assertTrue(result["same_bytes"])
        self.assertFalse(result["approved"])
        self.assertNotIn("claims_manifest_path", result)
        self.assertEqual([claim["claim_text"] for claim in result["claims"]],
                         ["Probe analysis"])

    def test_preparing_needs_a_predecessor(self):
        self.assertEqual(self.refused("/api/resume-migrations/prepare", {})[1]["error"],
                         "predecessor_required")

    # ---- looking at the document -------------------------------------------------

    def test_the_document_under_review_can_be_read_and_matches_the_original(self):
        prepared = self.prepared()
        request = urllib.request.Request(
            f"{self.origin}/api/resume-file?version_id={prepared['successor_version_id']}")
        request.add_header("X-Jobloom-Token", self.token)
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
            headers = dict(response.headers)
        self.assertEqual(body, self.pdf.read_bytes())
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Content-Disposition"], "inline")

    def test_reading_the_document_advances_nothing(self):
        """A GET that approved something would make opening a file into approving it."""
        prepared = self.prepared()
        request = urllib.request.Request(
            f"{self.origin}/api/resume-file?version_id={prepared['successor_version_id']}")
        request.add_header("X-Jobloom-Token", self.token)
        urllib.request.urlopen(request, timeout=10).read()
        connection = sqlite3.connect(str(self.db_path))
        self.assertEqual(connection.execute(
            "SELECT status FROM resume_migrations").fetchone()[0], "prepared")
        connection.close()

    def test_only_a_document_under_review_can_be_read(self):
        """Not a way to read the resume store, and not a way to name a file."""
        self.assertEqual(
            self.refused("/api/resume-file?version_id=resume-a")[1]["error"],
            "version_not_under_review")
        self.prepared()
        self.assertEqual(
            self.refused("/api/resume-file?version_id=nothing-like-it")[1]["error"],
            "version_not_under_review")

    def test_the_successor_is_filed_with_the_resumes_not_with_the_profiles(self):
        """Two stores, and the carry writes to the resume one.

        The window was handed a single store, and the one it was handed is the candidate
        snapshot store, so a carried resume landed inside the profile store — working, and
        in a directory nothing else looks in for a resume.
        """
        prepared = self.prepared()
        connection = sqlite3.connect(str(self.db_path))
        stored = Path(connection.execute(
            "SELECT snapshot_path FROM resume_versions WHERE version_id=?",
            (prepared["successor_version_id"],)).fetchone()[0])
        connection.close()
        self.assertEqual(stored.parent.parent, (self.root / "resumes").resolve())
        self.assertNotIn(self.store.resolve(), stored.parents)

    def test_the_document_must_still_be_the_file_that_was_registered(self):
        prepared = self.prepared()
        connection = sqlite3.connect(str(self.db_path))
        stored = Path(connection.execute(
            "SELECT snapshot_path FROM resume_versions WHERE version_id=?",
            (prepared["successor_version_id"],)).fetchone()[0])
        connection.close()
        stored.chmod(0o600)
        stored.write_bytes(synthetic_pdf(["Swapped underneath"]))
        self.assertEqual(
            self.refused(f"/api/resume-file?version_id={prepared['successor_version_id']}")[1]
            ["error"], "version_changed")

    # ---- the two approvals --------------------------------------------------------

    def test_approving_requires_saying_the_materials_were_reviewed(self):
        prepared = self.prepared()
        status, payload = self.refused("/api/resume-migrations/approve", {
            "successor_version_id": prepared["successor_version_id"]})
        self.assertEqual((status, payload["error"]), (409, "materials_not_reviewed"))
        connection = sqlite3.connect(str(self.db_path))
        self.assertEqual(connection.execute(
            "SELECT status FROM resume_versions WHERE version_id=?",
            (prepared["successor_version_id"],)).fetchone()[0], "draft")
        connection.close()

    def test_approving_is_not_binding(self):
        """Two presses, because approving a document and using it are two decisions."""
        prepared = self.prepared()
        approved = self.call("/api/resume-migrations/approve", {
            "successor_version_id": prepared["successor_version_id"],
            "materials_reviewed": True})
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(self.app_state(), "ready_to_fill")
        connection = sqlite3.connect(str(self.db_path))
        self.assertIsNone(connection.execute(
            "SELECT lock_id FROM material_locks WHERE invalidated_at IS NULL").fetchone())
        connection.close()

    def test_the_page_cannot_name_the_candidate_document(self):
        """It is read from the active snapshot's row, so there is nowhere to put a path."""
        prepared = self.prepared()
        self.call("/api/resume-migrations/approve", {
            "successor_version_id": prepared["successor_version_id"],
            "materials_reviewed": True, "candidate_path": "/somewhere/else.json"})
        connection = sqlite3.connect(str(self.db_path))
        approved_against = connection.execute(
            "SELECT successor_snapshot_sha256 FROM resume_migrations").fetchone()[0]
        active = connection.execute(
            "SELECT content_sha256 FROM candidate_snapshots WHERE status='active'").fetchone()[0]
        connection.close()
        self.assertEqual(approved_against, active)

    # ---- binding -------------------------------------------------------------------

    def test_the_whole_carry_restores_the_application(self):
        prepared = self.prepared()
        self.call("/api/resume-migrations/approve", {
            "successor_version_id": prepared["successor_version_id"],
            "materials_reviewed": True})
        bound = self.call("/api/resume-migrations/bind", {
            "successor_version_id": prepared["successor_version_id"],
            "application_id": "app-1"})
        self.assertEqual(bound["status"], "bound")
        self.assertEqual(self.app_state(), "ready_to_fill")
        # The predecessor is still approved against a superseded snapshot, so it is still
        # listed — with nothing left to do about it, and pointing at what carried it.
        listed = self.call("/api/resume-migrations")
        self.assertEqual(listed["carryable"], 0)
        self.assertEqual(listed["stranded"][0]["carried_by"],
                         prepared["successor_version_id"])
        self.assertFalse(listed["stranded"][0]["migratable"])

    def test_binding_before_approval_is_refused_and_moves_nothing(self):
        prepared = self.prepared()
        status, payload = self.refused("/api/resume-migrations/bind", {
            "successor_version_id": prepared["successor_version_id"],
            "application_id": "app-1"})
        self.assertEqual(status, 409)
        self.assertIn("has not been approved", payload["detail"])
        self.assertEqual(self.app_state(), "ready_to_fill")

    def test_binding_names_both_the_successor_and_the_application(self):
        self.prepared()
        self.assertEqual(self.refused("/api/resume-migrations/bind", {})[1]["error"],
                         "successor_required")
        self.assertEqual(self.refused("/api/resume-migrations/bind",
                                      {"successor_version_id": "x"})[1]["error"],
                         "application_required")

    # ---- the surface itself ----------------------------------------------------------

    def test_every_migration_route_needs_the_token_and_this_origin(self):
        for path, body in (("/api/resume-migrations", None),
                           ("/api/resume-file?version_id=resume-a", None),
                           ("/api/resume-migrations/prepare", {"predecessor_version_id": "x"}),
                           ("/api/resume-migrations/approve", {"successor_version_id": "x"}),
                           ("/api/resume-migrations/bind", {"successor_version_id": "x"})):
            with self.subTest(path=path):
                self.assertEqual(self.refused(path, body, token="wrong")[1]["error"],
                                 "bad_token")
                self.assertEqual(
                    self.refused(path, body, origin="https://example.invalid")[1]["error"],
                    "bad_token")

    def test_a_cover_letter_left_behind_stops_the_carry_by_name(self):
        connection = sqlite3.connect(str(self.db_path))
        old = connection.execute(
            "SELECT content_sha256 FROM candidate_snapshots WHERE status='superseded'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO cover_letter_versions (version_id, kind, status, snapshot_path, "
            "file_sha256, file_size, file_format, candidate_profile_sha256, created_at) "
            "VALUES ('cover-1', 'direction', 'approved', ?, 'f', 1, 'pdf', ?, ?)",
            (str(self.root / "cover.pdf"), old, AT.isoformat()))
        connection.execute("UPDATE applications SET cover_letter_version_id='cover-1' "
                           "WHERE application_id='app-1'")
        connection.commit()
        connection.close()
        prepared = self.prepared()
        self.call("/api/resume-migrations/approve", {
            "successor_version_id": prepared["successor_version_id"],
            "materials_reviewed": True})
        status, payload = self.refused("/api/resume-migrations/bind", {
            "successor_version_id": prepared["successor_version_id"],
            "application_id": "app-1"})
        self.assertEqual(status, 409)
        self.assertIn("cover letter needs its own migration", payload["detail"])
        self.assertEqual(self.app_state(), "ready_to_fill")


if __name__ == "__main__":
    unittest.main()


# ---- the application-assist preflight -------------------------------------------

LOCKED_EMAIL = "preflight@example.invalid"
LIBRARY_QUESTION = "How did you hear about this opportunity?"
PROFILE_QUESTION = "Email Address"


class PreflightFixture(AppFixture):
    """A profile that is not the file beside the database, an application, and one answer.

    The setup is deliberately adversarial on the point that mattered: `<private_root>/
    candidate.json` is written with a *different* profile from the registered snapshot, so any
    path that reads the file instead of the row answers with the wrong one and these tests say
    so.
    """

    def setUp(self):
        super().setUp()
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        self.stale = self.write_stale_candidate_file()
        self.add_opening()
        # The profile meaning the corpus reviewed for this label. Without it the question is
        # unmapped, which is a different lane and not the one under test.
        ANSWERS.add_question_form(self.db, "contact.email", PROFILE_QUESTION,
                                  verified_by_user=True)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def write_stale_candidate_file(self):
        """A superseded profile, at the path the first version of this slice trusted."""
        candidate = {
            "schema_version": "0.2.0", "profile_id": "stale-profile",
            "work_authorization": {"country": "XX", "authorized_now": False,
                                   "sponsorship_now": True, "sponsorship_future": True,
                                   "employer_action_required": True, "confirmed": False},
            "search": {}, "facts": []}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        (self.private / "candidate.json").write_text(json.dumps(candidate), encoding="utf-8")
        return candidate

    def add_opening(self):
        card = {"job_id": "job-1", "canonical_url": "https://example.invalid/job-1",
                "employer": "Example Labs", "title": "Data Analyst", "country": "US",
                "location": "Testville", "work_arrangement": "remote",
                "employment_type": "full_time", "status": "open", "sponsorship": "unknown",
                "required_skills": [], "requirements_reviewed": False,
                "sponsorship_statements": [], "already_applied": False}
        self.db.execute(
            "INSERT INTO jobs (job_id, canonical_url, original_url, employer, title, location,"
            " normalized_employer, normalized_title, normalized_location, description_sha256,"
            " source, ats, job_card_json, status, created_at, updated_at)"
            " VALUES ('job-1', ?, ?, 'Example Labs', 'Data Analyst', 'Testville',"
            " 'example labs', 'data analyst', 'testville', 'd', 'test', 'test', ?, 'open', ?, ?)",
            (card["canonical_url"], card["canonical_url"], json.dumps(card),
             AT.isoformat(), AT.isoformat()))
        self.db.execute(
            "INSERT INTO applications (application_id, job_id, state, category,"
            " submission_policy, resume_version_id, created_at, updated_at)"
            " VALUES ('app-1', 'job-1', 'ready_to_fill', 'review', 'stop_before_submit',"
            " 'resume-a', ?, ?)", (AT.isoformat(), AT.isoformat()))

    def register_snapshot(self, extra=()):
        """The active profile: same composite contact fact, plus one locked profile field."""
        return super().register_snapshot(extra=tuple(extra) + (
            {"id": "fact-email", "type": "contact", "value": LOCKED_EMAIL,
             "status": "locked", "locked": True, "evidence_strength": "direct",
             "canonical_id": "contact.email"},
        ))

    def add_library_answer(self, *, authorized):
        ANSWERS.add_question_form(self.db, "discovery_source", LIBRARY_QUESTION,
                                  verified_by_user=True)
        ANSWERS.add_answer(self.db, {
            "answer_id": "answer-discovery", "canonical_id": "discovery_source",
            "canonical_meaning": "How the opening was discovered",
            "question": LIBRARY_QUESTION, "answer": "A careers page",
            "source_type": "user_confirmed", "answer_type": "application_specific",
            "confirmation_status": "confirmed", "confirmed_at": AT.isoformat(),
            "validity_class": "per_application", "scope": {"application_id": "app-1"},
            "auto_fill_allowed": True, "auto_submit_allowed": False})
        if authorized:
            # A standing authorization may not run more than fourteen days, and the preflight
            # asks whether one is live *now*, so this is anchored to the clock the request
            # will use rather than to the fixture's frozen AT.
            granted = datetime.now(timezone.utc) - timedelta(minutes=1)
            ANSWERS.add_authorization(self.db, {
                "authorization_id": "auth-1", "confirmed_at": granted.isoformat(),
                "expires_at": (granted + timedelta(days=7)).isoformat(),
                "scope": {"application_id": "app-1"}})
        self.db.commit()

    def classify(self, questions):
        return self.call("/api/apply/classify",
                         {"application_id": "app-1", "questions": questions})

    def lane_for(self, question, payload):
        return next(item for item in payload["questions"] if item["question"] == question)

    def counts(self):
        """Every table this database actually has, not a list that could drift past it."""
        tables = [row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        self.assertIn("audit_events", tables)
        return {table: self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables}


class PreflightBoundaryTests(PreflightFixture):
    """The four routes as the page reaches them, and what they refuse."""

    ROUTES = (("/api/apply/queue", None),
              ("/api/apply/readiness", {"application_id": "app-1"}),
              ("/api/apply/split", {"text": "Email Address"}),
              ("/api/apply/classify", {"application_id": "app-1", "questions": ["x"]}))

    def test_every_preflight_route_needs_the_session_token(self):
        for path, body in self.ROUTES:
            with self.subTest(path=path):
                status, payload = self.refused(path, body, token="wrong")
                self.assertEqual((status, payload["error"]), (403, "bad_token"))

    def test_a_foreign_origin_is_refused_on_every_preflight_route(self):
        for path, body in self.ROUTES:
            with self.subTest(path=path):
                status, payload = self.refused(path, body, origin="https://elsewhere.invalid")
                self.assertEqual((status, payload["error"]), (403, "bad_token"))

    def test_a_refusal_never_carries_a_path_a_value_or_an_exception(self):
        self.add_library_answer(authorized=True)
        refusals = [self.refused("/api/apply/readiness", {"application_id": "app-absent"})[1],
                    self.refused("/api/apply/classify", {"application_id": 5,
                                                         "questions": ["x"]})[1],
                    self.refused("/api/apply/classify", {"application_id": "app-1",
                                                         "questions": "not a list"})[1]]
        blob = json.dumps(refusals)
        for secret in (str(self.root), str(self.private), LOCKED_EMAIL, "A careers page",
                       "answer-discovery", "Traceback", ".json", "sqlite"):
            self.assertNotIn(secret, blob)
        for refusal in refusals:
            self.assertEqual(set(refusal), {"error"})

    def test_the_page_cannot_name_the_profile_the_answer_or_the_authorization(self):
        """Every extra key is ignored, and the answer is the one the rows already decided."""
        self.add_library_answer(authorized=False)
        smuggled = self.call("/api/apply/classify", {
            "application_id": "app-1", "questions": [LIBRARY_QUESTION],
            "candidate_path": str(self.private / "candidate.json"),
            "snapshot_sha256": "0" * 64, "snapshot_path": str(self.root / "anything.json"),
            "fact_id": "fact-email", "answer_id": "answer-discovery",
            "authorization_id": "auth-1", "authorized": True, "auto_fill_ready": True,
            "auto_submit_ready": True})
        honest = self.classify([LIBRARY_QUESTION])
        self.assertEqual(smuggled["questions"], honest["questions"])
        self.assertEqual(self.lane_for(LIBRARY_QUESTION, smuggled)["lane"],
                         "answer_needs_authorization")

    def test_the_returned_shape_is_an_allowlist_not_a_database_row(self):
        payload = self.classify([PROFILE_QUESTION])
        self.assertEqual(set(payload["questions"][0]), set(APP.CLASSIFIED_FIELDS))
        readiness = self.call("/api/apply/readiness", {"application_id": "app-1"})
        self.assertEqual(set(readiness["application"]), set(APP.READINESS_APPLICATION_FIELDS))
        self.assertTrue(set(readiness["evaluation"]) <= set(APP.EVALUATION_FIELDS))

    def test_the_preflight_page_is_served_and_fetches_nothing_from_outside(self):
        with urllib.request.urlopen(self.origin + "/apply", timeout=10) as response:
            policy = response.headers["Content-Security-Policy"]
            body = response.read().decode("utf-8")
        self.assertIn("default-src 'none'", policy)
        self.assertIn("connect-src 'self'", policy)
        self.assertNotIn("http://", body.replace(self.origin, ""))
        self.assertNotIn("localStorage", body)


class PreflightProfileTests(PreflightFixture):
    """Which profile answers, and what a profile field is allowed to look like."""

    def test_the_active_snapshot_answers_not_the_file_beside_the_database(self):
        readiness = self.call("/api/apply/readiness", {"application_id": "app-1"})
        # The stale file says unauthorised, sponsorship required, country XX. The registered
        # snapshot says authorised in the US, so the evaluation cannot have read the file.
        self.assertNotIn("not_authorized_to_work_now",
                         readiness["evaluation"]["hard_filter_failures"])
        self.assertNotIn("country_outside_search_scope",
                         readiness["evaluation"]["hard_filter_failures"])

    def test_a_locked_profile_field_reaches_the_profile_lane(self):
        payload = self.classify([PROFILE_QUESTION])
        item = self.lane_for(PROFILE_QUESTION, payload)
        self.assertEqual(item["lane"], "profile_ready")
        self.assertEqual(item["canonical_id"], "contact.email")
        self.assertEqual(item["source"], "profile")

    def test_a_profile_field_that_is_only_confirmed_is_not_fillable(self):
        """`fact-0002` is confirmed and not locked. Confirmed is not permission to fill."""
        ANSWERS.add_question_form(self.db, "contact.phone", "Phone Number",
                                  verified_by_user=True)
        self.db.commit()
        item = self.lane_for("Phone Number", self.classify(["Phone Number"]))
        self.assertEqual(item["lane"], "you_answer")
        self.assertFalse(item["auto_fill_ready"])
        self.assertIn(item["reason"], {PROFILE.PROFILE_FACT_MISSING,
                                       PROFILE.PROFILE_FACT_NOT_LOCKED,
                                       PROFILE.PROFILE_FACT_AMBIGUOUS})

    def test_no_lane_this_surface_returns_is_ever_submit_ready(self):
        self.add_library_answer(authorized=True)
        payload = self.classify([PROFILE_QUESTION, LIBRARY_QUESTION, "Race/Ethnicity",
                                 "Tell us about a project you are proud of."])
        for item in payload["questions"]:
            self.assertIs(item["auto_submit_ready"], False)

    def test_a_missing_active_snapshot_is_refused_with_a_bare_code(self):
        self.db.execute("UPDATE candidate_snapshots SET status='superseded'")
        self.db.commit()
        status, payload = self.refused("/api/apply/classify",
                                       {"application_id": "app-1", "questions": ["x"]})
        self.assertEqual((status, payload), (409, {"error": "no_active_candidate_snapshot"}))


class PreflightAnswerTests(PreflightFixture):
    """An answer existing, and an answer being fillable, are two different screens."""

    def test_an_answer_without_a_standing_authorization_is_not_shown_as_fillable(self):
        self.add_library_answer(authorized=False)
        item = self.lane_for(LIBRARY_QUESTION, self.classify([LIBRARY_QUESTION]))
        self.assertEqual(item["lane"], "answer_needs_authorization")
        self.assertTrue(item["answer_exists"])
        self.assertFalse(item["auto_fill_ready"])
        self.assertEqual(item["authorization_reason"], "standing_authorization_missing")

    def test_an_answer_with_a_live_authorization_is_ready(self):
        self.add_library_answer(authorized=True)
        item = self.lane_for(LIBRARY_QUESTION, self.classify([LIBRARY_QUESTION]))
        self.assertEqual(item["lane"], "answer_ready")
        self.assertTrue(item["auto_fill_ready"])

    def test_an_answer_value_never_crosses_the_boundary(self):
        self.add_library_answer(authorized=True)
        payload = self.classify([LIBRARY_QUESTION])
        self.assertNotIn("A careers page", json.dumps(payload))

    def test_classifying_a_hit_changes_nothing_in_the_database(self):
        """The claim the docs make, checked against the thing that used to break it.

        `match_answer` writes an `answer_matched` audit event and commits when an answer is
        used, so the first version of this slice was read-only only while nothing matched.
        """
        self.add_library_answer(authorized=True)
        before_counts = self.counts()
        before_changes = self.db.total_changes
        before_bytes = self.db_path.read_bytes()

        payload = self.classify([LIBRARY_QUESTION, PROFILE_QUESTION])
        self.assertEqual(self.lane_for(LIBRARY_QUESTION, payload)["lane"], "answer_ready")

        self.assertEqual(self.counts(), before_counts)
        self.assertEqual(self.db.total_changes, before_changes)
        self.assertEqual(self.db_path.read_bytes(), before_bytes)
        self.assertIs(payload["writes"], False)


class PreflightRefusalTests(PreflightFixture):
    """What the page may not talk the service into."""

    def test_a_manual_only_question_cannot_be_talked_out_of_its_lane(self):
        questions = ["Tell us about a time when you required visa sponsorship.",
                     "Describe a time your salary expectations were not met.",
                     "Why are you willing to disclose your race/ethnicity to us?",
                     "Walk us through how you are related to an employee here."]
        payload = self.classify(questions)
        for item in payload["questions"]:
            self.assertEqual(item["lane"], "manual_only")
            self.assertFalse(item["narrative_hint"])
            self.assertFalse(item["auto_fill_ready"])

    def test_a_verified_form_cannot_route_a_forbidden_meaning_to_the_profile(self):
        """A question form mapping to a forbidden meaning reaches the user, not the profile."""
        ANSWERS.add_question_form(self.db, "eeo.race", "Which of these describes you",
                                  verified_by_user=True)
        self.db.commit()
        item = self.lane_for("Which of these describes you",
                             self.classify(["Which of these describes you"]))
        self.assertEqual(item["lane"], "manual_only")

    def test_an_oversized_paste_is_refused(self):
        status, payload = self.refused("/api/apply/split", {"text": "x" * 100_001})
        self.assertEqual(status, 409)
        self.assertEqual(set(payload), {"error", "detail"})

    def test_an_empty_or_oversized_question_list_is_refused(self):
        for questions in ([], ["q"] * 251):
            with self.subTest(count=len(questions)):
                status, _ = self.refused("/api/apply/classify",
                                         {"application_id": "app-1", "questions": questions})
                self.assertEqual(status, 409)

    def test_a_question_list_that_is_not_strings_is_refused_before_any_lookup(self):
        status, payload = self.refused(
            "/api/apply/classify", {"application_id": "app-1", "questions": [{"q": 1}]})
        self.assertEqual((status, payload["error"]), (400, "bad_questions"))


# ---- the sponsorship triage page -------------------------------------------------

TRIAGE_VISA = ("We are currently unable to consider candidates who require, or will require "
               "in the future, sponsorship for work authorization.")
TRIAGE_SECOND = ("Applicants must be authorized to work in the US on a permanent and ongoing "
                 "basis without employer-sponsored work authorization.")
TRIAGE_TRIAL = "Assists with coordinating Investigator Meeting attendees with Sponsor(s)."


class TriageSurfaceTests(AppFixture):
    """The triage routes as the page reaches them.

    Written because the routes existed for a commit with no test behind them, and a refactor
    deleted the two functions they called: every unit test still passed and the page answered
    `app_failure`. A route nothing calls over HTTP is a route nothing checks.
    """

    def setUp(self):
        super().setUp()
        (self.private / "jobs-wide").mkdir()
        rows = []
        for job_id, employer, statements in (
            ("job-t1", "Beghou Consulting", [TRIAGE_VISA, TRIAGE_SECOND]),
            ("job-t2", "Beghou Consulting", [TRIAGE_VISA, TRIAGE_SECOND]),
            ("job-t3", "Science 37", [TRIAGE_TRIAL]),
            ("job-t4", "Veeva Systems", []),
        ):
            body = ("About the role. " + " ".join(statements) + " Join a great team.")
            card = {"job_id": job_id, "employer": employer, "title": "Analyst",
                    "location": "Boston", "canonical_url": f"https://example.invalid/{job_id}",
                    "sponsorship": "unknown", "sponsorship_statements": statements,
                    "description": body, "description_sha256": "0" * 64}
            (self.private / "jobs-wide" / f"{job_id}.json").write_text(
                json.dumps(card), encoding="utf-8")
            rows.append({"job_id": job_id})
        (self.private / "review-queue-app-test.json").write_text(
            json.dumps({"rows": rows}), encoding="utf-8")

    def test_the_triage_page_and_both_routes_answer(self):
        with urllib.request.urlopen(self.origin + "/triage", timeout=10) as response:
            policy = response.headers["Content-Security-Policy"]
            body = response.read().decode("utf-8")
        self.assertIn("default-src 'none'", policy)
        self.assertNotIn("localStorage", body)
        report = self.call("/api/sponsorship/queue")
        self.assertEqual(report["needing_triage"], 3)
        self.assertIs(report["writes"], False)
        posting = self.call("/api/sponsorship/posting", {"job_id": "job-t1"})
        self.assertIn(TRIAGE_VISA, posting["description"])

    def test_both_triage_routes_need_the_token_and_this_origin(self):
        for path, body in (("/api/sponsorship/queue", None),
                           ("/api/sponsorship/posting", {"job_id": "job-t1"})):
            with self.subTest(path=path):
                self.assertEqual(self.refused(path, body, token="wrong")[1]["error"],
                                 "bad_token")
                self.assertEqual(
                    self.refused(path, body, origin="https://elsewhere.invalid")[1]["error"],
                    "bad_token")

    def test_an_unknown_opening_is_refused_with_a_bare_code(self):
        status, payload = self.refused("/api/sponsorship/posting", {"job_id": "job-absent"})
        self.assertEqual((status, payload), (404, {"error": "no_such_opening"}))
        status, payload = self.refused("/api/sponsorship/posting", {"job_id": 5})
        self.assertEqual((status, payload), (400, {"error": "bad_job_id"}))

    def test_the_hint_orders_the_page_and_hides_nothing(self):
        report = self.call("/api/sponsorship/queue")
        self.assertEqual([item["hint"] for item in report["items"]],
                         ["employment_or_visa_signal", "employment_or_visa_signal",
                          "possible_trial_sponsor_only"])
        self.assertIn("job-t3", [item["job_id"] for item in report["items"]])
        for item in report["items"]:
            self.assertEqual(item["sponsorship"], "unknown")

    def test_identical_sentences_group_while_each_opening_keeps_its_own_identity(self):
        report = self.call("/api/sponsorship/queue")
        by_text = {group["text"]: group for group in report["groups"]}
        self.assertEqual(by_text[TRIAGE_VISA]["occurrences"], 2)
        members = by_text[TRIAGE_VISA]["members"]
        self.assertEqual(sorted(m["job_id"] for m in members), ["job-t1", "job-t2"])
        self.assertEqual(len({m["job_card_sha256"] for m in members}), 2)
        for member in members:
            self.assertIs(member["selected"], False)

    def test_adjacent_sentences_are_merged_for_display_only(self):
        report = self.call("/api/sponsorship/queue")
        item = next(i for i in report["items"] if i["job_id"] == "job-t1")
        self.assertEqual(len(item["statements"]), 2)
        self.assertEqual(len(item["blocks"]), 1)
        self.assertEqual(len(item["blocks"][0]["statement_sha256s"]), 2)
        self.assertEqual([s["statement_sha256"] for s in item["statements"]],
                         item["blocks"][0]["statement_sha256s"])

    def test_the_triage_payload_suggests_no_verdict_anywhere(self):
        blob = json.dumps(self.call("/api/sponsorship/queue"))
        for key in ('"verdict"', '"suggested"', '"default"', '"eligibility"'):
            self.assertNotIn(key, blob)
