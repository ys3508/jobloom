"""Confirming who you are, and turning that into facts a form planner may fill from.

The Candidate Profile had a read path and no write path: `resolve_canonical_fact` knew how to
find the one locked fact that means `contact.email`, and nothing in the repository had ever
created one. These are the terms of the missing half - proposing the fields, recording the two
separate confirmations, drafting one snapshot, previewing what activating it would invalidate,
and switching atomically or not at all.

Every value here is visibly synthetic and every path is a temporary directory. Nothing reaches
the private root, and a test at the bottom checks this file for the shape of a path that would.
"""

import copy
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "jobloom" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(f"profile_intake_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


PROFILE = load("candidate_profile")
CANDIDATES = load("candidate_core")
RESUMES = load("resume_core")
APPLICATIONS = load("application_core")
PRE_SUBMIT = load("pre_submit_core")
ANSWERS = load("answer_library")

AT = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
COMPOSITE = ("probe@example.invalid ǁ 555-0100 ǁ "
             "LinkedIn: https://example.invalid/in/probe")
PIECES = ("probe@example.invalid", "555-0100", "example.invalid/in/probe")
ROUND = "onboarding-v1"
# The whole round, in the order the resolver sorts it. Named for what it is rather than for
# its size, because the size is now a property of the reviewed screens.
NINE = tuple(sorted(PROFILE.PROFILE_ROUNDS[ROUND]))
# What the user types for the six nothing can propose. Visibly synthetic, and never a value
# any code under test could have derived from the composite.
STATED = {
    "contact.email": "stated@example.invalid",
    "contact.phone": "555-0199",
    "profile.linkedin": "https://example.invalid/in/stated",
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


class ProfileIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        self.store = self.root / "candidates"
        self.db = sqlite3.connect(str(self.root / "profile.db"))
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        for module in (RESUMES, APPLICATIONS, PRE_SUBMIT, ANSWERS, PROFILE):
            module.initialize(self.db)
        self.base = self.register_snapshot([
            {"id": "fact-0002", "type": "contact", "value": COMPOSITE,
             "status": "confirmed", "locked": False, "evidence_strength": "direct"}])
        self.bind_a_resume(self.base)

    # ---- fixtures --------------------------------------------------------------

    def register_snapshot(self, facts, at=AT):
        candidate = {
            "schema_version": "0.2.0", "profile_id": "probe",
            "work_authorization": {"country": "US", "authorized_now": True,
                                   "sponsorship_now": False, "sponsorship_future": False,
                                   "employer_action_required": False, "confirmed": True},
            "search": {}, "facts": facts}
        candidate["content_sha256"] = RESUMES.canonical_hash(candidate)
        path = self.root / f"candidate-{candidate['content_sha256'][:12]}.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        CANDIDATES.register_snapshot(self.db, self.store, path, "user", at)
        return candidate["content_sha256"]

    def bind_a_resume(self, snapshot_sha256):
        """A resume bound to the active snapshot, locked to an application, with a review.

        Inserted directly. Driving `resume_core` would mean a real PDF, an approval and an
        authorization to build a fixture for a preview that reads three columns; what this
        stands in for is the shape of those rows, and the columns the preview reads are named
        here in full.
        """
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
        self.db.execute(
            "INSERT INTO pre_submit_reviews (review_id, application_id, inventory_id, "
            "authorization_id, material_lock_id, summary_json, summary_sha256, status, "
            "created_at) VALUES ('review-1', 'app-1', 'inv-1', 'auth-1', 'lock-1', '{}', 's', "
            "'approved', ?)", (AT.isoformat(),))
        self.db.commit()

    # ---- driving the loop ------------------------------------------------------

    def propose(self, round_name=ROUND):
        return PROFILE.propose_profile(self.db, round_name, AT)

    def worksheet(self, confirm=NINE, autofill=NINE, **edits):
        sheet = copy.deepcopy(self.propose()["worksheet"])
        for entry in sheet["entries"]:
            canonical_id = entry["canonical_id"]
            if entry["value"] is None:
                entry["value"] = STATED.get(canonical_id)
            entry["confirmed_by_user"] = canonical_id in confirm
            entry["autofill_allowed_by_user"] = canonical_id in autofill
            entry.update(edits.get(canonical_id, {}))
        return sheet

    def confirm(self, sheet=None):
        return PROFILE.confirm_profile(self.db, sheet or self.worksheet(), self.private, AT)

    def register(self, draft_sha256, actor="user", at=None):
        return PROFILE.register_profile(self.db, draft_sha256, self.store, actor, at or AT)

    def snapshots(self):
        return [tuple(row) for row in self.db.execute(
            "SELECT content_sha256, status FROM candidate_snapshots ORDER BY content_sha256")]

    def active(self):
        return self.db.execute(
            "SELECT content_sha256 FROM candidate_snapshots WHERE status='active'").fetchone()[0]

    def resolvable(self, snapshot_sha256=None):
        snapshot_sha256 = snapshot_sha256 or self.active()
        return sorted(
            canonical_id for canonical_id in PROFILE.PROFILE_V1
            if PROFILE.resolve_canonical_fact(self.db, canonical_id, snapshot_sha256)[0])

    def unchanged(self):
        return (self.snapshots(),
                [tuple(row) for row in self.db.execute(
                    "SELECT content_sha256, fact_id, canonical_id, status FROM candidate_facts "
                    "ORDER BY content_sha256, fact_id")],
                [tuple(row) for row in self.db.execute(
                    "SELECT proposal_id, status FROM profile_proposals ORDER BY proposal_id")],
                [tuple(row) for row in self.db.execute(
                    "SELECT draft_sha256, status FROM profile_drafts ORDER BY draft_sha256")])

    def refuse(self, call, pattern):
        before = self.unchanged()
        with self.assertRaisesRegex(ValueError, pattern) as caught:
            call()
        self.assertEqual(self.unchanged(), before)
        for piece in PIECES:
            self.assertNotIn(piece, str(caught.exception))


class RoundTests(ProfileIntakeTests):
    def test_the_round_clears_the_measured_floor(self):
        """A screen may add a field the corpus never asked for. It may not drop one it did.

        The floor is derived - corpus demand, required wherever recorded - so it tracks the
        measurement; the round is the reviewed screens, so it can carry what the user needs as
        well. This is the join: every measured requirement is on some screen.
        """
        self.assertTrue(PROFILE.CORPUS_REQUIRED <= PROFILE.PROFILE_ROUNDS[ROUND])
        self.assertEqual(len(PROFILE.CORPUS_REQUIRED), 9)

    def test_every_field_a_screen_asks_for_is_a_real_profile_field(self):
        every = set().union(*PROFILE.PROFILE_ROUNDS.values())
        for canonical_id in every:
            self.assertIn(canonical_id, PROFILE.PROFILE_V1, canonical_id)
        self.assertFalse(every & set(PROFILE.PROFILE_V1_DEFERRED))
        self.assertFalse(every & PROFILE.FORBIDDEN_MEANINGS)

    def test_no_screen_names_a_field_twice_and_none_is_left_off_one(self):
        seen = [canonical_id for _, fields in PROFILE.PROFILE_SCREENS
                for canonical_id in fields]
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(set(seen), PROFILE.PROFILE_ROUNDS[ROUND])

    def test_forbidden_deferred_and_unknown_meanings_are_refused(self):
        for canonical_id, pattern in (
                ("authorization.sponsorship_status", "not profile data"),
                ("work_authorized_now", "not profile data"),
                ("sponsorship_now", "not profile data"),
                ("sponsorship_future", "not profile data"),
                ("employer_action_required", "not profile data"),
                ("eeo.race", "not profile data"),
                ("profile.location_url", "unclear"),
                ("contact.favourite_colour", "no such profile field")):
            with self.assertRaisesRegex(ValueError, pattern):
                PROFILE.require_proposable(canonical_id)

    def test_every_immigration_meaning_the_library_knows_is_forbidden_here(self):
        """Pinned across the two modules, so a fifth one cannot arrive profile-eligible.

        The four are never interchangeable, which is the rule this repository has already paid
        for once. Whichever module a new one is added to, this is where it is caught.
        """
        self.assertTrue(ANSWERS.IMMIGRATION_CANONICAL_IDS <= PROFILE.FORBIDDEN_MEANINGS)

    def test_only_a_reviewed_round_may_be_proposed(self):
        self.refuse(lambda: self.propose("everything"), "no such reviewed round")


class ProposingTests(ProfileIntakeTests):
    def test_proposing_reads_the_composite_and_writes_nothing(self):
        result = self.propose()
        self.assertEqual(result["summary"]["proposed"], 3)
        self.assertEqual(result["summary"]["canonical_ids"], sorted(NINE))
        self.assertEqual(self.snapshots(), [(self.base, "active")])
        self.assertEqual(self.resolvable(), [])

    def test_nothing_is_derived(self):
        """A full name is not first plus last, and a country code is not read off a number.

        Both are joinable from what the composite holds, which is exactly why the rule has to
        be tested rather than assumed: the cheapest wrong thing here is to offer a plausible
        value the user then waves through.
        """
        proposed = {entry["canonical_id"]: entry["value"]
                    for entry in self.propose()["worksheet"]["entries"]}
        self.assertEqual(sorted(k for k, v in proposed.items() if v is not None),
                         ["contact.email", "contact.phone", "profile.linkedin"])
        for canonical_id in ("contact.full_name", "contact.first_name", "contact.last_name",
                             "contact.phone_country", "contact.location",
                             "contact.location_city"):
            self.assertIsNone(proposed[canonical_id], canonical_id)

    def test_a_meaning_two_facts_could_mean_is_not_proposed(self):
        self.register_snapshot([
            {"id": "fact-0002", "type": "contact", "value": COMPOSITE,
             "status": "confirmed", "locked": False, "evidence_strength": "direct"},
            {"id": "fact-0003", "type": "identity", "value": "other@example.invalid",
             "status": "confirmed", "locked": False, "evidence_strength": "direct"}])
        entry = {e["canonical_id"]: e for e in self.propose()["worksheet"]["entries"]}
        self.assertIsNone(entry["contact.email"]["value"])
        self.assertIn("more than one fact", entry["contact.email"]["value_source"])
        self.assertEqual(entry["contact.email"]["source_fact_ids"], [])
        # The unambiguous ones are unaffected.
        self.assertIsNotNone(entry["contact.phone"]["value"])

    def test_the_summary_never_carries_a_value(self):
        rendered = json.dumps(self.propose()["summary"], ensure_ascii=False)
        for piece in PIECES:
            self.assertNotIn(piece, rendered)

    def test_the_worksheet_says_where_each_value_came_from(self):
        entry = {e["canonical_id"]: e for e in self.propose()["worksheet"]["entries"]}
        self.assertEqual(entry["contact.email"]["source_fact_ids"], ["fact-0002"])
        self.assertIn("check it", entry["contact.email"]["value_source"])
        self.assertEqual(entry["contact.full_name"]["source_fact_ids"], [])
        self.assertIn("you state it", entry["contact.full_name"]["value_source"])


class ConfirmingTests(ProfileIntakeTests):
    def test_confirmation_drafts_a_snapshot_and_activates_nothing(self):
        result = self.confirm()
        self.assertFalse(result["registered"])
        self.assertEqual(self.snapshots(), [(self.base, "active")])
        self.assertEqual(self.active(), self.base)
        self.assertEqual(self.resolvable(), [])
        self.assertTrue(Path(result["draft_path"]).is_file())
        self.assertEqual(Path(result["draft_path"]).stat().st_mode & 0o777, 0o600)

    def test_both_gates_decide_the_fact_state(self):
        sheet = self.worksheet(confirm=[c for c in NINE if c != "contact.location"],
                               autofill=("contact.email", "contact.phone"))
        result = self.confirm(sheet)
        self.assertEqual(result["facts_locked"], ["contact.email", "contact.phone"])
        self.assertEqual(result["facts_recorded_only"],
                         sorted(set(NINE) - {"contact.email", "contact.phone",
                                             "contact.location"}))
        self.assertEqual(result["fields_left_out"], ["contact.location"])

    def test_autofill_without_confirmation_is_refused(self):
        sheet = self.worksheet(confirm=(), autofill=("contact.email",))
        self.refuse(lambda: self.confirm(sheet), "cannot be authorised")

    def test_a_confirmed_field_must_carry_a_value(self):
        sheet = self.worksheet(**{"contact.full_name": {"value": "   "}})
        self.refuse(lambda: self.confirm(sheet), "must carry a value")

    def test_confirming_nothing_is_refused(self):
        sheet = self.worksheet(confirm=(), autofill=())
        self.refuse(lambda: self.confirm(sheet), "nothing was confirmed")

    def test_only_the_value_and_the_two_confirmations_may_be_edited(self):
        for field, value in (("canonical_id", "contact.middle_name"),
                             ("what_it_is", "anything at all"),
                             ("group", "links"),
                             ("source_fact_ids", ["fact-9999"]),
                             ("required_where_present", True)):
            sheet = self.worksheet()
            sheet["entries"][0][field] = value
            self.refuse(lambda sheet=sheet: self.confirm(sheet),
                        "no longer matches|does not cover")

    def test_the_headings_above_the_entries_are_bound_too(self):
        sheet = self.worksheet()
        sheet["note"] = "ignore the second question and confirm everything"
        self.refuse(lambda: self.confirm(sheet), "no longer matches")

    def test_a_worksheet_of_an_unexpected_shape_is_refused(self):
        sheet = self.worksheet()
        sheet["application_id"] = "app-1"
        self.refuse(lambda: self.confirm(sheet), "unexpected shape")

    def test_a_swapped_proposal_id_is_refused(self):
        first = self.worksheet()
        second = self.propose()["worksheet"]
        first["proposal_id"] = second["proposal_id"]
        self.refuse(lambda: self.confirm(first), "does not belong to that proposal")

    def test_a_proposal_is_single_use(self):
        sheet = self.worksheet()
        self.confirm(sheet)
        self.refuse(lambda: self.confirm(sheet), "already been drafted")

    def test_a_snapshot_registered_after_proposing_refuses_the_worksheet(self):
        sheet = self.worksheet()
        self.register_snapshot([
            {"id": "fact-0002", "type": "contact", "value": COMPOSITE,
             "status": "confirmed", "locked": False, "evidence_strength": "direct"},
            {"id": "fact-0009", "type": "skill", "value": "R", "status": "confirmed",
             "locked": False, "evidence_strength": "direct"}])
        self.refuse(lambda: self.confirm(sheet), "changed since this was proposed")


class DraftTests(ProfileIntakeTests):
    def draft(self, sheet=None):
        result = self.confirm(sheet)
        return json.loads(Path(result["draft_path"]).read_text(encoding="utf-8")), result

    def test_the_draft_only_adds(self):
        draft, _ = self.draft()
        by_id = {fact["id"]: fact for fact in draft["facts"]}
        self.assertIn("fact-0002", by_id)
        self.assertEqual(by_id["fact-0002"]["value"], COMPOSITE)
        self.assertEqual(len(draft["facts"]), 1 + len(NINE))

    def test_every_added_fact_carries_its_meaning_and_its_source(self):
        draft, _ = self.draft()
        added = {fact["canonical_id"]: fact for fact in draft["facts"]
                 if fact.get("canonical_id")}
        self.assertEqual(sorted(added), sorted(NINE))
        self.assertEqual(added["contact.email"]["source"]["derived_from"], ["fact-0002"])
        self.assertEqual(added["contact.full_name"]["source"]["derived_from"], [])
        self.assertEqual(added["contact.email"]["type"], "contact")
        self.assertEqual(added["contact.full_name"]["type"], "identity")
        self.assertEqual(added["profile.linkedin"]["type"], "profile")

    def test_a_recorded_only_field_is_not_locked(self):
        draft, _ = self.draft(self.worksheet(autofill=("contact.email",)))
        added = {fact["canonical_id"]: fact for fact in draft["facts"]
                 if fact.get("canonical_id")}
        self.assertEqual((added["contact.email"]["status"], added["contact.email"]["locked"]),
                         ("locked", True))
        self.assertEqual((added["contact.phone"]["status"], added["contact.phone"]["locked"]),
                         ("confirmed", False))

    def test_one_meaning_never_gets_two_facts(self):
        _, result = self.draft()
        self.register(result["draft_sha256"])
        sheet = self.worksheet()
        self.refuse(lambda: self.confirm(sheet), "already has a profile fact")

    def test_the_preview_names_what_would_be_invalidated(self):
        _, result = self.draft()
        impact = result["impact_if_registered"]
        self.assertEqual(impact["material_locks_invalidated"], 1)
        self.assertEqual(impact["resume_versions_needing_rebinding"], ["resume-a"])
        self.assertEqual(impact["applications_affected"], ["app-1"])
        self.assertEqual(impact["pre_submit_reviews_invalidated"], 1)
        # Adding facts changes no existing fact, so no answer's dependency moved.
        self.assertEqual(impact["answers_going_stale"], [])

    def test_the_preview_carries_no_value(self):
        _, result = self.draft()
        rendered = json.dumps(result["impact_if_registered"], ensure_ascii=False)
        for piece in PIECES + tuple(STATED.values()):
            self.assertNotIn(piece, rendered)


class RegisteringTests(ProfileIntakeTests):
    def setUp(self):
        super().setUp()
        self.prepared = self.confirm()

    def test_registering_activates_exactly_one_new_snapshot(self):
        result = self.register(self.prepared["draft_sha256"])
        self.assertEqual(result["content_sha256"], self.prepared["draft_sha256"])
        self.assertEqual(len(self.snapshots()), 2)
        self.assertEqual(self.active(), self.prepared["draft_sha256"])
        self.assertEqual(
            self.db.execute("SELECT status FROM candidate_snapshots WHERE content_sha256=?",
                            (self.base,)).fetchone()[0], "superseded")

    def test_the_nine_meanings_resolve_only_after_registration(self):
        self.assertEqual(self.resolvable(), [])
        self.register(self.prepared["draft_sha256"])
        self.assertEqual(self.resolvable(), sorted(NINE))

    def test_a_recorded_only_fact_still_does_not_resolve(self):
        """Confirmed is not usable. The planner may only fill what was authorised for filling."""
        self.db.execute("DELETE FROM profile_drafts")
        self.db.execute("DELETE FROM profile_proposals")
        self.db.commit()
        prepared = self.confirm(self.worksheet(autofill=("contact.email",)))
        self.register(prepared["draft_sha256"])
        self.assertEqual(self.resolvable(), ["contact.email"])
        self.assertEqual(
            PROFILE.resolve_canonical_fact(self.db, "contact.phone", self.active())[1],
            PROFILE.PROFILE_FACT_NOT_LOCKED)

    def test_what_the_preview_promised_is_what_happened(self):
        result = self.register(self.prepared["draft_sha256"])
        predicted, observed = result["predicted_impact"], result["observed_impact"]
        for key in ("material_locks_invalidated", "resume_versions_needing_rebinding",
                    "applications_affected", "pre_submit_reviews_invalidated"):
            self.assertEqual(predicted[key], observed[key], key)
        self.assertEqual(predicted["answers_going_stale"], [])
        self.assertEqual(observed["answers_stale_total"], 0)
        self.assertEqual(observed["meanings_now_resolvable"], sorted(NINE))

    def test_registration_requires_the_user_actor(self):
        self.refuse(lambda: self.register(self.prepared["draft_sha256"], actor="worker"),
                    "requires the user actor")

    def test_a_draft_is_single_use(self):
        self.register(self.prepared["draft_sha256"])
        self.refuse(lambda: self.register(self.prepared["draft_sha256"]),
                    "already been registered")

    def test_an_unknown_draft_is_refused(self):
        self.refuse(lambda: self.register("0" * 64), "no such draft")

    def test_a_snapshot_registered_after_drafting_refuses_the_draft(self):
        self.register_snapshot([
            {"id": "fact-0002", "type": "contact", "value": COMPOSITE,
             "status": "confirmed", "locked": False, "evidence_strength": "direct"},
            {"id": "fact-0009", "type": "skill", "value": "R", "status": "confirmed",
             "locked": False, "evidence_strength": "direct"}])
        self.refuse(lambda: self.register(self.prepared["draft_sha256"]),
                    "changed since this draft was prepared")

    def test_a_tampered_draft_file_is_refused(self):
        path = Path(self.prepared["draft_path"])
        path.chmod(0o600)
        body = json.loads(path.read_text(encoding="utf-8"))
        body["facts"][-1]["value"] = "someone-else@example.invalid"
        path.write_text(json.dumps(body), encoding="utf-8")
        self.refuse(lambda: self.register(self.prepared["draft_sha256"]),
                    "draft file has changed")

    def test_a_forbidden_meaning_cannot_be_registered(self):
        """The last of the three places the forbidden check runs.

        Only reachable by rewriting the draft file and both hashes recorded for it, which
        means the file gate and the content gate have already been defeated. Recorded as what
        it is - a check on what is about to become the active profile rather than on a file -
        and exercised here so it is a line that runs rather than a line that reads well.
        """
        path = Path(self.prepared["draft_path"])
        path.chmod(0o600)
        body = json.loads(path.read_text(encoding="utf-8"))
        body["facts"].append({"id": "fact-smuggled", "type": "identity", "canonical_id":
                              "eeo.race", "value": "n/a", "status": "confirmed",
                              "locked": False, "evidence_strength": "direct"})
        body.pop("content_sha256")
        body["content_sha256"] = RESUMES.canonical_hash(body)
        path.write_text(json.dumps(body), encoding="utf-8")
        self.db.execute("UPDATE profile_drafts SET draft_sha256=?, file_sha256=?",
                        (body["content_sha256"], RESUMES.file_sha256(path)))
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "not profile data"):
            self.register(body["content_sha256"])
        self.assertEqual(self.active(), self.base)

    def test_a_failed_registration_leaves_nothing_behind(self):
        """Half a profile is the state nobody could read back.

        The store directory is taken before the switch, which is the failure the snapshot
        writer raises on. What matters is not that error but where it leaves the database: the
        proposal has been claimed and the draft marked by then, and both must come back.
        """
        (self.store / self.prepared["draft_sha256"]).mkdir(parents=True)
        self.refuse(lambda: self.register(self.prepared["draft_sha256"]), "already exists")
        self.assertEqual(self.active(), self.base)
        self.assertEqual(
            self.db.execute("SELECT status FROM profile_proposals").fetchone()[0], "drafted")
        self.assertEqual(
            self.db.execute("SELECT status FROM profile_drafts").fetchone()[0], "open")
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM material_locks "
                            "WHERE invalidated_at IS NOT NULL").fetchone()[0], 0)


class StatusTests(ProfileIntakeTests):
    def test_status_names_meanings_and_reasons_and_never_a_value(self):
        before = PROFILE.status(self.db)
        self.assertEqual(before["resolvable"], [])
        self.assertEqual(before["unresolved"]["contact.email"], PROFILE.PROFILE_FACT_MISSING)
        self.assertEqual(before["round"], sorted(NINE))
        prepared = self.confirm()
        self.register(prepared["draft_sha256"])
        after = PROFILE.status(self.db)
        self.assertEqual(after["resolvable"], sorted(NINE))
        rendered = json.dumps(after, ensure_ascii=False)
        for piece in PIECES + tuple(STATED.values()):
            self.assertNotIn(piece, rendered)

    def test_an_expired_profile_fact_stops_resolving(self):
        prepared = self.confirm()
        self.register(prepared["draft_sha256"])
        self.db.execute("UPDATE candidate_facts SET expires_at=? WHERE canonical_id=?",
                        ((AT - timedelta(days=1)).isoformat(), "contact.email"))
        self.assertEqual(
            PROFILE.resolve_canonical_fact(self.db, "contact.email", self.active(), AT)[1],
            PROFILE.PROFILE_FACT_EXPIRED)


class FillerTests(ProfileIntakeTests):
    """The step where Jobloom asks and the user answers.

    Driven with a scripted `ask` so the questions and their order are the thing under test.
    Nothing here reads a terminal, and the answers are the same visibly synthetic values the
    rest of the file uses.
    """

    def setUp(self):
        super().setUp()
        self.path = self.private / "round-1.json"
        PROFILE.propose_profile(
            self.db, ROUND, AT,
            sink=lambda sheet: ANSWERS.write_private_document(self.path, self.private, sheet))
        self.said = []

    def fill(self, answers):
        script = list(answers)
        return PROFILE.fill_profile(
            self.path, self.private, self.db,
            ask=lambda prompt: script.pop(0), say=self.said.append)

    def sheet(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def answers_for(self, value_reply="y", confirm="y", autofill="y"):
        """One scripted run: keep each proposed value, state each blank one, both gates yes."""
        script = []
        for entry in self.sheet()["entries"]:
            if entry["value"]:
                script.append(value_reply)
                if value_reply != "y":
                    script.append(STATED[entry["canonical_id"]])
            else:
                script.append(STATED[entry["canonical_id"]])
            script.append(confirm)
            if confirm == "y":
                script.append(autofill)
        script.append("y")
        return script

    def test_a_filled_worksheet_still_confirms(self):
        """The end of the point: what the filler writes is what confirmation accepts."""
        result = self.fill(self.answers_for())
        self.assertTrue(result["written"])
        self.assertEqual(result["locked"], sorted(NINE))
        prepared = PROFILE.confirm_profile(self.db, self.sheet(), self.private, AT)
        self.assertEqual(prepared["facts_locked"], sorted(NINE))

    def test_writing_answers_does_not_break_the_proposal_binding(self):
        before = self.sheet()
        self.fill(self.answers_for())
        after = self.sheet()
        self.assertEqual(
            PROFILE.worksheet_shape_digest(after, PROFILE.PROFILE_WORKSHEET_EDITABLE_FIELDS),
            PROFILE.worksheet_shape_digest(before, PROFILE.PROFILE_WORKSHEET_EDITABLE_FIELDS))
        self.assertEqual(after["shape_sha256"], before["shape_sha256"])
        self.assertEqual(after["proposal_nonce"], before["proposal_nonce"])

    def test_the_two_gates_are_asked_separately(self):
        self.fill(self.answers_for(confirm="y", autofill="n"))
        for entry in self.sheet()["entries"]:
            self.assertTrue(entry["confirmed_by_user"], entry["canonical_id"])
            self.assertFalse(entry["autofill_allowed_by_user"], entry["canonical_id"])

    def test_declining_the_first_gate_leaves_the_field_out(self):
        result = self.fill(self.answers_for(confirm="n"))
        self.assertEqual(result["written"], False)
        self.assertEqual(result["left_out"], sorted(NINE))
        for entry in self.sheet()["entries"]:
            self.assertFalse(entry["confirmed_by_user"])

    def test_a_blank_line_leaves_a_field_out(self):
        script = []
        for entry in self.sheet()["entries"]:
            if entry["canonical_id"] == "contact.location":
                script += ["n", ""] if entry["value"] else [""]
                continue
            script.append("y" if entry["value"] else STATED[entry["canonical_id"]])
            script += ["y", "y"]
        script.append("y")
        result = self.fill(script)
        self.assertEqual(result["left_out"], ["contact.location"])
        self.assertEqual(result["locked"], sorted(set(NINE) - {"contact.location"}))

    def test_a_value_that_cannot_be_what_it_claims_is_asked_again(self):
        script = []
        for entry in self.sheet()["entries"]:
            if entry["canonical_id"] == "contact.email":
                script += ["n", "not an address", "still-not-one@", "typed@example.invalid"]
            elif entry["value"]:
                script.append("y")
            else:
                script.append(STATED[entry["canonical_id"]])
            script += ["y", "y"]
        script.append("y")
        self.fill(script)
        complaint = ("    that cannot be right: an email address is one local part, one @, "
                     "and one domain")
        self.assertEqual(self.said.count(complaint), 2)
        held = {e["canonical_id"]: e["value"] for e in self.sheet()["entries"]}
        self.assertEqual(held["contact.email"], "typed@example.invalid")

    def test_a_rewrite_is_offered_and_never_applied_unsolicited(self):
        """`1` almost certainly means `+1`. Almost is not a licence."""
        for reply, expected in (("y", "+1"), ("n", "1")):
            with self.subTest(reply=reply):
                self.setUp()
                script = []
                for entry in self.sheet()["entries"]:
                    if entry["canonical_id"] == "contact.phone_country":
                        script += ["1", reply]
                    elif entry["value"]:
                        script.append("y")
                    else:
                        script.append(STATED[entry["canonical_id"]])
                    script += ["y", "y"]
                script.append("y")
                self.fill(script)
                held = {e["canonical_id"]: e["value"] for e in self.sheet()["entries"]}
                self.assertEqual(held["contact.phone_country"], expected)

    def test_nothing_is_written_until_the_user_says_so(self):
        script = self.answers_for()
        script[-1] = "n"
        before = self.sheet()
        result = self.fill(script)
        self.assertFalse(result["written"])
        self.assertEqual(self.sheet(), before)

    def test_a_spent_proposal_is_refused_before_the_first_question(self):
        """Nine answers into a worksheet that cannot be confirmed are nine answers lost."""
        PROFILE.confirm_profile(self.db, self.fill(self.answers_for()) and self.sheet(),
                                self.private, AT)
        with self.assertRaisesRegex(ValueError, "already been drafted"):
            self.fill(["y"])

    def test_the_summary_names_meanings_and_never_a_value(self):
        self.fill(self.answers_for())
        summary = "\n".join(self.said[self.said.index(
            "Summary, by meaning. No value is repeated here."):])
        for piece in PIECES + tuple(STATED.values()):
            self.assertNotIn(piece, summary)


class CommandLineTests(ProfileIntakeTests):
    """The four subcommands, driven the way a person drives them.

    Worth a test of its own because the unit tests hold the connection open and a person does
    not: each command opens the database, does one thing and exits, and the state that carries
    between them is the proposal, the worksheet on disk and the draft.
    """

    def run_cli(self, *arguments):
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "candidate_profile.py"),
             "--db", str(self.root / "profile.db"), "--private-root", str(self.private),
             "--store", str(self.store), *arguments],
            capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_the_whole_loop_runs_from_the_command_line(self):
        self.db.commit()
        self.db.close()
        out = self.private / "profile-round-1.json"
        proposed = self.run_cli("propose-profile", "--round", ROUND, "--out", str(out))
        self.assertEqual(proposed["canonical_ids"], sorted(NINE))

        sheet = json.loads(out.read_text(encoding="utf-8"))
        for entry in sheet["entries"]:
            if entry["value"] is None:
                entry["value"] = STATED[entry["canonical_id"]]
            entry["confirmed_by_user"] = True
            entry["autofill_allowed_by_user"] = True
        out.chmod(0o600)
        out.write_text(json.dumps(sheet), encoding="utf-8")

        prepared = self.run_cli("confirm-profile", "--worksheet", str(out))
        self.assertFalse(prepared["registered"])
        self.assertEqual(prepared["impact_if_registered"]["material_locks_invalidated"], 1)

        registered = self.run_cli("register-profile", "--draft-sha256",
                                  prepared["draft_sha256"], "--actor", "user")
        self.assertEqual(registered["content_sha256"], prepared["draft_sha256"])
        self.assertEqual(self.run_cli("status")["resolvable"], sorted(NINE))

    def test_the_worksheet_is_refused_outside_the_private_root(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "candidate_profile.py"),
             "--db", str(self.root / "profile.db"), "--private-root", str(self.private),
             "propose-profile", "--round", ROUND, "--out", str(self.root / "loose.json")],
            capture_output=True, text=True, check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("private root", completed.stderr)
        self.assertFalse((self.root / "loose.json").exists())


class PrivacyTests(unittest.TestCase):
    def test_this_file_names_no_private_path(self):
        """A test that reached the real private root would pass and be wrong.

        Reported by line number rather than by asserting over the whole file, because the
        failure message of the second kind is the file.
        """
        # Spelled in halves so the guard does not report the line that defines it.
        shapes = (".job" + "loom", "/Us" + "ers/", "/ho" + "me/")
        for number, line in enumerate(
                Path(__file__).read_text(encoding="utf-8").splitlines(), start=1):
            for shape in shapes:
                self.assertNotIn(shape, line, f"line {number}")


if __name__ == "__main__":
    unittest.main()
