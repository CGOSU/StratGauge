from __future__ import annotations

import runpy
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from stratgauge.api.app import create_app
from stratgauge.data_sources.base import MarketDataAdapter

_SCRIPT_GLOBALS = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "benchmark_get_endpoints.py"),
    run_name="benchmark_get_endpoints",
)
_load_cases = cast(
    Callable[[str | Path], dict[str, Any]],
    _SCRIPT_GLOBALS["load_cases"],
)
_discover_get_routes = cast(
    Callable[[Mapping[str, object]], tuple[str, ...]],
    _SCRIPT_GLOBALS["discover_get_routes"],
)
_validate_cases = cast(
    Callable[[Mapping[str, object], Mapping[str, Any]], None],
    _SCRIPT_GLOBALS["validate_cases"],
)
_render_case_url = cast(Callable[[str, Any], str], _SCRIPT_GLOBALS["render_case_url"])

_CASES_PATH = Path(__file__).parents[1] / "scripts" / "benchmark_get_cases.json"


class BenchmarkGetCasesTests(unittest.TestCase):
    def test_formal_cases_cover_isolated_app_get_routes_and_runnable_cases_return_200(self) -> None:
        with TemporaryDirectory() as directory:
            app = create_app(
                data_root=directory,
                market_adapter_factory=_unexpected_provider_call,
            )
            openapi = cast(Mapping[str, object], app.openapi())
            cases = _load_cases(_CASES_PATH)

            _validate_cases(openapi, cases)
            discovered = _discover_get_routes(openapi)
            self.assertEqual(set(cases), set(discovered))
            self.assertEqual(len(discovered), 44)

            dynamic_routes = [route for route in cases if "{" in route]
            self.assertTrue(dynamic_routes)
            for route in dynamic_routes:
                self.assertTrue(cases[route].skip_reason)

            with TestClient(app) as client:
                for route, case in sorted(cases.items()):
                    if case.skip_reason is not None:
                        continue
                    rendered_url = _render_case_url("http://testserver", case)
                    parsed = urlsplit(rendered_url)
                    request_target = parsed.path + ("?" + parsed.query if parsed.query else "")
                    response = client.get(request_target)
                    self.assertEqual(response.status_code, 200, route)


def _unexpected_provider_call(_: str) -> MarketDataAdapter:
    raise AssertionError("query benchmark cases must not create a provider")


if __name__ == "__main__":
    unittest.main()
