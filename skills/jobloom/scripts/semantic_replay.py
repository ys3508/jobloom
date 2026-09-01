#!/usr/bin/env python3
"""Turn reviewed semantic fixtures into local HTML, and say plainly what that proves.

The fixtures under `tests/fixtures/ats-semantic/upstream/` are compiled semantic models from
`neonwatty/job-apply-plugin` (MIT, Jeremy Watt) — field kinds, ARIA roles, labels, choices and
requiredness, with the original recording reduced upstream to a hash that is not published.
They are evidence that a field combination came from a real recording. They are not DOM, not
selectors, and not evidence that current Lever, Greenhouse or Ashby markup can be filled.

So this renderer is Jobloom-owned code producing Jobloom-owned markup. It reproduces no
upstream HTML, because none was published. What a passing replay tests is whether Jobloom
handles the *combination of fields* real employers ship — the part that is genuinely
transferable — and nothing about live selectors.

Every upstream `kind` must appear in `KIND_DISPOSITIONS`. An unmapped kind is not rendered
as an ordinary text box; it fails closed, because a kind nobody classified is a kind nobody
decided the authority for.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import field_policy  # noqa: E402

UPSTREAM_COMMIT = "081a5d9d793da29111e2d5331767021718f1d8b5"
UPSTREAM_URL = "https://github.com/neonwatty/job-apply-plugin"
UPSTREAM_LICENSE = "MIT"

ROLE_CONTROLS = {"textbox": "text", "combobox": "select", "radiogroup": "radio", "file": "file"}

# Which protected authority may answer each upstream kind. Jobloom's four immigration
# meanings are never collapsed into upstream's broader sponsorship names: those map to a
# pause, not to an answer, exactly as `field_policy.sponsorship_is_ambiguous` requires.
KIND_DISPOSITIONS: dict[str, tuple[str, str]] = {
    # contact and profile facts
    "contact.full_name": ("fact", "contact.full_name"),
    "contact.first_name": ("fact", "contact.first_name"),
    "contact.last_name": ("fact", "contact.last_name"),
    "contact.preferred_name": ("fact", "contact.preferred_name"),
    "contact.email": ("fact", "contact.email"),
    "contact.phone": ("fact", "contact.phone"),
    "contact.phone_country": ("fact", "contact.phone_country"),
    "contact.location": ("fact", "contact.location"),
    "contact.location_city": ("fact", "contact.location_city"),
    "location.city_state": ("fact", "contact.location_city"),
    "profile.linkedin": ("fact", "profile.linkedin"),
    "profile.github": ("fact", "profile.github"),
    "profile.portfolio": ("fact", "profile.portfolio"),
    "profile.website": ("fact", "profile.website"),
    "profile.location_url": ("fact", "profile.location_url"),
    # career evidence
    "employment.current_company": ("fact", "employment.current_company"),
    "employment.prior_company": ("answer", "prior_employment_at_this_company"),
    "employment.prior_affiliate": ("answer", "prior_employment_at_an_affiliate"),
    # materials
    "resume.file": ("material", "resume"),
    "cover_letter.file": ("material", "cover_letter"),
    # legal status: four separate canonical meanings, never merged
    "authorization.work_authorized": ("answer", "work_authorized_now"),
    "authorization.us_citizen": ("answer", "citizenship_status"),
    "authorization.green_card": ("answer", "permanent_residence_status"),
    "location.us_resident": ("answer", "current_country_of_residence"),
    # upstream's broad sponsorship controls cover more than one meaning at once
    "authorization.sponsorship_status": ("always_manual", "sponsorship_meaning_ambiguous"),
    "authorization.sponsorship_select": ("always_manual", "sponsorship_meaning_ambiguous"),
    # employer-defined brackets and per-employer disclosures
    "compensation.total_range": ("always_manual", "employer_defined_compensation_manual"),
    "compensation.target_salary": ("always_manual", "employer_defined_compensation_manual"),
    "conflict.related_person": ("always_manual", "employer_entity_not_approved"),
    "conflict.customer_partner_reseller": ("always_manual", "employer_entity_not_approved"),
    # acquisition source: the user's statement, never inferred
    "source.discovery": ("answer", "discovery_source"),
    "source.discovery_radio": ("answer", "discovery_source"),
    "referral.contact": ("always_manual", "referral_contact_requires_user"),
    # voluntary protected characteristics
    "eeo.race": ("always_manual", "voluntary_eeo"),
    "eeo.gender": ("always_manual", "voluntary_eeo"),
    "eeo.disability": ("always_manual", "voluntary_eeo"),
    "eeo.veteran": ("always_manual", "voluntary_eeo"),
}

# Jobloom-owned hazards, not upstream content. Each one must pause.
SAFETY_VARIANTS = {
    "hidden_control": ("text", "A hidden question"),
    "disabled_control": ("text", "A disabled question"),
    "duplicate_control": ("text", "A duplicated question"),
    "dom_replaced_control": ("text", "A question replaced after observation"),
    "unknown_question": ("text", "A question Jobloom has never seen"),
    "standard_attestation": ("checkbox", "I certify the above is accurate"),
    "ambiguous_role": ("text", "A question whose role is ambiguous"),
    "captcha": ("text", "Solve this challenge to continue"),
    "payment": ("text", "Card number for the application fee"),
    "identity_document": ("file", "Upload a government identity document"),
    "assessment": ("text", "Begin the timed assessment"),
    "biometric_video": ("file", "Record a video introduction"),
    "iframe_control": ("text", "A question inside a nested frame"),
    "unexpected_origin": ("text", "A question served from another origin"),
}


class UnmappedKind(ValueError):
    """An upstream kind nobody classified. Not rendered; not guessed at."""


def load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def disposition_for(kind: str) -> tuple[str, str]:
    if kind not in KIND_DISPOSITIONS:
        raise UnmappedKind(f"upstream kind has no Jobloom disposition: {kind}")
    return KIND_DISPOSITIONS[kind]


def control_for(role: str) -> str:
    if role not in ROLE_CONTROLS:
        raise UnmappedKind(f"upstream role has no Jobloom control: {role}")
    return ROLE_CONTROLS[role]


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def render_control(control: dict[str, Any], test_id: str) -> str:
    """One accessible control. Labels and roles are what an observer reads; `data-test-id`
    is a stable identity for tests only, never a selector Jobloom is allowed to rely on."""
    kind = control["kind"]
    disposition_for(kind)
    role = control["role"]
    label = _escape(control.get("label") or kind)
    required = " required" if control.get("required") else ""
    common = f'id="{test_id}" name="{test_id}" data-test-id="{test_id}" data-kind="{_escape(kind)}"'
    if role == "radiogroup":
        options = "".join(
            f'<label><input type="radio" name="{test_id}" value="{_escape(choice)}" '
            f'data-test-id="{test_id}--{index}"> {_escape(choice)}</label>'
            for index, choice in enumerate(control.get("choices") or [])
        )
        return (f'<fieldset role="radiogroup" aria-label="{label}" data-kind="{_escape(kind)}" '
                f'data-test-id="{test_id}"><legend>{label}</legend>{options}</fieldset>')
    if role == "combobox":
        options = "".join(f'<option value="{_escape(choice)}">{_escape(choice)}</option>'
                          for choice in control.get("choices") or [])
        return (f'<label for="{test_id}">{label}</label>'
                f'<select {common}{required}><option value=""></option>{options}</select>')
    if role == "file":
        return (f'<label for="{test_id}">{label}</label>'
                f'<input type="file" accept="application/pdf" {common}{required}>')
    return (f'<label for="{test_id}">{label}</label>'
            f'<input type="text" {common}{required}>')


def render_page(fixture: dict[str, Any], step_index: int, *, include_variants: bool = False,
                final: bool = False) -> str:
    """One page of a semantic fixture as a standalone local document.

    There is no automatic navigation anywhere in this markup: moving between pages is a link
    the test or the user follows, never something a renderer or a worker does.
    """
    step = fixture["steps"][step_index]
    family = fixture["platformFamily"]
    controls = "".join(
        render_control(control, f"{family}-{step_index}-{index}")
        for index, control in enumerate(step["controls"])
    )
    variants = ""
    if include_variants:
        variants = "".join(_render_variant(name, kind, label)
                           for name, (kind, label) in sorted(SAFETY_VARIANTS.items()))
    final_control = ""
    if final:
        # A stop boundary, rendered so it can be observed and never activated. Its handler
        # only counts, so a test can read the counter without touching the control.
        final_control = (
            '<button type="button" id="final-action" data-test-id="final-action" '
            'onclick="window.__finalActionActivations=(window.__finalActionActivations||0)+1">'
            'Submit application</button>')
    next_link = ""
    if step_index + 1 < len(fixture["steps"]):
        next_link = (f'<a href="/{family}/{step_index + 1}" id="next-page" '
                     f'data-test-id="next-page">Continue</a>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{_escape(family)} replay page {step_index}</title>
<script>window.__finalActionActivations = 0;</script>
</head><body>
<main><h1>Synthetic {_escape(family)} application</h1>
<p>Generated locally from a reviewed semantic fixture. Not a real employer form.</p>
<form id="application" onsubmit="return false;">
{controls}{variants}{final_control}
</form>
{next_link}
</main></body></html>
"""


def _render_variant(name: str, kind: str, label: str) -> str:
    escaped = _escape(label)
    if name == "hidden_control":
        return (f'<label for="{name}" hidden>{escaped}</label>'
                f'<input type="text" id="{name}" data-test-id="{name}" hidden>')
    if name == "disabled_control":
        return (f'<label for="{name}">{escaped}</label>'
                f'<input type="text" id="{name}" data-test-id="{name}" disabled>')
    if name == "duplicate_control":
        return "".join(
            f'<label for="{name}">{escaped}</label>'
            f'<input type="text" id="{name}" data-test-id="{name}" data-copy="{copy}">'
            for copy in (1, 2))
    if name == "iframe_control":
        return f'<iframe title="{escaped}" data-test-id="{name}" srcdoc="&lt;p&gt;nested&lt;/p&gt;"></iframe>'
    if kind == "file":
        return (f'<label for="{name}">{escaped}</label>'
                f'<input type="file" accept="application/pdf" id="{name}" data-test-id="{name}">')
    if kind == "checkbox":
        return (f'<label for="{name}"><input type="checkbox" id="{name}" '
                f'data-test-id="{name}"> {escaped}</label>')
    return (f'<label for="{name}">{escaped}</label>'
            f'<input type="text" id="{name}" data-test-id="{name}">')
