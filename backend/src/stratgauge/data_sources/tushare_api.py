"""Small HTTPS client for the Tushare Pro data API."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from stratgauge.data_sources.base import (
    AdapterError,
    RetryableAdapterError,
    is_retryable_provider_message,
)

_ENDPOINT = "https://api.tushare.pro/dataapi"
_TIMEOUT_SECONDS = 30.0


def query_tushare(endpoint: str, params: Mapping[str, str]) -> tuple[dict[str, Any], ...]:
    """Query Tushare without exposing credentials to stored request evidence."""

    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise AdapterError("TUSHARE_TOKEN is not set")

    request_params = {key: value for key, value in params.items() if key != "fields"}
    payload = {
        "api_name": endpoint,
        "token": token,
        "params": request_params,
        "fields": params.get("fields", ""),
    }
    request = Request(
        _ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
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
    except json.JSONDecodeError as exc:
        raise AdapterError("Tushare returned invalid JSON") from exc

    if not isinstance(response_payload, dict):
        raise AdapterError("Tushare returned an invalid response")
    code = response_payload.get("code")
    if code not in (0, "0"):
        message = str(response_payload.get("msg") or "unknown provider error")
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
