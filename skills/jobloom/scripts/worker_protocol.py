#!/usr/bin/env python3
"""The narrow contract between `fill_core` and a browser worker that does not exist yet.

Written before any browser code so the vocabulary is settled by the domain decisions in
`docs/lever-first-form-readiness.md` rather than by whatever the first adapter found
convenient. Three envelopes:

    worker request   what a worker is told to do, value-free
    package metadata what identifies and bounds the private action package
    worker result    what comes back: hashes and codes, never values or page text

The worker is an untrusted executor. `fill_core` is the authority, so everything here is a
check performed on the way in, and every check fails closed. There is no submission action in
this protocol; a control named `submit` is a stop boundary, not an instruction.

Coverage is the other half. `finish_session` used to prove only that every page already in
the database had been checkpointed and that some page had shown a submit control — which a
single page at index 49 satisfies. That is coverage of what was seen, not of the form. The
page chain below is what makes "the whole form was observed" a claim rather than an
assumption, and it is the precondition for writing `not_present`.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from _common import parse_time  # noqa: E402

PROTOCOL_VERSION = "1.0.0"
SUPPORTED_PROTOCOL_VERSIONS = {PROTOCOL_VERSION}

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# Operations a worker may perform. Deliberately absent: click, submit, navigate, press,
# download, evaluate. The v1 bound is one page per run with every Next, Continue and final
# action belonging to the user, so the worker has no way to leave the page it was given.
ALLOWED_OPERATIONS = {"fill", "select", "check", "uncheck", "upload"}
FORBIDDEN_OPERATIONS = {"submit", "click", "navigate", "press", "download", "evaluate", "open"}

OUTCOME_CODES = {"verified", "mismatch", "not_found", "not_actionable", "refused", "error"}

# "Bounded" is not "value-free": 64 characters is room for a short answer or a line of page
# text, so a hostile or careless worker could return one in an error code. The vocabulary is
# closed instead, and a worker with something else to say has to say it as `unknown_error`.
ERROR_CODES = {
    "selector_not_found", "selector_ambiguous", "control_disabled", "control_hidden",
    "control_detached", "control_changed_since_observation", "control_type_mismatch",
    "value_rejected_by_page", "upload_rejected", "upload_type_not_pdf",
    "outside_top_frame", "outside_allowed_origin", "navigation_attempted",
    "final_action_refused", "timeout", "unknown_error",
}

# The Python validator, not the schema, is what actually runs, so the allowed field sets live
# here too. A schema that drifts from the code would otherwise be the only thing refusing an
# unknown top-level field.
REQUEST_FIELDS = {
    "protocol_version", "session_id", "page_id", "page_index", "locale", "package_sha256",
    "allowed_origin", "expires_at", "action_ids", "operations", "stop_before_submit",
    "submission_action", "predecessor_checkpoint_sha256",
}
RESULT_FIELDS = {
    "protocol_version", "session_id", "page_id", "package_sha256",
    "final_action_activations", "results",
}
RESULT_ENTRY_FIELDS = {"action_id", "outcome", "observed_sha256", "control", "error_code"}

# A result envelope carries hashes and codes. These names are how a value would arrive if a
# worker were careless or hostile, so they are refused wherever they appear.
FORBIDDEN_RESULT_KEYS = {
    "value", "values", "text", "inner_text", "html", "outer_html", "content", "label",
    "options", "cookie", "cookies", "token", "session_token", "authorization", "path",
    "file_path", "local_path", "screenshot", "page_text", "answer",
}

# What a verified chain is evidence of. Named so that a later reader of an archive cannot
# mistake it for proof that the employer's form had no further pages. It is deliberately not
# strong enough to support `not_present`, which nothing writes.
COVERAGE_BASIS = "self_reported_page_chain"

SHA256 = re.compile(r"^[0-9a-f]{64}$")
LOOPBACK_ORIGIN = re.compile(r"^http://127\.0\.0\.1:\d{1,5}$")


class ProtocolError(ValueError):
    """A protocol violation. Distinct so a caller cannot confuse it with a domain error."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ProtocolError(code)


def _reject_forbidden_keys(value: Any, path: str = "result") -> None:
    """Refuse a value-bearing key anywhere in the structure, at any depth."""
    if isinstance(value, dict):
        for key, item in value.items():
            _require(key.casefold() not in FORBIDDEN_RESULT_KEYS, f"forbidden_result_field:{key}")
            _reject_forbidden_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_keys(item, path)


def schema(name: str) -> dict[str, Any]:
    """Load one of the tracked, value-free JSON Schemas."""
    return json.loads((ASSETS / f"{name}.schema.json").read_text(encoding="utf-8"))


def validate_request(request: dict[str, Any], *, expected_session: str, expected_page: str,
                     expected_package_sha256: str, allowed_origin: str,
                     at: datetime) -> dict[str, Any]:
    """Check a worker request against what `fill_core` believes it issued."""
    _require(isinstance(request, dict), "request_not_an_object")
    _require(not (set(request) - REQUEST_FIELDS), "unknown_request_field")
    _require(request.get("protocol_version") in SUPPORTED_PROTOCOL_VERSIONS,
             "unsupported_protocol_version")
    _require(request.get("session_id") == expected_session, "session_mismatch")
    _require(request.get("page_id") == expected_page, "page_mismatch")
    _require(isinstance(request.get("package_sha256"), str)
             and SHA256.fullmatch(request["package_sha256"] or ""), "malformed_package_hash")
    _require(request["package_sha256"] == expected_package_sha256, "package_hash_mismatch")
    _require(request.get("allowed_origin") == allowed_origin, "unexpected_origin")
    _require(LOOPBACK_ORIGIN.fullmatch(allowed_origin or "") is not None,
             "origin_outside_loopback")
    _require(request.get("stop_before_submit") is True, "stop_before_submit_not_asserted")
    _require("submission_action" in request and request["submission_action"] is None,
             "submission_action_present")
    expires = parse_time(request.get("expires_at"))
    _require(expires is not None, "missing_expiry")
    _require(at < expires, "package_expired")
    action_ids = request.get("action_ids")
    _require(isinstance(action_ids, list) and action_ids, "missing_action_ids")
    _require(len(set(action_ids)) == len(action_ids), "duplicate_action_ids")
    operations = request.get("operations")
    _require(isinstance(operations, dict), "malformed_operations")
    # Exact equality, not containment: an operation nobody looked up is an operation nobody
    # checked, and `{"evil": "submit"}` alongside the real actions would have been ignored.
    _require(set(operations) == set(action_ids), "operations_do_not_match_action_ids")
    for action_id in action_ids:
        operation = operations[action_id]
        _require(operation in ALLOWED_OPERATIONS, f"unsupported_operation:{action_id}")
        _require(operation not in FORBIDDEN_OPERATIONS, f"forbidden_operation:{action_id}")
    return {"action_ids": list(action_ids), "protocol_version": request["protocol_version"]}


def validate_result(result: dict[str, Any], *, expected_session: str, expected_page: str,
                    expected_package_sha256: str, expected_action_ids: list[str],
                    at: datetime) -> dict[str, Any]:
    """Check what a worker returned. Hashes and codes only, one entry per issued action."""
    _require(isinstance(result, dict), "result_not_an_object")
    _require(not (set(result) - RESULT_FIELDS), "unknown_result_field")
    _reject_forbidden_keys(result)
    _require(result.get("protocol_version") in SUPPORTED_PROTOCOL_VERSIONS,
             "unsupported_protocol_version")
    _require(result.get("session_id") == expected_session, "session_mismatch")
    _require(result.get("page_id") == expected_page, "page_mismatch")
    _require(result.get("package_sha256") == expected_package_sha256, "package_hash_mismatch")
    _require(result.get("final_action_activations") == 0, "final_action_activated")
    entries = result.get("results")
    _require(isinstance(entries, list), "malformed_results")
    seen: list[str] = []
    for entry in entries:
        _require(isinstance(entry, dict), "malformed_result_entry")
        _require(set(entry) <= RESULT_ENTRY_FIELDS, "unknown_result_entry_field")
        action_id = entry.get("action_id")
        _require(action_id in expected_action_ids, "unexpected_action_id")
        _require(action_id not in seen, "duplicate_result")
        seen.append(action_id)
        _require(entry.get("outcome") in OUTCOME_CODES, "unknown_outcome_code")
        if entry["outcome"] == "verified":
            _require(isinstance(entry.get("observed_sha256"), str)
                     and SHA256.fullmatch(entry["observed_sha256"] or ""), "malformed_observed_hash")
        error_code = entry.get("error_code")
        _require(error_code is None or error_code in ERROR_CODES, "unknown_error_code")
    _require(seen == list(expected_action_ids), "result_order_or_completeness_mismatch")
    return {"verified": [entry["action_id"] for entry in entries if entry["outcome"] == "verified"]}


# --- the page chain -------------------------------------------------------

def chain_issue(pages: list[dict[str, Any]]) -> str | None:
    """Why these pages are not a complete form, or `None` if they are.

    Every rule here exists because its absence let something be assumed. The chain must start
    at index 0, advance one index at a time with each page naming the checkpoint of the page
    before it, and end on a page that declared itself final and saw the submit control.

    **What this proves, and what it does not.** Every input is self-reported by the observer:
    the index, the predecessor hash, the final-page flag. So a passing chain establishes that
    the observer reported a contiguous sequence it could not have fabricated cheaply — each
    link names a checkpoint hash computed from steps that were actually verified — and that
    nothing was skipped *within what was reported*. It does not establish that the reported
    sequence is the employer's whole form. An observer that never saw a page cannot report
    its absence, and no artefact available here can. So nothing is allowed to rest on it:
    `finalize_handling` does not write `not_present` at all, and the voluntary-disclosure
    status stays `unknown` until something that can enumerate a whole form exists. Naming the
    evidence more honestly was tried first and was not enough — a weak claim stated precisely
    is still a weak claim.
    """
    if not pages:
        return "no_pages_observed"
    ordered = sorted(pages, key=lambda page: page["page_index"])
    if ordered[0]["page_index"] != 0:
        return "chain_does_not_start_at_first_page"
    for position, page in enumerate(ordered):
        if page["page_index"] != position:
            return "page_index_not_consecutive"
        if page["status"] != "completed":
            return "page_not_checkpointed"
        if position == 0:
            if page.get("predecessor_checkpoint_sha256"):
                return "first_page_has_a_predecessor"
        else:
            expected = ordered[position - 1].get("checkpoint_sha256")
            if not expected or page.get("predecessor_checkpoint_sha256") != expected:
                return "page_predecessor_mismatch"
    final_pages = [page for page in ordered if page.get("final_page")]
    if not final_pages:
        return "no_final_page_observed"
    if len(final_pages) > 1 or final_pages[0]["page_index"] != ordered[-1]["page_index"]:
        return "final_page_is_not_the_last_page"
    if not final_pages[0].get("submit_control_seen"):
        return "final_page_without_submit_control"
    return None
