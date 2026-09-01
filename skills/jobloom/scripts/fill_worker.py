#!/usr/bin/env python3
"""The untrusted executor: one validated package, one page, hashes back.

Deliberately outside `fill_core`. `fill_core` is the authority and this is the thing it does
not trust — so this module reads no database, no AnswerLibrary and no CandidateFact, consumes
exactly one already-verified private package, and returns observations rather than claims.

What it cannot do is the point, and most of it is structural rather than promised. The
operation vocabulary is fill, select, check, uncheck and upload; there is no click, no Enter,
no navigate, no evaluate, no download. So Next, Continue and the final action are not refused
by a rule that could be argued with — they are not expressible. A page that wants to be
submitted has to be submitted by the user.

Trust in the surface does not come from the address. `127.0.0.1` is a network location and
any local process can listen on one, so the package carries an attestation `fill_core` wrote
from a surface record it holds: the exact origin, the renderer version, and the digest of the
page this worker is expected to load. This worker checks the page it actually gets against
that, and refuses everything else.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import worker_protocol  # noqa: E402

RENDERER_VERSION = "1.0.0"
LOOPBACK_ORIGIN = re.compile(r"^http://127\.0\.0\.1:\d{1,5}$")

# Every operation this worker knows how to perform. Nothing here can leave the page.
SUPPORTED_OPERATIONS = {"fill", "select", "check", "uncheck", "upload"}


class WorkerRefusal(RuntimeError):
    """A boundary this worker will not cross. Never a partial run: nothing has been done."""


def _refuse(code: str) -> None:
    raise WorkerRefusal(code)


def load_grant(grant_path: Path, package_path: Path, at: datetime) -> dict[str, Any]:
    """The proof that `fill_core` authorised this exact package to run, once, until when.

    File permissions and a loopback address prove nothing about provenance: any process
    running as this user can write a 0600 file and start a local server. That is the hole
    this closes at the execution boundary, the same one that was closed for option mappings
    at the planning boundary.
    """
    if (os.stat(grant_path).st_mode & 0o777) != 0o600:
        _refuse("grant_permissions")
    if grant_path.with_suffix(grant_path.suffix + ".revoked").exists():
        # Revocation has to be legible on disk, because this worker reads no database.
        _refuse("grant_revoked")
    document = json.loads(grant_path.read_text(encoding="utf-8"))
    grant, secret = document.get("grant"), document.get("secret")
    if not isinstance(grant, dict) or not isinstance(secret, str):
        _refuse("malformed_execution_grant")
    try:
        worker_protocol.verify_grant(grant, secret, package_path.read_bytes(), at)
    except worker_protocol.ProtocolError as problem:
        _refuse(str(problem))
    return grant


def load_package(path: Path) -> dict[str, Any]:
    """Read one private package. Its permissions are part of its validity."""
    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        _refuse("package_permissions")
    package = json.loads(path.read_text(encoding="utf-8"))
    if package.get("mode") != "fill_only":
        _refuse("unsupported_mode")
    if package.get("stop_before_submit") is not True:
        _refuse("stop_before_submit_not_asserted")
    if "submission_action" not in package or package["submission_action"] is not None:
        _refuse("submission_action_present")
    surface = package.get("surface")
    if not isinstance(surface, dict):
        _refuse("no_attested_surface")
    for field in ("origin", "renderer_version", "page_path", "page_sha256"):
        if not isinstance(surface.get(field), str) or not surface[field]:
            _refuse("incomplete_surface_attestation")
    if not LOOPBACK_ORIGIN.fullmatch(surface["origin"]):
        _refuse("surface_outside_loopback")
    if surface["renderer_version"] != RENDERER_VERSION:
        _refuse("renderer_version_mismatch")
    if not worker_protocol.SHA256.fullmatch(surface["page_sha256"]):
        _refuse("malformed_page_digest")
    for action in package.get("actions") or []:
        if action.get("operation") not in SUPPORTED_OPERATIONS:
            _refuse("unsupported_operation")
    return package


def consume(path: Path) -> None:
    """A package is executed once. Consumption is recorded beside it, never by deletion.

    Deleting it would destroy the audit trail; a marker leaves the package for review and
    makes a replay fail on its second attempt rather than its second effect.
    """
    marker = path.with_suffix(path.suffix + ".consumed")
    try:
        handle = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        _refuse("package_already_consumed")
    os.close(handle)


class PageGuards:
    """Scoped submit and navigation guards, installed before the first action.

    Removed before control returns to the user, because a guard left behind would change how
    their own browsing behaves. Upload traffic is allowed by exact URL: a file upload is a
    POST, so refusing every POST would refuse uploads while calling itself submit protection.
    """

    def __init__(self, page, origin: str, target: str,
                 allowed_upload_urls: tuple[str, ...] = ()):
        self.page = page
        self.origin = origin
        self.target = target
        self.allowed_upload_urls = allowed_upload_urls
        self.violations: list[str] = []
        self._document_loaded = False
        self._handlers: list[tuple[str, Any]] = []

    def allow_initial_navigation(self) -> None:
        """One document load, to the exact attested URL, before any action runs."""
        self._document_loaded = False

    def seal(self) -> None:
        """After this, no document may load for any reason."""
        self._document_loaded = True

    def _route(self, route, request):
        is_document = request.resource_type == "document"
        if is_document:
            # A same-origin GET is not safe by virtue of being same-origin: a form with
            # `method="GET"` submits by navigating, and an input handler can set
            # `window.location`. Both would leave the attested page while every other check
            # still reported success. Exactly one document load is allowed, to the exact URL
            # the authority attested, before any action runs.
            if (not self._document_loaded and request.url == self.target
                    and request.method == "GET"):
                self._document_loaded = True
                route.continue_()
                return
            # A form submit is a document request too, so name which one happened: a POST
            # document request is the form being sent, a GET one is the page being left.
            self.violations.append(
                "submit_attempted" if request.method == "POST" else "navigation_attempted")
            route.abort()
            return
        if request.method == "POST":
            if request.url in self.allowed_upload_urls:
                route.continue_()
                return
            self.violations.append("submit_attempted")
            route.abort()
            return
        if not request.url.startswith(self.origin):
            self.violations.append("outside_allowed_origin")
            route.abort()
            return
        route.continue_()

    def _record(self, code):
        def handler(*_arguments):
            self.violations.append(code)
        return handler

    def __enter__(self):
        self.page.route("**/*", self._route)
        # A frame appearing is not the worker doing something wrong — the replay ships an
        # iframe as a hazard to be discovered. What matters is never acting inside one, and
        # `_locator` checks that per control against the live document.
        for event, code in (("popup", "popup_opened"), ("download", "download_started")):
            handler = self._record(code)
            self.page.on(event, handler)
            self._handlers.append((event, handler))
        return self

    def __exit__(self, *exception):
        for event, handler in self._handlers:
            self.page.remove_listener(event, handler)
        self.page.unroute_all(behavior="ignoreErrors")
        return False


def _locator(page, action: dict[str, Any]):
    """One control, re-verified immediately before it is touched.

    Everything checked here can change between observation and action — a page can re-render,
    duplicate a control, detach it, or swap its type — so none of it is taken on the strength
    of the observation that produced the package.
    """
    # `page.locator` searches the top frame only — reaching into a frame needs
    # `frame_locator`, which this worker does not have — so the top-frame requirement is
    # structural rather than a check that could be skipped.
    identity = f'[data-test-id="{action["field_id"]}"]'
    locator = page.locator(identity)
    count = locator.count()
    if count == 0:
        return None, "selector_not_found"
    if count > 1:
        return None, "selector_ambiguous"
    if not locator.is_visible():
        return None, "control_hidden"
    if not locator.is_enabled():
        return None, "control_disabled"
    # Type agreement is checked by asking for the control again with its expected tag, not by
    # evaluating an expression in the page: no JavaScript of any origin runs here, so there
    # is no place for page-supplied script to be executed by accident.
    expected = {"text": "input", "textarea": "textarea", "select": "select",
                "radio": "input", "checkbox": "input", "file": "input"}.get(action["control"])
    if expected and page.locator(f"{expected}{identity}").count() != 1:
        return None, "control_type_mismatch"
    return locator, None


def _perform(page, action: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Do exactly one thing and hash what the page holds afterwards."""
    locator, problem = _locator(page, action)
    if problem:
        return "not_actionable", None, problem
    operation = action["operation"]
    value = action["value"]
    try:
        if operation == "fill":
            locator.fill(str(value))
            observed = locator.input_value()
        elif operation == "select":
            locator.select_option(str(value))
            observed = locator.input_value()
        elif operation in {"check", "uncheck"}:
            getattr(locator, operation)()
            observed = str(locator.is_checked())
        elif operation == "upload":
            path = Path(value)
            if not path.is_file():
                return "error", None, "upload_rejected"
            with path.open("rb") as handle:
                head = handle.read(5)
            if path.suffix.casefold() != ".pdf" or head != b"%PDF-":
                return "error", None, "upload_type_not_pdf"
            locator.set_input_files(str(path))
            observed = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            return "refused", None, "control_type_mismatch"
    except Exception:  # noqa: BLE001 - a page can fail in ways this cannot enumerate
        return "error", None, "value_rejected_by_page"
    if operation == "upload":
        digest = observed
    else:
        digest = hashlib.sha256(
            json.dumps(observed, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")).hexdigest()
    return "verified", digest, None


def _read_oracle(oracle_url: str | None) -> int | None:
    """The target's own count of final-action activations, if the target keeps one.

    Only the replay does. On any other surface this is `None`, and `None` is reported rather
    than a zero, because a run that cannot observe the counter has not shown it did not move.
    """
    if not oracle_url:
        return None
    try:
        with urllib.request.urlopen(oracle_url, timeout=5) as response:
            return int(json.load(response)["final_action_activations"])
    except Exception:  # noqa: BLE001 - an unreadable oracle is an unknown, not a zero
        return None


def run(package_path: Path, output_path: Path, grant_path: Path, *, headed: bool = True,
        page_url: str | None = None, oracle_url: str | None = None,
        at: datetime | None = None) -> dict[str, Any]:
    """Execute one package against one page and write a result envelope.

    Headed by default: a run the user cannot see is a run they cannot stop.
    """
    from playwright.sync_api import sync_playwright

    now = at or datetime.now(timezone.utc)
    load_grant(grant_path, package_path, now)
    package = load_package(package_path)
    surface = package["surface"]
    expires = worker_protocol.parse_time(surface.get("expires_at"))
    if expires and now >= expires:
        # The surface can lapse between export and execution; the same bytes served from the
        # same address afterwards are not a live surface.
        _refuse("surface_expired")
    target = page_url or (surface["origin"] + surface["page_path"])
    if target != surface["origin"] + surface["page_path"]:
        _refuse("page_outside_attested_surface")
    consume(package_path)

    before = _read_oracle(oracle_url)
    results: list[dict[str, Any]] = []
    stopped_on: str | None = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        try:
            context = browser.new_context()
            page = context.new_page()
            # Short and explicit. A page that will not settle should not hold the user's
            # browser for the framework default, and a guard-aborted navigation makes every
            # retry pointless anyway.
            page.set_default_timeout(5000)
            with PageGuards(page, surface["origin"], target) as guards:
                response = page.goto(target, wait_until="domcontentloaded")
                # The response body, not `page.content()`: the latter is the serialized DOM
                # after the browser has parsed and normalised the markup, so it would never
                # equal the bytes the attestation covers.
                served = hashlib.sha256(response.body()).hexdigest() if response else ""
                if served != surface["page_sha256"]:
                    guards.violations.append("page_digest_mismatch")
                for index, action in enumerate(package["actions"]):
                    if guards.violations:
                        # Stop at the first violation. Continuing would act on a page that
                        # has already done something it was not allowed to do, and reporting
                        # every field as "refused" would hide which one caused it.
                        stopped_on = stopped_on or _violation_code(guards.violations[0])
                        results.append({"action_id": action["step_id"],
                                        "outcome": "not_attempted",
                                        "control": action["control"],
                                        "error_code": stopped_on})
                        continue
                    outcome, digest, error = _perform(page, action)
                    entry: dict[str, Any] = {"action_id": action["step_id"],
                                             "outcome": outcome,
                                             "control": action["control"]}
                    if digest:
                        entry["observed_sha256"] = digest
                    if error:
                        entry["error_code"] = error
                    if guards.violations and index == len(results):
                        # The action itself tripped a guard: it is a refusal, not a success.
                        entry = {"action_id": action["step_id"], "outcome": "refused",
                                 "control": action["control"],
                                 "error_code": _violation_code(guards.violations[0])}
                        stopped_on = entry["error_code"]
                    results.append(entry)
                guards.seal()
                observed_violations = list(guards.violations)
        finally:
            browser.close()

    after = _read_oracle(oracle_url)
    envelope = {
        "protocol_version": worker_protocol.PROTOCOL_VERSION,
        "session_id": package["session_id"], "page_id": package["page_id"],
        "package_sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
        # A number only when the target itself was counting. Otherwise `null`: the guard
        # observing nothing is evidence about the guard, not about the target.
        "final_action_activations": (
            after - before if before is not None and after is not None else None),
        "results": results,
    }
    handle = os.open(output_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(envelope, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return {"output": str(output_path), "action_count": len(package["actions"]),
            "guard_observations": observed_violations,
            "final_action_evidence": "replay_oracle" if before is not None else "unobservable",
            "stopped_on": stopped_on}


def _violation_code(violation: str) -> str:
    return {
        "submit_attempted": "final_action_refused",
        "navigation_attempted": "navigation_attempted",
        "outside_allowed_origin": "outside_allowed_origin",
        "popup_opened": "navigation_attempted",
        "download_started": "navigation_attempted",
        "frame_attached": "outside_top_frame",
        "page_digest_mismatch": "control_changed_since_observation",
    }.get(violation, "unknown_error")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--grant", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--oracle-url", default=None,
                        help="a target that counts its own final-action activations")
    parser.add_argument("--headless", action="store_true",
                        help="tests only; a run the user cannot see is a run they cannot stop")
    arguments = parser.parse_args()
    # Value-free by construction: counts and a path the caller already knows.
    print(json.dumps(run(arguments.package, arguments.output, arguments.grant,
                         headed=not arguments.headless,
                         oracle_url=arguments.oracle_url), indent=2))


if __name__ == "__main__":
    main()
