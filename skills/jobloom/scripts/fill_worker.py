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
import urllib.error
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
# Exactly one shape: loopback literal, a real port, and the reserve path. No hostname
# variants (`localhost` resolves wherever the host file says), no userinfo, no query, no
# fragment, no other path.
AUTHORITY_URL = re.compile(r"^http://127\.0\.0\.1:(?:[1-9]\d{0,3}|[1-5]\d{4}|6[0-4]\d{3}"
                           r"|65[0-4]\d{2}|655[0-2]\d|6553[0-5])/reserve$")

# Every operation this worker knows how to perform. Nothing here can leave the page.
SUPPORTED_OPERATIONS = {"fill", "select", "check", "uncheck", "upload"}

# A pause between actions so an immediate side effect can be attributed to the action that
# caused it. It is **not** a safety boundary and nothing may rest on it: a page only has to
# call `setTimeout(..., 400)` to land after any number chosen here. Safety comes from the
# guard outliving every action — it is removed by destroying the browser context, not before
# it — and from the target's own counter being read after that destruction.
ATTRIBUTION_MILLISECONDS = 150


class WorkerRefusal(RuntimeError):
    """A boundary this worker will not cross. Never a partial run: nothing has been done."""


def _refuse(code: str) -> None:
    raise WorkerRefusal(code)


def read_capability(path: Path) -> tuple[str, str]:
    """Read the redemption endpoint and its token from a 0600 file.

    Not from `argv`, which is visible in the process list to other users, and not from the
    environment, which children inherit. The mode excludes other Unix users; it does not
    exclude a hostile process running as this same user, which can simply read the file.
    That limit is real and is not papered over: closing it needs a different OS identity, a
    sandbox, or an inherited descriptor, none of which a file permission can provide.
    """
    if (os.stat(path).st_mode & 0o777) != 0o600:
        _refuse("capability_permissions")
    document = json.loads(path.read_text(encoding="utf-8"))
    url, token = document.get("authority_url"), document.get("token")
    if not isinstance(url, str) or not isinstance(token, str) or not url or not token:
        _refuse("malformed_capability")
    # The design says "a narrow local authority endpoint", so the worker holds it to exactly
    # that shape. A misconfigured or hostile capability file would otherwise send the token,
    # the grant id and the package digest to any address on the network — and then act on
    # whatever that address said was authorised.
    if not AUTHORITY_URL.fullmatch(url):
        _refuse("authority_url_not_loopback")
    return url, token


def _call_authority(authority_url: str, token: str, path: str,
                    payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        authority_url.rsplit("/", 1)[0] + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as refusal:
        try:
            return json.load(refusal)
        except Exception:  # noqa: BLE001
            _refuse("grant_refused")
    except Exception:  # noqa: BLE001 - an unreachable authority authorises nothing
        _refuse("authority_unreachable")


def consume(authority_url: str, token: str, grant_id: str, reservation: str) -> None:
    """Spend the grant, once, after everything that could refuse the run has passed.

    Split from reservation because an unreadable oracle used to burn the grant: the worker
    consumed first and only then discovered it could not prove anything, leaving the user a
    failure that looked retryable and never was.
    """
    answer = _call_authority(authority_url, token, "/consume",
                             {"grant_id": grant_id, "reservation": reservation})
    if not answer.get("consumed"):
        _refuse(answer.get("reason") or "grant_refused")


def reserve(authority_url: str, token: str, grant_id: str,
            package_sha256: str) -> dict[str, Any]:
    """Ask the authority whether this package may run, and get back what it may use.

    A secret cannot verify anything while it travels beside the signature it verifies, which
    is what the previous version did. So nothing here is self-verifying: the authority holds
    the state, consumes the grant atomically, and answers. A package it never exported has no
    grant, and writing more local files does not create one.

    Everything security-relevant in the answer replaces the package's own account of it. The
    package is untrusted data whose digest has been matched, nothing more.
    """
    answer = _call_authority(authority_url, token, "/reserve",
                             {"grant_id": grant_id, "package_sha256": package_sha256})
    if not answer.get("authorised"):
        _refuse(answer.get("reason") or "grant_refused")
    for field in ("target", "origin", "renderer_version", "page_sha256", "oracle_url",
                  "reservation"):
        if not isinstance(answer.get(field), str) or not answer[field]:
            _refuse("incomplete_authorisation")
    if not LOOPBACK_ORIGIN.fullmatch(answer["origin"]):
        _refuse("surface_outside_loopback")
    if answer["renderer_version"] != RENDERER_VERSION:
        _refuse("renderer_version_mismatch")
    if not answer["oracle_url"].startswith(answer["origin"] + "/"):
        # The oracle is a capability of the attested surface. A caller-named URL could be any
        # service returning a constant zero.
        _refuse("oracle_outside_surface")
    return answer


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
    # The package's own `surface` block is not consulted for anything security-relevant: it
    # is the package describing itself, and the authority's answer replaces it. It is checked
    # for shape only, so a malformed package is refused before a browser starts.
    surface = package.get("surface")
    if not isinstance(surface, dict):
        _refuse("no_attested_surface")
    for field in ("origin", "renderer_version", "page_path", "page_sha256"):
        if not isinstance(surface.get(field), str) or not surface[field]:
            _refuse("incomplete_surface_attestation")
    for action in package.get("actions") or []:
        if action.get("operation") not in SUPPORTED_OPERATIONS:
            _refuse("unsupported_operation")
    return package


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
        # Deliberately nothing. An earlier version unrouted here and closed the browser
        # afterwards, which left a short but real window in which the page was live and
        # unguarded. The guard is removed by destroying the context it is installed on, so
        # there is no moment at which the page exists without it.
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
            # An upload action carries an object, not a bare path: `_plan_upload` emits the
            # locked version id, its snapshot path and its digest together. Treating it as a
            # path meant every real upload failed as `value_rejected_by_page`, which only a
            # package built by hand could hide.
            if not isinstance(value, dict) or not isinstance(value.get("path"), str):
                return "error", None, "upload_rejected"
            path = Path(value["path"])
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


def run(package_path: Path, output_path: Path, authority_url: str, authority_token: str,
        grant_id: str, *, headed: bool = True, at: datetime | None = None) -> dict[str, Any]:
    """Execute one package against one page and write a result envelope.

    Headed by default: a run the user cannot see is a run they cannot stop.
    """
    from playwright.sync_api import sync_playwright

    package = load_package(package_path)
    # Named so it cannot be shadowed. An earlier version reused `digest` for each action's
    # observed hash, so the envelope carried the last field's value hash where the package
    # digest belonged and every real import would have failed on `package_hash_mismatch`.
    package_digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    # Redemption is what makes this single-use, and it happens in the authority's own state
    # rather than beside the package: copying the package somewhere else cannot buy a second
    # run, because the second redemption updates no rows.
    authorisation = reserve(authority_url, authority_token, grant_id, package_digest)
    surface = {"origin": authorisation["origin"],
               "page_sha256": authorisation["page_sha256"]}
    target = authorisation["target"]
    oracle_url = authorisation["oracle_url"]

    # Before a browser exists, let alone a filled field. The replay's acceptance rests on
    # the target's own counter, so a run that cannot read it is refused now rather than
    # executed and rejected at import — by which point the user's data is on the page and the
    # grant is spent.
    before = _read_oracle(oracle_url)
    if before is None:
        # Refused while the grant is still only reserved, so the reservation lapses and the
        # same grant can be run again once the target is readable.
        _refuse("oracle_unavailable")
    consume(authority_url, authority_token, grant_id, authorisation["reservation"])

    results: list[dict[str, Any]] = []
    stopped_on: str | None = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        try:
            # The worker's own context, so removing the guard means destroying the context
            # rather than unrouting a page that then keeps living.
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
                    outcome, observed_digest, error = _perform(page, action)
                    # A request an action triggers can reach the route handler after the
                    # action call returns, so let the page settle before deciding. Without
                    # this a fill that submitted the form could be reported `verified`
                    # because the abort had not happened yet.
                    page.wait_for_timeout(ATTRIBUTION_MILLISECONDS)
                    entry: dict[str, Any] = {"action_id": action["step_id"],
                                             "outcome": outcome,
                                             "control": action["control"]}
                    if observed_digest:
                        entry["observed_sha256"] = observed_digest
                    if error:
                        entry["error_code"] = error
                    if guards.violations and index == len(results):
                        # The action itself tripped a guard: it is a refusal, not a success.
                        entry = {"action_id": action["step_id"], "outcome": "refused",
                                 "control": action["control"],
                                 "error_code": _violation_code(guards.violations[0])}
                        stopped_on = entry["error_code"]
                    results.append(entry)
                # Count what the actions produced before sealing, so anything the page does
                # from here on — including during teardown — is late by construction.
                during_actions = len(guards.violations)
                guards.seal()
            # Destroying the context is what removes the guard, and it happens here, while
            # every route handler is still registered. A side effect scheduled to land after
            # the last action either meets the guard or does not happen at all.
            context.close()
            late_violations = guards.violations[during_actions:]
            observed_violations = list(guards.violations)
        finally:
            browser.close()

    # Read after the context is gone. A delayed submit that slipped past the guard would show
    # up here as a moved counter; one the guard caught shows up as a late violation. Either
    # way the run is not reported as clean.
    after = _read_oracle(oracle_url)
    envelope = {
        "protocol_version": worker_protocol.PROTOCOL_VERSION,
        "session_id": package["session_id"], "page_id": package["page_id"],
        "package_sha256": package_digest,
        # A number only when the target itself was counting. Otherwise `null`: the guard
        # observing nothing is evidence about the guard, not about the target.
        "final_action_activations": (after - before if after is not None else None),
        # Whether every side effect can be attributed to the action that caused it. A
        # violation that only appeared while the context was being destroyed cannot be, and
        # `validate_result` refuses anything but `complete`, so an unattributable run is not
        # importable rather than quietly recorded as clean.
        "side_effect_attribution": "unproven" if late_violations else "complete",
        "results": results,
    }
    handle = os.open(output_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(envelope, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return {"output": str(output_path), "action_count": len(package["actions"]),
            "guard_observations": observed_violations,
            "late_violations": late_violations,
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capability", type=Path, required=True,
                        help="a 0600 file naming the redemption endpoint and its token")
    parser.add_argument("--grant-id", required=True)
    parser.add_argument("--headless", action="store_true",
                        help="tests only; a run the user cannot see is a run they cannot stop")
    arguments = parser.parse_args()
    # Value-free by construction: counts and a path the caller already knows.
    # The token arrives in a 0600 file, never in `argv`: process arguments are readable by
    # other processes this user runs, which is exactly who the token excludes.
    authority_url, token = read_capability(arguments.capability)
    # No `--oracle-url` either: the oracle is a capability of the surface the authority
    # names, not something a caller may point at a service that returns a constant zero.
    summary = run(arguments.package, arguments.output, authority_url, token,
                  arguments.grant_id, headed=not arguments.headless)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
