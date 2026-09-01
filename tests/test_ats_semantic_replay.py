"""The vendored semantic corpus, its attribution, and the local replays generated from it.

These fixtures prove that a field combination came from a real recording. They do not prove
that current Lever, Greenhouse or Ashby DOM can be filled, and no test here may be read as
saying otherwise: there are no upstream selectors to test against, because none were
published. What is tested is that Jobloom has an explicit disposition for every field class
real employers ship, and that the local target is inert.
"""

import hashlib
import importlib.util
import json
import re
import socket
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parents[1]
UPSTREAM = ROOT / "tests" / "fixtures" / "ats-semantic" / "upstream"
UPSTREAM_COMMIT = "081a5d9d793da29111e2d5331767021718f1d8b5"


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"replay_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


REPLAY = load_script("semantic_replay")
POLICY = load_script("field_policy")

from tests.fixtures.ats_replay_server import ReplayServer

FAMILIES = ("lever-application-2026-08-v1", "greenhouse-single-page-2026-08-v1",
            "ashby-application-2026-08-v1")


class VendoredCorpusTests(unittest.TestCase):
    def fixture(self, name):
        return json.loads((UPSTREAM / name / "fixture.json").read_text(encoding="utf-8"))

    def test_vendored_bytes_match_the_recorded_manifest_and_upstream_provenance(self):
        manifest = json.loads((UPSTREAM / "SHA256SUMS.json").read_text(encoding="utf-8"))
        for name in FAMILIES:
            with self.subTest(fixture=name):
                for part in ("fixture", "provenance", "approval"):
                    raw = (UPSTREAM / name / f"{part}.json").read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest[name][part])
                # Upstream's own provenance names the digest of the fixture it approved, so
                # the copy is checked against the source's record, not only against ours.
                provenance = json.loads((UPSTREAM / name / "provenance.json").read_text())
                self.assertEqual(
                    hashlib.sha256((UPSTREAM / name / "fixture.json").read_bytes()).hexdigest(),
                    provenance["fixtureSha256"])
                self.assertEqual(provenance["approvedBy"], "qa-owner")
        self.assertEqual(
            hashlib.sha256((UPSTREAM / "LICENSE").read_bytes()).hexdigest(), manifest["LICENSE"])

    def test_attribution_names_the_source_licence_and_pinned_commit(self):
        notice = (UPSTREAM / "NOTICE.md").read_text(encoding="utf-8")
        for required in (REPLAY.UPSTREAM_URL, UPSTREAM_COMMIT, "MIT", "Jeremy Watt"):
            self.assertIn(required, notice)
        self.assertEqual(REPLAY.UPSTREAM_COMMIT, UPSTREAM_COMMIT)
        licence = (UPSTREAM / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", licence)
        self.assertIn("Jeremy Watt", licence)
        # The claim these fixtures do not support has to be written where they are stored.
        self.assertIn("current live-site acceptance evidence", notice)
        self.assertIn("says nothing about", notice)
        self.assertIn("supervised live acceptance test", notice)

    def test_only_the_nine_permitted_files_were_copied(self):
        copied = sorted(path.relative_to(UPSTREAM).as_posix()
                        for path in UPSTREAM.rglob("*") if path.is_file())
        expected = sorted(
            [f"{name}/{part}.json" for name in FAMILIES
             for part in ("approval", "fixture", "provenance")]
            + ["LICENSE", "NOTICE.md", "SHA256SUMS.json"])
        self.assertEqual(copied, expected)
        # No upstream executable code, markup, or recording came with them.
        for path in UPSTREAM.rglob("*.json"):
            body = path.read_text(encoding="utf-8")
            for forbidden in ("<html", "<div", "querySelector", "css", "xpath", "screenshot"):
                self.assertNotIn(forbidden, body.casefold(), f"{path.name}: {forbidden}")

    def test_every_upstream_kind_and_role_has_an_explicit_disposition(self):
        seen = set()
        for name in FAMILIES:
            for step in self.fixture(name)["steps"]:
                for control in step["controls"]:
                    seen.add(control["kind"])
                    self.assertIn(control["role"], REPLAY.ROLE_CONTROLS)
                    disposition, target = REPLAY.disposition_for(control["kind"])
                    self.assertIn(disposition, POLICY.DISPOSITIONS)
                    self.assertTrue(target)
        self.assertEqual(seen, set(REPLAY.KIND_DISPOSITIONS),
                         "the mapping and the corpus have drifted apart")

    def test_an_unmapped_kind_fails_closed_rather_than_rendering_as_a_text_box(self):
        with self.assertRaises(REPLAY.UnmappedKind):
            REPLAY.disposition_for("eeo.invented_category")
        with self.assertRaises(REPLAY.UnmappedKind):
            REPLAY.render_control({"kind": "unknown.kind", "role": "textbox",
                                   "label": "x", "required": False}, "t-1")
        with self.assertRaises(REPLAY.UnmappedKind):
            REPLAY.control_for("slider")

    def test_the_four_immigration_meanings_are_never_collapsed(self):
        # Upstream's broad sponsorship controls cover more than one meaning at once, so they
        # map to a pause. Nothing may route them to one of the four canonical answers.
        for broad in ("authorization.sponsorship_status", "authorization.sponsorship_select"):
            disposition, reason = REPLAY.disposition_for(broad)
            self.assertEqual(disposition, "always_manual")
            self.assertEqual(reason, "sponsorship_meaning_ambiguous")
        answers = {target for disposition, target in REPLAY.KIND_DISPOSITIONS.values()
                   if disposition == "answer"}
        self.assertEqual(answers & {"sponsorship_now", "sponsorship_future"}, set())

    def test_voluntary_eeo_compensation_and_conflict_kinds_are_always_manual(self):
        for kind in ("eeo.race", "eeo.gender", "eeo.disability", "eeo.veteran",
                     "compensation.total_range", "compensation.target_salary",
                     "conflict.related_person", "conflict.customer_partner_reseller"):
            with self.subTest(kind=kind):
                self.assertEqual(REPLAY.disposition_for(kind)[0], "always_manual")


class GeneratedReplayTests(unittest.TestCase):
    def test_each_family_renders_every_page_from_its_semantic_fixture(self):
        for name in FAMILIES:
            fixture = json.loads((UPSTREAM / name / "fixture.json").read_text())
            for index in range(len(fixture["steps"])):
                with self.subTest(fixture=name, page=index):
                    page = REPLAY.render_page(fixture, index)
                    self.assertIn("<!doctype html>", page)
                    for control in fixture["steps"][index]["controls"]:
                        self.assertIn(f'data-kind="{control["kind"]}"', page)

    def test_the_markup_is_jobloom_owned_and_reaches_nothing_outside_the_process(self):
        for name in FAMILIES:
            fixture = json.loads((UPSTREAM / name / "fixture.json").read_text())
            page = REPLAY.render_page(fixture, 0, include_variants=True, final=True)
            with self.subTest(fixture=name):
                self.assertNotIn("http://", page.replace("http://www.w3.org", ""))
                self.assertNotIn("https://", page)
                for forbidden in ("fetch(", "XMLHttpRequest", "import ", "src=", "action="):
                    self.assertNotIn(forbidden, page)
                # Synthetic wording only: no real employer name reaches the repository.
                self.assertIn("Not a real employer form", page)

    def test_every_safety_variant_is_discoverable_on_the_first_page(self):
        fixture = json.loads((UPSTREAM / FAMILIES[0] / "fixture.json").read_text())
        page = REPLAY.render_page(fixture, 0, include_variants=True)
        for variant in REPLAY.SAFETY_VARIANTS:
            with self.subTest(variant=variant):
                self.assertIn(f'data-test-id="{variant}"', page)
        self.assertIn("hidden>", page)
        self.assertIn("disabled>", page)
        self.assertIn('data-copy="2"', page)
        self.assertIn("<iframe", page)

    def test_no_file_control_accepts_anything_but_a_pdf(self):
        for name in FAMILIES:
            fixture = json.loads((UPSTREAM / name / "fixture.json").read_text())
            for index in range(len(fixture["steps"])):
                page = REPLAY.render_page(fixture, index, include_variants=(index == 0))
                for control in re.findall(r'<input type="file"[^>]*>', page):
                    with self.subTest(fixture=name, control=control[:60]):
                        self.assertIn('accept="application/pdf"', control)

    def test_nothing_in_the_markup_navigates_or_submits_on_its_own(self):
        fixture = json.loads((UPSTREAM / FAMILIES[0] / "fixture.json").read_text())
        first = REPLAY.render_page(fixture, 0, final=False)
        last = REPLAY.render_page(fixture, len(fixture["steps"]) - 1, final=True)
        # Continuing is a link a person or a test follows, never something the page does.
        self.assertIn('id="next-page"', first)
        self.assertNotIn("location.href", first)
        self.assertNotIn("window.open", first)
        self.assertNotIn("setTimeout", first)
        self.assertNotIn("submit()", first + last)
        # The final control is a stop boundary that only counts.
        self.assertIn('type="button"', last)
        self.assertNotIn('type="submit"', last)
        self.assertIn("onsubmit=\"return false;\"", last)


class ReplayServerTests(unittest.TestCase):
    def test_the_server_binds_loopback_on_an_allocated_port_and_stops_cleanly(self):
        with ReplayServer() as server:
            self.assertEqual(server.host, "127.0.0.1")
            self.assertGreater(server.port, 0)
            self.assertRegex(server.origin, r"^http://127\.0\.0\.1:\d+$")
            port = server.port
            with urllib.request.urlopen(f"{server.origin}/lever/0", timeout=5) as response:
                self.assertEqual(response.status, 200)
        # The port is released, so the server really stopped rather than leaking a thread.
        with socket.socket() as probe:
            probe.settimeout(1)
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)

    def test_all_three_families_are_served_and_pages_are_reached_one_at_a_time(self):
        with ReplayServer() as server:
            state = json.load(urllib.request.urlopen(f"{server.origin}/__state", timeout=5))
            self.assertEqual(sorted(state["pages"]),
                             ["/ashby/0", "/ashby/1", "/greenhouse/0", "/greenhouse/1",
                              "/lever/0", "/lever/1"])
            for path in state["pages"]:
                with self.subTest(path=path):
                    with urllib.request.urlopen(f"{server.origin}{path}", timeout=5) as page:
                        self.assertIn(b"Synthetic", page.read())

    def test_the_final_action_counter_starts_at_zero_and_reads_without_being_touched(self):
        with ReplayServer() as server:
            for _ in range(3):
                state = json.load(urllib.request.urlopen(f"{server.origin}/__state", timeout=5))
                self.assertEqual(state["final_action_activations"], 0)
            urllib.request.urlopen(f"{server.origin}/lever/1", timeout=5).read()
            state = json.load(urllib.request.urlopen(f"{server.origin}/__state", timeout=5))
            self.assertEqual(state["final_action_activations"], 0)


if __name__ == "__main__":
    unittest.main()
