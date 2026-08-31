import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from tests.pdf_fixture import synthetic_pdf

SCRIPTS = Path(__file__).parents[1] / "skills" / "jobloom" / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


MODULE = load("machine_claim_bindings")
AUDIT = load("artifact_integrity_audit")


def packet(tmp: Path, claims):
    """A minimal review packet: one PDF, one manifest, sealed exactly as the audit seals it."""
    store = tmp / "resumes"
    (store / "v1").mkdir(parents=True)
    (store / "v1" / "resume.pdf").write_bytes(
        synthetic_pdf(["Led 2 focus groups", "Analyzed clinical data in R", "SKILLS: R, SAS"]))
    (store / "v1" / "claims-manifest.json").write_text(json.dumps(
        {"schema_version": "1", "claims": claims}))
    out = tmp / "packet"
    result = AUDIT.run(store, None, include_spans=False, preserve=True)
    AUDIT.write_review_packet(out, result)
    return out


def claim(claim_id, text, strength="direct"):
    return {"claim_id": claim_id, "claim_text": text, "fact_ids": ["f-1"],
            "evidence_strength": strength}


class SessionStart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.packet = packet(self.root, [
            claim("c-exact", "Led 2 focus groups"),
            claim("c-repeat", "R"),
            claim("c-alt", "SKILLS | R | SAS"),
        ])

    def session(self):
        return MODULE.start_session(self.packet, None, at="2026-08-30T00:00:00+00:00")

    def test_every_claim_enters_the_session_not_only_the_unresolved_ones(self):
        """Leaving the exact matches out would strand them as proposals forever."""
        session = self.session()
        self.assertEqual(set(session["decisions"]), {"c-exact", "c-repeat", "c-alt"})

    def test_an_exact_unique_match_is_deterministic_never_human_confirmed(self):
        decision = self.session()["decisions"]["c-exact"]
        self.assertEqual(decision["binding_basis"], MODULE.DETERMINISTIC)
        self.assertEqual(decision["state"], "decided")

    def test_a_repeated_claim_and_an_alternate_render_both_wait_for_a_person(self):
        decisions = self.session()["decisions"]
        self.assertEqual(decisions["c-repeat"]["state"], "pending")
        self.assertEqual(decisions["c-alt"]["state"], "pending")
        self.assertIsNone(decisions["c-alt"]["binding_basis"])

    def test_a_single_candidate_is_still_not_accepted_for_the_user(self):
        decision = self.session()["decisions"]["c-alt"]
        self.assertEqual(len(decision["candidate_block_indexes"]), 1)
        self.assertIsNone(decision["accepted_block"])
        self.assertEqual(decision["state"], "pending")

    def test_the_session_binds_the_binding_set_key_not_the_report_key(self):
        """An identity contract written later must leave confirmed bindings standing."""
        session = self.session()
        index = json.loads((self.packet / "audit-packet.json").read_text())
        reference, = index["reports"]
        self.assertEqual(session["binding_set_key"], reference["binding_set_key"])
        self.assertNotIn("report_key", session)

    def test_the_session_locks_what_it_was_reviewed_against(self):
        locked = self.session()["locked_inputs"]
        for field in ("pdf_sha256", "claims_manifest_sha256", "audit_result_sha256",
                      "raw_view_sha256", "canonical_view_sha256", "extractor_policy_id",
                      "canonicalizer_version", "audit_version", "binding_schema_version"):
            self.assertIsNotNone(locked[field], field)

    def test_a_session_never_carries_a_confirmation(self):
        body = json.dumps(self.session())
        self.assertNotIn("human_confirmed", body)
        self.assertEqual(self.session()["status"], "in_progress")

    def test_a_tampered_audit_result_makes_the_packet_stale(self):
        index = json.loads((self.packet / "audit-packet.json").read_text())
        path = self.packet / index["reports"][0]["report_path"]
        path.chmod(0o600)
        path.write_text(json.dumps({"tampered": True}))
        with self.assertRaises(ValueError):
            self.session()

    def test_a_tampered_machine_view_makes_the_packet_stale(self):
        view = next(self.packet.rglob("canonical-machine-view.txt"))
        view.chmod(0o600)
        view.write_text("something else entirely")
        with self.assertRaises(ValueError):
            self.session()


class Decisions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.packet = packet(Path(self.tmp.name), [
            claim("c-exact", "Led 2 focus groups"),
            claim("c-repeat", "R"),
            claim("c-alt", "SKILLS | R | SAS"),
        ])
        self.s = MODULE.start_session(self.packet, None, at="2026-08-30T00:00:00+00:00")

    def test_accepting_a_candidate_records_the_rest_as_rejected(self):
        block = self.s["decisions"]["c-alt"]["candidate_block_indexes"][0]
        decision = MODULE.decide(self.s, "c-alt", accept_block=block)
        self.assertEqual(decision["binding_basis"], MODULE.CONFIRMED_ALTERNATE)
        self.assertEqual(decision["accepted_block"]["block_index"], block)
        self.assertNotIn(block, decision["rejected_candidate_block_indexes"])

    def test_a_block_that_was_never_a_candidate_is_refused(self):
        with self.assertRaises(ValueError):
            MODULE.decide(self.s, "c-alt", accept_block=999)

    def test_rejecting_leaves_the_claim_unresolved_on_purpose(self):
        decision = MODULE.decide(self.s, "c-alt", reject=True)
        self.assertEqual(decision["binding_basis"], MODULE.UNRESOLVED)
        self.assertIn("c-alt", MODULE.summary(self.s)["still_unresolved_claim_ids"])

    def test_needing_more_context_is_a_recordable_answer(self):
        decision = MODULE.decide(self.s, "c-alt", needs_context=True)
        self.assertEqual(decision["binding_basis"], MODULE.NEEDS_CONTEXT)

    def test_occurrences_are_chosen_explicitly_for_a_repeated_claim(self):
        decision = MODULE.decide(self.s, "c-repeat", accept_occurrences=[0, 1], primary_occurrence=0)
        self.assertEqual(decision["binding_basis"], MODULE.CONFIRMED_OCCURRENCES)
        self.assertEqual(decision["accepted_occurrence_indexes"], [0, 1])

    def test_an_occurrence_out_of_range_is_refused(self):
        with self.assertRaises(ValueError):
            MODULE.decide(self.s, "c-repeat", accept_occurrences=[99], primary_occurrence=99)

    def test_an_exact_match_takes_no_decision(self):
        with self.assertRaises(ValueError):
            MODULE.decide(self.s, "c-exact", accept_block=0)

    def test_an_unknown_claim_is_refused(self):
        with self.assertRaises(ValueError):
            MODULE.decide(self.s, "c-nope", reject=True)


class Finalizing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.packet = packet(self.root, [
            claim("c-exact", "Led 2 focus groups"),
            claim("c-repeat", "R"),
            claim("c-alt", "SKILLS | R | SAS"),
        ])
        self.s = MODULE.start_session(self.packet, None, at="2026-08-30T00:00:00+00:00")

    def decide_all(self):
        MODULE.decide(self.s, "c-repeat", accept_occurrences=[0], primary_occurrence=0)
        MODULE.decide(self.s, "c-alt",
                      accept_block=self.s["decisions"]["c-alt"]["candidate_block_indexes"][0])

    def test_an_unfinished_session_cannot_be_confirmed(self):
        with self.assertRaises(ValueError):
            MODULE.finalize(self.s, "user", MODULE.summary_sha256(self.s))

    def test_only_the_user_may_confirm(self):
        self.decide_all()
        with self.assertRaises(ValueError):
            MODULE.finalize(self.s, "system", MODULE.summary_sha256(self.s))

    def test_confirmation_is_against_the_exact_summary_hash(self):
        self.decide_all()
        stale = MODULE.summary_sha256(self.s)
        MODULE.decide(self.s, "c-alt", reject=True)
        with self.assertRaises(ValueError):
            MODULE.finalize(self.s, "user", stale)

    def test_the_confirmed_set_covers_every_claim_with_its_own_basis(self):
        self.decide_all()
        record = MODULE.finalize(self.s, "user", MODULE.summary_sha256(self.s),
                                 at="2026-08-30T01:00:00+00:00")
        bases = {b["claim_id"]: b["binding_basis"] for b in record["bindings"]}
        self.assertEqual(bases, {"c-exact": MODULE.DETERMINISTIC,
                                 "c-repeat": MODULE.CONFIRMED_OCCURRENCES,
                                 "c-alt": MODULE.CONFIRMED_ALTERNATE})
        self.assertEqual(record["claims_total"], 3)

    def test_the_summary_carries_no_resume_text(self):
        self.decide_all()
        body = json.dumps(MODULE.summary(self.s))
        for word in ("focus", "clinical", "SKILLS", "SAS"):
            self.assertNotIn(word, body)

    def test_a_confirmed_sidecar_is_private_and_never_overwritten(self):
        self.decide_all()
        record = MODULE.finalize(self.s, "user", MODULE.summary_sha256(self.s))
        out = self.root / "bindings.json"
        MODULE.write_private(out, json.dumps(record))
        self.assertEqual(oct(out.stat().st_mode)[-3:], "600")
        with self.assertRaises(FileExistsError):
            MODULE.write_private(out, json.dumps(record))

    def test_a_rejected_claim_is_named_in_the_confirmed_set(self):
        MODULE.decide(self.s, "c-repeat", accept_occurrences=[0], primary_occurrence=0)
        MODULE.decide(self.s, "c-alt", reject=True)
        record = MODULE.finalize(self.s, "user", MODULE.summary_sha256(self.s))
        self.assertEqual(record["still_unresolved_claim_ids"], ["c-alt"])

    def test_the_confirmed_set_keeps_the_binding_set_key(self):
        self.decide_all()
        record = MODULE.finalize(self.s, "user", MODULE.summary_sha256(self.s))
        self.assertEqual(record["binding_set_key"], self.s["binding_set_key"])


class SummaryCommitment(unittest.TestCase):
    """The hash a user confirms must move when their answer moves."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.packet = packet(Path(self.tmp.name), [
            claim("c-alt", "SKILLS | R | SAS"), claim("c-repeat", "R")])

    def session(self):
        return MODULE.start_session(self.packet, None, at="2026-08-30T00:00:00+00:00")

    def test_choosing_a_different_block_changes_the_summary_hash(self):
        first, second = self.session(), self.session()
        candidates = first["decisions"]["c-alt"]["candidate_block_indexes"]
        if len(candidates) < 2:
            block_a = candidates[0]
            MODULE.decide(first, "c-alt", accept_block=block_a)
            MODULE.decide(second, "c-alt", reject=True)
        else:
            MODULE.decide(first, "c-alt", accept_block=candidates[0])
            MODULE.decide(second, "c-alt", accept_block=candidates[1])
        self.assertNotEqual(MODULE.summary_sha256(first), MODULE.summary_sha256(second))

    def test_choosing_different_occurrences_changes_the_summary_hash(self):
        first, second = self.session(), self.session()
        MODULE.decide(first, "c-repeat", accept_occurrences=[0], primary_occurrence=0)
        MODULE.decide(second, "c-repeat", accept_occurrences=[1], primary_occurrence=1)
        self.assertNotEqual(MODULE.summary_sha256(first), MODULE.summary_sha256(second))

    def test_choosing_a_different_primary_changes_the_summary_hash(self):
        first, second = self.session(), self.session()
        MODULE.decide(first, "c-repeat", accept_occurrences=[0, 1], primary_occurrence=0)
        MODULE.decide(second, "c-repeat", accept_occurrences=[0, 1], primary_occurrence=1)
        self.assertNotEqual(MODULE.summary_sha256(first), MODULE.summary_sha256(second))

    def test_the_commitment_records_where_an_accepted_block_is(self):
        session = self.session()
        block = session["decisions"]["c-alt"]["candidate_block_indexes"][0]
        MODULE.decide(session, "c-alt", accept_block=block)
        commitment, = [c for c in MODULE.summary(session)["decision_commitments"]
                       if c["claim_id"] == "c-alt"]
        accepted = commitment["accepted_block"]
        for field in ("block_index", "page", "raw_start", "raw_end", "anchor_sha256"):
            self.assertIsNotNone(accepted[field], field)
        self.assertEqual(accepted["binding_granularity"], "block",
                         "a block is not the claim's exact span and must not pass for one")

    def test_the_primary_must_be_one_of_the_accepted_occurrences(self):
        session = self.session()
        with self.assertRaises(ValueError):
            MODULE.decide(session, "c-repeat", accept_occurrences=[0], primary_occurrence=1)

    def test_accepting_occurrences_without_naming_a_primary_is_refused(self):
        session = self.session()
        with self.assertRaises(ValueError):
            MODULE.decide(session, "c-repeat", accept_occurrences=[0])

    def test_a_summary_carries_no_resume_text(self):
        session = self.session()
        MODULE.decide(session, "c-alt",
                      accept_block=session["decisions"]["c-alt"]["candidate_block_indexes"][0])
        body = json.dumps(MODULE.summary(session))
        for word in ("focus", "clinical", "SKILLS", "SAS"):
            self.assertNotIn(word, body)


class BindingSetCompleteness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.packet = packet(Path(self.tmp.name), [claim("c-alt", "SKILLS | R | SAS")])
        self.s = MODULE.start_session(self.packet, None, at="2026-08-30T00:00:00+00:00")

    def test_an_answered_but_unbound_claim_leaves_the_set_incomplete(self):
        """Every question answered is not every claim bound."""
        MODULE.decide(self.s, "c-alt", reject=True)
        record = MODULE.finalize(self.s, "user", MODULE.summary_sha256(self.s))
        self.assertEqual(record["review_status"], "human_confirmed")
        self.assertEqual(record["binding_set_status"], "incomplete")
        self.assertFalse(record["usable_for_integrity"])
        self.assertEqual(record["still_unresolved_claim_ids"], ["c-alt"])

    def test_needing_context_also_leaves_the_set_incomplete(self):
        MODULE.decide(self.s, "c-alt", needs_context=True)
        record = MODULE.finalize(self.s, "user", MODULE.summary_sha256(self.s))
        self.assertEqual(record["binding_set_status"], "incomplete")

    def test_a_fully_bound_set_is_complete_and_usable(self):
        MODULE.decide(self.s, "c-alt",
                      accept_block=self.s["decisions"]["c-alt"]["candidate_block_indexes"][0])
        record = MODULE.finalize(self.s, "user", MODULE.summary_sha256(self.s))
        self.assertEqual(record["binding_set_status"], "complete")
        self.assertTrue(record["usable_for_integrity"])


class SessionStateGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.packet = packet(Path(self.tmp.name), [claim("c-alt", "SKILLS | R | SAS")])
        self.s = MODULE.start_session(self.packet, None, at="2026-08-30T00:00:00+00:00")
        MODULE.decide(self.s, "c-alt", reject=True)

    def test_a_confirmed_session_cannot_be_decided_again(self):
        MODULE.finalize(self.s, "user", MODULE.summary_sha256(self.s))
        self.s["status"] = "confirmed"
        with self.assertRaises(ValueError):
            MODULE.decide(self.s, "c-alt", reject=True)

    def test_a_confirmed_session_cannot_be_confirmed_again(self):
        self.s["status"] = "confirmed"
        with self.assertRaises(ValueError):
            MODULE.finalize(self.s, "user", MODULE.summary_sha256(self.s))


class MultiReportResume(unittest.TestCase):
    def test_a_session_resumes_from_a_packet_holding_more_than_one_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "resumes"
            for name, text in (("v1", "Led 2 focus groups"), ("v2", "Analyzed clinical data in R")):
                (store / name).mkdir(parents=True)
                (store / name / "resume.pdf").write_bytes(
                    synthetic_pdf(["Led 2 focus groups", "Analyzed clinical data in R"]))
                (store / name / "claims-manifest.json").write_text(json.dumps(
                    {"schema_version": name, "claims": [claim("c-1", text)]}))
            out = root / "packet"
            AUDIT.write_review_packet(out, AUDIT.run(store, None, include_spans=False,
                                                     preserve=True))
            index = json.loads((out / "audit-packet.json").read_text())
            self.assertEqual(len(index["reports"]), 2)

            with self.assertRaises(ValueError):
                MODULE.start_session(out, None)
            key = index["reports"][0]["report_key"]
            session = MODULE.start_session(out, key, at="2026-08-30T00:00:00+00:00")
            self.assertEqual(session["source_report_key"], key)
            MODULE.verify_session(session, out)


class Resuming(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.packet = packet(self.root, [claim("c-alt", "SKILLS | R | SAS")])
        self.s = MODULE.start_session(self.packet, None, at="2026-08-30T00:00:00+00:00")

    def test_a_session_survives_a_round_trip_through_disk(self):
        path = self.root / "session.json"
        MODULE.write_private(path, json.dumps(self.s))
        MODULE.replace_private(path, json.dumps(self.s))
        self.assertEqual(oct(path.stat().st_mode)[-3:], "600")
        self.assertEqual(MODULE.read_session(path)["binding_set_key"], self.s["binding_set_key"])

    def test_resuming_rechecks_the_packet(self):
        MODULE.verify_session(self.s, self.packet)
        index = json.loads((self.packet / "audit-packet.json").read_text())
        path = self.packet / index["reports"][0]["report_path"]
        path.chmod(0o600)
        path.write_text(json.dumps({"tampered": True}))
        with self.assertRaises(ValueError):
            MODULE.verify_session(self.s, self.packet)

    def test_progress_counts_what_is_left(self):
        self.assertEqual(MODULE.progress(self.s), {"total": 1, "decided": 0, "pending": 1})
        MODULE.decide(self.s, "c-alt", reject=True)
        self.assertEqual(MODULE.progress(self.s), {"total": 1, "decided": 1, "pending": 0})


if __name__ == "__main__":
    unittest.main()
