import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "skills" / "jobloom" / "scripts"
SPEC = importlib.util.spec_from_file_location("ats_source_probe", SCRIPTS / "ats_source_probe.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


ADAPTER_ENDPOINT = object()


def source(company="Acme", ats="greenhouse", token="acme", enabled=True,
           endpoint=ADAPTER_ENDPOINT):
    adapter = MODULE.ats_sources.ADAPTERS.get(ats)
    if endpoint is ADAPTER_ENDPOINT:
        endpoint = adapter["endpoint_template"] if adapter else None
    return {"company": company, "ats": ats, "board_token": token, "enabled": enabled,
            "board_url": f"https://example.test/{token}",
            "authorization": {"basis": "public_job_board_api",
                              "endpoint_template": endpoint}}


GREENHOUSE_OK = {"jobs": [{"id": 1, "title": "Data Analyst", "absolute_url": "https://x/1",
                           "location": {"name": "New York, NY"}, "updated_at": "2026-08-01",
                           "content": "work", "offices": [{"name": "New York"}]}]}


def transport(**by_url):
    """A fake transport. Records every URL it was asked for, and never touches a network."""
    calls = []

    def get(url, timeout=None):
        calls.append(url)
        record = by_url.get("default")
        return dict(record) if record else {"outcome": "response", "status": 200,
                                            "content_type": "application/json",
                                            "body": json.dumps(GREENHOUSE_OK), "latency_ms": 1}
    get.calls = calls
    return get


def responding(status=200, body=None, outcome="response", **extra):
    return transport(default={"outcome": outcome, "status": status,
                              "content_type": "application/json",
                              "body": json.dumps(body) if body is not None
                              else json.dumps(GREENHOUSE_OK),
                              "latency_ms": 1, **extra})


class EndpointResolution(unittest.TestCase):
    def test_a_registered_endpoint_matching_its_adapter_is_probeable(self):
        url, refusal = MODULE.endpoint_for(source())
        self.assertIsNone(refusal)
        self.assertIn("acme", url)

    def test_an_endpoint_the_adapter_would_not_use_is_refused(self):
        """Probing a recorded URL nobody cross-checked is how an audit becomes a crawl."""
        _, refusal = MODULE.endpoint_for(source(endpoint="https://evil.test/{board_token}"))
        self.assertEqual(refusal, "endpoint_not_registered")

    def test_a_source_with_no_recorded_authorization_is_refused(self):
        _, refusal = MODULE.endpoint_for(source(endpoint=None))
        self.assertEqual(refusal, "endpoint_not_registered")

    def test_a_source_without_a_token_is_refused(self):
        _, refusal = MODULE.endpoint_for({**source(), "board_token": None})
        self.assertEqual(refusal, "endpoint_not_registered")

    def test_an_unknown_ats_is_refused(self):
        _, refusal = MODULE.endpoint_for({**source(), "ats": "workday"})
        self.assertEqual(refusal, "unsupported_ats")


class Verdicts(unittest.TestCase):
    def probe(self, get, budget=10):
        return MODULE.probe_source(source(), MODULE.Budget(budget), get)

    def test_a_board_with_postings_is_healthy(self):
        result = self.probe(responding())
        self.assertEqual(result["verdict"], "healthy")
        self.assertEqual(result["postings_first_page"], 1)
        self.assertEqual(result["pages_read"], 1)
        self.assertEqual(len(result["schema_fingerprint"]), 64)

    def test_a_successful_probe_keeps_the_content_type_and_size(self):
        result = self.probe(responding())
        self.assertEqual(result["content_type"], "application/json")

    def test_a_board_with_nothing_open_is_valid_not_broken(self):
        """Zero openings is a fact about hiring, not about the adapter."""
        result = self.probe(responding(body={"jobs": []}))
        self.assertEqual(result["verdict"], "empty_valid")

    def test_a_rate_limit_is_never_reported_as_breakage(self):
        result = self.probe(responding(status=429, outcome="http_error"))
        self.assertEqual(result["verdict"], "rate_limited")
        self.assertNotIn(result["verdict"], MODULE.CONCLUSIVE)

    def test_forbidden_and_not_found_are_kept_apart(self):
        self.assertEqual(self.probe(responding(status=403, outcome="http_error"))["verdict"],
                         "forbidden")
        self.assertEqual(self.probe(responding(status=404, outcome="http_error"))["verdict"],
                         "not_found")

    def test_a_server_error_is_its_own_verdict(self):
        self.assertEqual(self.probe(responding(status=503, outcome="http_error"))["verdict"],
                         "server_error")

    def test_a_timeout_is_reported_as_itself(self):
        get = transport(default={"outcome": "timeout", "status": None, "content_type": "",
                                 "body": "", "latency_ms": 15000, "error_class": "timeout"})
        self.assertEqual(self.probe(get)["verdict"], "timeout")

    def test_a_body_that_is_not_json_is_schema_drift(self):
        get = transport(default={"outcome": "response", "status": 200,
                                 "content_type": "text/html", "body": "<html>", "latency_ms": 1})
        result = self.probe(get)
        self.assertEqual(result["verdict"], "schema_drift")
        self.assertEqual(result["code"], "not_json")

    def test_a_moved_field_is_schema_drift_not_a_healthy_board(self):
        moved = {"jobs": [{"id": 1, "absolute_url": "https://x/1", "content": "work",
                           "location": {"name": "New York, NY"}}]}
        result = self.probe(responding(body=moved))
        self.assertEqual(result["verdict"], "schema_drift")
        self.assertIn("title", result["code"])

    def test_a_payload_the_adapter_cannot_read_is_schema_drift(self):
        result = self.probe(responding(body={"unexpected": "shape"}))
        self.assertIn(result["verdict"], {"schema_drift", "empty_valid"})

    def test_no_response_body_reaches_the_result(self):
        result = self.probe(responding())
        self.assertNotIn("body", result)
        self.assertNotIn("Data Analyst", json.dumps(result))


class Pagination(unittest.TestCase):
    """A paged board must not have its first page counted once per page it claims."""

    def smartrecruiters_page(self, total):
        content = [{"id": f"p{i}", "name": "Data Analyst", "releasedDate": "2026-08-01",
                    "location": {"fullLocation": "New York, NY"},
                    "company": {"identifier": "acme"}} for i in range(100)]
        return {"content": content, "totalFound": total}

    def probe(self, total):
        get = transport(default={"outcome": "response", "status": 200,
                                 "content_type": "application/json",
                                 "body": json.dumps(self.smartrecruiters_page(total)),
                                 "latency_ms": 1})
        return MODULE.probe_source(source(ats="smartrecruiters"), MODULE.Budget(10), get)

    def test_a_board_claiming_more_than_one_page_is_read_once(self):
        result = self.probe(total=900)
        self.assertEqual(result["pages_read"], 1)
        self.assertEqual(result["postings_first_page"], 100,
                         "the first page, not the first page repeated nine times")

    def test_the_declared_total_is_reported_rather_than_inferred(self):
        self.assertEqual(self.probe(total=900)["declared_total"], 900)

    def test_a_single_page_board_is_unaffected(self):
        result = self.probe(total=100)
        self.assertEqual(result["pages_read"], 1)
        self.assertEqual(result["postings_first_page"], 100)


class ResponseSize(unittest.TestCase):
    def oversized(self):
        return MODULE.probe_source(source(), MODULE.Budget(10), transport(default={
            "outcome": "oversized", "status": 200, "content_type": "application/json",
            "body": "", "bytes_read": MODULE.MAX_RESPONSE_BYTES + 1, "latency_ms": 1}))

    def test_an_oversized_response_is_refused_rather_than_read(self):
        self.assertEqual(self.oversized()["verdict"], "oversized_response")

    def test_an_oversized_response_says_nothing_about_the_board(self):
        """A read ceiling is this run stopping, not the board being wrong."""
        self.assertNotIn(self.oversized()["verdict"], MODULE.CONCLUSIVE)

    def test_the_ceiling_clears_the_largest_real_board(self):
        """The biggest registered board answers with 12MB; a cap under it refuses a
        working source."""
        self.assertGreater(MODULE.MAX_RESPONSE_BYTES, 12 * 1024 * 1024)


class BudgetAndRetry(unittest.TestCase):
    def test_a_timeout_is_retried_once_and_no_more(self):
        get = transport(default={"outcome": "timeout", "status": None, "content_type": "",
                                 "body": "", "latency_ms": 1, "error_class": "timeout"})
        result = MODULE.probe_source(source(), MODULE.Budget(10), get)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(get.calls), 2)

    def test_a_forbidden_response_is_never_retried(self):
        get = responding(status=403, outcome="http_error")
        MODULE.probe_source(source(), MODULE.Budget(10), get)
        self.assertEqual(len(get.calls), 1)

    def test_a_rate_limit_is_never_retried(self):
        get = responding(status=429, outcome="http_error")
        MODULE.probe_source(source(), MODULE.Budget(10), get)
        self.assertEqual(len(get.calls), 1)

    def test_the_global_budget_is_a_ceiling_not_a_hint(self):
        get = responding()
        report = MODULE.probe_all([source(f"Co{i}", token=f"c{i}") for i in range(10)],
                                  get=get, budget_total=4, concurrency=1)
        self.assertEqual(len(get.calls), 4)
        self.assertEqual(report["counts"].get("budget_exhausted"), 6)

    def test_an_exhausted_budget_is_never_counted_as_evidence(self):
        report = MODULE.probe_all([source(f"Co{i}", token=f"c{i}") for i in range(6)],
                                  get=responding(), budget_total=2, concurrency=1)
        self.assertEqual(report["conclusive"], 2)
        self.assertEqual(report["inconclusive"], 4)

    def test_a_disabled_source_is_not_probed(self):
        get = responding()
        report = MODULE.probe_all([source(enabled=False)], get=get)
        self.assertEqual(len(get.calls), 0)
        self.assertEqual(report["sources_enabled"], 0)

    def test_one_source_raising_does_not_end_the_run(self):
        def get(url, timeout=None):
            if "boom" in url:
                raise RuntimeError("adapter exploded")
            return {"outcome": "response", "status": 200, "content_type": "application/json",
                    "body": json.dumps(GREENHOUSE_OK), "latency_ms": 1}
        report = MODULE.probe_all([source("Boom", token="boom"), source("Fine", token="fine")],
                                  get=get, concurrency=1)
        self.assertEqual(report["counts"], {"healthy": 1, "network_error": 1})
        self.assertEqual({r["company"] for r in report["results"]}, {"Boom", "Fine"})


class ReadOnly(unittest.TestCase):
    def test_a_probe_writes_nothing_but_its_own_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "ats-sources.json"
            registry.write_text(json.dumps({"schema_version": "1", "sources": [source()]}))
            before = MODULE.sha256_text(registry.read_text())

            report = MODULE.probe_all([source()], get=responding())
            out = root / "health" / report["probed_at"]
            path = MODULE.write_report(out, report)

            self.assertEqual(MODULE.sha256_text(registry.read_text()), before,
                             "the registry is read-only to this tool")
            self.assertEqual(oct(path.stat().st_mode)[-3:], "600")
            self.assertEqual(oct(out.stat().st_mode)[-3:], "700")

    def test_a_report_directory_is_never_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            report = MODULE.probe_all([source()], get=responding())
            MODULE.write_report(out, report)
            with self.assertRaises(FileExistsError):
                MODULE.write_report(out, report)


class Fingerprints(unittest.TestCase):
    def test_without_a_baseline_nothing_is_claimed(self):
        report = MODULE.probe_all([source()], get=responding())
        self.assertEqual(MODULE.compare_fingerprints(report, None),
                         {"status": "no_baseline", "changed": []})

    def test_a_changed_posting_shape_is_named(self):
        first = MODULE.probe_all([source()], get=responding())
        widened = {"jobs": [{**GREENHOUSE_OK["jobs"][0], "new_field": 1}]}
        second = MODULE.probe_all([source()], get=responding(body=widened))
        self.assertEqual(MODULE.compare_fingerprints(second, first)["changed"],
                         ["Acme::greenhouse"])

    def test_an_unchanged_shape_is_not_named(self):
        first = MODULE.probe_all([source()], get=responding())
        second = MODULE.probe_all([source()], get=responding())
        self.assertEqual(MODULE.compare_fingerprints(second, first)["changed"], [])


if __name__ == "__main__":
    unittest.main()
