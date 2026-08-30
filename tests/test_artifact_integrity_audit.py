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
                               "source_mode TEXT, status TEXT)")
            connection.executemany("INSERT INTO resume_versions VALUES (?,?,?,?)", rows)
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
        (self.store / "v1").mkdir(parents=True)
        (self.store / "v1" / "resume.pdf").write_bytes(synthetic_pdf(["Led 2 focus groups"]))
        (self.store / "v1" / "claims-manifest.json").write_text(json.dumps(
            {"schema_version": "1", "claims": [{"claim_id": "c-1", "claim_text": "Led 2 focus groups",
                                                "fact_ids": ["f-1"], "evidence_strength": "direct"}]}))

    def test_scan_keeps_only_hashes_and_is_not_admissible_for_approval(self):
        packet = MODULE.run(self.store, None, include_spans=False, preserve=False)
        self.assertEqual(packet["execution"]["observation_storage"], "hash_only")
        self.assertFalse(packet["execution"]["admissible_for_approval"])

    def test_prepare_review_preserves_the_exact_views_at_0600(self):
        packet = MODULE.run(self.store, None, include_spans=False, preserve=True)
        out = Path(self.tmp.name) / "packet"
        MODULE.write_review_packet(out, packet, packet["artifacts"])

        self.assertEqual(packet["execution"]["observation_storage"], "observation_preserved")
        self.assertTrue(packet["execution"]["admissible_for_approval"])
        self.assertEqual(oct(out.stat().st_mode)[-3:], "700")

        folder = out / packet["artifacts"][0]["artifact_sha256"][:12]
        raw = folder / "raw-machine-view.txt"
        canonical = folder / "canonical-machine-view.txt"
        for path in (raw, canonical, folder / "diagnostics.txt", out / "audit-packet.json"):
            self.assertTrue(path.is_file(), path)
            self.assertEqual(oct(path.stat().st_mode)[-3:], "600", path)

        # the preserved view is the observation the hash was taken over
        stored = json.loads((out / "audit-packet.json").read_text())["artifacts"][0]
        self.assertEqual(MODULE.sha256_text(raw.read_text()),
                         stored["machine_view"]["raw_sha256"])
        self.assertEqual(MODULE.sha256_text(canonical.read_text()),
                         stored["machine_view"]["canonical_sha256"])

    def test_a_review_packet_is_never_overwritten(self):
        packet = MODULE.run(self.store, None, include_spans=False, preserve=True)
        out = Path(self.tmp.name) / "packet"
        MODULE.write_review_packet(out, packet, packet["artifacts"])
        again = MODULE.run(self.store, None, include_spans=False, preserve=True)
        with self.assertRaises(FileExistsError):
            MODULE.write_review_packet(out, again, again["artifacts"])

    def test_write_private_refuses_an_existing_file(self):
        path = Path(self.tmp.name) / "x.txt"
        MODULE.write_private(path, "a")
        with self.assertRaises(FileExistsError):
            MODULE.write_private(path, "b")

    def test_the_packet_json_carries_no_view_text_unless_spans_were_asked_for(self):
        packet = MODULE.run(self.store, None, include_spans=False, preserve=True)
        out = Path(self.tmp.name) / "packet"
        MODULE.write_review_packet(out, packet, packet["artifacts"])
        stored = json.loads((out / "audit-packet.json").read_text())["artifacts"][0]
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

    def test_bind_and_lock_still_have_no_artifact_format_check(self):
        for name in ("bind_version", "lock_materials"):
            body = self.body_of(name)
            self.assertNotRegex(
                body, r"\.pdf|suffix|POLICY_FORMATS|artifact_format",
                f"{name} appears to gate on artifact format now; update "
                "AUDIT_ASSUMPTIONS['format_gate_absent_in_bind_and_lock'] and the "
                "format_exposure_candidate semantics before relaxing this canary")
        self.assertIn("format_gate_absent_in_bind_and_lock", MODULE.AUDIT_ASSUMPTIONS)


class ReadabilityChecks(unittest.TestCase):
    def fact(self, fact_id, kind, value, locked=True, status="locked"):
        return {"id": fact_id, "type": kind, "value": value, "locked": locked, "status": status}

    def run_checks(self, text, facts=()):
        return {c["check"]: c for c in MODULE.readability_checks(text, [text], list(facts))}

    def test_an_empty_text_layer_fails(self):
        self.assertEqual(self.run_checks("")["text_layer"]["status"], "fail")

    def test_garbage_glyphs_are_detected(self):
        checks = self.run_checks("Name (cid:42) \ufffd here")
        self.assertEqual(checks["garbage_glyphs"]["status"], "fail")
        self.assertEqual(checks["garbage_glyphs"]["detail"],
                         {"cid_marker": 1, "replacement_character": 1})

    def test_an_unreachable_contact_value_is_a_failure(self):
        """The icon-font case: the page shows an address the text layer never carries."""
        facts = [self.fact("f-1", "contact", "jane@example.com", locked=False, status="confirmed")]
        check = self.run_checks("Jane Doe  Experience", facts)["contact_survives_extraction"]
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["detail"]["absent"], 1)
        self.assertEqual(check["detail"]["render_difference"], 0)

    def test_a_separator_difference_is_review_not_failure(self):
        facts = [self.fact("f-1", "contact", "+1 (555) 123-4567", locked=False, status="confirmed")]
        check = self.run_checks("Jane Doe 15551234567", facts)["contact_survives_extraction"]
        self.assertEqual(check["status"], "review")
        self.assertEqual(check["detail"]["absent"], 0)
        self.assertEqual(check["detail"]["render_difference"], 1)

    def test_a_literal_match_passes_without_a_review_item(self):
        facts = [self.fact("f-1", "contact", "jane@example.com", locked=False, status="confirmed")]
        check = self.run_checks("Jane Doe jane@example.com", facts)["contact_survives_extraction"]
        self.assertEqual(check["status"], "pass")

    def test_an_absent_education_value_is_review_never_failure(self):
        facts = [self.fact("f-1", "education", "MPH, Somewhere University, 2021")]
        check = self.run_checks("unrelated text", facts)["education_survives_extraction"]
        self.assertEqual(check["status"], "review")

    def test_unusable_fact_statuses_are_ignored(self):
        facts = [self.fact("f-1", "education", "absent value", status="revoked")]
        self.assertNotIn("education_survives_extraction", self.run_checks("text", facts))

    def test_reading_order_is_never_auto_passed(self):
        for text in ("", "a perfectly clean single column resume"):
            self.assertEqual(self.run_checks(text)["reading_order"]["status"], "user_review")

    def test_checks_are_never_aggregated_into_a_score(self):
        checks = MODULE.readability_checks("text", ["text"], [])
        self.assertIsInstance(checks, list)
        for check in checks:
            self.assertIn(check["status"], {"pass", "fail", "review", "user_review"})
            self.assertNotIn("score", check)
