"""Run sequential performance checks for every configured GET endpoint."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

_CASES_VERSION = 1
_DEFAULT_CASES_FILE = Path(__file__).with_name("benchmark_get_cases.json")
_ROUTE_PARAMETER_PATTERN = re.compile(r"\{([^{}]+)\}")

ParameterScalar = str | int | float | bool
ParameterValue = ParameterScalar | tuple[ParameterScalar, ...]


class BenchmarkError(RuntimeError):
    """Base error with a safe, stable message for CLI output."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BenchmarkConfigError(BenchmarkError):
    """The cases file or OpenAPI document cannot be used safely."""


class BenchmarkExecutionError(BenchmarkError):
    """The OpenAPI document or endpoint requests could not be completed."""


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """One OpenAPI route invocation or an explicit skip declaration."""

    route: str
    path_params: Mapping[str, ParameterValue]
    query_params: Mapping[str, ParameterValue]
    expected_status: int
    max_median_ms: float | None = None
    skip_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _RequestResult:
    status: int | None
    response_bytes: int
    elapsed_ms: float
    error_code: str | None
    body: bytes


class _Response(Protocol):
    def read(self) -> bytes: ...

    def getcode(self) -> int | None: ...

    def close(self) -> None: ...


class _Opener(Protocol):
    def __call__(self, request: Request, timeout: float) -> _Response: ...


def discover_get_routes(openapi: Mapping[str, object]) -> tuple[str, ...]:
    """Return the sorted GET path set from an OpenAPI document."""

    raw_paths = openapi.get("paths")
    if not isinstance(raw_paths, Mapping):
        raise BenchmarkConfigError("OPENAPI_PATHS_INVALID", "OpenAPI paths are missing or invalid")

    routes: list[str] = []
    for raw_route, raw_item in raw_paths.items():
        if not isinstance(raw_route, str) or not raw_route.startswith("/"):
            raise BenchmarkConfigError("OPENAPI_ROUTE_INVALID", "OpenAPI contains an invalid route")
        if not isinstance(raw_item, Mapping):
            raise BenchmarkConfigError(
                "OPENAPI_ROUTE_INVALID", "OpenAPI contains an invalid route item"
            )
        if "get" in raw_item:
            routes.append(raw_route)
    return tuple(sorted(routes))


def load_cases(path: str | Path) -> dict[str, BenchmarkCase]:
    """Load the versioned route case map without making network requests."""

    cases_path = Path(path)
    try:
        document = json.loads(cases_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkConfigError(
            "CASES_FILE_INVALID", "GET endpoint cases file cannot be read"
        ) from exc

    if not isinstance(document, Mapping):
        raise BenchmarkConfigError("CASES_FILE_INVALID", "GET endpoint cases must be a JSON object")
    if document.get("version") != _CASES_VERSION:
        raise BenchmarkConfigError(
            "CASES_VERSION_UNSUPPORTED", "GET endpoint cases version is unsupported"
        )
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, Mapping):
        raise BenchmarkConfigError(
            "CASES_FILE_INVALID", "GET endpoint cases must contain a cases object"
        )

    cases: dict[str, BenchmarkCase] = {}
    for raw_route, raw_case in raw_cases.items():
        if not isinstance(raw_route, str) or not raw_route.startswith("/"):
            raise BenchmarkConfigError(
                "CASE_ROUTE_INVALID", "GET endpoint case contains an invalid route"
            )
        if raw_route in cases:
            raise BenchmarkConfigError(
                "CASE_ROUTE_DUPLICATE", "GET endpoint cases contain a duplicate route"
            )
        cases[raw_route] = _parse_case(raw_route, raw_case)
    return cases


def validate_cases(
    openapi: Mapping[str, object],
    cases: Mapping[str, BenchmarkCase],
) -> None:
    """Require exact route coverage and valid required parameters."""

    discovered = set(discover_get_routes(openapi))
    configured = set(cases)
    missing = sorted(discovered - configured)
    extra = sorted(configured - discovered)
    if missing:
        raise BenchmarkConfigError(
            "CASES_MISSING_ROUTES",
            "GET endpoint cases are missing routes: " + ", ".join(missing),
        )
    if extra:
        raise BenchmarkConfigError(
            "CASES_UNKNOWN_ROUTES",
            "GET endpoint cases contain unknown routes: " + ", ".join(extra),
        )

    raw_paths = cast(Mapping[object, object], openapi["paths"])
    for route, case in cases.items():
        if case.skip_reason is not None:
            continue
        _validate_route_parameters(route, case, raw_paths[route])


def render_case_url(base_url: str, case: BenchmarkCase) -> str:
    """Render a case URL while encoding path and query values."""

    _validate_base_url(base_url)
    placeholders = tuple(_ROUTE_PARAMETER_PATTERN.findall(case.route))
    placeholder_set = set(placeholders)
    if placeholder_set != set(case.path_params):
        raise BenchmarkConfigError(
            "CASE_PATH_PARAMETERS_INVALID",
            f"GET endpoint case has invalid path parameters: {case.route}",
        )

    rendered_route = case.route
    for name in placeholders:
        rendered_route = rendered_route.replace(
            "{" + name + "}",
            quote(str(case.path_params[name]), safe=""),
        )
    query_items: list[tuple[str, ParameterScalar]] = []
    for name, value in case.query_params.items():
        if isinstance(value, tuple):
            query_items.extend((name, item) for item in value)
        else:
            query_items.append((name, value))
    query = urlencode(query_items)
    route = rendered_route + ("?" + query if query else "")
    return base_url.rstrip("/") + route


def run_benchmark(
    base_url: str,
    cases: Mapping[str, BenchmarkCase],
    *,
    iterations: int = 5,
    warmup_requests: int = 1,
    timeout: float = 30.0,
    opener: _Opener | None = None,
) -> dict[str, object]:
    """Run configured cases sequentially and return a response-safe report."""

    _validate_base_url(base_url)
    request_opener = opener or _default_opener
    if iterations < 1:
        raise BenchmarkConfigError("ITERATIONS_INVALID", "iterations must be at least 1")
    if warmup_requests < 0:
        raise BenchmarkConfigError(
            "WARMUP_REQUESTS_INVALID", "warmup_requests must not be negative"
        )
    if not math.isfinite(timeout) or timeout <= 0:
        raise BenchmarkConfigError("TIMEOUT_INVALID", "timeout must be greater than zero")

    endpoint_results: list[dict[str, object]] = []
    for route in sorted(cases):
        case = cases[route]
        if case.skip_reason is not None:
            endpoint_results.append(
                {
                    "path": route,
                    "skipped": True,
                    "skip_reason": case.skip_reason,
                    "passed": True,
                }
            )
            continue

        url = render_case_url(base_url, case)
        warmups = [
            _request_once(url, timeout=timeout, opener=request_opener)
            for _ in range(warmup_requests)
        ]
        measurements = [
            _request_once(url, timeout=timeout, opener=request_opener) for _ in range(iterations)
        ]
        endpoint_results.append(_build_endpoint_result(case, warmups, measurements))

    passed = all(bool(result["passed"]) for result in endpoint_results)
    return {
        "passed": passed,
        "iterations": iterations,
        "warmup_requests": warmup_requests,
        "endpoints": endpoint_results,
    }


def fetch_openapi(
    base_url: str,
    *,
    timeout: float = 30.0,
    opener: _Opener | None = None,
) -> Mapping[str, object]:
    """Read and validate the running service's OpenAPI JSON."""

    _validate_base_url(base_url)
    request_opener = opener or _default_opener
    if not math.isfinite(timeout) or timeout <= 0:
        raise BenchmarkConfigError("TIMEOUT_INVALID", "timeout must be greater than zero")
    result = _request_once(
        base_url.rstrip("/") + "/openapi.json",
        timeout=timeout,
        opener=request_opener,
    )
    if result.error_code is not None or result.status != 200:
        raise BenchmarkExecutionError("OPENAPI_REQUEST_FAILED", "OpenAPI request failed")
    try:
        document = json.loads(result.body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkExecutionError(
            "OPENAPI_RESPONSE_INVALID", "OpenAPI response is invalid"
        ) from exc
    if not isinstance(document, Mapping):
        raise BenchmarkExecutionError("OPENAPI_RESPONSE_INVALID", "OpenAPI response is invalid")
    return cast(Mapping[str, object], document)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    try:
        openapi = fetch_openapi(arguments.base_url, timeout=arguments.timeout)
        cases = load_cases(arguments.cases)
        validate_cases(openapi, cases)
        report = run_benchmark(
            arguments.base_url,
            cases,
            iterations=arguments.iterations,
            warmup_requests=arguments.warmup_requests,
            timeout=arguments.timeout,
        )
    except BenchmarkConfigError as exc:
        _print_json({"error": {"code": exc.code, "message": exc.message}})
        return 2
    except BenchmarkExecutionError as exc:
        _print_json({"error": {"code": exc.code, "message": exc.message}})
        return 1

    _print_json(report)
    return 0 if bool(report["passed"]) else 1


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="正在运行的 FastAPI 服务地址")
    parser.add_argument("--cases", type=Path, default=_DEFAULT_CASES_FILE)
    parser.add_argument("--iterations", type=_positive_int, default=5)
    parser.add_argument("--warmup-requests", type=_nonnegative_int, default=1)
    parser.add_argument("--timeout", type=_positive_float, default=30.0)
    return parser.parse_args(argv)


def _parse_case(route: str, raw_case: object) -> BenchmarkCase:
    if not isinstance(raw_case, Mapping):
        raise BenchmarkConfigError("CASE_INVALID", f"GET endpoint case is invalid: {route}")
    allowed_keys = {"path", "query", "expected_status", "max_median_ms", "skip_reason"}
    unknown_keys = sorted(str(key) for key in raw_case if key not in allowed_keys)
    if unknown_keys:
        raise BenchmarkConfigError("CASE_INVALID", f"GET endpoint case has unknown fields: {route}")

    path_params = _parse_parameter_mapping(raw_case.get("path", {}), route, "path")
    query_params = _parse_parameter_mapping(raw_case.get("query", {}), route, "query")
    skip_reason = raw_case.get("skip_reason")
    if skip_reason is not None and (not isinstance(skip_reason, str) or not skip_reason.strip()):
        raise BenchmarkConfigError("CASE_INVALID", f"GET endpoint skip reason is invalid: {route}")

    raw_status = raw_case.get("expected_status", 200)
    if (
        isinstance(raw_status, bool)
        or not isinstance(raw_status, int)
        or not 100 <= raw_status <= 599
    ):
        raise BenchmarkConfigError("CASE_INVALID", f"GET endpoint status is invalid: {route}")
    max_median_ms = _parse_threshold(raw_case.get("max_median_ms"), route)
    return BenchmarkCase(
        route=route,
        path_params=path_params,
        query_params=query_params,
        expected_status=raw_status,
        max_median_ms=max_median_ms,
        skip_reason=skip_reason.strip() if isinstance(skip_reason, str) else None,
    )


def _parse_parameter_mapping(
    raw_value: object, route: str, field: str
) -> dict[str, ParameterValue]:
    if not isinstance(raw_value, Mapping):
        raise BenchmarkConfigError(
            "CASE_INVALID", f"GET endpoint {field} parameters are invalid: {route}"
        )
    values: dict[str, ParameterValue] = {}
    for raw_name, raw_parameter in raw_value.items():
        if not isinstance(raw_name, str) or not raw_name:
            raise BenchmarkConfigError(
                "CASE_INVALID", f"GET endpoint {field} parameter is invalid: {route}"
            )
        values[raw_name] = _parse_parameter_value(raw_parameter, route, field)
    return values


def _parse_parameter_value(raw_value: object, route: str, field: str) -> ParameterValue:
    if isinstance(raw_value, (str, int, bool)):
        return raw_value
    if isinstance(raw_value, float):
        if math.isfinite(raw_value):
            return raw_value
    if isinstance(raw_value, list):
        values: list[ParameterScalar] = []
        for item in raw_value:
            if isinstance(item, (str, int, bool)):
                values.append(item)
            elif isinstance(item, float) and math.isfinite(item):
                values.append(item)
            else:
                raise BenchmarkConfigError(
                    "CASE_INVALID",
                    f"GET endpoint {field} parameter value is invalid: {route}",
                )
        return tuple(values)
    raise BenchmarkConfigError(
        "CASE_INVALID", f"GET endpoint {field} parameter value is invalid: {route}"
    )


def _parse_threshold(raw_value: object, route: str) -> float | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise BenchmarkConfigError("CASE_INVALID", f"GET endpoint threshold is invalid: {route}")
    threshold = float(raw_value)
    if not math.isfinite(threshold) or threshold < 0:
        raise BenchmarkConfigError("CASE_INVALID", f"GET endpoint threshold is invalid: {route}")
    return threshold


def _validate_route_parameters(
    route: str,
    case: BenchmarkCase,
    raw_path_item: object,
) -> None:
    placeholders = set(_ROUTE_PARAMETER_PATTERN.findall(route))
    if placeholders != set(case.path_params):
        raise BenchmarkConfigError(
            "CASE_PATH_PARAMETERS_INVALID",
            f"GET endpoint case has invalid path parameters: {route}",
        )
    if not isinstance(raw_path_item, Mapping):
        raise BenchmarkConfigError("OPENAPI_ROUTE_INVALID", "OpenAPI route item is invalid")

    required_query: set[str] = set()
    required_path: set[str] = set(placeholders)
    for raw_parameter in _path_parameters(raw_path_item):
        if not isinstance(raw_parameter, Mapping):
            continue
        name = raw_parameter.get("name")
        location = raw_parameter.get("in")
        if not isinstance(name, str) or location not in {"path", "query"}:
            continue
        if location == "path":
            required_path.add(name)
        elif raw_parameter.get("required") is True:
            required_query.add(name)

    if not required_path.issubset(case.path_params):
        raise BenchmarkConfigError(
            "CASE_REQUIRED_PARAMETER_MISSING",
            f"GET endpoint case is missing a required path parameter: {route}",
        )
    if not required_query.issubset(case.query_params):
        raise BenchmarkConfigError(
            "CASE_REQUIRED_PARAMETER_MISSING",
            f"GET endpoint case is missing a required query parameter: {route}",
        )


def _path_parameters(raw_path_item: Mapping[object, object]) -> tuple[object, ...]:
    parameters: list[object] = []
    raw_level_parameters = raw_path_item.get("parameters", ())
    if isinstance(raw_level_parameters, list):
        parameters.extend(raw_level_parameters)
    raw_get = raw_path_item.get("get")
    if isinstance(raw_get, Mapping):
        raw_operation_parameters = raw_get.get("parameters", ())
        if isinstance(raw_operation_parameters, list):
            parameters.extend(raw_operation_parameters)
    return tuple(parameters)


def _build_endpoint_result(
    case: BenchmarkCase,
    warmups: Sequence[_RequestResult],
    measurements: Sequence[_RequestResult],
) -> dict[str, object]:
    timings = [sample.elapsed_ms for sample in measurements]
    statuses = [sample.status for sample in measurements]
    response_bytes = [sample.response_bytes for sample in measurements]
    errors = [
        sample.error_code for sample in (*warmups, *measurements) if sample.error_code is not None
    ]
    median_ms = statistics.median(timings) if timings else None
    max_ms = max(timings) if timings else None
    warmup_ok = all(
        sample.error_code is None and sample.status == case.expected_status for sample in warmups
    )
    status_ok = all(
        sample.error_code is None and sample.status == case.expected_status
        for sample in measurements
    )
    threshold_ok = case.max_median_ms is None or (
        median_ms is not None and median_ms <= case.max_median_ms
    )
    result: dict[str, object] = {
        "path": case.route,
        "skipped": False,
        "passed": warmup_ok and status_ok and threshold_ok,
        "expected_status": case.expected_status,
        "warmup_status_codes": [sample.status for sample in warmups],
        "status_codes": statuses,
        "response_bytes": response_bytes,
        "iterations": len(measurements),
        "median_ms": _rounded(median_ms),
        "max_ms": _rounded(max_ms),
        "max_median_ms": case.max_median_ms,
    }
    if errors:
        result["errors"] = errors
    return result


def _request_once(url: str, *, timeout: float, opener: _Opener) -> _RequestResult:
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    started = time.perf_counter()
    response: _Response | None = None
    try:
        response = opener(request, timeout)
        body = response.read()
        status = response.getcode()
        if status is None:
            return _RequestResult(None, len(body), _elapsed_ms(started), "invalid_response", body)
        return _RequestResult(status, len(body), _elapsed_ms(started), None, body)
    except HTTPError as exc:
        try:
            body = exc.read()
        except OSError:
            body = b""
        finally:
            exc.close()
        return _RequestResult(exc.code, len(body), _elapsed_ms(started), None, body)
    except (OSError, TimeoutError, URLError):
        return _RequestResult(None, 0, _elapsed_ms(started), "network_error", b"")
    finally:
        if response is not None:
            response.close()


def _default_opener(request: Request, timeout: float) -> _Response:
    return cast(_Response, urlopen(request, timeout=timeout))


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _validate_base_url(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BenchmarkConfigError("BASE_URL_INVALID", "base-url must be an HTTP or HTTPS URL")
    if parsed.query or parsed.fragment:
        raise BenchmarkConfigError(
            "BASE_URL_INVALID", "base-url must not contain query or fragment"
        )


def _positive_int(raw_value: str) -> int:
    value = int(raw_value)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _nonnegative_int(raw_value: str) -> int:
    value = int(raw_value)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def _positive_float(raw_value: str) -> float:
    value = float(raw_value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _print_json(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
