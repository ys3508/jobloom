"""Reading a live Lever form, and the things reading one must never do.

No network and no browser here: `build_fields` is given the structure a page would have
returned, which is where every decision this observer makes actually happens. The live run is
a supervised acceptance test, not something a suite can assert.

Most of these are about absence — no value, no write, no guess — because that is what the
observer is for. A test that only checked it found the fields would pass on a version that
also read what was typed into them.
"""

import importlib.util
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"lever_observer_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


OBSERVER = load_script("lever_observer")
ANSWERS = load_script("answer_library")


def frame(**overrides):
    base = {"src": "", "title": "", "width": 300, "height": 200,
            "same_origin": False, "control_count": 0}
    base.update(overrides)
    return base


def control(**overrides):
    """One entry as the page script returns it. `label`/`group_label` are gone: a control now
    carries the employer's words, the normalised question, and what it took to get there."""
    base = {"tag": "input", "type": "text", "name": "name", "id": "name",
            "selector": "#name", "raw_question": "Full name", "match_question": "Full name",
            "normalization": [], "label_source": "lever_application_label",
            "had_required_marker": False, "question_container": "1", "choice_label": "",
            "required": True, "disabled": False, "visible": True, "options": None}
    base.update(overrides)
    # Convenience for the many tests that only care about the question text.
    if "label" in base:
        base["raw_question"] = base["match_question"] = base.pop("label")
    if "group_label" in base:
        group = base.pop("group_label")
        if group:
            base["raw_question"] = base["match_question"] = group
    return base


def build(raw, connection=None):
    """`build_fields` returns the fields and what it skipped; most tests want the fields."""
    fields, _skipped = OBSERVER.build_fields(raw, connection)
    return fields


def page(controls, *, frames=(), text=""):
    return {"controls": controls, "iframes": len(frames), "frames": list(frames),
            "text_markers": text, "title": "Apply"}


class OriginTest(unittest.TestCase):
    def test_only_the_one_approved_host_over_https(self):
        self.assertEqual(OBSERVER.check_origin("https://jobs.lever.co/acme/abc"),
                         "https://jobs.lever.co/acme/abc")
        for url in ("http://jobs.lever.co/acme/abc",
                    "https://jobs.lever.co.evil.example/acme/abc",
                    "https://evil.example/jobs.lever.co/acme",
                    "https://boards.greenhouse.io/acme/abc",
                    "https://JOBS.LEVER.CO.evil/acme", "", "not a url"):
            with self.subTest(url=url):
                with self.assertRaises(OBSERVER.Paused) as caught:
                    OBSERVER.check_origin(url)
                self.assertEqual(caught.exception.code, "origin_not_approved")

    def test_the_host_is_matched_case_insensitively(self):
        self.assertEqual(OBSERVER.check_origin("https://JOBS.LEVER.CO/acme/abc"),
                         "https://jobs.lever.co/acme/abc")

    def test_the_approved_set_is_frozen(self):
        """A set something could add to at runtime is the generic fallback the ADR excluded."""
        self.assertIsInstance(OBSERVER.APPROVED_HOSTS, frozenset)
        with self.assertRaises(AttributeError):
            OBSERVER.APPROVED_HOSTS.add("boards.greenhouse.io")


class ReadsNoValueTest(unittest.TestCase):
    """The property that makes this safe to run on a page somebody is standing in front of."""

    VALUE_READS = (
        r"\.value\b", r"\.files\b", r"\.defaultValue\b", r"\.checked\b",
        r"\.selectedIndex\b", r"getAttribute\(\s*['\"]value['\"]", r"\.innerText\s*\)",
        r"input_value|inner_text\(\)",
    )

    def test_the_page_script_never_reads_what_was_typed(self):
        script = OBSERVER.READ_STRUCTURE
        for pattern in self.VALUE_READS:
            with self.subTest(pattern=pattern):
                # `body.innerText` is read for the CAPTCHA marker and is sliced, so it is
                # matched precisely rather than by the bare word.
                found = [m.group(0) for m in re.finditer(pattern, script)]
                self.assertEqual(found, [], f"the page script reads {pattern}")

    def test_the_script_selects_a_selected_option_nowhere(self):
        self.assertNotIn("selected", OBSERVER.READ_STRUCTURE)

    def test_an_observation_carries_no_value_key(self):
        fields = build(page([control()]), None)
        for field in fields:
            for key in ("value", "current_value", "answer", "text", "files"):
                self.assertNotIn(key, field)

    def test_the_page_script_writes_nothing_to_the_document(self):
        """An observer that stamped an attribute on each question container was writing to a
        page it promised never to change, and to one a person may be looking at."""
        script = OBSERVER.READ_STRUCTURE
        for write in ("dataset.", "setAttribute(", ".remove()\n", "appendChild",
                      "insertBefore", "classList.add", ".innerHTML ="):
            with self.subTest(write=write):
                if write == ".remove()\n":
                    # `remove()` appears, but only on a clone made for the heading text.
                    self.assertIn("cloneNode", script)
                    continue
                self.assertNotIn(write, script)

    def test_the_module_calls_no_method_that_changes_a_page(self):
        source = (ROOT / "skills" / "jobloom" / "scripts" / "lever_observer.py").read_text(
            encoding="utf-8")
        for verb in (".click(", ".fill(", ".type(", ".press(", ".select_option(",
                     ".check(", ".uncheck(", ".set_input_files(", ".go_back(",
                     ".go_forward(", ".reload("):
            with self.subTest(verb=verb):
                self.assertNotIn(verb, source)


class FieldTest(unittest.TestCase):
    def build(self, controls, connection=None):
        return build(page(controls), connection)

    def test_the_five_control_kinds_are_recognised(self):
        fields = self.build([
            control(id="name", selector="#name"),
            control(tag="textarea", type="", id="why", selector="#why", label="Why us?"),
            control(tag="select", type="", id="src", selector="#src", label="How did you hear?",
                    options=["A friend", "LinkedIn"]),
            control(type="radio", name="auth", id="auth1", selector="#auth1",
                    choice_label="Yes", label="Are you authorized to work?"),
            control(type="checkbox", id="terms", selector="#terms", label="I agree"),
            control(type="file", id="resume", selector="#resume", label="Resume"),
        ])
        self.assertEqual([f["control"] for f in fields],
                         ["text", "textarea", "select", "radio", "checkbox", "file"])

    def test_a_radio_group_is_one_question_with_its_choices(self):
        fields = self.build([
            control(type="radio", name="auth", id="a1", selector="#a1", choice_label="Yes",
                    label="Are you authorized to work?"),
            control(type="radio", name="auth", id="a2", selector="#a2", choice_label="No",
                    label="Are you authorized to work?"),
        ])
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0]["match_question"], "Are you authorized to work?")
        self.assertEqual(fields[0]["options"], ["Yes", "No"])
        self.assertEqual(fields[0]["grouping_basis"], "lever_question_container")

    def test_a_checkbox_group_sharing_a_name_is_one_multi_select_question(self):
        """The 2026-09-11 acceptance run: nine `name="pronouns"` checkboxes, one question."""
        fields = self.build([
            control(type="checkbox", name="pronouns", id="p1", selector="#p1",
                    choice_label="She/her", label="Pronouns"),
            control(type="checkbox", name="pronouns", id="p2", selector="#p2",
                    choice_label="He/him", label="Pronouns"),
            control(type="checkbox", name="pronouns", id="p3", selector="#p3",
                    choice_label="They/them", label="Pronouns"),
        ])
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0]["match_question"], "Pronouns")
        self.assertEqual(fields[0]["options"], ["She/her", "He/him", "They/them"])
        self.assertEqual(fields[0]["field_id"], "pronouns")

    def test_a_control_beside_a_group_in_one_container_is_auxiliary_not_a_member(self):
        """The real shape: nine `name="pronouns"` boxes plus a nameless `Custom` toggle."""
        fields = self.build([
            control(type="checkbox", name="pronouns", id="p1", selector="#p1",
                    choice_label="She/her", label="Pronouns"),
            control(type="checkbox", name="pronouns", id="p2", selector="#p2",
                    choice_label="He/him", label="Pronouns"),
            control(type="checkbox", name="", id="customPronounsOption",
                    selector="#customPronounsOption", choice_label="Custom",
                    label="Pronouns"),
        ])
        self.assertEqual(len(fields), 2)
        # No reviewed meaning for "Pronouns" here, so the group itself is unplannable too —
        # what is under test is that the odd control out is named as auxiliary regardless.
        self.assertEqual(fields[0]["automation"], "no_canonical_meaning")
        self.assertEqual(fields[1]["automation"], "unsupported_auxiliary_control")
        self.assertEqual(fields[1]["grouping_basis"], "not_in_the_container_group")
        self.assertIsNone(fields[1]["canonical_id"], "nothing unfillable is ever matched")

    def test_a_lone_checkbox_keeps_its_own_label_as_the_question(self):
        """"I agree to the terms" is the question. A group of one would replace it."""
        fields = self.build([control(type="checkbox", name="terms", id="terms",
                                     selector="#terms", label="I agree to the terms")])
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0]["match_question"], "I agree to the terms")
        self.assertEqual(fields[0]["grouping_basis"], "single_control")
        self.assertNotIn("options", fields[0])

    def test_a_group_whose_heading_lever_did_not_mark_is_not_guessed_at(self):
        """No `.application-label` in the container means the question cannot be proved."""
        with self.assertRaises(OBSERVER.Paused) as caught:
            self.build([
                control(type="radio", name="auth", id="a1", selector="#a1",
                        choice_label="Yes", raw_question="", match_question="",
                        label_source="none"),
                control(type="radio", name="auth", id="a2", selector="#a2",
                        choice_label="No", raw_question="", match_question="",
                        label_source="none")])
        self.assertEqual(caught.exception.code, "ambiguous_label")

    def test_required_is_carried_from_the_page(self):
        fields = self.build([control(required=False), control(id="b", selector="#b",
                                                              label="Email", required=True)])
        self.assertEqual([f["required"] for f in fields], [False, True])

    def test_structural_inputs_are_skipped_without_a_pause(self):
        fields = self.build([
            control(type="hidden", id="csrf", selector="#csrf", label=""),
            control(type="submit", id="go", selector="#go", label="Submit"),
            control(id="name", selector="#name"),
        ])
        self.assertEqual([f["field_id"] for f in fields], ["name"])

    def test_a_disabled_control_is_not_a_question_being_asked(self):
        fields = self.build([control(), control(id="b", selector="#b", label="Later",
                                                disabled=True)])
        self.assertEqual(len(fields), 1)


class StopsRatherThanGuessesTest(unittest.TestCase):
    def build(self, controls, connection=None):
        return build(page(controls), connection)

    def paused(self, controls, code, connection=None):
        with self.assertRaises(OBSERVER.Paused) as caught:
            self.build(controls, connection)
        self.assertEqual(caught.exception.code, code)

    def test_an_unknown_control_stops_instead_of_becoming_a_text_box(self):
        self.paused([control(type="color", id="c", selector="#c", label="Pick")],
                    "unsupported_control")

    def test_a_field_with_no_readable_question_stops(self):
        self.paused([control(label="")], "unlabelled_field")

    def test_a_field_with_no_stable_selector_stops(self):
        self.paused([control(id="", name="", selector="")], "unlabelled_field")

    def test_two_fields_with_one_identifier_stop(self):
        self.paused([control(id="name", selector="#name"),
                     control(id="name", selector="#name", label="Name again")],
                    "duplicate_field_id")

    def test_a_page_with_nothing_to_answer_stops(self):
        self.paused([], "no_fields_found")

    def test_two_questions_claiming_one_reviewed_meaning_stop(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        ANSWERS.initialize(connection)
        self.addCleanup(connection.close)
        for question in ("Email Address", "Email"):
            ANSWERS.add_question_form(connection, "contact.email", question,
                                      verified_by_user=True)
        self.paused([control(id="e1", selector="#e1", label="Email Address"),
                     control(id="e2", selector="#e2", label="Email")],
                    "conflicting_canonical_meaning", connection)

    def test_a_pause_never_carries_page_text(self):
        """The detail is something this module wrote, never something the page said."""
        secret = "Applicant salary expectation 250000 USD"
        with self.assertRaises(OBSERVER.Paused) as caught:
            self.build([control(type="color", id="c", selector="#c", label=secret)])
        self.assertNotIn("250000", str(caught.exception.detail or ""))
        self.assertNotIn(secret, str(caught.exception.detail or ""))


class MeaningAndDispositionTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        ANSWERS.initialize(self.connection)
        self.addCleanup(self.connection.close)

    def test_a_reviewed_question_carries_its_meaning_and_an_unreviewed_one_does_not(self):
        ANSWERS.add_question_form(self.connection, "contact.email", "Email Address",
                                  verified_by_user=True)
        fields = build(page([
            control(id="e", selector="#e", label="Email Address"),
            control(id="q", selector="#q", label="What excites you about this role?"),
        ]), self.connection)
        self.assertEqual(fields[0]["canonical_id"], "contact.email")
        self.assertIsNone(fields[1]["canonical_id"])

    def test_meaning_is_exact_and_never_resembled(self):
        ANSWERS.add_question_form(self.connection, "contact.email", "Email Address",
                                  verified_by_user=True)
        fields = build(page([
            control(id="e", selector="#e", label="Email address (work)")]), self.connection)
        self.assertIsNone(fields[0]["canonical_id"])

    def test_a_sensitive_question_is_reported_as_always_manual(self):
        fields = build(page([
            control(id="s", selector="#s",
                    label="Will you now or in the future require sponsorship?"),
            control(id="r", selector="#r", label="Race/Ethnicity (voluntary)"),
        ]), self.connection)
        self.assertEqual([f["disposition"] for f in fields],
                         ["always_manual", "always_manual"])
        self.assertEqual([f["domain"] for f in fields], ["sponsorship", "voluntary_eeo"])


class CaptchaTest(unittest.TestCase):
    """The 2026-09-11 acceptance run: a real Lever apply page ships hCaptcha in a cross-origin
    frame, so none of its text reaches the document this observer reads."""

    def test_a_challenge_frame_is_found_by_its_host(self):
        observed = page([], frames=[{"src": "https://newassets.hcaptcha.com/captcha/v1/"
                                            "abc/static/hcaptcha-enclave.htm", "title": ""}])
        self.assertTrue(OBSERVER.captcha_present(observed))

    def test_a_challenge_frame_is_found_by_its_title_from_an_unlisted_host(self):
        observed = page([], frames=[{"src": "https://vendor.example/widget",
                                     "title": "Widget containing checkbox for hCaptcha "
                                              "security challenge"}])
        self.assertTrue(OBSERVER.captcha_present(observed))

    def test_the_top_document_text_is_still_checked(self):
        self.assertTrue(OBSERVER.captcha_present(page([], text="Please solve the CAPTCHA")))

    def test_an_ordinary_embed_is_not_a_challenge(self):
        observed = page([], frames=[{"src": "about:blank",
                                     "title": "LinkedIn Embedded Content"},
                                    {"src": "", "title": ""}])
        self.assertFalse(OBSERVER.captcha_present(observed))

    def test_every_listed_vendor_is_matched(self):
        for host in OBSERVER.CAPTCHA_FRAME_HOSTS:
            with self.subTest(host=host):
                self.assertTrue(OBSERVER.captcha_present(
                    page([], frames=[{"src": f"https://{host}/widget", "title": ""}])))


class FrameClassTest(unittest.TestCase):
    """Every frame gets a class. Unknown is one of them, and it stops the run.

    The shapes here are the four the 2026-09-11 acceptance run found on a real Beghou
    Consulting apply page.
    """

    def test_a_challenge_frame_is_recognised_by_host_and_by_title(self):
        by_host = frame(src="https://newassets.hcaptcha.com/captcha/v1/abc/static/"
                            "hcaptcha-enclave.htm", width=422, height=849)
        by_title = frame(src="https://vendor.example/w",
                         title="Widget containing checkbox for hCaptcha security challenge")
        self.assertEqual(OBSERVER.classify_frame(by_host), "captcha_frame")
        self.assertEqual(OBSERVER.classify_frame(by_title), "captcha_frame")

    def test_the_linkedin_share_widget_is_a_known_non_form_embed(self):
        self.assertEqual(OBSERVER.classify_frame(
            frame(src="about:blank", title="LinkedIn Embedded Content",
                  width=233, height=48)), "known_non_form_embed")

    def test_a_widget_that_grew_is_no_longer_the_widget_that_was_measured(self):
        """The size is part of the rule, so a share button that becomes a panel is unknown."""
        self.assertEqual(OBSERVER.classify_frame(
            frame(title="LinkedIn Embedded Content", width=900, height=700)), "unknown_frame")

    def test_a_one_by_one_frame_is_a_tracking_pixel(self):
        self.assertEqual(OBSERVER.classify_frame(frame(width=1, height=1)), "tracking_pixel")

    def test_a_readable_frame_holding_controls_is_a_possible_form(self):
        self.assertEqual(OBSERVER.classify_frame(
            frame(same_origin=True, control_count=4)), "possible_form_frame")

    def test_a_readable_frame_holding_nothing_is_still_classified_by_shape(self):
        self.assertEqual(OBSERVER.classify_frame(
            frame(same_origin=True, control_count=0, width=600, height=400)), "unknown_frame")

    def test_anything_unclassified_is_unknown_and_not_ignored(self):
        for shape in (frame(src="https://ads.example/banner", width=728, height=90),
                      frame(title="Some widget", width=500, height=500),
                      frame()):
            with self.subTest(shape=shape["src"] or shape["title"] or "bare"):
                self.assertEqual(OBSERVER.classify_frame(shape), "unknown_frame")

    def test_a_challenge_beside_the_form_is_not_a_reason_to_stop_reading_it(self):
        """The decision of 2026-09-11: a challenge Jobloom never touches does not make the
        ordinary fields beside it unobservable."""
        observed = page([control()], frames=[
            frame(src="https://newassets.hcaptcha.com/x", width=422, height=849)])
        self.assertTrue(OBSERVER.captcha_present(observed))
        self.assertEqual(len(build(observed)), 1)


class ChallengeFieldTest(unittest.TestCase):
    """What a challenge writes into the top document is never a question being asked."""

    def test_a_response_token_field_is_excluded_and_counted(self):
        fields, skipped = OBSERVER.build_fields(page([
            control(),
            control(tag="textarea", type="", name="h-captcha-response",
                    id="h-captcha-response", selector="#h-captcha-response",
                    label="", visible=False),
            control(tag="textarea", type="", name="g-recaptcha-response",
                    id="g-recaptcha-response", selector="#g-recaptcha-response",
                    label="", visible=False),
        ]), None)
        self.assertEqual([f["field_id"] for f in fields], ["name"])
        self.assertEqual(skipped["challenge_field"], 2)

    def test_a_response_token_never_reaches_the_observation(self):
        fields, _ = OBSERVER.build_fields(page([
            control(),
            control(name="h-captcha-response", id="hc", selector="#hc", label="",
                    visible=False)]), None)
        blob = json.dumps(fields)
        self.assertNotIn("captcha", blob.lower())

    def test_a_hidden_control_is_observed_and_marked_never_fillable(self):
        """Skipping them silently let a collapsed EEO section pass without being mentioned."""
        fields, _ = OBSERVER.build_fields(page([
            control(),
            control(id="eeo-race", selector="#eeo-race", label="Race", visible=False)]), None)
        self.assertEqual(len(fields), 2)
        self.assertEqual(fields[1]["visibility"], "hidden")
        self.assertEqual(fields[1]["automation"], "not_visible")
        self.assertIsNone(fields[1]["canonical_id"])

    def test_a_readable_field_with_no_reviewed_meaning_is_not_called_fillable(self):
        """Nothing can supply a value for it, so a plan naming it could not be written."""
        fields, _ = OBSERVER.build_fields(page([control(label="Anything unmapped")]), None)
        self.assertEqual(fields[0]["automation"], "no_canonical_meaning")
        self.assertIsNone(fields[0]["canonical_id"])

    def test_a_hidden_control_nobody_can_read_is_hidden_unknown_and_blocks_the_planner(self):
        fields, _ = OBSERVER.build_fields(page([
            control(),
            control(id="ghost", selector="#ghost", label="", label_source="none",
                    visible=False)]), None)
        self.assertEqual(fields[1]["automation"], "hidden_unknown")
        self.assertIn("hidden_unknown", OBSERVER.AUTOMATION_STATES)
        self.assertIn("hidden_unknown", OBSERVER.NOT_FILLABLE)

    def test_nothing_is_dropped_without_being_counted(self):
        _, skipped = OBSERVER.build_fields(page([
            control(),
            control(type="hidden", id="csrf", selector="#csrf", label=""),
            control(id="off", selector="#off", label="Later", disabled=True),
            control(name="h-captcha-response", id="hc", selector="#hc", label="",
                    visible=False),
        ]), None)
        self.assertEqual(skipped, {"structural": 1, "challenge_field": 1,
                                   "disabled": 1, "not_visible": 0})


class NormalizationTest(unittest.TestCase):
    """Two strings per question: what the employer wrote, and what may be matched on.

    The transformations are a finite enumeration, each provable from the DOM. There is no
    free-text cleaning here and no model rewriting, because a question nobody can account for
    is a question nobody may look a canonical meaning up for.
    """

    def test_the_employers_words_are_kept_beside_the_matchable_ones(self):
        fields = build(page([control(raw_question="Email✱", match_question="Email",
                                     normalization=["required_marker_removed"],
                                     had_required_marker=True, required=False)]))
        self.assertEqual(fields[0]["raw_question"], "Email✱")
        self.assertEqual(fields[0]["match_question"], "Email")
        self.assertEqual(fields[0]["normalization"], ["required_marker_removed"])

    def test_the_marker_itself_is_the_evidence_that_the_field_is_required(self):
        """Lever ships `<span class="required">✱</span>`. Removing the glyph is only sound
        because requiredness survives it — recorded, with the basis that carried it."""
        fields = build(page([control(raw_question="Resume/CV✱", match_question="Resume/CV",
                                     normalization=["required_marker_removed"],
                                     had_required_marker=True, required=False)]))
        self.assertTrue(fields[0]["required"])
        self.assertEqual(fields[0]["required_basis"], "lever_required_marker")

    def test_an_attribute_is_named_as_the_basis_when_it_is_the_one(self):
        fields = build(page([control(required=True, had_required_marker=False)]))
        self.assertEqual(fields[0]["required_basis"], "attribute_or_aria")

    def test_every_reason_code_is_one_of_the_enumerated_ones(self):
        for field in build(page([control(normalization=["required_marker_removed",
                                                        "lever_text_node_used"])])):
            for reason in field["normalization"]:
                self.assertIn(reason, OBSERVER.NORMALIZATIONS)

    def test_a_label_lever_did_not_mark_is_ambiguous_rather_than_cut_down(self):
        with self.assertRaises(OBSERVER.Paused) as caught:
            build(page([control(label_source="none", raw_question="Resume/CV ATTACH",
                                match_question="Resume/CV ATTACH")]))
        self.assertEqual(caught.exception.code, "ambiguous_label")

    def test_the_matchable_string_is_what_a_meaning_is_looked_up_on(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        ANSWERS.initialize(connection)
        self.addCleanup(connection.close)
        ANSWERS.add_question_form(connection, "contact.email", "Email", verified_by_user=True)
        fields = build(page([control(raw_question="Email✱", match_question="Email",
                                     normalization=["required_marker_removed"],
                                     had_required_marker=True)]), connection)
        self.assertEqual(fields[0]["canonical_id"], "contact.email")

    def test_the_page_script_reads_leverspecific_nodes_and_not_nearby_text(self):
        script = OBSERVER.READ_STRUCTURE
        self.assertIn(".application-label", script)
        self.assertIn("li.application-question", script)
        self.assertIn("cloneNode", script)


class AcceptanceShapeTest(unittest.TestCase):
    """What the live run produced, pinned as a shape so a regression is visible.

    The page is not fetched here. These are the properties the 2026-09-11 observation had,
    asserted against a synthetic page with the same structure.
    """

    def test_a_challenge_beside_a_form_yields_an_observation_that_demands_takeover(self):
        observed = page([
            control(id="name", selector="#name", label="Full name"),
            control(type="radio", name="auth", id="a1", selector="#a1", choice_label="Yes",
                    label="Are you authorized to work in the US?"),
            control(type="radio", name="auth", id="a2", selector="#a2", choice_label="No",
                    label="Are you authorized to work in the US?"),
            control(type="checkbox", name="pronouns", id="p1", selector="#p1",
                    choice_label="She/her", label="Pronouns"),
            control(type="checkbox", name="pronouns", id="p2", selector="#p2",
                    choice_label="He/him", label="Pronouns"),
        ], frames=[
            frame(src="https://newassets.hcaptcha.com/x", width=422, height=849),
            frame(src="about:blank", title="LinkedIn Embedded Content", width=233, height=48),
            frame(width=1, height=1),
        ])
        self.assertTrue(OBSERVER.captcha_present(observed))
        classes = [f["class"] for f in OBSERVER.classify_frames(observed)]
        self.assertEqual(sorted(classes),
                         ["captcha_frame", "known_non_form_embed", "tracking_pixel"])
        fields = build(observed)
        self.assertEqual([f["match_question"] for f in fields],
                         ["Full name", "Are you authorized to work in the US?", "Pronouns"])
        self.assertEqual([f["control"] for f in fields], ["text", "radio", "checkbox"])

    def test_the_sponsorship_question_is_always_manual(self):
        """Two radios, because one is not a group and its own label would be the question."""
        question = "Will you require work sponsorship now or in the future?"
        fields = build(page([
            control(type="radio", name="s", id="s1", selector="#s1", choice_label="Yes",
                    label=question),
            control(type="radio", name="s", id="s2", selector="#s2", choice_label="No",
                    label=question)]))
        self.assertEqual(fields[0]["match_question"], question)
        self.assertEqual(fields[0]["disposition"], "always_manual")
        self.assertEqual(fields[0]["domain"], "sponsorship")


class DigestTest(unittest.TestCase):
    def test_the_hash_covers_the_question_and_not_the_answer(self):
        asked = {"raw_question": "Full name", "control": "text", "required": True}
        self.assertEqual(OBSERVER.field_digest(asked),
                         OBSERVER.field_digest({**asked, "value": "Anyone at all"}))

    def test_the_hash_is_over_the_employers_words_not_the_normalised_ones(self):
        """A change to Jobloom's normalisation must not read as the employer changing the form."""
        asked = {"raw_question": "Email✱", "control": "text", "required": True}
        self.assertEqual(OBSERVER.field_digest(asked),
                         OBSERVER.field_digest({**asked, "match_question": "Email"}))

    def test_changing_the_question_changes_the_hash(self):
        asked = {"raw_question": "Full name", "control": "text", "required": True}
        for change in ({"raw_question": "Legal name"}, {"control": "textarea"},
                       {"required": False}, {"options": ["A", "B"]}):
            with self.subTest(change=change):
                self.assertNotEqual(OBSERVER.field_digest(asked),
                                    OBSERVER.field_digest({**asked, **change}))

    def test_the_page_hash_covers_its_fields_in_order(self):
        fields = build(page([
            control(id="a", selector="#a", label="A"),
            control(id="b", selector="#b", label="B")]), None)
        reversed_fields = list(reversed(fields))
        self.assertNotEqual(OBSERVER.page_digest(fields),
                            OBSERVER.page_digest(reversed_fields))

    def test_every_field_carries_its_own_hash(self):
        for field in build(page([control()]), None):
            self.assertRegex(field["field_sha256"], r"^[0-9a-f]{64}$")


class OutputTest(unittest.TestCase):
    def test_the_observation_is_written_private_and_says_it_wrote_no_rows(self):
        observation = {"schema_version": "0.1.0", "observer_version": OBSERVER.OBSERVER_VERSION,
                       "page_url": "https://jobs.lever.co/acme/abc", "page_sha256": "0" * 64,
                       "field_count": 0, "fields": [], "legal_items": [],
                       "restricted_requests": [], "values_read": False, "database_writes": 0}
        root = Path(tempfile.mkdtemp())
        output = root / "obs" / "observation.json"
        OBSERVER.write_observation(observation, output)
        self.assertEqual(oct(output.stat().st_mode)[-3:], "600")
        written = json.loads(output.read_text(encoding="utf-8"))
        self.assertIs(written["values_read"], False)
        self.assertEqual(written["database_writes"], 0)

    def test_the_summary_prints_questions_and_no_values(self):
        fields = build(page([control()]), None)
        text = OBSERVER.summarise({"field_count": 1, "page_sha256": "a" * 64,
                                   "fields": fields})
        self.assertIn("Full name", text)
        self.assertIn("text", text)


if __name__ == "__main__":
    unittest.main()
