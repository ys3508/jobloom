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
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "jobloom" / "scripts"))

import semantic_replay  # noqa: E402

# What the replay's own markup maps a control to. `_plan_upload` needs a kind, a fact needs
# its id; anything not named here is reported as unsupported rather than filled.
FACT_IDS = {
    "contact.full_name": "fact-name",
    "location.city_state": "fact-city",
    "employment.current_company": "fact-company",
}
UPLOAD_KINDS = {"resume.file": "resume", "cover_letter.file": "cover_letter"}


class ObservationRefused(RuntimeError):
    """The page cannot be described unambiguously, so it is not described at all."""


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
    identified = page.locator("[data-test-id]")
    seen: list[str] = []
    fields: list[dict[str, Any]] = []
    for index in range(identified.count()):
        test_id = identified.nth(index).get_attribute("data-test-id")
        if not test_id or test_id in seen or "--" in test_id or test_id == "next-page":
            # Radio options carry `<group>--<n>`; the group itself is the control.
            continue
        seen.append(test_id)
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
            if kind not in FACT_IDS:
                raise ObservationRefused(f"no candidate fact is mapped for {kind}")
            field["source_kind"] = "fact"
            field["source_id"] = FACT_IDS[kind]
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
