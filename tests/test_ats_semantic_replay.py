"""The vendored semantic corpus, its attribution, and the local replays generated from it.

These fixtures prove that a field combination came from a real recording. They do not prove
that current Lever, Greenhouse or Ashby DOM can be filled, and no test here may be read as
saying otherwise: there are no upstream selectors to test against, because none were
published. What is tested is that Jobloom has an explicit disposition for every field class
real employers ship, and that the local target is inert.
"""

import base64
import hashlib
import importlib.util
import json
import re
import socket
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parents[1]
UPSTREAM = ROOT / "tests" / "fixtures" / "ats-semantic" / "upstream"
UPSTREAM_COMMIT = "081a5d9d793da29111e2d5331767021718f1d8b5"
NONCE = "t" * 48


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

    def test_vendored_bytes_are_proven_to_be_in_the_pinned_commits_tree(self):
        """Walk the real object chain: commit -> root tree -> qa -> fixtures -> path -> blob.

        The previous version of this test recomputed each local file's blob id and compared
        it to a manifest that lived beside the files, so editing a file and updating the
        manifest kept it green. It compared the vendoring to itself. Here every stored object
        is re-hashed, which is what makes the chain unforgeable offline: changing a blob
        changes its id, which changes the tree that names it, which changes the tree above,
        which changes the commit — and the commit id is the constant this test starts from.
        """
        anchor = json.loads((UPSTREAM / "GIT-ANCHOR.json").read_text(encoding="utf-8"))
        self.assertEqual(anchor["commit"], UPSTREAM_COMMIT)

        def payload(object_id):
            stored = anchor["objects"][object_id]
            data = base64.b64decode(stored["base64"])
            header = f"{stored['type']} {len(data)}".encode() + b"\0"
            self.assertEqual(hashlib.sha1(header + data).hexdigest(), object_id,
                             f"stored object does not hash to its own id: {object_id}")
            return stored["type"], data

        def entries(tree_id):
            kind, data = payload(tree_id)
            self.assertEqual(kind, "tree")
            found, index = {}, 0
            while index < len(data):
                space = data.index(b" ", index)
                nul = data.index(b"\0", space)
                found[data[space + 1:nul].decode()] = data[nul + 1:nul + 21].hex()
                index = nul + 21
            return found

        kind, commit = payload(UPSTREAM_COMMIT)
        self.assertEqual(kind, "commit")
        root = commit.split(b"\n", 1)[0].split()[1].decode()
        fixtures = entries(entries(entries(root)["qa"])["fixtures"])
        resolved = {"LICENSE": entries(root)["LICENSE"]}
        for name in FAMILIES:
            directory = entries(fixtures[name])
            for part in ("fixture", "provenance", "approval"):
                resolved[f"{name}/{part}.json"] = directory[f"{part}.json"]

        self.assertEqual(resolved, anchor["paths"])
        for relative, blob_id in resolved.items():
            with self.subTest(path=relative):
                data = (UPSTREAM / relative).read_bytes()
                self.assertEqual(
                    hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(), blob_id,
                    f"{relative} is not the blob the pinned commit's tree names")

    def test_the_recorded_digests_still_describe_the_vendored_files(self):
        manifest = json.loads((UPSTREAM / "SHA256SUMS.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["upstream_commit"], UPSTREAM_COMMIT)
        for relative, digests in manifest["files"].items():
            with self.subTest(path=relative):
                data = (UPSTREAM / relative).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digests["sha256"])
        self.assertEqual(
            set(manifest["files"]),
            {f"{name}/{part}.json" for name in FAMILIES
             for part in ("fixture", "provenance", "approval")} | {"LICENSE"})

    def test_vendored_bytes_match_upstream_provenance(self):
        for name in FAMILIES:
            with self.subTest(fixture=name):
                # Upstream's own provenance names the digest of the fixture it approved, so
                # the copy is checked against the source's record, not only against ours.
                provenance = json.loads((UPSTREAM / name / "provenance.json").read_text())
                self.assertEqual(
                    hashlib.sha256((UPSTREAM / name / "fixture.json").read_bytes()).hexdigest(),
                    provenance["fixtureSha256"])
                self.assertEqual(provenance["approvedBy"], "qa-owner")

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
            + ["LICENSE", "NOTICE.md", "SHA256SUMS.json", "GIT-ANCHOR.json"])
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
                                   "label": "x", "required": False}, "t-1", NONCE)
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
                    page = REPLAY.render_page(fixture, index, NONCE)
                    self.assertIn("<!doctype html>", page)
                    for control in fixture["steps"][index]["controls"]:
                        self.assertIn(f'data-kind="{control["kind"]}"', page)

    def test_the_page_states_that_its_wording_is_recorded_not_synthetic(self):
        # An earlier version asserted the page was synthetic on the strength of a banner
        # reading "Not a real employer form". The labels come from upstream recordings, so
        # that assertion was checking a sentence rather than a property.
        fixture = json.loads((UPSTREAM / FAMILIES[0] / "fixture.json").read_text())
        labels = [control.get("label") for step in fixture["steps"]
                  for control in step["controls"] if control.get("label")]
        page = REPLAY.render_page(fixture, 0, NONCE)
        rendered = [label for label in labels if label in page]
        self.assertTrue(rendered, "labels should be rendered as recorded")
        self.assertIn("recorded upstream wording", page)
        self.assertIn("MIT", page)

    def test_the_markup_is_jobloom_owned_and_reaches_nothing_outside_the_process(self):
        for name in FAMILIES:
            fixture = json.loads((UPSTREAM / name / "fixture.json").read_text())
            page = REPLAY.render_page(fixture, 0, NONCE, include_variants=True, final=True)
            with self.subTest(fixture=name):
                self.assertNotIn("http://", page.replace("http://www.w3.org", ""))
                self.assertNotIn("https://", page)
                for forbidden in ("fetch(", "XMLHttpRequest", "import ", "src=", "<script"):
                    self.assertNotIn(forbidden, page)
                # The only action target is the loopback counting endpoint.
                for action in re.findall(r'action="([^"]*)"', page):
                    self.assertIn(action, ("", "/__final_action"))
                # The banner is not a claim about the labels. They are upstream recorded
                # wording, vendored under MIT and rendered as recorded, because a paraphrase
                # would test a form no employer ships. What the banner says is that the page
                # is local and inert; it does not make the wording synthetic.
                self.assertIn("recorded upstream wording", page)

    def test_every_safety_variant_is_discoverable_on_the_first_page(self):
        fixture = json.loads((UPSTREAM / FAMILIES[0] / "fixture.json").read_text())
        page = REPLAY.render_page(fixture, 0, NONCE, include_variants=True)
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
                page = REPLAY.render_page(fixture, index, NONCE, include_variants=(index == 0))
                for control in re.findall(r'<input type="file"[^>]*>', page):
                    with self.subTest(fixture=name, control=control[:60]):
                        self.assertIn('accept="application/pdf"', control)

    def test_nothing_in_the_markup_navigates_or_submits_on_its_own(self):
        fixture = json.loads((UPSTREAM / FAMILIES[0] / "fixture.json").read_text())
        first = REPLAY.render_page(fixture, 0, NONCE, final=False)
        last = REPLAY.render_page(fixture, len(fixture["steps"]) - 1, NONCE, final=True)
        # Continuing is a link a person or a test follows, never something the page does.
        self.assertIn('id="next-page"', first)
        self.assertNotIn("location.href", first)
        self.assertNotIn("window.open", first)
        self.assertNotIn("setTimeout", first)
        self.assertNotIn("submit()", first + last)
        # The final control really can submit; that is the point. An oracle that cannot
        # observe an activation cannot fail, and the earlier inert button plus a `window`
        # counter the server never read was exactly that.
        self.assertIn('type="submit"', last)
        self.assertIn('action="/__final_action"', last)
        self.assertNotIn('action="/__final_action"', first)


class SurfaceProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.db = __import__("sqlite3").connect(":memory:")
        self.db.row_factory = __import__("sqlite3").Row
        POLICY.initialize(self.db)
        self.addCleanup(self.db.close)
        self.at = __import__("datetime").datetime(
            2026, 8, 25, 12, tzinfo=__import__("datetime").timezone.utc)

    def test_only_the_server_jobloom_started_grants_option_trust(self):
        """A rogue local server serving the same markup earns nothing.

        Loopback is a network location. What makes a pair checkable is the nonce the running
        server generated and registered, which nothing else on the machine holds.
        """
        with ReplayServer(connection=self.db, clock=lambda: self.at) as server:
            label = "Decline to answer"
            genuine = {"label": label,
                       "value": POLICY.replay_option_value(label, server.nonce)}
            self.assertTrue(POLICY.option_mapping_trusted(
                self.db, f"{server.origin}/lever/0", genuine, self.at))
            # Same host, same algorithm, a nonce it had to guess.
            rogue = {"label": label,
                     "value": POLICY.replay_option_value(label, "r" * 48)}
            self.assertFalse(POLICY.option_mapping_trusted(
                self.db, f"{server.origin}/lever/0", rogue, self.at))
            # The value really is the one the served page offers.
            page = urllib.request.urlopen(f"{server.origin}/lever/0", timeout=5).read().decode()
            self.assertIn(genuine["value"], page)
        # Stopping the server withdraws the trust it carried.
        self.assertFalse(POLICY.option_mapping_trusted(
            self.db, f"{server.origin}/lever/0",
            {"label": "Decline to answer",
             "value": POLICY.replay_option_value("Decline to answer", server.nonce)}, self.at))

    def test_two_servers_do_not_share_a_nonce(self):
        with ReplayServer(connection=self.db, clock=lambda: self.at) as first:
            with ReplayServer(connection=self.db, clock=lambda: self.at) as second:
                self.assertNotEqual(first.nonce, second.nonce)
                self.assertNotEqual(first.origin, second.origin)
                crossed = {"label": "Decline to answer",
                           "value": POLICY.replay_option_value("Decline to answer", first.nonce)}
                self.assertFalse(POLICY.option_mapping_trusted(
                    self.db, f"{second.origin}/lever/0", crossed, self.at))

    def test_two_live_surfaces_on_one_origin_fail_closed(self):
        # Two processes claiming the same origin is not something this can adjudicate, and
        # taking the most recent row would be a guess presented as a fact.
        label = "Decline to answer"
        with ReplayServer(connection=self.db, clock=lambda: self.at) as server:
            pair = {"label": label,
                    "value": POLICY.replay_option_value(label, server.nonce)}
            self.assertTrue(POLICY.option_mapping_trusted(
                self.db, f"{server.origin}/lever/0", pair, self.at))
            POLICY.register_replay_surface(
                self.db, "impostor", server.origin, "i" * 48, "f" * 64, "1.0.0",
                self.at, self.at + __import__("datetime").timedelta(hours=1))
            self.assertFalse(POLICY.option_mapping_trusted(
                self.db, f"{server.origin}/lever/0", pair, self.at))

    def test_the_surface_digest_covers_content_not_page_names(self):
        # The old digest hashed `/lever/0` and friends, so changing a fixture's labels or the
        # rendered markup left it identical.
        with ReplayServer(connection=self.db, clock=lambda: self.at) as server:
            recorded = self.db.execute(
                "SELECT fixture_sha256 FROM replay_surfaces WHERE surface_id=?",
                (server.surface_id,)).fetchone()["fixture_sha256"]
            self.assertEqual(recorded, server.content_sha256())
            names_only = hashlib.sha256("".join(sorted(server.pages)).encode()).hexdigest()
            self.assertNotEqual(recorded, names_only)
            original = server.pages["/lever/0"]
            server.pages["/lever/0"] = original.replace("Full name", "Full  name")
            self.assertNotEqual(server.content_sha256(), recorded)
            server.pages["/lever/0"] = original
            self.assertEqual(server.content_sha256(), recorded)

    def test_a_surface_created_without_an_injected_clock_is_live_now(self):
        # A default of a fixed historical date made every real run register an
        # already-expired surface; the tests could not see it because they shared the same
        # constant.
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        with ReplayServer(connection=self.db) as server:
            label = "Decline to answer"
            pair = {"label": label,
                    "value": POLICY.replay_option_value(label, server.nonce)}
            self.assertTrue(POLICY.option_mapping_trusted(
                self.db, f"{server.origin}/lever/0", pair, now))


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
            # `/lever/split/*` is a Jobloom pagination of the same reviewed controls, added
            # so a two-package flow has two pages of fields to work with.
            self.assertEqual(sorted(state["pages"]),
                             ["/ashby/0", "/ashby/1", "/greenhouse/0", "/greenhouse/1",
                              "/lever/0", "/lever/1", "/lever/split/0", "/lever/split/1"])
            for path in state["pages"]:
                with self.subTest(path=path):
                    with urllib.request.urlopen(f"{server.origin}{path}", timeout=5) as page:
                        self.assertIn(b"Local", page.read())

    def test_the_final_action_counter_starts_at_zero_and_reads_without_being_touched(self):
        with ReplayServer() as server:
            for _ in range(3):
                state = json.load(urllib.request.urlopen(f"{server.origin}/__state", timeout=5))
                self.assertEqual(state["final_action_activations"], 0)
            urllib.request.urlopen(f"{server.origin}/lever/1", timeout=5).read()
            state = json.load(urllib.request.urlopen(f"{server.origin}/__state", timeout=5))
            self.assertEqual(state["final_action_activations"], 0)

    def test_the_counter_actually_moves_when_the_final_action_is_activated(self):
        # Without this the zero above proves nothing: the previous oracle was a page-side
        # variable the server never read, so it reported zero unconditionally.
        with ReplayServer() as server:
            request = urllib.request.Request(f"{server.origin}/__final_action", data=b"")
            with self.assertRaises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(refused.exception.code, 403)
            state = json.load(urllib.request.urlopen(f"{server.origin}/__state", timeout=5))
            self.assertEqual(state["final_action_activations"], 1)


if __name__ == "__main__":
    unittest.main()
