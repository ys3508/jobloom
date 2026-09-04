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
