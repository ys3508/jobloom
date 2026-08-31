import importlib.util
import json
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "skills" / "jobloom" / "scripts" / "artifact_integrity_audit.py"
SPEC = importlib.util.spec_from_file_location("artifact_integrity_audit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def synthetic_pdf(lines: list[str]) -> bytes:
    """A minimal one-page PDF whose text layer holds `lines`, one per rendered row."""
    rows = "\n".join(f"BT /F1 12 Tf 72 {720 - 18 * i} Td ({line}) Tj ET"
                     for i, line in enumerate(lines))
    stream = rows.encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref))
    return bytes(out)


class Canonicalization(unittest.TestCase):
    def test_offset_map_is_total_and_projects_back(self):
        raw = "Led  2 focus\n groups"
        canonical, mapping = MODULE.canonicalize(raw)
        self.assertEqual(canonical, "Led 2 focus groups")
        self.assertEqual(len(mapping), len(canonical))
        for index, char in enumerate(canonical):
            if char != " ":
                self.assertEqual(raw[mapping[index]], char)

    def test_whitespace_run_anchors_on_its_first_raw_character(self):
        raw = "a   b"
        canonical, mapping = MODULE.canonicalize(raw)
        self.assertEqual(canonical, "a b")
        self.assertEqual(mapping[1], 1)

    def test_character_wise_nfkc_expands_without_losing_the_map(self):
        canonical, mapping = MODULE.canonicalize("eﬃcient")
        self.assertEqual(canonical, "efficient")
        self.assertEqual(len(mapping), len(canonical))
        self.assertEqual(mapping[1], mapping[2], "both f's come from the same raw ligature")

    def test_leading_and_trailing_whitespace_is_dropped(self):
        canonical, mapping = MODULE.canonicalize("\n  R, SAS  \n")
        self.assertEqual(canonical, "R, SAS")
        self.assertEqual(mapping[0], 3)

    def test_project_rejects_an_empty_span(self):
        _, mapping = MODULE.canonicalize("abc")
        with self.assertRaises(ValueError):
            MODULE.project(1, 1, mapping)


class Occurrences(unittest.TestCase):
    def test_every_non_overlapping_occurrence_is_returned(self):
        self.assertEqual(MODULE.occurrences("R and R and R", "R"), [(0, 1), (6, 7), (12, 13)])

    def test_absent_and_empty_needles_return_nothing(self):
        self.assertEqual(MODULE.occurrences("abc", "z"), [])
        self.assertEqual(MODULE.occurrences("abc", ""), [])


class BlockProjection(unittest.TestCase):
    def test_a_claim_spanning_two_lines_binds_both_blocks(self):
        """The regression that made whole-line containment report 8 blocks instead of 39."""
        raw, blocks = MODULE.raw_blocks(["Led 2 focus groups and\nanalyzed market data\n"])
        canonical, mapping = MODULE.canonicalize(raw)
        needle, _ = MODULE.canonicalize("groups and analyzed market")
        (start, end), = MODULE.occurrences(canonical, needle)
        raw_start, raw_end = MODULE.project(start, end, mapping)
        self.assertEqual(MODULE.blocks_for_span(raw_start, raw_end, blocks), [0, 1])

    def test_two_claims_on_one_line_both_bind_that_block(self):
        raw, blocks = MODULE.raw_blocks(["R, SAS, SQL, SPSS, Python\n"])
        canonical, mapping = MODULE.canonicalize(raw)
        for term in ("SAS", "Python"):
            (start, end), = MODULE.occurrences(canonical, term)
            raw_start, raw_end = MODULE.project(start, end, mapping)
            self.assertEqual(MODULE.blocks_for_span(raw_start, raw_end, blocks), [0])

    def test_blank_lines_are_not_blocks_and_offsets_survive_pages(self):
        raw, blocks = MODULE.raw_blocks(["first\n\n", "second\n"])
        self.assertEqual([b["page"] for b in blocks], [1, 2])
        for block in blocks:
            self.assertTrue(raw[block["raw_start"]:block["raw_end"]].strip())


class AuditArtifact(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pdf = Path(self.tmp.name) / "resume.pdf"
        self.pdf.write_bytes(synthetic_pdf([
            "Jane Doe  jane@example.com",
            "EXPERIENCE",
            "Led 2 focus groups for 3 products",
            "Analyzed clinical trial data in R",
            "SKILLS: R, SAS, SQL",
        ]))

    def claim(self, claim_id, text, strength="direct"):
        return {"claim_id": claim_id, "claim_text": text, "fact_ids": ["f-1"],
                "evidence_strength": strength}

    def test_single_occurrence_binds_and_never_self_confirms(self):
        result = MODULE.audit_artifact(self.pdf, [self.claim("c-1", "Led 2 focus groups")])
        self.assertEqual(result["claims"]["single_occurrence"], 1)
        self.assertEqual(result["claims"]["unresolved"], 0)
        binding, = result["bindings"]
        self.assertEqual(binding["binding_basis"], "proposed")
        self.assertFalse(binding["review_required"])
        self.assertEqual(binding["occurrences"][0]["role"], "primary")

    def test_repeated_capability_keeps_every_occurrence_for_review(self):
        result = MODULE.audit_artifact(self.pdf, [self.claim("c-2", "R")])
        binding, = result["bindings"]
        self.assertGreater(binding["occurrence_count"], 1)
        self.assertTrue(binding["review_required"])
        self.assertEqual([o["role"] for o in binding["occurrences"]][0], "primary")
        self.assertEqual(set(o["role"] for o in binding["occurrences"][1:]), {"repeated"})
        self.assertEqual(result["claims"]["multiple_occurrences_requiring_review"], 1)

    def test_alternate_render_is_unresolved_and_only_proposed_never_accepted(self):
        result = MODULE.audit_artifact(self.pdf, [self.claim("c-3", "SKILLS | R | SAS | SQL")])
        self.assertEqual(result["claims"]["unresolved"], 1)
        candidate, = result["unresolved_candidates"]
        self.assertFalse(candidate["auto_accepted"])
        self.assertEqual(candidate["binding_basis"], "proposed")
        self.assertTrue(candidate["candidate_block_indexes"])

    def test_blocks_are_never_classified_by_the_tool(self):
        result = MODULE.audit_artifact(self.pdf, [self.claim("c-1", "Led 2 focus groups")])
        self.assertEqual(result["blocks"]["classification"], "pending_user_confirmation")
        self.assertTrue(all(b["classification"] == "pending_user_confirmation"
                            for b in result["block_inventory"]))
        self.assertTrue(all(b["classification_basis"] == "unconfirmed"
                            for b in result["block_inventory"]),
                        "the audit proposes; only a sidecar may record human_confirmed")

    def test_diagnostics_are_recorded_for_review_never_auto_benign(self):
        result = MODULE.audit_artifact(self.pdf, [self.claim("c-1", "Led 2 focus groups")])
        self.assertIn("raw_sha256", result["diagnostics"])
        self.assertTrue(all(code["status"] == "recorded_review"
                            for code in result["diagnostics"]["codes"]))

    def test_no_resume_content_leaves_the_audit_unless_asked(self):
        quiet = MODULE.audit_artifact(self.pdf, [self.claim("c-1", "Led 2 focus groups")])
        self.assertNotIn("span_text", quiet["bindings"][0]["occurrences"][0])
        self.assertNotIn("text", quiet["block_inventory"][0])
        loud = MODULE.audit_artifact(self.pdf, [self.claim("c-1", "Led 2 focus groups")],
                                     include_spans=True)
        self.assertIn("span_text", loud["bindings"][0]["occurrences"][0])
        self.assertIn("text", loud["block_inventory"][0])

    def test_machine_view_offset_map_is_total(self):
        result = MODULE.audit_artifact(self.pdf, [self.claim("c-1", "Led 2 focus groups")])
        self.assertTrue(result["machine_view"]["offset_map_total"])


class RegistryScan(unittest.TestCase):
    def build(self, versions):
        """versions: {dir_name: (artifact_filename, has_manifest, role_or_None)}"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Path(tmp.name) / "resumes"
        store.mkdir()
        rows = []
        for name, (filename, has_manifest, role) in versions.items():
            (store / name).mkdir()
            (store / name / filename).write_bytes(
                synthetic_pdf(["x"]) if filename.endswith(".pdf") else b"binary-docx-body")
            if has_manifest:
                (store / name / "claims-manifest.json").write_text(
                    json.dumps({"schema_version": "1", "claims": []}))
            if role:
                rows.append((name, *role))
        db = Path(tmp.name) / "jobloom.db"
        if rows:
            connection = sqlite3.connect(db)
            connection.execute("CREATE TABLE resume_versions (version_id TEXT, kind TEXT, "
                               "source_mode TEXT, status TEXT, candidate_profile_sha256 TEXT)")
            connection.executemany("INSERT INTO resume_versions VALUES (?,?,?,?,NULL)", rows)
            connection.commit()
            connection.close()
        return store, (db if rows else None)

    def test_an_approved_non_pdf_direction_version_is_a_format_exposure_candidate(self):
        """bind_version and lock_materials check approval, not format. Report that, not policy."""
        store, db = self.build({"docx-direction": ("resume.docx", True,
                                                   ("direction", "user_provided", "approved"))})
        row, = MODULE.scan_registry(store, db)["versions"]
        self.assertEqual(row["integrity_status"], "unsupported_format")
        self.assertTrue(row["submission_path_reachable_by_role"])
        self.assertTrue(row["format_exposure_candidate"], "unsupported_format must never read as a pass")

    def test_master_source_is_a_role_not_a_format_verdict(self):
        store, db = self.build({"master": ("resume.docx", True,
                                           ("master_source", "user_provided", "approved"))})
        row, = MODULE.scan_registry(store, db)["versions"]
        self.assertEqual(row["integrity_status"], "not_submission_artifact")
        self.assertFalse(row["submission_path_reachable_by_role"])
        self.assertFalse(row["format_exposure_candidate"])

    def test_a_revoked_version_is_not_submission_eligible(self):
        store, db = self.build({"old": ("resume.docx", True,
                                        ("direction", "user_provided", "revoked"))})
        row, = MODULE.scan_registry(store, db)["versions"]
        self.assertFalse(row["submission_path_reachable_by_role"])
        self.assertFalse(row["format_exposure_candidate"])

    def test_a_missing_manifest_is_an_explain_required_state_not_a_silent_exit(self):
        store, db = self.build({"bare": ("resume.docx", False,
                                         ("direction", "user_provided", "approved"))})
        row, = MODULE.scan_registry(store, db)["versions"]
        self.assertEqual(row["integrity_status"], "no_manifest")
        self.assertTrue(row["format_exposure_candidate"])

    def test_unknown_role_fails_closed_to_pending_never_to_a_benign_terminal(self):
        store, _ = self.build({"orphan": ("resume.pdf", True, None)})
        row, = MODULE.scan_registry(store, None)["versions"]
        self.assertEqual(row["integrity_status"], "pending_user_review")
        self.assertIsNone(row["submission_path_reachable_by_role"])

    def test_pdf_direction_awaiting_review_is_pending_and_exposed(self):
        store, db = self.build({"pdf-direction": ("resume.pdf", True,
                                                  ("direction", "user_provided", "approved"))})
        result = MODULE.scan_registry(store, db)
        row, = result["versions"]
        self.assertEqual(row["integrity_status"], "pending_user_review")
        self.assertTrue(row["format_exposure_candidate"])
        self.assertEqual(result["format_exposure_candidates"], 1)


    def test_scan_reports_format_and_hash_without_reading_a_body(self):
        store, _ = self.build({"v1": ("resume.docx", False, None)})
        row, = MODULE.scan_registry(store, None)["versions"]
        self.assertEqual(row["artifact_format"], "docx")
        self.assertEqual(len(row["artifact_sha256"]), 64)
        self.assertFalse(row["has_claims_manifest"])


class ExecutionRecord(unittest.TestCase):
    def test_the_audit_records_its_own_reproducibility_grade(self):
        record = MODULE.execution_record()
        self.assertEqual(record["byte_reproducibility"], "observation_only")
        self.assertEqual(record["canonicalizer_version"], MODULE.CANONICALIZER_VERSION)
        self.assertEqual(record["extraction_mode"], "layout")
        self.assertIn(record["extractor_version"], record["extractor_policy_id"])


if __name__ == "__main__":
    unittest.main()


class OverlapDetection(unittest.TestCase):
    def test_a_nested_span_is_not_missed_by_neighbour_comparison(self):
        """Sorted-neighbour comparison reports a green zero here; pairwise does not."""
        spans = [(0, 100, "A"), (10, 20, "B"), (30, 40, "C")]
        self.assertEqual(MODULE.count_overlaps(spans), 2)

    def test_repeated_occurrences_of_one_claim_are_not_overlaps(self):
        self.assertEqual(MODULE.count_overlaps([(0, 10, "A"), (0, 10, "A")]), 0)

    def test_touching_but_disjoint_spans_do_not_count(self):
        self.assertEqual(MODULE.count_overlaps([(0, 10, "A"), (10, 20, "B")]), 0)


class DiagnosticCodes(unittest.TestCase):
    def test_a_known_message_gets_a_stable_name(self):
        self.assertEqual(MODULE.diagnostic_code("Ignoring wrong pointing object 6 0 (offset 0)"),
                         "pypdf.wrong_pointing_object")

    def test_an_unknown_message_never_leaves_its_text_in_the_code(self):
        code = MODULE.diagnostic_code("failed on /Users/someone/private path (offset 12)")
        self.assertTrue(code.startswith("unknown:"))
        self.assertNotIn("someone", code)
        self.assertNotIn("private", code)


class ReviewPacket(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name) / "resumes"
        self.make("v1", "resume.pdf")

    def make(self, name, filename, claim="Led 2 focus groups", manifest_suffix=""):
        (self.store / name).mkdir(parents=True)
        (self.store / name / filename).write_bytes(synthetic_pdf(["Led 2 focus groups"]))
        (self.store / name / "claims-manifest.json").write_text(json.dumps(
            {"schema_version": "1" + manifest_suffix,
             "claims": [{"claim_id": "c-1", "claim_text": claim,
                         "fact_ids": ["f-1"], "evidence_strength": "direct"}]}))

    def build(self, preserve=True):
        packet = MODULE.run(self.store, None, include_spans=False, preserve=preserve)
        out = Path(self.tmp.name) / "packet"
        if preserve:
            MODULE.write_review_packet(out, packet)
        return packet, out

    def test_scan_keeps_only_hashes_and_no_packet_is_complete(self):
        packet, _ = self.build(preserve=False)
        self.assertEqual(packet["execution"]["observation_storage"], "hash_only")
        self.assertFalse(packet["execution"]["all_review_packets_complete"])
        self.assertFalse(packet["artifacts"][0]["review_packet_complete"])

    def test_a_failed_artifact_is_incomplete_while_its_neighbour_is_not_dragged_down(self):
        self.make("broken", "resume.pdf")
        (self.store / "broken" / "resume.pdf").write_bytes(b"not a pdf")
        packet, _ = self.build()
        by_status = {a["extraction_status"]: a["review_packet_complete"]
                     for a in packet["artifacts"]}
        self.assertEqual(by_status, {"ok": True, "failed": False})
        self.assertFalse(packet["execution"]["all_review_packets_complete"],
                         "a packet-level green light must not cover a failed artifact")

    def test_views_are_addressed_by_full_pdf_hash_and_policy(self):
        packet, out = self.build()
        artifact = packet["artifacts"][0]
        expected = (Path("views") / artifact["artifact_sha256"]
                    / packet["execution"]["extractor_policy_id"] / "raw-machine-view.txt")
        self.assertEqual(artifact["observation_files"]["raw"]["path"], str(expected))
        self.assertTrue((out / expected).is_file())

    def test_the_same_pdf_under_two_manifests_shares_views_and_splits_reports(self):
        self.make("v2", "resume.pdf", manifest_suffix="-alt")
        packet, out = self.build()
        self.assertEqual(len(packet["artifacts"]), 2)
        self.assertEqual(len({a["artifact_sha256"] for a in packet["artifacts"]}), 1)
        self.assertEqual(len(list((out / "views").iterdir())), 1, "one shared MachineView")
        self.assertEqual(len(list((out / "reports").rglob("audit-result.json"))), 2,
                         "two distinct reports")

    def test_one_pdf_and_manifest_under_two_snapshots_still_split_into_two_reports(self):
        """The collision: identical file, identical claims, a different candidate binding."""
        packet = MODULE.run(self.store, None, include_spans=False, preserve=True)
        artifact = packet["artifacts"][0]
        twin = json.loads(json.dumps({k: v for k, v in artifact.items() if k != "_views"}))
        twin["_views"] = artifact["_views"]
        twin["candidate_profile_sha256"] = "a-different-snapshot"
        packet["artifacts"].append(twin)
        out = Path(self.tmp.name) / "packet"
        MODULE.write_review_packet(out, packet)
        self.assertEqual(len(list((out / "views").rglob("raw-machine-view.txt"))), 1,
                         "one PDF under one policy is one observation")
        self.assertEqual(len(list((out / "reports").rglob("audit-result.json"))), 2,
                         "a different snapshot is a different report, not a collision")

    def test_observation_files_carry_their_own_hash_and_size(self):
        packet, out = self.build()
        artifact = packet["artifacts"][0]
        for name, key in (("raw", "raw_sha256"), ("canonical", "canonical_sha256")):
            entry = artifact["observation_files"][name]
            body = (out / entry["path"]).read_text()
            self.assertEqual(MODULE.sha256_text(body), entry["sha256"])
            self.assertEqual(MODULE.sha256_text(body), artifact["machine_view"][key])
            self.assertEqual(len(body.encode()), entry["size"])

    def test_a_shared_observation_whose_bytes_differ_is_an_error_not_a_merge(self):
        path = Path(self.tmp.name) / "view.txt"
        MODULE.write_shared_observation(path, "abc")
        MODULE.write_shared_observation(path, "abc")
        with self.assertRaises(ValueError):
            MODULE.write_shared_observation(path, "different")

    def test_the_index_names_every_report_and_its_decision(self):
        _, out = self.build()
        index = json.loads((out / "audit-packet.json").read_text())
        self.assertNotIn("artifacts", index)
        reference, = index["reports"]
        self.assertTrue((out / reference["report_path"]).is_file())
        self.assertTrue(reference["review_packet_complete"])

    def test_permissions_are_private_throughout(self):
        _, out = self.build()
        for path in out.rglob("*"):
            self.assertEqual(oct(path.stat().st_mode)[-3:],
                             "700" if path.is_dir() else "600", path)

    def test_a_review_packet_is_never_overwritten(self):
        _, out = self.build()
        again = MODULE.run(self.store, None, include_spans=False, preserve=True)
        with self.assertRaises(FileExistsError):
            MODULE.write_review_packet(out, again)

    def test_write_private_refuses_an_existing_file(self):
        path = Path(self.tmp.name) / "x.txt"
        MODULE.write_private(path, "a")
        with self.assertRaises(FileExistsError):
            MODULE.write_private(path, "b")

    def test_no_view_text_is_duplicated_into_the_report_json(self):
        _, out = self.build()
        index = json.loads((out / "audit-packet.json").read_text())
        stored = json.loads((out / index["reports"][0]["report_path"]).read_text())
        self.assertNotIn("_views", stored)
        self.assertNotIn("text", stored["block_inventory"][0])


class FailureIsolation(unittest.TestCase):
    def test_one_unreadable_artifact_does_not_hide_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "resumes"
            for name, body in (("good", synthetic_pdf(["Led 2 focus groups"])),
                               ("broken", b"not a pdf at all")):
                (store / name).mkdir(parents=True)
                (store / name / "resume.pdf").write_bytes(body)
                (store / name / "claims-manifest.json").write_text(json.dumps(
                    {"schema_version": "1", "claims": [
                        {"claim_id": "c-1", "claim_text": "Led 2 focus groups",
                         "fact_ids": ["f-1"], "evidence_strength": "direct"}]}))
            statuses = {a["extraction_status"] for a in
                        MODULE.run(store, None, include_spans=False, preserve=False)["artifacts"]}
            self.assertEqual(statuses, {"ok", "failed"})

    def test_a_malformed_manifest_is_isolated_with_a_stable_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "resumes"
            (store / "v1").mkdir(parents=True)
            (store / "v1" / "resume.pdf").write_bytes(synthetic_pdf(["x"]))
            (store / "v1" / "claims-manifest.json").write_text("{not json")
            artifact, = MODULE.run(store, None, include_spans=False, preserve=False)["artifacts"]
            self.assertEqual(artifact["extraction_status"], "failed")
            self.assertEqual(artifact["failure_code"], "unreadable_manifest")


class AssumptionCanary(unittest.TestCase):
    """The audit asserts a fact about resume_core. When that stops holding, fail here."""

    def body_of(self, name):
        source = (Path(__file__).parents[1] / "skills" / "jobloom" / "scripts"
                  / "resume_core.py").read_text()
        start = source.index(f"def {name}(")
        end = source.index("\ndef ", start)
        return source[start:end]

    def test_bind_and_lock_have_no_inline_artifact_format_check(self):
        """A drift reminder, not proof: a gate added via a helper would pass this."""
        for name in ("bind_version", "lock_materials"):
            body = self.body_of(name)
            self.assertNotRegex(
                body, r"\.pdf|suffix|POLICY_FORMATS|artifact_format",
                f"{name} appears to gate on artifact format now; update "
                "AUDIT_ASSUMPTIONS['format_gate_absent_in_bind_and_lock'] and the "
                "format_exposure_candidate semantics before relaxing this canary")
        self.assertIn("format_gate_absent_in_bind_and_lock", MODULE.AUDIT_ASSUMPTIONS)


class ValueComparison(unittest.TestCase):
    def test_two_different_mailboxes_are_not_a_render_difference(self):
        """Deleting every separator would merge john.smith@ and johnsmith@ into one address."""
        self.assertEqual(MODULE.compare_value("john.smith@example.com",
                                              "contact johnsmith@example.com here"), "absent")

    def test_an_email_present_verbatim_is_present(self):
        self.assertEqual(MODULE.compare_value("jane@example.com", "jane@example.com"), "present")

    def test_an_email_broken_only_by_a_zero_width_character_is_a_render_difference(self):
        self.assertEqual(MODULE.compare_value("jane@example.com", "jane@exam\u200bple.com"),
                         "render_difference")

    def test_a_phone_keeps_its_digits_across_formatting(self):
        self.assertEqual(MODULE.compare_value("+1 (555) 123-4567", "call 15551234567"),
                         "render_difference")

    def test_a_different_phone_number_is_absent(self):
        self.assertEqual(MODULE.compare_value("+1 (555) 123-4567", "call 15559999999"), "absent")

    def test_a_name_tolerates_punctuation(self):
        self.assertEqual(MODULE.compare_value("Jane Q. Doe", "Jane Q Doe"), "render_difference")

    def test_value_kind_is_decided_by_shape(self):
        self.assertEqual(MODULE.value_kind("a@b.com"), "email")
        self.assertEqual(MODULE.value_kind("(555) 123-4567"), "phone")
        self.assertEqual(MODULE.value_kind("Jane Doe"), "text")


class CompositeContactValues(unittest.TestCase):
    """One fact holding an address, a phone and a profile link is not an artifact defect."""

    LINE = ("sissi.example0123@gmail.com \u01c1 212-380-7559 \u01c1 "
            "LinkedIn: https://www.linkedin.com/in/example-person/")
    PAGE = ("| 212-380-7559 | sissi.example0123@gmail.com | linkedin.com/in/example-person |")

    def test_every_channel_is_pulled_out_of_one_value(self):
        kinds = [kind for kind, _ in MODULE.split_channels(self.LINE)]
        self.assertEqual(sorted(kinds), ["email", "phone", "url"])

    def test_a_plain_value_yields_no_channels(self):
        self.assertEqual(MODULE.split_channels("Jane Doe"), [])

    def test_a_reachable_composite_is_review_not_failure(self):
        """Compared whole it never matches; the address inside it is plainly there."""
        self.assertEqual(MODULE.compare_value(self.LINE, self.PAGE), "composite_value")

    def test_each_channel_is_judged_on_its_own_terms(self):
        channels = dict((kind, value) for kind, value in MODULE.split_channels(self.LINE))
        self.assertEqual(MODULE.compare_channel("email", channels["email"], self.PAGE), "present")
        self.assertEqual(MODULE.compare_channel("phone", channels["phone"], self.PAGE), "present")
        self.assertEqual(MODULE.compare_channel("url", channels["url"], self.PAGE),
                         "render_difference")

    def test_a_composite_with_an_unreachable_channel_is_absent(self):
        page = "| 212-380-7559 | linkedin.com/in/example-person |"
        self.assertEqual(MODULE.compare_value(self.LINE, page), "absent")

    def test_a_shortened_profile_link_is_a_render_difference(self):
        self.assertEqual(
            MODULE.compare_channel("url", "https://www.linkedin.com/in/example-person/",
                                   "linkedin.com/in/example-person"), "render_difference")

    def test_a_different_profile_link_is_absent(self):
        self.assertEqual(
            MODULE.compare_channel("url", "https://www.linkedin.com/in/example-person/",
                                   "linkedin.com/in/someone-else"), "absent")


class SnapshotResolution(unittest.TestCase):
    def build(self, *, profile_sha="sha-1", registered=True, file_present=True, tamper=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        snapshot = root / "candidate.json"
        body = json.dumps({"facts": [{"id": "f-1", "type": "contact",
                                      "value": "jane@example.com", "status": "confirmed"}]})
        if file_present:
            snapshot.write_text(body)
        stored = MODULE.sha256_bytes((b"tampered" if tamper else body.encode()))
        db = root / "jobloom.db"
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE candidate_snapshots (content_sha256 TEXT, "
                           "snapshot_path TEXT, file_sha256 TEXT, status TEXT)")
        if registered:
            connection.execute("INSERT INTO candidate_snapshots VALUES (?,?,?,?)",
                               (profile_sha, str(snapshot), stored, "superseded"))
        connection.commit()
        connection.close()
        return db

    def test_a_version_with_no_candidate_binding_is_not_judged(self):
        self.assertEqual(MODULE.resolve_snapshot(self.build(), None)["status"],
                         "no_candidate_binding")

    def test_the_snapshot_a_version_was_approved_against_is_used_not_the_active_one(self):
        """A superseded snapshot still resolves: the artifact was approved against it."""
        result = MODULE.resolve_snapshot(self.build(), "sha-1")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["snapshot_status"], "superseded")
        self.assertEqual(len(result["facts"]), 1)

    def test_an_unregistered_snapshot_is_reported_not_ignored(self):
        self.assertEqual(MODULE.resolve_snapshot(self.build(), "sha-other")["status"],
                         "snapshot_not_registered")

    def test_a_missing_snapshot_file_fails_closed(self):
        self.assertEqual(MODULE.resolve_snapshot(self.build(file_present=False), "sha-1")["status"],
                         "snapshot_file_missing")

    def test_a_tampered_snapshot_fails_closed(self):
        self.assertEqual(MODULE.resolve_snapshot(self.build(tamper=True), "sha-1")["status"],
                         "snapshot_hash_mismatch")

    def test_an_absent_registry_never_yields_facts(self):
        self.assertEqual(MODULE.resolve_snapshot(None, "sha-1"),
                         {"status": "registry_unavailable", "facts": []})


class IdentityContract(unittest.TestCase):
    FACTS = [{"id": "f-name", "type": "identity", "value": "Jane Doe", "status": "locked"},
             {"id": "f-email", "type": "contact", "value": "jane@example.com",
              "status": "confirmed"},
             {"id": "f-gone", "type": "contact", "value": "old@example.com",
              "status": "revoked"}]

    def valid(self):
        return {"schema_version": "1", "artifact_sha256": "pdf-1",
                "candidate_profile_sha256": "snap-1",
                "expected_identity_fact_ids": ["f-name"],
                "expected_contact_fact_ids": ["f-email"]}

    def build(self, contract=None, confirmation=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        version = Path(tmp.name)
        if contract is not None:
            (version / MODULE.CONTRACT_FILENAME).write_text(json.dumps(contract))
        if confirmation == "auto":
            confirmation = {"contract_sha256": MODULE.sha256_bytes(
                (version / MODULE.CONTRACT_FILENAME).read_bytes()),
                "actor": "user", "confirmed_at": "2026-08-30T00:00:00Z"}
        if confirmation is not None:
            (version / MODULE.CONFIRMATION_FILENAME).write_text(json.dumps(confirmation))
        return version

    def load(self, version):
        return MODULE.load_identity_contract(version, "pdf-1", "snap-1", self.FACTS)

    def test_a_confirmed_contract_declares_what_must_appear(self):
        result = self.load(self.build(self.valid(), "auto"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["expected"], ["f-name", "f-email"])

    def test_no_contract_disables_the_check_rather_than_widening_it(self):
        self.assertEqual(self.load(self.build())["status"], "no_contract")

    def test_a_contract_without_a_confirmation_record_is_not_trusted(self):
        """A word in a file anyone can type is not an approval."""
        self.assertEqual(self.load(self.build(self.valid()))["status"], "contract_unconfirmed")

    def test_a_self_declared_confirmation_basis_field_grants_nothing(self):
        contract = {**self.valid(), "confirmation_basis": "human_confirmed"}
        self.assertEqual(self.load(self.build(contract))["status"], "contract_unconfirmed")

    def test_editing_the_contract_after_confirmation_invalidates_it(self):
        version = self.build(self.valid(), "auto")
        edited = {**self.valid(), "expected_contact_fact_ids": []}
        (version / MODULE.CONTRACT_FILENAME).write_text(json.dumps(edited))
        self.assertEqual(self.load(version)["status"], "contract_modified_after_confirmation")

    def test_only_the_user_may_confirm(self):
        version = self.build(self.valid())
        record = {"contract_sha256": MODULE.sha256_bytes(
            (version / MODULE.CONTRACT_FILENAME).read_bytes()),
            "actor": "system", "confirmed_at": "2026-08-30T00:00:00Z"}
        (version / MODULE.CONFIRMATION_FILENAME).write_text(json.dumps(record))
        self.assertEqual(self.load(version)["status"], "confirmation_actor_not_user")

    def test_confirm_contract_writes_a_record_and_refuses_to_overwrite_it(self):
        version = self.build(self.valid())
        record = MODULE.confirm_contract(version, "pdf-1", "snap-1", self.FACTS,
                                         "user", "2026-08-30T00:00:00Z")
        self.assertEqual(record["actor"], "user")
        self.assertEqual(self.load(version)["status"], "ok")
        with self.assertRaises(FileExistsError):
            MODULE.confirm_contract(version, "pdf-1", "snap-1", self.FACTS,
                                    "user", "2026-08-31T00:00:00Z")

    def test_confirm_contract_refuses_a_non_user_actor(self):
        version = self.build(self.valid())
        with self.assertRaises(ValueError):
            MODULE.confirm_contract(version, "pdf-1", "snap-1", self.FACTS,
                                    "system", "2026-08-30T00:00:00Z")

    def test_confirm_contract_refuses_an_invalid_contract(self):
        version = self.build({**self.valid(), "expected_contact_fact_ids": []})
        with self.assertRaises(ValueError):
            MODULE.confirm_contract(version, "pdf-1", "snap-1", self.FACTS,
                                    "user", "2026-08-30T00:00:00Z")


class ContractValidation(unittest.TestCase):
    FACTS = IdentityContract.FACTS

    def check(self, **overrides):
        contract = {"artifact_sha256": "pdf-1", "candidate_profile_sha256": "snap-1",
                    "expected_identity_fact_ids": ["f-name"],
                    "expected_contact_fact_ids": ["f-email"], **overrides}
        return MODULE.validate_contract(contract, "pdf-1", "snap-1", self.FACTS)

    def test_a_well_formed_contract_validates(self):
        self.assertEqual(self.check(), "ok")

    def test_an_empty_contract_is_refused(self):
        """Declaring that nothing must appear would be the easiest green light."""
        self.assertEqual(self.check(expected_contact_fact_ids=[]),
                         "contract_no_contact_fact_declared")
        self.assertEqual(self.check(expected_identity_fact_ids=[]),
                         "contract_no_identity_fact_declared")

    def test_a_duplicated_fact_id_is_refused(self):
        self.assertEqual(self.check(expected_contact_fact_ids=["f-email", "f-email"]),
                         "contract_duplicate_fact_id")

    def test_a_fact_in_the_wrong_list_is_refused(self):
        self.assertEqual(self.check(expected_identity_fact_ids=["f-email"],
                                    expected_contact_fact_ids=["f-name"]),
                         "contract_fact_type_mismatch")

    def test_an_unknown_or_revoked_fact_is_refused(self):
        self.assertEqual(self.check(expected_contact_fact_ids=["f-missing"]),
                         "contract_fact_not_in_snapshot")
        self.assertEqual(self.check(expected_contact_fact_ids=["f-gone"]),
                         "contract_fact_not_in_snapshot")

    def test_a_wrong_artifact_or_snapshot_is_refused(self):
        self.assertEqual(self.check(artifact_sha256="pdf-2"), "contract_artifact_mismatch")
        self.assertEqual(self.check(candidate_profile_sha256="snap-2"),
                         "contract_profile_mismatch")

    def test_a_non_string_fact_id_is_refused(self):
        self.assertEqual(self.check(expected_contact_fact_ids=[7]),
                         "contract_fact_id_not_a_string")


class ProposedContracts(unittest.TestCase):
    def build(self, profile_sha="snap-1"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store = root / "resumes"
        (store / "v1").mkdir(parents=True)
        (store / "v1" / "resume.pdf").write_bytes(synthetic_pdf(["x"]))
        snapshot = root / "candidate.json"
        snapshot.write_text(json.dumps({"facts": [
            {"id": "f-name", "type": "identity", "value": "Jane Doe", "status": "locked"},
            {"id": "f-email", "type": "contact", "value": "jane@example.com", "status": "confirmed"},
            {"id": "f-old", "type": "contact", "value": "old@example.com", "status": "revoked"},
            {"id": "f-degree", "type": "education", "value": "MPH", "status": "locked"},
        ]}))
        db = root / "jobloom.db"
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE resume_versions (version_id TEXT, kind TEXT, "
                           "source_mode TEXT, status TEXT, candidate_profile_sha256 TEXT)")
        connection.execute("INSERT INTO resume_versions VALUES (?,?,?,?,?)",
                           ("v1", "direction", "user_provided", "approved", profile_sha))
        connection.execute("CREATE TABLE candidate_snapshots (content_sha256 TEXT, "
                           "snapshot_path TEXT, file_sha256 TEXT, status TEXT)")
        connection.execute("INSERT INTO candidate_snapshots VALUES (?,?,?,?)",
                           ("snap-1", str(snapshot),
                            MODULE.sha256_bytes(snapshot.read_bytes()), "active"))
        connection.commit()
        connection.close()
        return store, db

    def test_a_draft_lists_only_identity_and_contact_facts(self):
        store, db = self.build()
        draft, = MODULE.propose_contracts(store, db)
        self.assertEqual(draft["contract"]["expected_identity_fact_ids"], ["f-name"])
        self.assertEqual(draft["contract"]["expected_contact_fact_ids"], ["f-email"],
                         "a revoked fact is not proposed, and education is not identity")

    def test_a_draft_carries_no_confirmation_of_any_kind(self):
        store, db = self.build()
        draft, = MODULE.propose_contracts(store, db)
        self.assertNotIn("confirmation_basis", draft["contract"])
        self.assertFalse((store / "v1" / MODULE.CONFIRMATION_FILENAME).exists())

    def test_a_draft_binds_the_exact_artifact_and_snapshot(self):
        store, db = self.build()
        draft, = MODULE.propose_contracts(store, db)
        contract = draft["contract"]
        self.assertEqual(contract["artifact_sha256"],
                         MODULE.sha256_bytes((store / "v1" / "resume.pdf").read_bytes()))
        self.assertEqual(contract["candidate_profile_sha256"], "snap-1")
        

    def test_a_draft_a_version_cannot_resolve_carries_no_fact_ids(self):
        store, db = self.build(profile_sha="unregistered")
        draft, = MODULE.propose_contracts(store, db)
        self.assertEqual(draft["snapshot_status"], "snapshot_not_registered")
        self.assertEqual(draft["contract"]["expected_contact_fact_ids"], [])

    def test_writing_nothing_is_part_of_the_contract(self):
        store, db = self.build()
        MODULE.propose_contracts(store, db)
        self.assertFalse((store / "v1" / MODULE.CONTRACT_FILENAME).exists())


class IntegrityDecision(unittest.TestCase):
    def result(self, **overrides):
        base = {"extraction_status": "ok",
                "readability": [{"check": "text_layer", "status": "pass", "detail": {}}],
                "claims": {"unresolved": 0, "span_collision_overlap": 0}}
        return {**base, **overrides}

    def test_a_clean_artifact_is_pending_never_approved(self):
        self.assertEqual(MODULE.integrity_decision(self.result()), "pending")

    def test_a_readability_failure_blocks(self):
        blocked = self.result(readability=[{"check": "declared_values_survive_extraction",
                                            "status": "fail", "detail": {}}])
        self.assertEqual(MODULE.integrity_decision(blocked), "blocked")

    def test_a_review_item_alone_does_not_block(self):
        pending = self.result(readability=[{"check": "reading_order", "status": "user_review",
                                            "detail": {}}])
        self.assertEqual(MODULE.integrity_decision(pending), "pending")

    def test_unresolved_claims_block(self):
        self.assertEqual(
            MODULE.integrity_decision(self.result(claims={"unresolved": 14,
                                                          "span_collision_overlap": 0})), "blocked")

    def test_a_span_collision_blocks(self):
        self.assertEqual(
            MODULE.integrity_decision(self.result(claims={"unresolved": 0,
                                                          "span_collision_overlap": 1})), "blocked")

    def test_a_failed_extraction_blocks(self):
        self.assertEqual(MODULE.integrity_decision(self.result(extraction_status="failed")),
                         "blocked")


class ReadabilityChecks(unittest.TestCase):
    def checks(self, text, facts=(), snapshot_status="ok", expected=("f-1",),
               contract_status="ok"):
        snapshot = {"status": snapshot_status, "facts": list(facts)}
        contract = {"status": contract_status, "expected": list(expected), "sha256": "c" * 64}
        return {c["check"]: c
                for c in MODULE.readability_checks(text, [text], list(facts), snapshot, contract)}

    def fact(self, fact_id, kind, value, status="confirmed"):
        return {"id": fact_id, "type": kind, "value": value, "status": status}

    def test_an_empty_text_layer_fails(self):
        self.assertEqual(self.checks("")["text_layer"]["status"], "fail")

    def test_garbage_glyphs_are_detected(self):
        check = self.checks("Name (cid:42) \ufffd here")["garbage_glyphs"]
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["detail"], {"cid_marker": 1, "replacement_character": 1})

    def test_without_a_contract_nothing_is_judged_and_the_reason_is_named(self):
        check = self.checks("text", contract_status="no_contract")["declared_values_survive_extraction"]
        self.assertEqual(check["status"], "review")
        self.assertEqual(check["detail"]["reason"], "no_contract")

    def test_without_a_usable_snapshot_nothing_is_judged(self):
        check = self.checks("text", snapshot_status="snapshot_hash_mismatch")
        self.assertEqual(check["declared_values_survive_extraction"]["detail"]["reason"],
                         "snapshot_hash_mismatch")

    def test_a_composite_value_is_reported_for_review_not_as_a_failure(self):
        facts = [self.fact("f-1", "contact",
                           "jane@example.com \u01c1 212-380-7559")]
        check = self.checks("| 212-380-7559 | jane@example.com |",
                            facts)["declared_values_survive_extraction"]
        self.assertEqual(check["status"], "review")
        self.assertEqual(check["detail"]["composite_value"], 1)
        self.assertEqual(check["detail"]["absent"], 0)

    def test_a_declared_value_that_cannot_be_reached_fails(self):
        facts = [self.fact("f-1", "contact", "jane@example.com")]
        check = self.checks("Jane Doe Experience", facts)["declared_values_survive_extraction"]
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["detail"]["absent"], 1)

    def test_a_declared_value_present_verbatim_passes(self):
        facts = [self.fact("f-1", "contact", "jane@example.com")]
        check = self.checks("Jane jane@example.com", facts)["declared_values_survive_extraction"]
        self.assertEqual(check["status"], "pass")
        self.assertEqual(check["detail"]["present"], 1)

    def test_undeclared_facts_are_never_judged(self):
        """A resume legitimately omits facts it never claimed to carry."""
        facts = [self.fact("f-1", "contact", "jane@example.com"),
                 self.fact("f-2", "education", "a degree the resume left out")]
        check = self.checks("Jane jane@example.com", facts,
                            expected=("f-1",))["declared_values_survive_extraction"]
        self.assertEqual(check["status"], "pass")
        self.assertEqual(check["detail"]["declared"], 1)

    def test_a_declared_fact_absent_from_the_snapshot_is_a_review_item(self):
        check = self.checks("text", [], expected=("f-missing",))["declared_values_survive_extraction"]
        self.assertEqual(check["status"], "review")
        self.assertEqual(check["detail"]["not_in_snapshot"], 1)

    def test_unusable_fact_statuses_are_ignored(self):
        facts = [self.fact("f-1", "contact", "jane@example.com", status="revoked")]
        check = self.checks("text", facts)["declared_values_survive_extraction"]
        self.assertEqual(check["detail"]["not_in_snapshot"], 1)

    def test_reading_order_is_never_auto_passed(self):
        for text in ("", "a perfectly clean single column resume"):
            self.assertEqual(self.checks(text)["reading_order"]["status"], "user_review")

    def test_checks_are_never_aggregated_into_a_score(self):
        for check in MODULE.readability_checks("text", ["text"], [],
                                               {"status": "ok", "facts": []},
                                               {"status": "ok", "expected": []}):
            self.assertIn(check["status"], {"pass", "fail", "review", "user_review"})
            self.assertNotIn("score", check)
