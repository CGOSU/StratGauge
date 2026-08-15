"""Tushare Pro HTTPS transport with bounded retries and connection reuse."""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.client import HTTPSConnection
from types import TracebackType
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, getproxies, proxy_bypass, urlopen

from stratgauge.data_sources.base import (
    AdapterError,
    RetryableAdapterError,
    is_retryable_provider_message,
)

_ENDPOINT = "https://api.tushare.pro/dataapi"
_TIMEOUT_SECONDS = 30.0
_DEFAULT_ENDPOINT_INTERVAL_SECONDS = 0.2


class _HttpResponse(Protocol):
    status: int

    def read(self) -> bytes: ...

    def close(self) -> None: ...


class _HttpConnection(Protocol):
    def request(
        self,
        method: str,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> None: ...

    def getresponse(self) -> _HttpResponse: ...

    def set_tunnel(
        self,
        host: str,
        port: int,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str, int, float], _HttpConnection]


@dataclass(frozen=True)
class TushareRetryPolicy:
    """Bounded exponential retry settings for one transport."""

    max_attempts: int = 3
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 4.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 8:
            raise ValueError("max_attempts must be between 1 and 8")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds must not be negative")
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("max_backoff_seconds must not be below initial backoff")


class TushareTransportProtocol(Protocol):
    def query(self, endpoint: str, params: Mapping[str, str]) -> tuple[dict[str, Any], ...]: ...

    def close(self) -> None: ...


_DEFAULT_RETRY_POLICY = TushareRetryPolicy()


class TushareTransport:
    """Reusable task-scoped HTTPS transport.

    The transport owns one connection, applies endpoint throttling before every
    attempt, and closes the connection when the task runner exits. Proxy
    selection follows the host environment through ``urllib``'s proxy helpers;
    tokens are held in memory only and never included in error messages.
    """

    def __init__(
        self,
        *,
        endpoint_url: str = _ENDPOINT,
        token: str | None = None,
        timeout_seconds: float = _TIMEOUT_SECONDS,
        retry_policy: TushareRetryPolicy | None = None,
        endpoint_min_intervals: Mapping[str, float] | None = None,
        default_endpoint_interval_seconds: float = _DEFAULT_ENDPOINT_INTERVAL_SECONDS,
        connection_factory: ConnectionFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        parsed = urlsplit(endpoint_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Tushare endpoint_url must be an HTTPS URL")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if default_endpoint_interval_seconds < 0:
            raise ValueError("default endpoint interval must not be negative")
        intervals = dict(endpoint_min_intervals or {})
        if any(interval < 0 for interval in intervals.values()):
            raise ValueError("endpoint intervals must not be negative")
        self.endpoint_url = endpoint_url
        self._parsed_endpoint = parsed
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._retry_policy = retry_policy or _DEFAULT_RETRY_POLICY
        self._endpoint_min_intervals = intervals
        self._default_endpoint_interval_seconds = default_endpoint_interval_seconds
        self._connection_factory = connection_factory or self._default_connection
        self._sleep = sleep
        self._clock = clock
        self._connection: _HttpConnection | None = None
        self._last_request_at: dict[str, float] = {}

    def __enter__(self) -> TushareTransport:
        return self

    def __exit__(
        self,
        _: type[BaseException] | None,
        __: BaseException | None,
        ___: TracebackType | None,
    ) -> None:
        self.close()

    def query(self, endpoint: str, params: Mapping[str, str]) -> tuple[dict[str, Any], ...]:
        """Execute one bounded, throttled request and return normalized rows."""

        token = self._token if self._token is not None else os.environ.get("TUSHARE_TOKEN", "")
        token = token.strip()
        if not token:
            raise AdapterError("TUSHARE_TOKEN is not set")
        payload = _request_payload(endpoint, params, token)
        for attempt in range(self._retry_policy.max_attempts):
            try:
                self._throttle(endpoint)
                return self._request_once(endpoint, payload, token)
            except RetryableAdapterError as exc:
                self._drop_connection()
                if attempt + 1 >= self._retry_policy.max_attempts:
                    message = _redact_message(str(exc), token)
                    raise RetryableAdapterError(message) from exc
                delay = min(
                    self._retry_policy.initial_backoff_seconds * (2**attempt),
                    self._retry_policy.max_backoff_seconds,
                )
                if delay > 0:
                    self._sleep(delay)
        raise AssertionError("retry loop must return or raise")

    def close(self) -> None:
        """Close the reusable connection; safe to call more than once."""

        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _request_once(
        self,
        endpoint: str,
        payload: bytes,
        token: str,
    ) -> tuple[dict[str, Any], ...]:
        connection = self._connection
        try:
            if connection is None:
                connection = self._connection_factory(
                    self._parsed_endpoint.hostname or "",
                    self._parsed_endpoint.port or 443,
                    self._timeout_seconds,
                )
                self._connection = connection
            connection.request(
                "POST",
                self._parsed_endpoint.path or "/",
                payload,
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            status = response.status
            body = response.read()
            response.close()
        except (TimeoutError, OSError) as exc:
            self._drop_connection()
            raise RetryableAdapterError("Tushare request failed") from exc
        except Exception as exc:
            self._drop_connection()
            raise AdapterError("Tushare request failed") from exc

        if status in {408, 425, 429, 500, 502, 503, 504}:
            self._drop_connection()
            raise RetryableAdapterError(f"Tushare request failed with HTTP {status}")
        if status < 200 or status >= 300:
            self._drop_connection()
            raise AdapterError(f"Tushare request failed with HTTP {status}")
        try:
            response_payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterError("Tushare returned invalid JSON") from exc
        return _parse_response_payload(response_payload, token)

    def _throttle(self, endpoint: str) -> None:
        interval = self._endpoint_min_intervals.get(
            endpoint,
            self._default_endpoint_interval_seconds,
        )
        previous = self._last_request_at.get(endpoint)
        if previous is not None:
            delay = interval - (self._clock() - previous)
            if delay > 0:
                self._sleep(delay)
        self._last_request_at[endpoint] = self._clock()

    def _drop_connection(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _default_connection(self, host: str, port: int, timeout: float) -> _HttpConnection:
        proxy_url = self._proxy_url(host)
        if proxy_url is None:
            return HTTPSConnection(host, port, timeout=timeout)
        proxy = urlsplit(proxy_url)
        proxy_host = proxy.hostname
        if not proxy_host:
            raise AdapterError("Tushare proxy configuration is invalid")
        proxy_port = proxy.port or (443 if proxy.scheme == "https" else 80)
        connection = HTTPSConnection(proxy_host, proxy_port, timeout=timeout)
        tunnel_headers: dict[str, str] = {}
        if proxy.username is not None:
            credentials = f"{unquote(proxy.username)}:{unquote(proxy.password or '')}"
            encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            tunnel_headers["Proxy-Authorization"] = f"Basic {encoded}"
        connection.set_tunnel(host, port, headers=tunnel_headers)
        return connection

    def _proxy_url(self, host: str) -> str | None:
        if proxy_bypass(host):
            return None
        proxies = getproxies()
        return proxies.get(self._parsed_endpoint.scheme) or proxies.get("all")


def query_tushare(endpoint: str, params: Mapping[str, str]) -> tuple[dict[str, Any], ...]:
    """Legacy one-shot Tushare query kept for reference-data callers and tests."""

    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise AdapterError("TUSHARE_TOKEN is not set")

    payload = _request_payload(endpoint, params, token)
    request = Request(
        _ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        error_type = (
            RetryableAdapterError
            if exc.code in {408, 425, 429, 500, 502, 503, 504}
            else AdapterError
        )
        raise error_type(f"Tushare request failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RetryableAdapterError("Tushare request failed") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("Tushare returned invalid JSON") from exc

    return _parse_response_payload(response_payload, token)


def _request_payload(endpoint: str, params: Mapping[str, str], token: str) -> bytes:
    request_params = {key: value for key, value in params.items() if key != "fields"}
    payload = {
        "api_name": endpoint,
        "token": token,
        "params": request_params,
        "fields": params.get("fields", ""),
    }
    return json.dumps(payload).encode("utf-8")


def _parse_response_payload(response_payload: object, token: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(response_payload, dict):
        raise AdapterError("Tushare returned an invalid response")
    code = response_payload.get("code")
    if code not in (0, "0"):
        message = _redact_message(
            str(response_payload.get("msg") or "unknown provider error"),
            token,
        )
        error_type = (
            RetryableAdapterError if is_retryable_provider_message(message) else AdapterError
        )
        raise error_type(f"Tushare API error {code}: {message}")

    data = response_payload.get("data")
    if not isinstance(data, Mapping):
        raise AdapterError("Tushare response is missing data")
    fields = data.get("fields")
    items = data.get("items")
    if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
        raise AdapterError("Tushare response is missing field names")
    if not isinstance(items, list):
        raise AdapterError("Tushare response is missing rows")

    records: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            raise AdapterError("Tushare response row does not match its fields")
        records.append(dict(zip(fields, item, strict=True)))
    return tuple(records)


def _redact_message(message: str, token: str) -> str:
    return message.replace(token, "[redacted]") if token else message
