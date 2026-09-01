"""The one gate that decides whether an artifact may become an application material.

`known-liabilities.md` recorded the open path: registration accepts `.pdf`, `.docx`, `.txt`
and `.md`, and neither `bind_version` nor `lock_materials` looked at the format, so an
approved DOCX reached `fill_core._plan_upload` as the file an employer would receive. These
tests cover the three enforcement points together, because shipping only one of them would
have let a DOCX bind and lock and then fail at upload, after the material lock recorded it.
"""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"material_format_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


RESUMES = load_script("resume_core")
COVERS = load_script("cover_letter_core")
APPLICATIONS = load_script("application_core")
ANSWERS = load_script("answer_library")
PRE_SUBMIT = load_script("pre_submit_core")
CANDIDATES = load_script("candidate_core")
FILL = load_script("fill_core")

from tests.pdf_fixture import synthetic_pdf

AT = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)

# A renamed DOCX is a ZIP. The suffix cannot tell them apart, which is why the gate reads
# the leading bytes as well.
DOCX_BYTES = b"PK\x03\x04" + b"\x00" * 64


class ApplicationMaterialFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.store = self.root / "store"
        self.cover_store = self.root / "cover-letters"
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        APPLICATIONS.initialize(self.db)
        RESUMES.initialize(self.db)
        COVERS.initialize(self.db)
        ANSWERS.initialize(self.db)
        PRE_SUBMIT.initialize(self.db)
        CANDIDATES.initialize(self.db)
        self.addCleanup(self.db.close)
        self.candidate_path, self.manifest_path = self.candidate_and_manifest()
        CANDIDATES.register_snapshot(self.db, self.root / "candidates", self.candidate_path, "user", AT)

    # ---- fixtures ------------------------------------------------------

    def candidate_and_manifest(self):
        candidate = {
            "schema_version": "0.2.0", "profile_id": "candidate-1",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": False, "employer_action_required": False, "confirmed": True,
            },
            "search": {},
            "facts": [{"id": "fact-1", "type": "skill", "value": "Python",
                       "status": "confirmed", "locked": False, "evidence_strength": "direct"}],
        }
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        candidate_path = self.root / "candidate.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps({"schema_version": "0.1.0", "claims": [{
            "claim_id": "claim-1", "claim_text": "Python", "fact_ids": ["fact-1"],
            "evidence_strength": "direct", "exact_locked_value_preserved": True,
        }]}), encoding="utf-8")
        return candidate_path, manifest_path

    def source(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def approved_resume(self, version_id="resume-1", name="resume.pdf", data=None):
        source = self.source(name, synthetic_pdf(["Python"]) if data is None else data)
        RESUMES.register_version(self.db, self.store, source, version_id, "master_source", "general", at=AT)
        RESUMES.approve_version(self.db, version_id, self.candidate_path, self.manifest_path, "user", AT)
        return version_id

    def approved_cover(self, version_id="cover-1", name="cover-letter.pdf", data=None):
        source = self.source(name, synthetic_pdf(["Python for Example"]) if data is None else data)
        COVERS.register_version(self.db, self.cover_store, source, version_id,
                                "reusable_template", None, None, AT)
        COVERS.approve_version(self.db, version_id, self.candidate_path, self.manifest_path, "user", AT)
        return version_id

    def application_in_materials(self, application_id="app-1"):
        APPLICATIONS.ingest_job(self.db, {
            "job_id": "job-1", "canonical_url": "https://example.com/job/1", "employer": "Example",
            "title": "Engineer", "location": "New York", "status": "open",
        }, at=AT)
        APPLICATIONS.create_application(self.db, application_id, "job-1", at=AT)
        APPLICATIONS.transition(self.db, application_id, "pending_analysis", "system", "analysis", at=AT)
        APPLICATIONS.transition(self.db, application_id, "broad_recommended", "system", "match", at=AT)
        APPLICATIONS.transition(self.db, application_id, "approved", "user", "approved", at=AT)
        APPLICATIONS.transition(self.db, application_id, "materials_in_progress", "system", "materials", at=AT)
        return application_id

    # ---- the gate ------------------------------------------------------

    def test_valid_pdf_binds_locks_and_plans_an_upload(self):
        self.approved_resume()
        self.approved_cover()
        self.application_in_materials()
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        COVERS.bind_version(self.db, "app-1", "cover-1", at=AT)
        lock = RESUMES.lock_materials(self.db, "app-1", "user", "lock-1", at=AT)
        self.assertEqual(lock["lock_id"], "lock-1")
        session = {"application_id": "app-1"}
        kind, version_id, upload, digest = FILL._plan_upload(self.db, session, "resume")
        self.assertEqual((kind, version_id), ("resume", "resume-1"))
        self.assertTrue(upload["path"].endswith(".pdf"))
        self.assertEqual(digest, upload["file_sha256"])
        cover_kind, cover_version, _, _ = FILL._plan_upload(self.db, session, "cover_letter")
        self.assertEqual((cover_kind, cover_version), ("cover_letter", "cover-1"))

    def test_pdf_suffix_holding_docx_bytes_is_rejected(self):
        self.approved_resume(name="renamed.pdf", data=DOCX_BYTES)
        self.application_in_materials()
        with self.assertRaisesRegex(ValueError, "is not a PDF file"):
            RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)

    def test_pdf_bytes_under_a_docx_suffix_are_rejected(self):
        self.approved_resume(name="resume.docx", data=synthetic_pdf(["Python"]))
        self.application_in_materials()
        with self.assertRaisesRegex(ValueError, "must be a .pdf file"):
            RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)

    def test_docx_is_rejected_before_a_material_lock_exists(self):
        self.approved_resume(name="resume.docx", data=DOCX_BYTES)
        application_id = self.application_in_materials()
        with self.assertRaises(ValueError):
            RESUMES.bind_version(self.db, application_id, "resume-1", at=AT)
        with self.assertRaisesRegex(ValueError, "no resume version is bound"):
            RESUMES.lock_materials(self.db, application_id, "user", "lock-1", at=AT)
        self.assertIsNone(self.db.execute(
            "SELECT lock_id FROM material_locks WHERE application_id=?", (application_id,)
        ).fetchone())

    def test_master_source_docx_registers_but_cannot_be_selected_for_submission(self):
        # Registration formats are deliberately unchanged: the DOCX master source is the
        # canonical career record and must stay registrable.
        registered = self.approved_resume(name="master.docx", data=DOCX_BYTES)
        self.assertEqual(self.db.execute(
            "SELECT status FROM resume_versions WHERE version_id=?", (registered,)
        ).fetchone()["status"], "approved")
        self.application_in_materials()
        with self.assertRaisesRegex(ValueError, "must be a .pdf file"):
            RESUMES.bind_version(self.db, "app-1", registered, at=AT)

    def test_cover_letter_must_be_a_pdf_to_bind(self):
        self.approved_resume()
        self.approved_cover(name="cover-letter.docx", data=DOCX_BYTES)
        self.application_in_materials()
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        with self.assertRaisesRegex(ValueError, "cover letter bound to an application must be a .pdf file"):
            COVERS.bind_version(self.db, "app-1", "cover-1", at=AT)

    def test_lock_and_upload_planning_refuse_a_non_pdf_independently_of_binding(self):
        # Binding is the first gate, so these two would never fire in a healthy sequence.
        # They are tested anyway because the liability's whole point was that shipping only
        # one third of the gate lets a DOCX bind and lock before anything refuses it. The
        # snapshot is re-pointed at a `.docx` holding the same approved bytes, so the hash
        # check passes and only the format gate can refuse.
        self.approved_resume()
        self.application_in_materials()
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        approved = Path(self.db.execute(
            "SELECT snapshot_path FROM resume_versions WHERE version_id='resume-1'"
        ).fetchone()["snapshot_path"])
        disguised = self.root / "same-bytes.docx"
        disguised.write_bytes(approved.read_bytes())
        self.db.execute("UPDATE resume_versions SET snapshot_path=? WHERE version_id='resume-1'",
                        (str(disguised),))
        with self.assertRaisesRegex(ValueError, "must be a .pdf file"):
            RESUMES.lock_materials(self.db, "app-1", "user", "lock-1", at=AT)

    def test_upload_planning_refuses_a_material_whose_snapshot_stopped_being_a_pdf(self):
        self.approved_resume()
        self.application_in_materials()
        RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)
        RESUMES.lock_materials(self.db, "app-1", "user", "lock-1", at=AT)
        approved = Path(self.db.execute(
            "SELECT snapshot_path FROM resume_versions WHERE version_id='resume-1'"
        ).fetchone()["snapshot_path"])
        disguised = self.root / "planned.docx"
        disguised.write_bytes(approved.read_bytes())
        self.db.execute("UPDATE resume_versions SET snapshot_path=? WHERE version_id='resume-1'",
                        (str(disguised),))
        with self.assertRaisesRegex(ValueError, "must be a .pdf file"):
            FILL._plan_upload(self.db, {"application_id": "app-1"}, "resume")

    def test_replacing_the_approved_file_still_fails_hash_validation(self):
        # The format gate is added to hash validation, not in place of it: a PDF swapped for
        # another valid PDF passes the format check and must still be refused.
        self.approved_resume()
        self.application_in_materials()
        snapshot = Path(self.db.execute(
            "SELECT snapshot_path FROM resume_versions WHERE version_id='resume-1'"
        ).fetchone()["snapshot_path"])
        snapshot.chmod(0o600)
        snapshot.write_bytes(synthetic_pdf(["Replaced after approval"]))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            RESUMES.bind_version(self.db, "app-1", "resume-1", at=AT)


if __name__ == "__main__":
    unittest.main()
