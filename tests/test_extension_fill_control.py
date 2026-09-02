"""The panel is a control surface, and these tests are about what pressing it does.

Every claim here that could be made by reading the source is instead made by running it.
A search proving `chrome.tabs` does not appear inside the fill handler says nothing about
where the handler leads, and the boundary that matters — pressing Fill never touches a tab,
never sends a URL, never stores the execution id — is a property of the whole call, not of
one function's text. `panel_harness.mjs` loads the shipped `panel.js` against stubbed
browser APIs and records what it actually called.

The mode under test is **extension-controlled separate guarded worker**: the panel asks, the
bridge and the execution authority decide, and the run happens in a window that is not the
user's tab. Production ATS adapters remain unimplemented; this runs against the local
semantic replay only.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
EXTENSION = ROOT / "skills" / "jobloom" / "extension"
HARNESS = ROOT / "tests" / "fixtures" / "panel_harness.mjs"

PANEL_JS = (EXTENSION / "panel.js").read_text(encoding="utf-8")
PANEL_HTML = (EXTENSION / "panel.html").read_text(encoding="utf-8")
MANIFEST = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))

ENDPOINT = "http://127.0.0.1:8787"
PREPARED = {
    "execution_id": "e" * 32, "status": "prepared",
    "application": {"application_id": "app-1", "employer": "Example Corp",
                    "role": "Backend Engineer"},
    "page": {"page_index": 0, "final_page": False, "submit_control_seen": False},
    "actions": {"count": 3, "controls": {"file": 1, "text": 2},
                "operations": {"fill": 2, "upload": 1},
                "sources": {"fact": 2, "resume": 1}, "sensitivities": {"normal": 3}},
    "risks": {"legal_items": [], "restricted_requests": []},
    "expires_at": "2026-09-02T05:00:00+00:00",
    "stops_before_submit": True, "runs_in_separate_window": True,
}
EXECUTED = {
    "execution_id": "e" * 32, "status": "verified", "verified": 3, "pending": 0,
    "paused": 0, "session_status": "active", "reasons": [], "package_consumed": True,
    "package_expired": False, "submit_boundary": "observed_not_acted_on",
    "stops_before_submit": True, "runs_in_separate_window": True,
}


def run_panel(**plan):
    """Load the real panel against stubs and return what it did."""
    if not shutil.which("node"):
        raise unittest.SkipTest("node is required to run the panel behaviour harness")
    body = {
        "endpoint": ENDPOINT,
        "storage": {"token": "token-under-test", "endpoint": ENDPOINT},
        "responses": {"/health": {"body": {"store_enabled": False,
                                           "fact_store_ready": True}}},
    }
    body.update(plan)
    finished = subprocess.run(
        ["node", str(HARNESS), json.dumps(body)], capture_output=True, text=True,
        cwd=str(ROOT), timeout=60)
    if finished.returncode != 0:
        raise AssertionError(f"panel harness failed: {finished.stderr[-2000:]}")
    return json.loads(finished.stdout)


def fill_run_plan(**overrides):
    plan = {
        "responses": {
            "/health": {"body": {"store_enabled": False, "fact_store_ready": True}},
            "/fill/prepare": {"body": PREPARED},
            "/fill/execute": {"body": EXECUTED},
        },
        "steps": [
            {"click": "fill-prepare", "values": {"fill-application": "app-1"}},
            {"click": "fill-run"},
        ],
    }
    plan.update(overrides)
    return plan


class ManifestTests(unittest.TestCase):
    """Task 7 added a mode, not a reach."""

    def test_the_manifest_asks_for_nothing_new(self):
        self.assertEqual(sorted(MANIFEST["permissions"]),
                         ["activeTab", "scripting", "sidePanel", "storage", "webNavigation"])
        self.assertEqual(MANIFEST["host_permissions"], ["http://127.0.0.1:8787/*"])
        self.assertEqual(sorted(MANIFEST["optional_host_permissions"]),
                         ["https://*.indeed.com/*", "https://www.linkedin.com/*"])

    def test_no_content_script_is_declared(self):
        # A resident script is the thing that would turn "only when you ask" into "always".
        self.assertNotIn("content_scripts", MANIFEST)

    def test_the_extension_cannot_reach_an_arbitrary_ats(self):
        """No host permission covers a real employer form, optional or otherwise.

        This is the permission-level reason Task 7 could not have driven the user's tab even
        if it had wanted to, and the reason the run lives in the worker's own window.
        """
        every_host = MANIFEST["host_permissions"] + MANIFEST["optional_host_permissions"]
        for host in every_host:
            self.assertNotIn("<all_urls>", host)
            self.assertFalse(host.startswith("*://"), host)
        for ats in ("jobs.lever.co", "job-boards.greenhouse.io", "jobs.ashbyhq.com"):
            self.assertFalse(any(ats in host for host in every_host), ats)


class PanelBehaviourTests(unittest.TestCase):
    """What the shipped panel does when the buttons are pressed."""

    def test_pressing_fill_never_touches_a_tab(self):
        trace = run_panel(**fill_run_plan())
        names = [call["name"] for call in trace["calls"]]
        self.assertNotIn("tabs.query", names)
        self.assertNotIn("scripting.executeScript", names)
        paths = [request["path"] for request in trace["requests"]]
        self.assertEqual([path for path in paths if path.startswith("/fill")],
                         ["/fill/prepare", "/fill/execute"])

    def test_the_fill_requests_carry_only_an_opaque_id(self):
        """A closed body, checked from the wire rather than from the source.

        The panel could not send a target, an origin or a tab id even if a later change made
        it want to, because the bridge refuses an unexpected field — but the panel should not
        be trying, and this is where that is visible.
        """
        trace = run_panel(**fill_run_plan())
        sent = {request["path"]: request for request in trace["requests"]
                if request["path"].startswith("/fill")}
        self.assertEqual(sent["/fill/prepare"]["body"], {"application_id": "app-1"})
        self.assertEqual(sent["/fill/execute"]["body"], {"execution_id": "e" * 32})
        for request in sent.values():
            self.assertEqual(request["method"], "POST")
            self.assertIn("X-Jobloom-Token", request["headers"])
            rendered = json.dumps(request["body"])
            for forbidden in ("http", "://", "tab", "origin", "path", "url", "file"):
                self.assertNotIn(forbidden, rendered.lower())

    def test_a_double_press_sends_one_execute(self):
        """Both presses issued before either settles — the real double-click, not two clicks.

        This is the panel's own layer. It is not the safety boundary: the bridge refuses a
        second execute for the same id, and the authority consumes the grant exactly once.
        A user who gets past all three would still not fill a page twice.
        """
        plan = fill_run_plan()
        plan["steps"] = [
            {"click": "fill-prepare", "values": {"fill-application": "app-1"}},
            {"click": "fill-run", "twice": True},
        ]
        trace = run_panel(**plan)
        executes = [r for r in trace["requests"] if r["path"] == "/fill/execute"]
        self.assertEqual(len(executes), 1)

    def test_a_double_press_on_prepare_sends_one_prepare(self):
        plan = fill_run_plan()
        plan["steps"] = [
            {"click": "fill-prepare", "values": {"fill-application": "app-1"},
             "twice": True},
        ]
        trace = run_panel(**plan)
        prepares = [r for r in trace["requests"] if r["path"] == "/fill/prepare"]
        self.assertEqual(len(prepares), 1)

    def test_the_execution_id_is_never_written_to_extension_storage(self):
        """A capability that outlives the window it was granted in is unwatched.

        The bridge token beside it is stored, which is existing browser-assist behaviour and
        is deliberately left alone; what must not join it is anything to do with a run.
        """
        trace = run_panel(**fill_run_plan())
        self.assertEqual(sorted(trace["storage"]), ["endpoint", "token"])
        written = [call for call in trace["calls"] if call["name"] == "storage.set"]
        for call in written:
            self.assertNotIn("execution_id", json.dumps(call["detail"]))
        self.assertNotIn("e" * 32, json.dumps(trace["storage"]))

    def test_reloading_the_panel_does_not_re_run_anything(self):
        """A fresh load is a fresh panel with no run in it.

        Nothing about a prepared run survives the window, so reopening the panel cannot
        replay one — there is no id left to send.
        """
        trace = run_panel(storage={"token": "token-under-test", "endpoint": ENDPOINT},
                          responses={"/health": {"body": {"store_enabled": False,
                                                          "fact_store_ready": True}}})
        self.assertEqual([r["path"] for r in trace["requests"] if r["path"].startswith("/fill")],
                         [])

    def test_the_posting_read_path_never_reaches_a_fill_route(self):
        """The automatic re-read exists, and it must stay on its own side.

        `followUser` fires on `webNavigation` and calls `readPosting` without a gesture. That
        is the existing browser-assist behaviour and it is kept — what is asserted is that no
        amount of it produces a prepare or an execute.
        """
        trace = run_panel(
            pageAccess=True,
            responses={
                "/health": {"body": {"store_enabled": False, "fact_store_ready": True}},
                "/positioning": {"body": {"job": {"title": "Analyst"}, "verdict": {},
                                          "classified": {}, "job_card": {}}},
                "/fill/prepare": {"body": PREPARED},
                "/fill/execute": {"body": EXECUTED},
            },
            steps=[{"navigate": True}, {"navigate": True}, {"navigate": True}])
        self.assertGreater(trace["navigationListeners"], 0)
        paths = [request["path"] for request in trace["requests"]]
        self.assertIn("/positioning", paths)
        self.assertEqual([path for path in paths if path.startswith("/fill")], [])

    def test_the_panel_polls_nothing_and_navigates_nowhere(self):
        """After the run settles, the panel has stopped asking.

        A polling panel would keep issuing requests once the buttons were done with; this
        counts what was sent, waits, and counts again.
        """
        trace = run_panel(**fill_run_plan())
        paths = [request["path"] for request in trace["requests"]]
        # /health once at load, then exactly the two presses. Nothing repeats.
        self.assertEqual(paths, ["/health", "/fill/prepare", "/fill/execute"])
        names = [call["name"] for call in trace["calls"]]
        for forbidden in ("tabs.update", "tabs.create", "tabs.query",
                          "scripting.executeScript"):
            self.assertNotIn(forbidden, names)

    def test_the_finished_run_is_never_reported_as_submitted(self):
        trace = run_panel(**fill_run_plan())
        rendered = json.dumps(trace["status"], ensure_ascii=False).lower()
        self.assertIn("nothing was submitted", rendered)
        for claim in ("application submitted", "submitted your", "sent the form"):
            self.assertNotIn(claim, rendered)


class PanelSurfaceTests(unittest.TestCase):
    """The two modes are separate on screen, and the promise is on screen with them."""

    def test_filling_has_its_own_section_button_and_status(self):
        for element in ('id="fill"', 'id="fill-prepare"', 'id="fill-run"',
                        'id="fill-status"', 'id="fill-summary"'):
            self.assertIn(element, PANEL_HTML)
        # Reading keeps its own status line; a shared one would let one mode narrate the
        # other's outcome.
        self.assertIn('id="status"', PANEL_HTML)
        self.assertNotIn('id="fill"', PANEL_HTML.split('<section id="result"')[1]
                         .split("</section>")[0])

    def test_the_panel_states_the_window_the_tab_and_the_stop(self):
        for key in ("fillSeparateWindow", "fillNoTabChange", "fillStopsBeforeSubmit"):
            self.assertIn(f'data-i18n="{key}"', PANEL_HTML)
        self.assertIn(
            'fillSeparateWindow: "Runs one page in a separate guarded Jobloom browser window."',
            PANEL_JS)
        self.assertIn('fillNoTabChange: "Your current tab will not be changed."', PANEL_JS)
        self.assertIn('fillStopsBeforeSubmit: "Stops before Submit."', PANEL_JS)

    def test_no_wording_suggests_the_run_happens_in_the_current_tab(self):
        lowered = PANEL_JS.lower()
        for misleading in ("fill this tab", "fill this page", "fill the current tab",
                           "in this tab", "on this page"):
            self.assertNotIn(misleading, lowered)

    def test_the_fill_control_never_names_a_tab_or_a_url(self):
        """A source check on top of the behavioural ones, not instead of them.

        The harness proves the buttons do not reach a tab. This proves the section was not
        written as though it might: a URL or a `chrome.tabs` call sitting unused in the fill
        control is the next person's starting point.
        """
        section = PANEL_JS.split("// ---- filling one page")[1]
        # Comments stripped first: the section explains at length that it does not call
        # these, and a check that the prose is absent would be a check on the prose.
        code = "\n".join(line.split("//")[0] for line in section.splitlines())
        for forbidden in ("chrome.tabs", "chrome.scripting", "chrome.webNavigation",
                          "chrome.storage", "setInterval", "http://", "https://"):
            self.assertNotIn(forbidden, code)
        # And the section really is the whole fill control, not a prefix of it.
        self.assertIn("/fill/prepare", code)
        self.assertIn("/fill/execute", code)


if __name__ == "__main__":
    unittest.main()
