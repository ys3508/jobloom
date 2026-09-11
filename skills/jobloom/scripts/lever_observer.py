#!/usr/bin/env python3
"""Read one real Lever application page and say what it asks. Nothing else.

The Fill-Only ADR built everything except the thing that looks at a live page:
`semantic_replay` renders reviewed fixtures on a loopback origin, `fill_worker` executes a
package whose selectors somebody else found, and `fill_core.observe_page` consumes an
observation nobody produces. This produces one, from the page an employer actually ships, and
that is the whole of its job.

**It fills nothing, clicks nothing, navigates nowhere, and submits nothing.** Those are not
refused by a rule that could be argued with — the vocabulary here is `query_selector_all` and
attribute reads, and no method that changes a page is called anywhere in this file. The ADR's
first v1 bound is that a worker may not advance a page; an observer does not even have the
verbs.

**No value leaves the page.** Not the `value` attribute, not `textContent` of an input, not a
placeholder that a browser may have autofilled, not a file name. What is read is the question:
label, control kind, requiredness, and the choices a select or radio group offers. A form the
user has already typed into observes identically to an empty one, which is the property that
makes it safe to run on a page somebody is standing in front of.

**The origin is a frozen constant, not a setting.** `APPROVED_HOSTS` cannot be extended at
runtime, by a page, by a payload or by a database row. `field_policy.register_replay_surface`
refuses anything but loopback and stays that way — a replay surface is a different thing from
a production posting, and widening it to hold one would have made every replay guarantee
weaker to gain a production feature.

**Nothing is written to the database.** The observation is a private file. No
`answer_matched` audit event, no application field, no submission record: reading a page is
not an application event, and the three rungs of `saved_jobs` stay exactly where they are.

**Stops rather than guesses.** A duplicate field id, two fields resolving to one canonical
meaning, an unknown control, an iframe, a CAPTCHA, a navigation away from the page it was
pointed at, or a structure that changes under it — each ends the run with a reason code. An
observation is emitted only when the page held still and every field was legible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import answer_library  # noqa: E402
import field_policy  # noqa: E402

OBSERVER_VERSION = "lever-observer-0.1.0"

# The one vendor this observer knows, frozen. The ADR measured Lever at 68% of the queue and
# ordered the adapters Lever, then Greenhouse, then Ashby; it also put a generic fallback out
# of the milestone. A set that could be extended at runtime is a generic fallback with an
# extra step, so this one cannot be.
APPROVED_HOSTS = frozenset({"jobs.lever.co"})

# Controls `fill_core.ALLOWED_CONTROLS` already names. Anything else pauses rather than being
# recorded as the nearest thing that fits: a control nobody classified is a control nobody
# decided the authority for.
CONTROL_BY_INPUT_TYPE = {
    "text": "text", "email": "text", "tel": "text", "url": "text", "number": "text",
    "search": "text", "date": "text",
    "radio": "radio", "checkbox": "checkbox", "file": "file",
}
# Input types that are not questions and are skipped without a pause: they carry no label a
# person answers, and Lever ships several on every form.
IGNORED_INPUT_TYPES = frozenset({"hidden", "submit", "button", "reset", "image"})

PAUSE_CODES = (
    "origin_not_approved", "navigated_away", "page_changed_under_observation",
    "unknown_frame_present", "possible_form_frame", "captcha_gates_the_form",
    "duplicate_field_id", "conflicting_canonical_meaning", "unsupported_control",
    "unlabelled_field", "ambiguous_label", "no_fields_found",
    "page_closed_or_navigated", "browser_error",
)

CAPTCHA_MARKERS = re.compile(
    r"recaptcha|hcaptcha|turnstile|are you a robot|captcha", re.IGNORECASE)
# A CAPTCHA on a real Lever page is a cross-origin iframe, so none of its text is in the
# document this observer can read: the 2026-09-11 acceptance run found two hCaptcha frames
# whose only trace in the top document was the frame's own `src` and `title`. Checking the
# body text alone would have reported the blunter `iframe_present` and left the actual
# blocker unnamed. The ADR is explicit that a CAPTCHA is a mandatory pause and never an
# obstacle to route around, so naming it precisely is the point — it is not a way past it.
CAPTCHA_FRAME_HOSTS = ("hcaptcha.com", "recaptcha.net", "google.com/recaptcha",
                       "challenges.cloudflare.com", "arkoselabs.com", "funcaptcha.com")

# Frames that are neither a challenge nor a place an application field could hide. Each is
# listed because it was seen on a real Lever page, not because a class of thing sounds
# harmless: a rule broad enough to cover "widgets" would cover a form in a widget.
KNOWN_NON_FORM_EMBEDS = (
    # The share button under the posting. Renders at a fixed small size and carries no form.
    {"title": "linkedin embedded content", "max_width": 400, "max_height": 120},
)
# A frame too small to show a question. Kept as its own rule with the size stated, so a
# "tracking pixel" that grows into something is no longer a tracking pixel.
TRACKING_PIXEL_MAX = 4

FRAME_CLASSES = ("captcha_frame", "known_non_form_embed", "tracking_pixel",
                 "possible_form_frame", "unknown_frame")

# What a challenge writes into the *top* document for its own bookkeeping. hCaptcha and
# reCAPTCHA both inject a hidden textarea holding the response token. It is not a question an
# employer is asking and Jobloom must never read, hash, carry or fill it, so it is excluded by
# name before anything else looks at it — ahead of the unlabelled-field rule, which would
# otherwise stop the run on a field that should simply not be there.
CHALLENGE_FIELD_NAMES = re.compile(
    r"captcha|challenge[-_]?response|cf[-_]turnstile", re.IGNORECASE)

# Every transformation that may stand between what the employer wrote and the string an exact
# canonical lookup is done on. Finite, enumerated, and each one provable from the DOM. There
# is no free-text cleaning and no model rewriting: a question nobody can account for is a
# question nobody may match.
NORMALIZATIONS = (
    # A `<span class="required">✱</span>` beside the label, removed only when requiredness is
    # independently recorded — the span is itself the evidence, and it is named as such.
    "required_marker_removed",
    # Lever wraps a radio group's question in `div.text` inside the label. Taking that node's
    # text is reading the heading, not editing it.
    "lever_text_node_used",
    # Buttons, controls and status text that live inside the label element and are not part of
    # the question: "ATTACH RESUME/CV", "Couldn't auto-read resume".
    "control_and_status_excluded",
    # Runs of whitespace folded to single spaces. The only change made to the characters.
    "whitespace_folded",
)

# What may sit inside a Lever label element without being part of the question. Enumerated
# rather than matched by shape, so a node type nobody listed keeps its text and the question
# stays whole.
LABEL_NOISE_SELECTOR = (
    ".required, button, input, select, textarea, "
    ".resume-upload-button, .filename, .parse-status, .application-field")

# Why a control may not be planned against, beyond the dispositions `field_policy` owns.
# What may be planned against, and why not when not. Every control gets exactly one, so a
# planner never meets a field it has no instruction for.
AUTOMATION_STATES = (
    # A value may be filled, and only these are ever matched to a canonical meaning.
    "fillable",
    # The file input. Supplied from the application's material lock, never from an answer.
    "material",
    # `field_policy` says no authority here may speak: legal, compensation, EEO, conflict.
    "manual_only",
    # In a question container whose group it is not part of — a reveal toggle, not an answer.
    "unsupported_auxiliary_control",
    # Present in the document and not shown. Classified, reported, never filled. The EEO and
    # demographic sections of a Lever form load collapsed, and skipping them silently meant a
    # page could ask about race and veteran status without the observation saying so.
    "not_visible",
    # Hidden *and* not classifiable. Recorded rather than dropped, and it blocks the planner:
    # a control nobody could read is a control nobody can promise is harmless.
    "hidden_unknown",
    # Readable, shown, and nothing can supply a value for it: no reviewed question form maps
    # it to a meaning. Derived rather than judged — a field with no meaning has no source, so
    # calling it fillable would describe a plan that cannot be written. Which of these deserve
    # a form, a story or a permanent manual mark is a separate decision, per field.
    "no_canonical_meaning",
)
NOT_FILLABLE = tuple(state for state in AUTOMATION_STATES if state != "fillable")


class Paused(Exception):
    """The run stopped. Carries a code and never a value read from the page."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        # Bounded, and only ever something this file wrote — never page text, which is an
        # untrusted observation and would carry a value into a log the moment a label did.
        self.detail = detail


def check_origin(url: str) -> str:
    """The posting URL, or a refusal. Scheme and host both, and no redirect is followed."""
    parsed = urlparse(url or "")
    if parsed.scheme != "https":
        raise Paused("origin_not_approved", "scheme")
    host = (parsed.hostname or "").lower()
    if host not in APPROVED_HOSTS:
        raise Paused("origin_not_approved", "host")
    return f"{parsed.scheme}://{host}{parsed.path}"


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def field_digest(field: dict[str, Any]) -> str:
    """A hash of what the field *asks*, never of what it holds.

    Over `raw_question` — the employer's own words — rather than the normalised one, so a
    change to Jobloom's normalisation rules does not read as the employer changing the form.
    """
    return _digest({key: field[key] for key in
                    ("raw_question", "control", "required", "options") if key in field})


def page_digest(fields: list[dict[str, Any]]) -> str:
    """The structure of the page as a whole, in the order the fields appear."""
    return _digest([field["field_sha256"] for field in fields])


def canonical_meanings(connection: sqlite3.Connection | None,
                       questions: list[str]) -> dict[str, str | None]:
    """The reviewed meaning of each exact question, read and never invented.

    `answer_library.canonical_meaning` is exact on the normalised text, which is the property
    that matters here: a resemblance score deciding that a Lever label means
    `work_authorized_now` would be a machine concluding what an immigration field asks. An
    unreviewed label comes back as `None`, which is a thing for the user to resolve.
    """
    if connection is None:
        return {question: None for question in questions}
    meanings: dict[str, str | None] = {}
    for question in questions:
        canonical_id, _reason = answer_library.canonical_meaning(connection, question)
        meanings[question] = canonical_id
    return meanings


# The one script this observer runs in the page, written here and never assembled from
# anything the page said. It reads labels, kinds, requiredness and choices — and no value.
#
# `fill_worker` forbids `evaluate` in the *action* vocabulary a package may express, which is
# a different thing: that rule stops a package from smuggling in code. This script is
# Jobloom's own, fixed at build time, and what it returns is still treated as an untrusted
# observation by everything downstream.
#
# `value`, `files`, `textContent` of a control and `defaultValue` are deliberately absent. A
# form somebody has already typed into returns exactly what an empty one returns.
READ_STRUCTURE = r"""
(noiseSelector) => {
  const squash = (text) => (text || '').replace(/\s+/g, ' ').trim().slice(0, 2000);

  // Lever's own question container and label node. Anchoring on the nodes Lever marks as the
  // heading — rather than walking up to whatever element happened to hold text — is the whole
  // of the structural rule: if there is no such node the field is not legible, and that is
  // reported instead of a string cut down to look like one.
  const questionContainer = (el) =>
    el.closest('li.application-question, .application-question');
  const leverLabel = (el) => {
    const box = questionContainer(el);
    return box ? box.querySelector('.application-label') : null;
  };

  // The heading's text with the parts that are provably not the question removed, and a list
  // of what was removed. Done on a clone, so the page itself is never touched.
  const headingText = (head) => {
    const applied = [];
    const raw = squash(head.textContent);
    const clone = head.cloneNode(true);
    let source = clone;
    // A radio group's question lives in `div.text` inside the label; a plain field's sits as
    // the label's own text. Preferring the marked node is reading Lever's structure.
    const textNode = clone.querySelector(':scope > .text');
    if (textNode) { source = textNode; applied.push('lever_text_node_used'); }
    const hadRequired = !!source.querySelector('.required');
    let removedOther = false;
    for (const node of source.querySelectorAll(noiseSelector)) {
      if (!node.classList || !node.classList.contains('required')) removedOther = true;
      node.remove();
    }
    if (hadRequired) applied.push('required_marker_removed');
    if (removedOther) applied.push('control_and_status_excluded');
    const before = source.textContent || '';
    const match = squash(before);
    if (match !== before.trim()) applied.push('whitespace_folded');
    return { raw, match, applied, had_required_marker: hadRequired,
             source: 'lever_application_label' };
  };

  const labelFor = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) {
      return { raw: squash(aria), match: squash(aria), applied: [],
               had_required_marker: false, source: 'aria_label' };
    }
    const head = leverLabel(el);
    if (head) return headingText(head);
    return { raw: '', match: '', applied: [], had_required_marker: false, source: 'none' };
  };

  // The text of the choice a single control stands for: its own wrapping label, which is a
  // different string from the question the group asks.
  const choiceLabel = (el) => {
    const wrap = el.closest('label');
    if (!wrap) return '';
    const clone = wrap.cloneNode(true);
    for (const node of clone.querySelectorAll('input, select, textarea')) node.remove();
    return squash(clone.textContent);
  };

  const selectorFor = (el) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    if (el.name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
    return '';
  };

  const frames = Array.from(document.querySelectorAll('iframe')).map((f) => {
    let sameOrigin = false;
    let controlCount = 0;
    try {
      // Touching `contentDocument` on a cross-origin frame throws, which is the check. The
      // challenge frames are cross-origin and are never entered — this only reads `null`.
      const doc = f.contentDocument;
      if (doc) {
        sameOrigin = true;
        controlCount = doc.querySelectorAll('input, textarea, select').length;
      }
    } catch (e) { sameOrigin = false; }
    return { src: squash(f.getAttribute('src')), title: squash(f.getAttribute('title')),
             width: f.offsetWidth, height: f.offsetHeight,
             same_origin: sameOrigin, control_count: controlCount };
  });

  const out = [];
  // Container identity without touching the page. An earlier version stamped a `data-`
  // attribute on each question container, which is a write — on a page an observer promises
  // never to change, and on one a person may be looking at. A Map holds the same identity in
  // this function and leaves the document exactly as it found it.
  const containerIds = new Map();
  const containerId = (box) => {
    if (!box) return '';
    if (!containerIds.has(box)) containerIds.set(box, String(containerIds.size + 1));
    return containerIds.get(box);
  };
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || (tag === 'input' ? 'text' : '')).toLowerCase();
    const heading = labelFor(el);
    const box = questionContainer(el);
    out.push({
      tag, type,
      name: el.getAttribute('name') || '',
      id: el.id || '',
      selector: selectorFor(el),
      raw_question: heading.raw,
      match_question: heading.match,
      normalization: heading.applied,
      label_source: heading.source,
      had_required_marker: heading.had_required_marker,
      // Which of Lever's question containers this control sits in. An identity, not text: it
      // is how a control is *proved* to belong to a question rather than assumed to.
      question_container: containerId(box),
      choice_label: choiceLabel(el),
      required: el.required === true || el.getAttribute('aria-required') === 'true',
      disabled: el.disabled === true,
      visible: el.getClientRects().length > 0,
      options: tag === 'select'
        ? Array.from(el.options).map((o) => squash(o.textContent)).filter(Boolean).slice(0, 100)
        : null,
    });
  }
  return { controls: out, iframes: frames.length, frames,
           text_markers: squash(document.body ? document.body.innerText.slice(0, 20000) : ''),
           title: squash(document.title) };
}
"""


def classify_frame(frame: dict[str, Any]) -> str:
    """What this frame is, from its own attributes. Unknown is a class, not a default to ignore.

    A challenge is recognised by vendor host or by a title naming one, checked independently
    because either can be absent. Everything that is not a challenge and not one of the two
    shapes measured on a real page is `unknown_frame`, which stops the run: a frame nobody
    classified could be holding the form.
    """
    source = (frame.get("src") or "").lower()
    title = (frame.get("title") or "").strip().lower()
    if any(host in source for host in CAPTCHA_FRAME_HOSTS):
        return "captcha_frame"
    if CAPTCHA_MARKERS.search(title):
        return "captcha_frame"
    if frame.get("same_origin") and frame.get("control_count"):
        # Readable, and it holds controls. A form in a frame needs its own adapter, and
        # guessing that these controls belong to the same application is exactly the guess
        # this observer does not make.
        return "possible_form_frame"
    width, height = frame.get("width") or 0, frame.get("height") or 0
    if width <= TRACKING_PIXEL_MAX and height <= TRACKING_PIXEL_MAX:
        return "tracking_pixel"
    for embed in KNOWN_NON_FORM_EMBEDS:
        if (title == embed["title"] and width <= embed["max_width"]
                and height <= embed["max_height"]):
            return "known_non_form_embed"
    return "unknown_frame"


def classify_frames(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Every frame, with its class. Counted and reported; none silently dropped."""
    return [{"class": classify_frame(frame),
             # Attributes, not content. Nothing here was typed by anybody.
             "title": (frame.get("title") or "")[:120],
             "host": urlparse(frame.get("src") or "").hostname or "",
             "width": frame.get("width") or 0, "height": frame.get("height") or 0}
            for frame in raw.get("frames") or []]


def captcha_present(raw: dict[str, Any]) -> bool:
    """A challenge on the page, in the top document or in a frame it embeds."""
    if CAPTCHA_MARKERS.search(raw.get("text_markers") or ""):
        return True
    return any(frame["class"] == "captcha_frame" for frame in classify_frames(raw))


def _group_controls(controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Controls the DOM proves are one question, grouped; anything else left alone.

    The proof is two facts together: the same Lever question container, and the same `name`.
    HTML makes same-named radios mutually exclusive and same-named checkboxes submit as one
    list, so the pair is what an employer asking one question actually ships.

    **The container alone is not enough.** On the acceptance page the pronouns question holds
    nine `name="pronouns"` checkboxes *and* a `customPronounsOption` with no name at all, which
    reveals a text box. Same container, different group — so it is not folded in on the
    strength of sitting nearby, and it is not given a fillable action either. It becomes
    `unsupported_auxiliary_control`: the risk of answering the wrong thing is real and the
    control is worth nothing to a production fill.

    Only when more than one shares the pair. A lone checkbox — "I agree to the terms" — is a
    question whose own label is the question, and a group of one would replace it with
    whatever heading sat above it.
    """
    counts: dict[tuple[str, str, str], int] = {}
    for control in controls:
        if control["type"] in ("radio", "checkbox") and control["name"]:
            key = (control["type"], control["name"], control["question_container"])
            counts[key] = counts.get(key, 0) + 1

    # A container that holds a group and also holds an odd control out.
    grouped_containers = {key[2] for key, count in counts.items() if count >= 2 and key[2]}

    out: list[dict[str, Any]] = []
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for control in controls:
        key = (control["type"], control["name"], control["question_container"])
        if counts.get(key, 0) >= 2:
            existing = seen.get(key)
            if existing is None:
                entry = dict(control)
                entry["options"] = [control["choice_label"]] if control["choice_label"] else []
                entry["id"] = ""
                entry["selector"] = f'{control["tag"]}[name="{control["name"]}"]'
                entry["grouping_basis"] = "lever_question_container"
                entry["automation"] = "fillable"
                seen[key] = entry
                out.append(entry)
            elif control["choice_label"]:
                existing["options"].append(control["choice_label"])
            continue
        entry = dict(control)
        if (control["type"] in ("radio", "checkbox")
                and control["question_container"] in grouped_containers):
            # In a container whose question is answered by a group this control is not part of.
            entry["automation"] = "unsupported_auxiliary_control"
            entry["grouping_basis"] = "not_in_the_container_group"
        else:
            entry["automation"] = "fillable"
            entry["grouping_basis"] = "single_control"
        out.append(entry)
    return out


def build_fields(raw: dict[str, Any], connection: sqlite3.Connection | None
                 ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Turn what the page showed into the observation shape, or stop.

    Every refusal here is a stop rather than a default. A control kind nobody classified, a
    field with no readable question, two fields with one id, two questions with one reviewed
    meaning: each is a thing a person has to look at, and recording a guess would put that
    guess into a form.
    """
    skipped = {"structural": 0, "challenge_field": 0, "disabled": 0, "not_visible": 0}
    controls = []
    for entry in raw["controls"]:
        if entry["tag"] == "input" and entry["type"] in IGNORED_INPUT_TYPES:
            skipped["structural"] += 1
        elif CHALLENGE_FIELD_NAMES.search(f"{entry['name']} {entry['id']}"):
            # The challenge's own response token. Excluded before the label rule sees it, and
            # counted rather than dropped: never read, never hashed, never in a package.
            skipped["challenge_field"] += 1
        elif entry["disabled"]:
            skipped["disabled"] += 1
        else:
            # Hidden controls are kept. What a page asks is worth knowing even where it is
            # not shown yet, and the alternative — counting them and moving on — let a
            # collapsed EEO section pass unmentioned.
            controls.append(entry)
    if not controls:
        raise Paused("no_fields_found")

    fields: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for control in _group_controls(controls):
        tag, type_name = control["tag"], control["type"]
        if tag == "textarea":
            kind = "textarea"
        elif tag == "select":
            kind = "select"
        else:
            kind = CONTROL_BY_INPUT_TYPE.get(type_name)
        if kind is None:
            raise Paused("unsupported_control", f"{tag}:{type_name}")
        hidden = not control.get("visible", True)
        question = control["match_question"] or control["choice_label"]
        illegible = not question or control["label_source"] == "none"
        if illegible and not hidden:
            # A shown control nobody can read stops the run: it is part of the form in front
            # of the user and a guess about it would reach a real answer.
            if not question:
                raise Paused("unlabelled_field", control["selector"] or f"{tag}:{type_name}")
            raise Paused("ambiguous_label", control["selector"] or f"{tag}:{type_name}")
        if not control["selector"]:
            raise Paused("unlabelled_field", "no_stable_selector")

        if illegible:
            # Hidden and unreadable. Recorded with what little is provable — never a guessed
            # question — and it blocks the planner by its automation state.
            question = ""
        field_id = control["id"] or control["name"]
        field_id = re.sub(r"[^A-Za-z0-9._:-]", "-", field_id)[:128].strip("-")
        if not field_id:
            if hidden:
                # Nothing to name it by and nothing to read: counted, and the page is still
                # observed, because the planner gate below refuses to run on it.
                skipped["hidden_unnameable"] = skipped.get("hidden_unnameable", 0) + 1
                continue
            raise Paused("unlabelled_field", "no_stable_identifier")
        if field_id in seen_ids:
            raise Paused("duplicate_field_id", field_id)
        seen_ids.add(field_id)

        # The required marker is Lever's own element, so its presence is DOM evidence and is
        # recorded with the basis that carried it. Removing the glyph from `match_question` is
        # only sound because requiredness survives independently of the glyph.
        required = bool(control["required"]) or bool(control["had_required_marker"])
        basis = ("attribute_or_aria" if control["required"]
                 else "lever_required_marker" if control["had_required_marker"] else "absent")
        field: dict[str, Any] = {
            "field_id": field_id,
            # The employer's own words, never edited. What the structure hash covers.
            "raw_question": control["raw_question"] or question,
            # The same question with the enumerated transformations applied. The only string
            # an exact canonical lookup is ever done on.
            "match_question": question,
            "normalization": list(control["normalization"]),
            "label_source": control["label_source"],
            "selector": control["selector"],
            "control": kind,
            "required": required,
            "required_basis": basis,
            "visibility": "hidden" if hidden else "visible",
            "grouping_basis": control.get("grouping_basis", "single_control"),
            # Carried on the field rather than read from the loop variable of an earlier
            # pass, which is what the first version did — and it left the auxiliary pronouns
            # toggle marked fillable because `control` by then meant the last control seen.
            "_grouping_automation": control.get("automation", "fillable"),
        }
        if control["options"]:
            field["options"] = control["options"]
        fields.append(field)

    meanings = canonical_meanings(connection, [f["match_question"] for f in fields])
    claimed: dict[str, str] = {}
    for field in fields:
        # Who may answer it at all, from the module that already owns that question. The
        # observer reports the disposition; it does not act on it.
        disposition, domain, family = field_policy.disposition(
            field_id=field["field_id"], question=field["raw_question"],
            control=field["control"], source_kind=None)
        field["disposition"] = disposition
        field["domain"] = domain
        field["family"] = family
        # One automation state per control, decided in order of consequence. A hidden control
        # is still shown its disposition — a collapsed EEO section is `always_manual`, and
        # saying so is the point of observing it at all.
        if field["visibility"] == "hidden":
            field["automation"] = "hidden_unknown" if not field["match_question"] \
                else "not_visible"
        elif field["_grouping_automation"] == "unsupported_auxiliary_control":
            field["automation"] = "unsupported_auxiliary_control"
        elif disposition == "always_manual":
            field["automation"] = "manual_only"
        elif field["control"] == "file":
            field["automation"] = "material"
        elif disposition == "unsupported":
            field["automation"] = "unsupported_auxiliary_control"
        else:
            field["automation"] = "fillable"

        if field["automation"] != "fillable":
            # Never planned against, so never matched: a meaning attached to a control nothing
            # may fill is an invitation for something later to try.
            field["canonical_id"] = None
            field.pop("_grouping_automation", None)
            field["field_sha256"] = field_digest(field)
            continue
        canonical_id = meanings[field["match_question"]]
        if canonical_id is None:
            field["automation"] = "no_canonical_meaning"
        field["canonical_id"] = canonical_id
        if canonical_id is not None:
            if canonical_id in claimed and claimed[canonical_id] != field["field_id"]:
                raise Paused("conflicting_canonical_meaning", canonical_id)
            claimed[canonical_id] = field["field_id"]
        field.pop("_grouping_automation", None)
        field["field_sha256"] = field_digest(field)
    return fields, skipped


def _guard(page, expected_url: str) -> None:
    """Nothing here may leave the page it was pointed at.

    `fill_worker` installs a routing guard for the same reason and this mirrors it: a page
    that navigates while being read is a page whose observation would describe one document
    and be labelled with another.
    """
    def _refuse_navigation(frame) -> None:
        if frame is page.main_frame and frame.url.split("#")[0] != expected_url.split("#")[0]:
            raise Paused("navigated_away")
    page.on("framenavigated", _refuse_navigation)


def observe(url: str, *, connection: sqlite3.Connection | None = None,
            headed: bool = True, timeout_ms: int = 30000) -> dict[str, Any]:
    """One page, read twice, and an observation only if it held still.

    Read twice because a form that rewrites itself between two reads is a form whose structure
    hash means nothing, and that is exactly the page a later fill would go wrong on. The second
    read is compared on the digest, not on the text, so a changing advert or a ticking clock in
    the page does not count as the form changing.
    """
    target = check_origin(url)
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    with sync_playwright() as playwright:
        # Headed by default: this runs on a real employer's page, and the ADR's live gate is a
        # supervised test. A person who cannot see the page cannot supervise it.
        browser = playwright.chromium.launch(headless=not headed)
        try:
            context = browser.new_context()
            page = context.new_page()
            _ = page
            page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
            if page.url.split("#")[0] != target.split("#")[0]:
                # A redirect off the approved host is the case worth naming: the address bar
                # decides nothing, the host does.
                check_origin(page.url)
            _guard(page, page.url)
            page.wait_for_timeout(1200)

            first = page.evaluate(READ_STRUCTURE, LABEL_NOISE_SELECTOR)
            frames = classify_frames(first)
            # Unknown and form-bearing frames stop the run. A challenge frame does not: it is
            # allowed to exist beside the form, and is never entered, read or operated.
            for frame in frames:
                if frame["class"] == "possible_form_frame":
                    raise Paused("possible_form_frame")
                if frame["class"] == "unknown_frame":
                    raise Paused("unknown_frame_present")
            challenge = captcha_present(first)

            fields, skipped = build_fields(first, connection)
            if challenge and not fields:
                # Nothing to answer beside a challenge means the challenge is the gate, and
                # a form that appears only after it is passed is a form nobody has observed.
                raise Paused("captcha_gates_the_form")
            digest = page_digest(fields)

            page.wait_for_timeout(800)
            second_raw = page.evaluate(READ_STRUCTURE, LABEL_NOISE_SELECTOR)
            second, _ = build_fields(second_raw, connection)
            if page_digest(second) != digest:
                raise Paused("page_changed_under_observation")
            if captcha_present(second_raw) != challenge:
                # A challenge that appeared while the page was being read is a challenge
                # something triggered, and the run stops rather than continuing beside it.
                raise Paused("page_changed_under_observation")

            observed_url = page.url
        except PlaywrightError as error:
            # A supervised run happens in a window a person can close, and losing the target
            # mid-read is an ordinary thing rather than a crash. It is also what a page that
            # navigates itself away looks like from here, so it stops with a code either way.
            message = str(error).lower()
            if "closed" in message or "navigat" in message:
                raise Paused("page_closed_or_navigated") from None
            raise Paused("browser_error") from None
        finally:
            browser.close()

    return {
        "schema_version": "0.2.0",
        "observer_version": OBSERVER_VERSION,
        "page_url": observed_url,
        "page_sha256": digest,
        "field_count": len(fields),
        "fields": fields,
        # `fill_core.observe_page` wants these two; an observer that cannot tell a legal
        # attestation from an ordinary checkbox says so by leaving them empty rather than
        # guessing which checkbox was the agreement.
        "legal_items": [],
        "restricted_requests": [],
        "values_read": False,
        "database_writes": 0,
        # Every frame on the page and what it was taken to be. Reported even when all of them
        # were benign, so "there were no frames" and "the frames were fine" stay different
        # statements.
        "frames": frames,
        # Counted, never silently dropped: a challenge's response token, a control nobody can
        # see, a disabled one, and the structural inputs every form carries.
        "skipped": skipped,
        # The three fields a later stage reads before it does anything at all.
        # The planner gate, computed here so nothing downstream has to re-derive it.
        # Every fillable field has a meaning by construction now, so the gate is about what
        # could not be read at all and about nothing being left in an unnamed state.
        "planner_ready": (not any(f["automation"] == "hidden_unknown" for f in fields)
                          and all(f["automation"] in AUTOMATION_STATES for f in fields)
                          and all(f["canonical_id"] for f in fields
                                  if f["automation"] == "fillable")),
        "automation_counts": {state: sum(1 for f in fields if f["automation"] == state)
                              for state in AUTOMATION_STATES},
        "captcha_present": challenge,
        "captcha_handling": "user_required" if challenge else "not_present",
        "user_takeover_required": challenge,
        "automation_scope": ("non_challenge_fields_only" if challenge else "all_observed_fields"),
    }


def write_observation(observation: dict[str, Any], output: Path) -> dict[str, Any]:
    """The observation, in the private root, readable only by its owner.

    A file and not a row. Reading a page is not an application event, and a database write
    here would be the first thing to argue with later about what it meant.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(observation, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return {"status": "written", "field_count": observation["field_count"],
            "page_sha256": observation["page_sha256"]}


def summarise(observation: dict[str, Any]) -> str:
    """What to print. Questions and dispositions, and no value, because there are none."""
    lines = [f"{observation['field_count']} fields · page {observation['page_sha256'][:12]}"
             + (f" · planner_ready={observation['planner_ready']}"
                if "planner_ready" in observation else "")]
    if observation.get("automation_counts"):
        lines.append("  automation: " + ", ".join(
            f"{k}={v}" for k, v in observation["automation_counts"].items() if v))
    if observation.get("captcha_present"):
        lines.append(f"  CAPTCHA present · handling={observation['captcha_handling']}"
                     f" · scope={observation['automation_scope']}"
                     f" · user_takeover_required={observation['user_takeover_required']}")
    frames = observation.get("frames") or []
    if frames:
        counts: dict[str, int] = {}
        for item in frames:
            counts[item["class"]] = counts.get(item["class"], 0) + 1
        lines.append("  frames: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if observation.get("skipped"):
        lines.append("  skipped: " + ", ".join(
            f"{k}={v}" for k, v in sorted(observation["skipped"].items()) if v))
    for field in observation["fields"]:
        meaning = field["canonical_id"] or "-"
        required = "required" if field["required"] else "optional"
        auto = field["automation"] if field["automation"] != "fillable" else ""
        hidden = "hidden" if field.get("visibility") == "hidden" else ""
        lines.append(f"  {field['control']:9} {required:8} {hidden:6} {meaning:26}"
                     f" {auto:28} {field['match_question'][:46]}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="a jobs.lever.co posting")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--db", type=Path, help="read-only, for reviewed question forms")
    parser.add_argument("--headless", action="store_true",
                        help="for tests; the live acceptance is supervised and headed")
    args = parser.parse_args()
    connection = None
    if args.db:
        connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
    try:
        observation = observe(args.url, connection=connection, headed=not args.headless)
    except Paused as paused:
        print(json.dumps({"status": "paused", "reason": paused.code,
                          "detail": paused.detail}, indent=2))
        raise SystemExit(2)
    finally:
        if connection is not None:
            connection.close()
    print(json.dumps(write_observation(observation, args.output), indent=2))
    print(summarise(observation))


if __name__ == "__main__":
    main()
