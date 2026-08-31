#!/usr/bin/env python3
"""Ask each registered job board whether it is still there, and nothing else.

The offline health audit compares one pull against another, so it can see an adapter
drifting but never answers "is this board reachable right now". This does: one request per
registered source, to the endpoint that source's own authorization record names, and a
verdict that keeps the ways of failing apart.

What it deliberately does not do:

* It never marks a source broken for being rate limited. A 429 is the board asking to be
  asked later, and a health check that reads it as breakage teaches people to ignore
  health checks. Same for a timeout, which is retried once and then reported as itself.
* It never disables, enables, edits or reorders a source. The registry is read-only here.
* It never builds a JobCard or writes to the database. It counts postings and fingerprints
  the shape of the first one; the bodies are not kept.
* It fetches only the endpoint recorded in each source's `authorization.endpoint_template`,
  and refuses a source whose recorded endpoint does not match the adapter that would be
  used for it. Probing something nobody registered is how an audit becomes a crawl.

Budgets are hard, not advisory: at most one request per source plus one retry, a global
cap, four at a time, and a per-request timeout.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import ats_sources  # noqa: E402

PROBE_SCHEMA_VERSION = "ats-source-probe-v1"
REQUEST_TIMEOUT = 15
# A ceiling, not a threshold. The largest real board here answers with 12MB for 886
# postings with full descriptions, so a cap below that would refuse a working board; a cap
# exists at all so an endpoint answering with something unbounded is stopped rather than
# read. Exceeding it says nothing about the board's shape, only that this run stopped.
MAX_RESPONSE_BYTES = 48 * 1024 * 1024
MAX_CONCURRENCY = 4
GLOBAL_REQUEST_BUDGET = 80
RETRYABLE = {"timeout", "server_error"}

VERDICTS = ("healthy", "empty_valid", "schema_drift", "forbidden", "not_found",
            "rate_limited", "timeout", "server_error", "network_error", "oversized_response",
            "endpoint_not_registered", "budget_exhausted", "unsupported_ats")

# Every verdict that says something about the board rather than about this run. A source
# outside this set was not tested, and must never be counted as evidence either way.
CONCLUSIVE = {"healthy", "empty_valid", "schema_drift", "forbidden", "not_found"}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class Budget:
    """A hard request ceiling shared across threads."""

    def __init__(self, total: int) -> None:
        import threading
        self._left = total
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self._left <= 0:
                return False
            self._left -= 1
            return True

    @property
    def remaining(self) -> int:
        return self._left


def http_get(url: str, timeout: int = REQUEST_TIMEOUT) -> dict:
    """One GET. Returns a plain record; raises nothing the caller has to interpret."""
    request = urllib.request.Request(
        url, headers={"User-Agent": getattr(ats_sources, "USER_AGENT", "jobloom"),
                      "Accept": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return {"outcome": "oversized", "status": response.status,
                        "content_type": response.headers.get("Content-Type", ""),
                        "body": "", "bytes_read": len(raw),
                        "latency_ms": round((time.monotonic() - started) * 1000)}
            body = raw.decode(charset, errors="replace")
            return {"outcome": "response", "status": response.status,
                    "bytes_read": len(raw),
                    "content_type": response.headers.get("Content-Type", ""),
                    "body": body, "latency_ms": round((time.monotonic() - started) * 1000)}
    except urllib.error.HTTPError as error:
        return {"outcome": "http_error", "status": error.code,
                "content_type": error.headers.get("Content-Type", "") if error.headers else "",
                "body": "", "latency_ms": round((time.monotonic() - started) * 1000)}
    except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
        reason = getattr(error, "reason", error)
        timed_out = isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason)
        return {"outcome": "timeout" if timed_out else "network_error",
                "status": None, "content_type": "", "body": "",
                "latency_ms": round((time.monotonic() - started) * 1000),
                "error_class": type(reason).__name__}


def endpoint_for(source: dict) -> tuple[str | None, str | None]:
    """The URL this source authorized, or the reason there is none to probe."""
    ats = source.get("ats")
    adapter = ats_sources.ADAPTERS.get(ats)
    if adapter is None:
        return None, "unsupported_ats"
    recorded = (source.get("authorization") or {}).get("endpoint_template")
    if not recorded:
        return None, "endpoint_not_registered"
    # A recorded endpoint that is not the adapter's own is not a probe target: it would
    # mean asking one board's API in another board's shape, on a record nobody checked.
    if recorded != adapter["endpoint_template"]:
        return None, "endpoint_not_registered"
    token = source.get("board_token")
    if not token:
        return None, "endpoint_not_registered"
    return recorded.format(board_token=token), None


def classify(record: dict, ats: str, token: str) -> dict:
    """A verdict, a stable code, and a shape fingerprint - never a response body."""
    if record["outcome"] in {"timeout", "network_error"}:
        return {"verdict": record["outcome"], "code": record.get("error_class", "unknown")}
    if record["outcome"] == "oversized":
        # Its own verdict, and inconclusive. Calling it schema_drift reported a run-level
        # limit as a fault in the board - the same mistake as reading a 429 as breakage.
        return {"verdict": "oversized_response", "code": "response_exceeded_read_ceiling",
                "bytes_read": record.get("bytes_read")}
    status = record["status"]
    if status == 403:
        return {"verdict": "forbidden", "code": "http_403"}
    if status == 404:
        return {"verdict": "not_found", "code": "http_404"}
    if status == 429:
        # Never `broken`. The board asked to be asked later; that is all this means.
        return {"verdict": "rate_limited", "code": "http_429"}
    if status is None or status >= 500:
        return {"verdict": "server_error", "code": f"http_{status}"}
    if status >= 400:
        return {"verdict": "schema_drift", "code": f"http_{status}"}

    try:
        payload = json.loads(record["body"])
    except json.JSONDecodeError:
        return {"verdict": "schema_drift", "code": "not_json",
                "content_type": record["content_type"]}

    # The adapter stays the authority on shape, but it must see one page. SmartRecruiters
    # pages until `offset >= totalFound`, and a getter that returned this same payload every
    # time would count the first page again for every page the board claims to have.
    # `served` is the page whose postings were counted; the adapter asks once more and gets
    # an empty page, which is how the loop is told to stop rather than a page anyone read.
    pages = {"requested": 0, "served": 0}

    def one_page(_url):
        pages["requested"] += 1
        if pages["requested"] == 1:
            pages["served"] = 1
            return payload
        return {"content": [], "jobs": []}

    try:
        postings, notes = ats_sources.ADAPTERS[ats]["fetch"](token, one_page)
    except Exception as error:  # noqa: BLE001 - the adapter rejecting the shape is the finding
        return {"verdict": "schema_drift", "code": f"adapter_{type(error).__name__}"}
    declared_total = payload.get("totalFound") if isinstance(payload, dict) else None

    if not postings:
        # A board with nothing open is a fact about hiring, not about the adapter.
        return {"verdict": "empty_valid", "code": "zero_postings", "postings": 0,
                "pages_read": pages["served"], "pages_requested": pages["requested"],
                "declared_total": declared_total}

    try:
        summary = ats_sources.ADAPTERS[ats]["summary"](postings[0])
    except Exception as error:  # noqa: BLE001
        return {"verdict": "schema_drift", "code": f"summary_{type(error).__name__}",
                "postings": len(postings), "pages_read": pages["served"]}
    missing = [field for field in ("title", "location") if not summary.get(field)]
    fingerprint = sha256_text(json.dumps(sorted(postings[0].keys()), separators=(",", ":")))
    return {"verdict": "schema_drift" if missing else "healthy",
            "code": f"missing:{','.join(missing)}" if missing else "ok",
            # First page only, and named as such: a board with more pages is not being
            # counted here, and `declared_total` is what it says it has.
            "postings_first_page": len(postings), "declared_total": declared_total,
            "pages_read": pages["served"], "pages_requested": pages["requested"],
            "schema_fingerprint": fingerprint,
            "adapter_notes": len(notes)}


def probe_source(source: dict, budget: Budget, get=http_get, timeout: int = REQUEST_TIMEOUT) -> dict:
    """One source, at most two requests, never raising into the run."""
    base = {"company": source.get("company"), "ats": source.get("ats"),
            "enabled": bool(source.get("enabled"))}
    url, refusal = endpoint_for(source)
    if refusal:
        return {**base, "verdict": refusal, "code": refusal, "attempts": 0}
    if not budget.take():
        return {**base, "verdict": "budget_exhausted", "code": "budget", "attempts": 0}

    attempts = 1
    try:
        record = get(url, timeout)
        result = classify(record, source["ats"], source["board_token"])
        if result["verdict"] in RETRYABLE and budget.take():
            attempts += 1
            record = get(url, timeout)
            result = classify(record, source["ats"], source["board_token"])
    except Exception as error:  # noqa: BLE001 - one source must not end the run
        return {**base, "verdict": "network_error", "code": type(error).__name__,
                "attempts": attempts}
    return {**base, **result, "attempts": attempts,
            "latency_ms": record.get("latency_ms"), "http_status": record.get("status"),
            "content_type": record.get("content_type"), "bytes_read": record.get("bytes_read")}


def probe_all(sources: list[dict], *, get=http_get, budget_total: int = GLOBAL_REQUEST_BUDGET,
              concurrency: int = MAX_CONCURRENCY, timeout: int = REQUEST_TIMEOUT) -> dict:
    enabled = [s for s in sources if s.get("enabled")]
    budget = Budget(budget_total)
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(lambda s: probe_source(s, budget, get, timeout), enabled))
    counts = collections.Counter(r["verdict"] for r in results)
    return {
        "schema_version": PROBE_SCHEMA_VERSION,
        "probed_at": now_stamp(),
        "sources_registered": len(sources),
        "sources_enabled": len(enabled),
        "requests_used": budget_total - budget.remaining,
        "request_budget": budget_total,
        "concurrency": concurrency,
        "request_timeout_seconds": timeout,
        "counts": dict(sorted(counts.items())),
        # Only these say something about the board. Everything else describes this run,
        # and averaging the two together is how a rate limit becomes a dead board.
        "conclusive": sum(counts[v] for v in CONCLUSIVE),
        "inconclusive": len(results) - sum(counts[v] for v in CONCLUSIVE),
        "results": sorted(results, key=lambda r: (r["ats"], str(r["company"]).casefold())),
    }


def compare_fingerprints(report: dict, baseline: dict | None) -> dict:
    """Shape changes against a recorded probe, if there is one to compare with."""
    if not baseline:
        return {"status": "no_baseline", "changed": []}
    was = {(r["company"], r["ats"]): r.get("schema_fingerprint") for r in baseline["results"]}
    changed = [f"{r['company']}::{r['ats']}" for r in report["results"]
               if r.get("schema_fingerprint") and was.get((r["company"], r["ats"]))
               and r["schema_fingerprint"] != was[(r["company"], r["ats"])]]
    return {"status": "compared", "changed": sorted(changed)}


def write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def write_report(directory: Path, report: dict) -> Path:
    directory.mkdir(mode=0o700, parents=True)
    path = directory / "probe-report.json"
    write_private(path, json.dumps(report, indent=2, ensure_ascii=False))
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", type=Path, default=Path(".jobloom/ats-sources.json"))
    parser.add_argument("--output-root", type=Path, default=Path(".jobloom/source-health"))
    parser.add_argument("--baseline", type=Path, help="a previous probe report to compare shapes")
    parser.add_argument("--budget", type=int, default=GLOBAL_REQUEST_BUDGET)
    parser.add_argument("--concurrency", type=int, default=MAX_CONCURRENCY)
    parser.add_argument("--timeout", type=int, default=REQUEST_TIMEOUT)
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve endpoints and budgets without making a request")
    args = parser.parse_args()

    sources = ats_sources.load_registry(args.registry)["sources"]
    if args.dry_run:
        resolved = collections.Counter()
        for source in sources:
            _, refusal = endpoint_for(source)
            resolved[refusal or "would_probe"] += 1
        print(json.dumps({"sources": len(sources), "resolution": dict(resolved)}, indent=2))
        return

    report = probe_all(sources, budget_total=args.budget, concurrency=args.concurrency,
                       timeout=args.timeout)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    report["fingerprint_comparison"] = compare_fingerprints(report, baseline)

    path = write_report(args.output_root / report["probed_at"], report)
    print(f"report {path} (0600)")
    print(json.dumps({k: report[k] for k in
                      ("sources_enabled", "requests_used", "counts", "conclusive",
                       "inconclusive")}, indent=2))
    if report["fingerprint_comparison"]["changed"]:
        print(f"schema fingerprint changed: {report['fingerprint_comparison']['changed']}")


if __name__ == "__main__":
    main()
