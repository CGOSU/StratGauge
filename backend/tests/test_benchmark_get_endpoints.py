from __future__ import annotations

import json
import runpy
import time
import unittest
from collections.abc import Callable
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch
from urllib.request import Request

_SCRIPT_GLOBALS = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "benchmark_get_endpoints.py"),
    run_name="benchmark_get_endpoints",
)
benchmark = cast(Any, SimpleNamespace(**_SCRIPT_GLOBALS))
_FUNCTION_GLOBALS = cast(Any, _SCRIPT_GLOBALS["main"]).__globals__


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.closed = False

    def read(self) -> bytes:
        return self.body

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        self.closed = True


class RecordingOpener:
    def __init__(self, responses: list[tuple[int, bytes]]) -> None:
        self.responses = iter(responses)
        self.urls: list[str] = []
        self.timeouts: list[float] = []

    def __call__(self, request: Request, timeout: float) -> FakeResponse:
        self.urls.append(request.full_url)
        self.timeouts.append(timeout)
        status, body = next(self.responses)
        return FakeResponse(status, body)


class BenchmarkGetEndpointsTests(unittest.TestCase):
    def test_discover_get_routes_ignores_non_get_operations(self) -> None:
        document: dict[str, object] = {
            "paths": {
                "/z": {"get": {}, "post": {}},
                "/a": {"post": {}},
                "/b": {"get": {}},
            }
        }

        self.assertEqual(benchmark.discover_get_routes(document), ("/b", "/z"))

    def test_validate_cases_requires_exact_route_coverage(self) -> None:
        document: dict[str, object] = {"paths": {"/a": {"get": {}}, "/b": {"get": {}}}}
        case = benchmark.BenchmarkCase("/a", {}, {}, 200)

        with self.assertRaises(benchmark.BenchmarkConfigError) as missing:
            benchmark.validate_cases(document, {"/a": case})
        self.assertEqual(missing.exception.code, "CASES_MISSING_ROUTES")

        extra_case = benchmark.BenchmarkCase("/old", {}, {}, 200)
        with self.assertRaises(benchmark.BenchmarkConfigError) as extra:
            benchmark.validate_cases(document, {"/a": case, "/b": case, "/old": extra_case})
        self.assertEqual(extra.exception.code, "CASES_UNKNOWN_ROUTES")

    def test_render_case_url_encodes_path_and_query_values(self) -> None:
        case = benchmark.BenchmarkCase(
            "/things/{thing_id}",
            {"thing_id": "A/B"},
            {"q": "a b", "tag": ("x", "y")},
            200,
        )

        self.assertEqual(
            benchmark.render_case_url("https://example.test/", case),
            "https://example.test/things/A%2FB?q=a+b&tag=x&tag=y",
        )

    def test_run_benchmark_warms_up_then_measures_sequentially(self) -> None:
        opener = RecordingOpener(
            [
                (200, b"warm"),
                (200, b"a"),
                (200, b"bb"),
                (200, b"ccc"),
            ]
        )
        case = benchmark.BenchmarkCase("/healthz", {}, {}, 200)

        report = benchmark.run_benchmark(
            "http://example.test",
            {case.route: case},
            iterations=3,
            warmup_requests=1,
            timeout=2.5,
            opener=opener,
        )

        result = _endpoint_result(report)
        self.assertTrue(report["passed"])
        self.assertEqual(opener.urls, ["http://example.test/healthz"] * 4)
        self.assertEqual(opener.timeouts, [2.5] * 4)
        self.assertEqual(result["warmup_status_codes"], [200])
        self.assertEqual(result["status_codes"], [200, 200, 200])
        self.assertEqual(result["response_bytes"], [1, 2, 3])
        median_ms = result["median_ms"]
        max_ms = result["max_ms"]
        self.assertIsInstance(median_ms, float)
        self.assertIsInstance(max_ms, float)
        if not isinstance(median_ms, float) or not isinstance(max_ms, float):
            raise AssertionError("timing values must be floats")
        self.assertGreaterEqual(max_ms, median_ms)
        self.assertNotIn("body", json.dumps(report))

    def test_skip_case_is_reported_without_a_request(self) -> None:
        opener = RecordingOpener([])
        case = benchmark.BenchmarkCase(
            "/v1/items/{item_id}",
            {},
            {},
            200,
            skip_reason="需要稳定的测试实体",
        )

        report = benchmark.run_benchmark(
            "http://example.test",
            {case.route: case},
            iterations=2,
            opener=opener,
        )

        result = _endpoint_result(report)
        self.assertTrue(report["passed"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["skip_reason"], "需要稳定的测试实体")
        self.assertEqual(opener.urls, [])

    def test_http_status_and_median_threshold_failures_are_reported(self) -> None:
        status_opener = RecordingOpener([(503, b"error"), (503, b"error")])
        status_case = benchmark.BenchmarkCase("/healthz", {}, {}, 200)
        status_report = benchmark.run_benchmark(
            "http://example.test",
            {status_case.route: status_case},
            iterations=1,
            warmup_requests=1,
            opener=status_opener,
        )
        self.assertFalse(status_report["passed"])
        self.assertEqual(_endpoint_result(status_report)["status_codes"], [503])

        threshold_opener = RecordingOpener([(200, b"ok"), (200, b"ok")])
        threshold_case = benchmark.BenchmarkCase(
            "/healthz",
            {},
            {},
            200,
            max_median_ms=0,
        )
        with patch.object(time, "perf_counter", side_effect=_increasing_clock()):
            threshold_report = benchmark.run_benchmark(
                "http://example.test",
                {threshold_case.route: threshold_case},
                iterations=1,
                warmup_requests=1,
                opener=threshold_opener,
            )
        self.assertFalse(threshold_report["passed"])
        self.assertEqual(_endpoint_result(threshold_report)["max_median_ms"], 0)

    def test_main_returns_config_error_for_uncovered_route_and_http_error_for_failed_query(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            cases_path = Path(directory) / "cases.json"
            cases_path.write_text(
                json.dumps({"version": 1, "cases": {}}, ensure_ascii=False),
                encoding="utf-8",
            )
            openapi_body = json.dumps({"paths": {"/healthz": {"get": {}}}}).encode()
            with patch.dict(
                _FUNCTION_GLOBALS,
                {
                    "urlopen": lambda request, timeout: FakeResponse(200, openapi_body),
                },
            ):
                output = StringIO()
                with redirect_stdout(output):
                    exit_code = benchmark.main(
                        ["--base-url", "http://example.test", "--cases", str(cases_path)]
                    )
            self.assertEqual(exit_code, 2)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "CASES_MISSING_ROUTES")

            cases_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "cases": {"/healthz": {"expected_status": 200}},
                    }
                ),
                encoding="utf-8",
            )

            def failed_query(request: Request, timeout: float) -> FakeResponse:
                del timeout
                if request.full_url.endswith("/openapi.json"):
                    return FakeResponse(200, openapi_body)
                return FakeResponse(500, b"not emitted")

            with patch.dict(_FUNCTION_GLOBALS, {"urlopen": failed_query}):
                output = StringIO()
                with redirect_stdout(output):
                    exit_code = benchmark.main(
                        [
                            "--base-url",
                            "http://example.test",
                            "--cases",
                            str(cases_path),
                            "--iterations",
                            "1",
                            "--warmup-requests",
                            "1",
                        ]
                    )
            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["passed"])
            self.assertEqual(payload["endpoints"][0]["status_codes"], [500])
            self.assertNotIn("not emitted", output.getvalue())


def _endpoint_result(report: dict[str, object]) -> dict[str, object]:
    endpoints = report["endpoints"]
    if not isinstance(endpoints, list) or len(endpoints) != 1:
        raise AssertionError("expected one endpoint result")
    result = endpoints[0]
    if not isinstance(result, dict):
        raise AssertionError("expected an endpoint result object")
    return result


def _increasing_clock() -> Callable[[], float]:
    value = 0.0

    def tick() -> float:
        nonlocal value
        value += 0.1
        return value

    return tick


if __name__ == "__main__":
    unittest.main()
