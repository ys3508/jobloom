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
    "unlabelled_field", "no_fields_found",
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

    The inputs are the label, the control kind, requiredness and the choices offered. A page
    that changes its question changes this; a page the user typed into does not.
    """
    return _digest({key: field[key] for key in
                    ("question", "control", "required", "options") if key in field})


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
() => {
  const labelFor = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return aria.trim();
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const parts = by.split(/\s+/).map((id) => document.getElementById(id))
        .filter(Boolean).map((n) => n.textContent.trim()).filter(Boolean);
      if (parts.length) return parts.join(' ');
    }
    if (el.id) {
      const tied = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (tied && tied.textContent.trim()) return tied.textContent.trim();
    }
    const wrapping = el.closest('label');
    if (wrapping && wrapping.textContent.trim()) return wrapping.textContent.trim();
    return groupLabel(el);
  };
  // The question a set of choices is asking, which is a different string from any one choice.
  // A radio's own label is "Yes"; what is being asked sits on the fieldset or the list item
  // around it, and losing that would record a form as asking "Yes".
  const groupLabel = (el) => {
    // Walked up, not `closest`. On a real Lever form the nearest container is the wrapper
    // holding the control itself — `div.application-field` — and the heading sits one level
    // further out on `li.application-question`. Stopping at the first container found a
    // wrapper with no heading in it and reported the field as unreadable.
    let node = el.parentElement;
    for (let depth = 0; node && depth < 12; depth += 1, node = node.parentElement) {
      // A heading is a candidate only if it holds no form control at all. Excluding just the
      // ones containing *this* control was not enough: on a real Lever page the level above a
      // checkbox holds its siblings' labels too, and the rule picked "She/her" — another
      // choice — as what the form was asking. A question is the text beside the controls, so
      // anything wrapping a control is a choice and never a heading.
      for (const heading of node.querySelectorAll(
          'legend, .application-label, label, h3, h4')) {
        if (heading.querySelector('input, select, textarea')) continue;
        if (heading.contains(el) || !heading.textContent.trim()) continue;
        return heading.textContent.trim();
      }
      if (node.tagName === 'FORM') break;
    }
    return '';
  };
  const squash = (text) => text.replace(/\s+/g, ' ').trim().slice(0, 2000);
  const selectorFor = (el) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    if (el.name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
    return '';
  };
  const out = [];
  const nodes = document.querySelectorAll('input, textarea, select');
  for (const el of nodes) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || (tag === 'input' ? 'text' : '')).toLowerCase();
    const entry = {
      tag, type,
      name: el.getAttribute('name') || '',
      id: el.id || '',
      selector: selectorFor(el),
      label: squash(labelFor(el)),
      group_label: squash(groupLabel(el)),
      required: el.required === true || el.getAttribute('aria-required') === 'true',
      disabled: el.disabled === true,
      // Whether a person can see it. A control with no box on the page is not a question
      // being asked, and a challenge's bookkeeping textarea is exactly that shape.
      visible: el.getClientRects().length > 0,
      // Choices are part of the question, not of the answer: which options exist is what the
      // employer is asking, and none of them is marked as chosen here.
      options: tag === 'select'
        ? Array.from(el.options).map((o) => squash(o.textContent)).filter(Boolean).slice(0, 100)
        : null,
    };
    out.push(entry);
  }
  // Frames are inventoried by src and title only. Neither is a value somebody typed, and
  // both are needed to tell a CAPTCHA challenge from a share widget.
  const frames = Array.from(document.querySelectorAll('iframe')).map((f) => {
    let sameOrigin = false;
    let controlCount = 0;
    try {
      // Touching `contentDocument` on a cross-origin frame throws, which is the check: a
      // frame this cannot read is a frame this cannot classify by content. The challenge
      // frames are cross-origin and are never entered — this only ever reads `null`.
      const doc = f.contentDocument;
      if (doc) {
        sameOrigin = true;
        controlCount = doc.querySelectorAll('input, textarea, select').length;
      }
    } catch (e) { sameOrigin = false; }
    return {
      src: squash(f.getAttribute('src') || ''),
      title: squash(f.getAttribute('title') || ''),
      width: f.offsetWidth, height: f.offsetHeight,
      same_origin: sameOrigin, control_count: controlCount,
    };
  });
  return {
    controls: out,
    iframes: frames.length,
    frames,
    text_markers: squash(document.body ? document.body.innerText.slice(0, 20000) : ''),
    title: squash(document.title || ''),
  };
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


def _group_by_name(controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Controls that share a name are one question, however many boxes the page drew.

    Radios and checkboxes both. HTML says so for each: same-named radios are mutually
    exclusive and same-named checkboxes submit as one list, so an employer asking "which
    pronouns do you use" ships nine inputs and one question. The 2026-09-11 acceptance run
    found exactly that and the first version reported nine fields colliding on one id.

    **Only when more than one shares the name.** A lone checkbox — "I agree to the terms" —
    is a question whose own label is the question, and turning it into a group of one would
    replace that label with whatever heading happened to sit above it.

    The group's question is the enclosing heading, and each control's label is a *choice*.
    A group with no heading has no legible question and stops the run: recording "Yes" or
    "She/her" as what a form asks would send the next step looking up its reviewed meaning.
    """
    counts: dict[tuple[str, str], int] = {}
    for control in controls:
        if control["type"] in ("radio", "checkbox") and control["name"]:
            key = (control["type"], control["name"])
            counts[key] = counts.get(key, 0) + 1

    grouped: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for control in controls:
        key = (control["type"], control["name"])
        if counts.get(key, 0) < 2:
            grouped.append(control)
            continue
        existing = seen.get(key)
        if existing is None:
            entry = dict(control)
            entry["options"] = [control["label"]] if control["label"] else []
            entry["label"] = control.get("group_label") or ""
            # The shared name is the group's identity; the per-box id belongs to one choice.
            entry["id"] = ""
            entry["selector"] = f'{control["tag"]}[name="{control["name"]}"]'
            seen[key] = entry
            grouped.append(entry)
        elif control["label"]:
            existing["options"].append(control["label"])
    return grouped


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
        elif not entry.get("visible", True):
            skipped["not_visible"] += 1
        else:
            controls.append(entry)
    if not controls:
        raise Paused("no_fields_found")

    fields: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for control in _group_by_name(controls):
        tag, type_name = control["tag"], control["type"]
        if tag == "textarea":
            kind = "textarea"
        elif tag == "select":
            kind = "select"
        else:
            kind = CONTROL_BY_INPUT_TYPE.get(type_name)
        if kind is None:
            raise Paused("unsupported_control", f"{tag}:{type_name}")
        if not control["label"]:
            raise Paused("unlabelled_field", control["selector"] or f"{tag}:{type_name}")
        if not control["selector"]:
            raise Paused("unlabelled_field", "no_stable_selector")

        field_id = control["id"] or control["name"]
        field_id = re.sub(r"[^A-Za-z0-9._:-]", "-", field_id)[:128].strip("-")
        if not field_id:
            raise Paused("unlabelled_field", "no_stable_identifier")
        if field_id in seen_ids:
            raise Paused("duplicate_field_id", field_id)
        seen_ids.add(field_id)

        field: dict[str, Any] = {
            "field_id": field_id,
            "question": control["label"],
            "selector": control["selector"],
            "control": kind,
            "required": bool(control["required"]),
        }
        if control["options"]:
            field["options"] = control["options"]
        fields.append(field)

    meanings = canonical_meanings(connection, [f["question"] for f in fields])
    claimed: dict[str, str] = {}
    for field in fields:
        # Who may answer it at all, from the module that already owns that question. The
        # observer reports the disposition; it does not act on it.
        disposition, domain, family = field_policy.disposition(
            field_id=field["field_id"], question=field["question"],
            control=field["control"], source_kind=None)
        field["disposition"] = disposition
        field["domain"] = domain
        field["family"] = family
        canonical_id = meanings[field["question"]]
        field["canonical_id"] = canonical_id
        if canonical_id is not None:
            if canonical_id in claimed and claimed[canonical_id] != field["field_id"]:
                raise Paused("conflicting_canonical_meaning", canonical_id)
            claimed[canonical_id] = field["field_id"]
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
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        # Headed by default: this runs on a real employer's page, and the ADR's live gate is a
        # supervised test. A person who cannot see the page cannot supervise it.
        browser = playwright.chromium.launch(headless=not headed)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
            if page.url.split("#")[0] != target.split("#")[0]:
                # A redirect off the approved host is the case worth naming: the address bar
                # decides nothing, the host does.
                check_origin(page.url)
            _guard(page, page.url)
            page.wait_for_timeout(1200)

            first = page.evaluate(READ_STRUCTURE)
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
            second_raw = page.evaluate(READ_STRUCTURE)
            second, _ = build_fields(second_raw, connection)
            if page_digest(second) != digest:
                raise Paused("page_changed_under_observation")
            if captcha_present(second_raw) != challenge:
                # A challenge that appeared while the page was being read is a challenge
                # something triggered, and the run stops rather than continuing beside it.
                raise Paused("page_changed_under_observation")

            observed_url = page.url
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
    lines = [f"{observation['field_count']} fields · page {observation['page_sha256'][:12]}"]
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
        lines.append(f"  {field['control']:9} {required:8} {meaning:28} {field['question'][:58]}")
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
