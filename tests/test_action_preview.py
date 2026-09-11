"""The plan a worker would be given, checked for the things it must never contain.

A preview is read before anything runs, so most of what matters here is absence: no value, no
execution, no consumption, and no action for a field whose authority said no. The fixtures are
synthetic and the database is temporary.
"""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"action_preview_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


PREVIEW = load_script("action_preview")
ANSWERS = load_script("answer_library")
APPLICATIONS = load_script("application_core")
RESUMES = load_script("resume_core")
CANDIDATE = load_script("candidate_core")

AT = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
SNAPSHOT = "a" * 64
URL = "https://jobs.lever.co/acme/abc/apply"
SECRET_EMAIL = "do-not-leak@example.invalid"


def field(**overrides):
    base = {"field_id": "email", "raw_question": "Email✱", "match_question": "Email",
            "normalization": ["required_marker_removed"], "label_source": "lever_application_label",
            "selector": 'input[name="email"]', "control": "text", "required": True,
            "required_basis": "attribute_or_aria", "visibility": "visible",
            "grouping_basis": "single_control", "automation": "fillable",
            "canonical_id": "contact.email", "disposition": "answer", "domain": None,
            "family": None, "field_sha256": "b" * 64}
    base.update(overrides)
    return base


def observation(fields, **overrides):
    base = {"schema_version": "0.2.0", "observer_version": "lever-observer-0.1.0",
            "page_url": URL, "page_sha256": "c" * 64, "field_count": len(fields),
            "fields": fields, "values_read": False, "database_writes": 0,
            "captcha_present": True, "user_takeover_required": True}
    base.update(overrides)
    return base


class PreviewFixture(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        for module in (RESUMES, APPLICATIONS, ANSWERS, CANDIDATE):
            module.initialize(self.db)
        self.addCleanup(self.db.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.install()

    def install(self):
        """An application whose materials are locked to the active, user-registered profile."""
        self.db.execute(
            "INSERT INTO candidate_snapshots (content_sha256, profile_id, snapshot_path,"
            " file_sha256, status, registered_at, registered_by) VALUES (?, 'p', '/x', 'f',"
            " 'active', ?, 'user')", (SNAPSHOT, AT.isoformat()))
        for canonical_id, fact_id, value in (
            ("contact.email", "fact-email", SECRET_EMAIL),
            ("contact.full_name", "fact-name", "A Person"),
        ):
            self.db.execute(
                "INSERT INTO candidate_facts (content_sha256, fact_id, fact_type, value_json,"
                " status, locked, evidence_strength, fact_sha256, source_json, keywords_json,"
                " invalidation_triggers_json, canonical_id) VALUES (?, ?, 'contact', ?,"
                " 'locked', 1, 'direct', 'h', '{}', '[]', '[]', ?)",
                (SNAPSHOT, fact_id, json.dumps(value), canonical_id))
        APPLICATIONS.ingest_job(self.db, {
            "job_id": "job-1", "canonical_url": URL, "employer": "Acme",
            "title": "Analyst"}, at=AT)
        APPLICATIONS.create_application(self.db, "app-1", "job-1", at=AT)
        self.db.execute(
            "INSERT INTO resume_versions (version_id, kind, direction, status, snapshot_path,"
            " file_sha256, file_size, file_format, candidate_profile_sha256, created_at,"
            " source_mode) VALUES ('resume-1', 'direction', 'd', 'approved', '/r.pdf',"
            " 'resumehash', 1, 'pdf', ?, ?, 'user_provided')", (SNAPSHOT, AT.isoformat()))
        self.db.execute(
            "UPDATE applications SET resume_version_id='resume-1' WHERE application_id='app-1'")
        self.db.execute(
            "INSERT INTO material_locks (lock_id, application_id, resume_version_id,"
            " resume_file_sha256, locked_at) VALUES ('lock-1', 'app-1', 'resume-1',"
            " 'resumehash', ?)", (AT.isoformat(),))
        self.db.commit()

    def plan(self, fields, **kwargs):
        return PREVIEW.plan(self.db, observation(fields), "app-1", at=AT, **kwargs)


class ResolutionTest(PreviewFixture):
    def test_a_profile_field_resolves_from_the_locked_snapshot(self):
        preview = self.plan([field()])
        action = preview["actions"][0]
        self.assertEqual(action["source_kind"], "fact")
        self.assertEqual(action["source_id"], "fact-email")
        self.assertEqual(preview["binding"]["candidate_snapshot_sha256"], SNAPSHOT)

    def test_a_file_comes_only_from_the_applications_live_material_lock(self):
        preview = self.plan([field(field_id="resume", match_question="Resume/CV",
                                   control="file", automation="material",
                                   canonical_id=None, selector="#resume")])
        action = preview["actions"][0]
        self.assertEqual((action["source_kind"], action["source_id"]),
                         ("material", "resume-1"))
        self.assertEqual(action["expected_sha256"], "resumehash")
        self.assertEqual(preview["binding"]["material_lock_id"], "lock-1")

    def test_an_invalidated_lock_leaves_the_file_unplanned(self):
        self.db.execute("UPDATE material_locks SET invalidated_at=?", (AT.isoformat(),))
        self.db.commit()
        with self.assertRaises(PREVIEW.Refused):
            self.plan([field(control="file", automation="material")])

    def test_a_profile_meaning_with_no_locked_fact_is_a_named_gap(self):
        preview = self.plan([field(field_id="gh", match_question="GitHub URL",
                                   canonical_id="profile.github")])
        self.assertEqual(preview["actions"], [])
        self.assertEqual((preview["unhandled"][0]["reason"], preview["unhandled"][0]["detail"]),
                         ("profile_gap", "no_locked_fact"))

    def test_an_answer_meaning_with_nothing_confirmed_is_a_named_gap(self):
        ANSWERS.add_question_form(self.db, "work_authorized_now",
                                  "Are you authorized to work in the US?",
                                  verified_by_user=True)
        self.db.commit()
        preview = self.plan([field(field_id="auth", control="radio",
                                   match_question="Are you authorized to work in the US?",
                                   canonical_id="work_authorized_now")])
        self.assertEqual((preview["unhandled"][0]["reason"],
                          preview["unhandled"][0]["detail"]),
                         ("answer_gap", "no_confirmed_answer"))


class NeverPlannedTest(PreviewFixture):
    def test_sponsorship_never_reaches_an_action(self):
        preview = self.plan([field(
            field_id="sp", control="radio", automation="manual_only", canonical_id=None,
            match_question="Will you require work sponsorship now or in the future?")])
        self.assertEqual(preview["actions"], [])
        self.assertEqual((preview["unhandled"][0]["reason"],
                          preview["unhandled"][0]["detail"]),
                         ("manual_only", "field_policy_domain"))

    def test_a_hidden_eeo_control_never_reaches_an_action(self):
        preview = self.plan([field(field_id="race", control="select", visibility="hidden",
                                   automation="not_visible", canonical_id=None,
                                   match_question="Race")])
        self.assertEqual(preview["actions"], [])
        self.assertEqual(preview["unhandled"][0]["detail"], "hidden")

    def test_an_auxiliary_control_never_reaches_an_action(self):
        preview = self.plan([field(field_id="custom", control="checkbox",
                                   automation="unsupported_auxiliary_control",
                                   canonical_id=None, match_question="Pronouns")])
        self.assertEqual(preview["actions"], [])
        self.assertEqual(preview["unhandled"][0]["reason"], "unsupported")

    def test_every_unplanned_field_carries_an_enumerated_reason(self):
        preview = self.plan([
            field(field_id="a", automation="manual_only", canonical_id=None),
            field(field_id="b", automation="not_visible", visibility="hidden",
                  canonical_id=None),
            field(field_id="c", automation="hidden_unknown", visibility="hidden",
                  canonical_id=None, match_question=""),
            field(field_id="d", automation="unsupported_auxiliary_control", canonical_id=None),
            field(field_id="e", automation="no_canonical_meaning", canonical_id=None,
                  match_question="Something nobody reviewed"),
        ])
        self.assertEqual(len(preview["unhandled"]), 5)
        for item in preview["unhandled"]:
            self.assertIn((item["reason"], item["detail"]), PREVIEW.UNHANDLED_REASONS)


class ReviewedReasonTest(PreviewFixture):
    def test_the_reviewed_reasons_are_matched_exactly(self):
        preview = self.plan([field(field_id="loc", control="select",
                                   automation="no_canonical_meaning", canonical_id=None,
                                   match_question="Which location are you applying for?")])
        self.assertEqual((preview["unhandled"][0]["reason"],
                          preview["unhandled"][0]["detail"]),
                         ("user_input_required", "application_specific"))

    def test_a_question_nobody_reviewed_says_so_rather_than_resembling_one(self):
        """The first version of the table was written from a summary line and three of its
        five entries missed. They failed open, to `unreviewed`, which is what an exact lookup
        does when it is wrong; a fuzzy one would have matched and been wrong quietly."""
        preview = self.plan([field(field_id="x", automation="no_canonical_meaning",
                                   canonical_id=None,
                                   match_question="Which location are you applying for")])
        self.assertEqual((preview["unhandled"][0]["reason"],
                          preview["unhandled"][0]["detail"]),
                         ("unreviewed", "no_reviewed_reason"))

    def test_every_reviewed_reason_is_one_of_the_enumerated_pairs(self):
        for pair in PREVIEW.REVIEWED_UNHANDLED.values():
            self.assertIn(pair, PREVIEW.UNHANDLED_REASONS)


class NoValueAndNoExecutionTest(PreviewFixture):
    def test_no_value_appears_anywhere_in_the_preview(self):
        preview = self.plan([field(), field(field_id="name", match_question="Full name",
                                            canonical_id="contact.full_name",
                                            selector='input[name="name"]',
                                            field_sha256="d" * 64)])
        blob = json.dumps(preview)
        self.assertNotIn(SECRET_EMAIL, blob)
        self.assertNotIn("A Person", blob)
        self.assertIs(preview["values_included"], False)
        for action in preview["actions"]:
            for key in ("value", "answer", "text", "file_path"):
                self.assertNotIn(key, action)

    def test_an_action_carries_a_reference_and_a_digest_and_nothing_else(self):
        action = self.plan([field()])["actions"][0]
        self.assertEqual(set(action), {"field_id", "selector", "control", "operation",
                                       "canonical_id", "source_kind", "source_id",
                                       "source_status", "expected_sha256", "field_sha256"})
        self.assertRegex(action["expected_sha256"], r"^[0-9a-f]{64}$")

    def test_the_module_names_no_operation_it_could_perform(self):
        source = (ROOT / "skills" / "jobloom" / "scripts" / "action_preview.py").read_text(
            encoding="utf-8")
        for verb in (".fill(", ".select_option(", ".check(", ".uncheck(",
                     ".set_input_files(", ".click(", "sync_playwright", "playwright"):
            with self.subTest(verb=verb):
                self.assertNotIn(verb, source)

    def test_the_preview_says_it_did_not_execute(self):
        preview = self.plan([field()])
        self.assertIs(preview["preview"], True)
        self.assertIs(preview["executed"], False)


class BindingTest(PreviewFixture):
    def test_the_package_is_bound_to_four_things(self):
        binding = self.plan([field()])["binding"]
        self.assertEqual(binding["application_id"], "app-1")
        self.assertEqual(binding["adapter_version"], PREVIEW.ADAPTER_VERSION)
        self.assertEqual(binding["page_sha256"], "c" * 64)
        self.assertRegex(binding["field_sha256_set"], r"^[0-9a-f]{64}$")

    def test_a_page_that_gained_a_question_is_a_different_binding(self):
        one = self.plan([field()])["binding"]["field_sha256_set"]
        two = self.plan([field(), field(field_id="b", field_sha256="e" * 64)]
                        )["binding"]["field_sha256_set"]
        self.assertNotEqual(one, two)

    def test_reordering_the_same_questions_is_the_same_binding(self):
        a, b = field(), field(field_id="b", field_sha256="e" * 64)
        self.assertEqual(self.plan([a, b])["binding"]["field_sha256_set"],
                         self.plan([b, a])["binding"]["field_sha256_set"])

    def test_the_package_carries_a_single_use_nonce_and_an_expiry_but_is_not_consumed(self):
        preview = self.plan([field()])
        self.assertIs(preview["consumed"], False)
        self.assertIs(preview["single_use"], True)
        self.assertRegex(preview["nonce"], r"^[0-9a-f]{32}$")
        self.assertEqual(preview["expires_at"], (AT + PREVIEW.PACKAGE_TTL).isoformat())

    def test_two_previews_of_one_page_do_not_share_a_nonce(self):
        self.assertNotEqual(self.plan([field()])["nonce"], self.plan([field()])["nonce"])

    def test_previewing_twice_consumes_nothing_and_writes_nothing(self):
        before = self.db.total_changes
        self.plan([field()])
        self.plan([field()])
        self.assertEqual(self.db.total_changes, before)


class RefusalTest(PreviewFixture):
    def test_an_application_not_locked_to_the_active_profile_is_refused(self):
        self.db.execute("UPDATE candidate_snapshots SET status='superseded'")
        self.db.commit()
        with self.assertRaises(PREVIEW.Refused) as caught:
            self.plan([field()])
        self.assertEqual(caught.exception.code,
                         "application_materials_not_locked_to_active_profile")

    def test_an_observation_that_claims_to_have_read_values_is_refused(self):
        with self.assertRaises(PREVIEW.Refused) as caught:
            PREVIEW.plan(self.db, observation([field()], values_read=True), "app-1", at=AT)
        self.assertEqual(caught.exception.code, "observation_carries_values")

    def test_an_empty_observation_is_refused(self):
        with self.assertRaises(PREVIEW.Refused):
            PREVIEW.plan(self.db, observation([]), "app-1", at=AT)


class OutputTest(PreviewFixture):
    def test_the_preview_is_written_private(self):
        output = Path(self.temp.name) / "p" / "preview.json"
        PREVIEW.write_preview(self.plan([field()]), output)
        self.assertEqual(oct(output.stat().st_mode)[-3:], "600")

    def test_the_summary_prints_references_and_digests_and_no_value(self):
        text = PREVIEW.summarise(self.plan([field()]))
        self.assertIn("fact-email", text)
        self.assertNotIn(SECRET_EMAIL, text)


if __name__ == "__main__":
    unittest.main()
