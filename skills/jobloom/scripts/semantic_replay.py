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

RENDERER_VERSION = "1.0.0"
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


# One page per hazard, because a page carrying all of them at once only ever proves that the
# observer refused it for *some* reason. `{anchor}` is the `data-test-id` of a real control on
# the same page, so the duplicate collides with something rather than with itself.
OBSERVER_HAZARDS = {
    # No `data-test-id`: the refusal has to come from the tag, not from the identity walk,
    # because a frame on a real page would not carry a Jobloom attribute either.
    "iframe": ('<iframe title="A question inside a nested frame" '
               'srcdoc="&lt;p&gt;nested&lt;/p&gt;"></iframe>'),
    # What every control on a real employer form looks like to this observer.
    "unknown": ('<label for="unreviewed">A control nobody reviewed</label>'
                '<input type="text" id="unreviewed" name="unreviewed">'),
    "duplicate": ('<label for="{anchor}">A duplicated question</label>'
                  '<input type="text" id="duplicate-of-{anchor}" data-test-id="{anchor}" '
                  'data-kind="contact.full_name">'),
    # Identified and mapped, so the only thing wrong with it is that nobody can see it.
    "hidden": ('<label for="hidden-question" hidden>A hidden question</label>'
               '<input type="text" id="hidden-question" data-test-id="hidden-question" '
               'data-kind="contact.full_name" hidden>'),
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


def render_control(control: dict[str, Any], test_id: str, nonce: str) -> str:
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
            f'<label><input type="radio" name="{test_id}" '
            f'value="{_escape(field_policy.replay_option_value(choice, nonce))}" '
            f'data-test-id="{test_id}--{index}"> {_escape(choice)}</label>'
            for index, choice in enumerate(control.get("choices") or [])
        )
        return (f'<fieldset role="radiogroup" aria-label="{label}" data-kind="{_escape(kind)}" '
                f'data-test-id="{test_id}"><legend>{label}</legend>{options}</fieldset>')
    if role == "combobox":
        # Value derived from the label by the one rule `field_policy` can recompute, so an
        # observed pair is checkable rather than merely readable. On a real control the two
        # are unrelated strings, which is exactly why a label match alone proves nothing.
        options = "".join(
            f'<option value="{_escape(field_policy.replay_option_value(choice, nonce))}">'
            f'{_escape(choice)}</option>'
            for choice in control.get("choices") or [])
        return (f'<label for="{test_id}">{label}</label>'
                f'<select {common}{required}><option value=""></option>{options}</select>')
    if role == "file":
        return (f'<label for="{test_id}">{label}</label>'
                f'<input type="file" accept="application/pdf" {common}{required}>')
    return (f'<label for="{test_id}">{label}</label>'
            f'<input type="text" {common}{required}>')


def render_controls(family: str, page_index: int, controls: list[dict[str, Any]],
                    nonce: str, *, include_variants: bool = False, final: bool = False,
                    next_path: str | None = None, hazard: str | None = None) -> str:
    """Render an explicit list of reviewed controls as one local page.

    The pagination is Jobloom's, not upstream's: every reviewed fixture puts its controls on
    one step and the final action on the next, so a two-package flow needs the controls split
    across two pages. The controls themselves are unchanged — only which page they appear on
    is ours, and multi-page application forms are ordinary.
    """
    body = "".join(
        render_control(control, f"{family}-0-{index}", nonce)
        for index, control in controls)
    if hazard:
        if hazard not in OBSERVER_HAZARDS:
            raise UnmappedKind(f"no such observer hazard: {hazard}")
        body += OBSERVER_HAZARDS[hazard].format(
            anchor=f"{family}-0-{controls[0][0]}")
    variants = ""
    if include_variants:
        variants = "".join(_render_variant(name, kind, label)
                           for name, (kind, label) in sorted(SAFETY_VARIANTS.items()))
    final_control = (
        '<input type="submit" id="final-action" data-test-id="final-action" '
        'value="Submit application">' if final else "")
    link = (f'<a href="{_escape(next_path)}" id="next-page" data-test-id="next-page">'
            f'Continue</a>' if next_path else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{_escape(family)} replay page {page_index}</title>
</head><body>
<main><h1>Local {_escape(family)} replay</h1>
<p>Generated locally and served from loopback. The control labels below are
<strong>recorded upstream wording</strong> from a reviewed semantic fixture
(<code>neonwatty/job-apply-plugin</code>, MIT, Jeremy Watt), rendered as recorded rather than
paraphrased, because a paraphrase would test a form no employer ships. Recorded wording is not
synthetic wording. Nothing here reaches an employer.</p>
<form id="application" method="post" action="{'/__final_action' if final else ''}">
{body}{variants}{final_control}
</form>
{link}
</main></body></html>
"""


def render_page(fixture: dict[str, Any], step_index: int, nonce: str, *,
                include_variants: bool = False, final: bool = False) -> str:
    """One page of a semantic fixture as a standalone local document.

    There is no automatic navigation anywhere in this markup: moving between pages is a link
    the test or the user follows, never something a renderer or a worker does.
    """
    step = fixture["steps"][step_index]
    family = fixture["platformFamily"]
    controls = "".join(
        render_control(control, f"{family}-{step_index}-{index}", nonce)
        for index, control in enumerate(step["controls"])
    )
    variants = ""
    if include_variants:
        variants = "".join(_render_variant(name, kind, label)
                           for name, (kind, label) in sorted(SAFETY_VARIANTS.items()))
    final_control = ""
    if final:
        # A real stop boundary, not a decorative one. The earlier version incremented a
        # `window` variable that the server never read, so the counter reported zero whether
        # or not anything had been activated — an oracle that could not fail. Activating this
        # control posts to the server, which is the only party that can honestly observe it,
        # and is also how a real form would behave.
        final_control = (
            '<input type="submit" id="final-action" data-test-id="final-action" '
            'value="Submit application">')
    next_link = ""
    if step_index + 1 < len(fixture["steps"]):
        next_link = (f'<a href="/{family}/{step_index + 1}" id="next-page" '
                     f'data-test-id="next-page">Continue</a>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{_escape(family)} replay page {step_index}</title>
</head><body>
<main><h1>Local {_escape(family)} replay</h1>
<p>Generated locally and served from loopback. The control labels below are
<strong>recorded upstream wording</strong> from a reviewed semantic fixture
(<code>neonwatty/job-apply-plugin</code>, MIT, Jeremy Watt), rendered as recorded rather than
paraphrased, because a paraphrase would test a form no employer ships. Recorded wording is not
synthetic wording. Nothing here reaches an employer.</p>
<form id="application" method="post" action="{'/__final_action' if final else ''}">
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
