"""Read a Jobloom replay page and produce a protocol observation from what is there.

This closes the half of the chain the end-to-end test could not otherwise reach. Handing
`fill_core` an observation written by hand proves planner → worker → import and nothing about
whether the observation matches the page: selector, control type, requiredness, options and
the final control could all drift while the suite stayed green. That is precisely the shape of
gap that hid the upload bug, where a package built by hand did not match the one production
emits.

**This is not an ATS adapter and must not be mistaken for one.** It reads the replay Jobloom
itself renders, whose controls carry a stable `data-test-id` and a `data-kind`; a live employer
page has neither. A production adapter is a separate thing that a supervised live acceptance
test would have to earn.

No model, no `page.evaluate`, no frame traversal — `page.locator` searches the top frame only,
so anything inside an iframe is invisible here rather than reachable. Every attribute is read
through Playwright's own accessors, and anything ambiguous, duplicated, hidden or unmapped
fails closed instead of being guessed at.

Being unable to see into a frame is not the same as being safe about one. An observation that
silently omits whatever a frame holds describes a form that is not the form on screen, and the
planner downstream has no way to tell the difference between "this page has three fields" and
"this page has three fields I could see". The same goes for a control that carries no
`data-test-id`: the old walk started from `[data-test-id]`, so an unidentified control was not
refused, it was never looked at. Both are now refusals of the whole observation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "jobloom" / "scripts"))

import semantic_replay  # noqa: E402

# What the replay's own markup maps a control to. Upload kinds are this observer's business;
# which fact a control reaches is not, and used to be a three-entry table of internal fact ids
# written here. A browser has never heard of `fact-name`. The reviewed corpus already records
# what each recorded control *means* - `expected_target` in the disposition approval - so the
# observer reports that and the planner resolves it against the locked snapshot.
UPLOAD_KINDS = {"resume.file": "resume", "cover_letter.file": "cover_letter"}
_APPROVAL = json.loads(
    (ROOT / "tests" / "fixtures" / "ats-semantic" / "FIELD-DISPOSITION-APPROVAL.json")
    .read_text(encoding="utf-8"))
CANONICAL_TARGETS = {
    entry["kind"]: entry["expected_target"] for entry in _APPROVAL["entries"]
    if entry["expected_disposition"] == "fact"
}


# A nested browsing context this observer cannot read and must not ignore.
FRAME_TAGS = ("iframe", "frame", "frameset", "object", "embed")

# Everything a person can put a value into. `option` is deliberately absent: it is part of the
# `select` that contains it, not a control in its own right.
INTERACTIVE = ("input", "select", "textarea", "button")


class ObservationRefused(RuntimeError):
    """The page cannot be described unambiguously, so it is not described at all."""


def _refuse_frames(page) -> None:
    """A page with a frame in it is not describable from the top frame alone."""
    for tag in FRAME_TAGS:
        count = page.locator(tag).count()
        if count:
            raise ObservationRefused(
                f"page embeds {count} nested browsing context(s): {tag}")


def _refuse_unidentified(page, described: list[str]) -> None:
    """Every interactive control on the page must be one of the ones being described.

    Walking `[data-test-id]` answers "what did the replay label?", never "what is on this
    page?". A control with no `data-test-id` — which is every control on a real employer form
    — produced no error and no field; it simply did not exist as far as the observation was
    concerned. Sweeping from the tag side is the direction that can see it.
    """
    known = set(described)
    for tag in INTERACTIVE:
        controls = page.locator(tag)
        for index in range(controls.count()):
            control = controls.nth(index)
            test_id = control.get_attribute("data-test-id")
            if not test_id:
                name = control.get_attribute("name") or control.get_attribute("id") or tag
                raise ObservationRefused(f"control carries no reviewed identity: {name}")
            # A radio member belongs to the fieldset that is its group; the group is the
            # control being described, so the member is covered when the group is.
            group = test_id.split("--", 1)[0]
            if test_id not in known and group not in known:
                raise ObservationRefused(f"control is outside the described set: {test_id}")


def _one(page, selector: str):
    locator = page.locator(selector)
    count = locator.count()
    if count != 1:
        raise ObservationRefused(
            f"{'duplicate' if count > 1 else 'missing'} control: {selector}")
    return locator


def _control_kind(page, test_id: str) -> str:
    """Which control this is, decided by asking for it under each tag in turn."""
    for tag, control in (("select", "select"), ("textarea", "textarea"),
                         ("fieldset", "radio")):
        if page.locator(f'{tag}[data-test-id="{test_id}"]').count() == 1:
            return control
    field = page.locator(f'input[data-test-id="{test_id}"]')
    if field.count() != 1:
        raise ObservationRefused(f"unrecognised control: {test_id}")
    kind = field.get_attribute("type") or "text"
    return {"file": "file", "submit": "submit", "checkbox": "checkbox",
            "radio": "radio"}.get(kind, "text")


def _question(page, test_id: str, control: str) -> str:
    if control == "radio":
        legend = page.locator(f'fieldset[data-test-id="{test_id}"] legend')
        if legend.count() == 1:
            return legend.inner_text().strip()
    label = page.locator(f'label[for="{test_id}"]')
    if label.count() == 1:
        return label.inner_text().strip()
    field = page.locator(f'[data-test-id="{test_id}"]')
    return (field.get_attribute("value") or test_id).strip()


def _options(page, test_id: str, control: str) -> list[dict[str, str]] | None:
    if control != "select":
        return None
    options = page.locator(f'select[data-test-id="{test_id}"] option')
    found = []
    for index in range(options.count()):
        option = options.nth(index)
        value = option.get_attribute("value") or ""
        label = option.inner_text().strip()
        if value and label:
            found.append({"label": label, "value": value})
    return found or None


def observe(page, page_id: str, page_index: int, page_url: str, *, locale: str | None = None,
            final: bool = False, predecessor: str | None = None) -> dict[str, Any]:
    """Describe the page in front of the browser, in the protocol's own vocabulary."""
    _refuse_frames(page)
    identified = page.locator("[data-test-id]")
    seen: list[str] = []
    for index in range(identified.count()):
        test_id = identified.nth(index).get_attribute("data-test-id")
        if not test_id or test_id in seen or "--" in test_id or test_id == "next-page":
            # Radio options carry `<group>--<n>`; the group itself is the control.
            continue
        seen.append(test_id)
    # Before anything is described, and from the other direction: nothing on the page may be
    # outside the set about to be walked.
    _refuse_unidentified(page, seen)
    fields: list[dict[str, Any]] = []
    for test_id in seen:
        element = _one(page, f'[data-test-id="{test_id}"]')
        control = _control_kind(page, test_id)
        if control == "submit":
            fields.append({"field_id": test_id, "question": _question(page, test_id, control),
                           "selector": f'[data-test-id="{test_id}"]', "control": "submit",
                           "required": True, "sensitivity": "normal"})
            continue
        if not element.is_visible() or not element.is_enabled():
            # A hidden or disabled control is reported as unsupported, never filled.
            raise ObservationRefused(f"control is not actionable: {test_id}")
        kind = element.get_attribute("data-kind")
        if not kind:
            raise ObservationRefused(f"control carries no reviewed kind: {test_id}")
        disposition, target = semantic_replay.disposition_for(kind)
        field: dict[str, Any] = {
            "field_id": test_id, "question": _question(page, test_id, control),
            "selector": f'[data-test-id="{test_id}"]', "control": control,
            "required": element.get_attribute("required") is not None,
            "sensitivity": "normal",
        }
        options = _options(page, test_id, control)
        if options:
            field["options"] = options
        if disposition == "material":
            field["upload_kind"] = UPLOAD_KINDS[kind]
        elif disposition == "fact":
            if kind not in CANONICAL_TARGETS:
                raise ObservationRefused(f"no reviewed meaning is recorded for {kind}")
            field["source_kind"] = "fact"
            field["canonical_id"] = CANONICAL_TARGETS[kind]
        elif disposition == "answer":
            field["source_kind"] = "answer"
        # `always_manual` and `unsupported` carry no source: the planner decides, from the
        # question text, that they are the user's.
        fields.append(field)
    if not fields:
        raise ObservationRefused("no identified controls on this page")
    observation = {
        "page_id": page_id, "page_index": page_index, "page_url": page_url,
        "fields": fields, "legal_items": [], "restricted_requests": [],
        "final_page": final,
    }
    if locale:
        observation["locale"] = locale
    if predecessor:
        observation["predecessor_checkpoint_sha256"] = predecessor
    return observation
