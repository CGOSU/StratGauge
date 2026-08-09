"""Temporary Waizao daily-bar adapter using its token-authenticated API."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from stratgauge.data_sources.base import (
    AdapterError,
    AdapterResult,
    Clock,
    DailyBarRequest,
    Fetcher,
    MarketDataAdapter,
    normalize_daily_rows,
    now_utc,
)

_ENDPOINT = "http://api.waizaowang.com/doc/getDayKLine"


class WaizaoAdapter(MarketDataAdapter):
    """Fetch unadjusted A-share daily bars from Waizao."""

    provider = "waizao"
    adapter_version = "0.2"

    def __init__(
        self,
        fetcher: Fetcher | None = None,
        clock: Clock = now_utc,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._fetcher = fetcher or self._fetch_from_waizao
        self._clock = clock
        self._timeout_seconds = timeout_seconds

    def fetch_daily_bars(self, request: DailyBarRequest) -> AdapterResult:
        fetched_at = self._clock()
        try:
            raw_records = tuple(self._fetcher(request))
            contract_records = tuple(
                _to_contract_row(row, request.security.local_code) for row in raw_records
            )
            bars = normalize_daily_rows(
                contract_records,
                request,
                self.provider,
                self.adapter_version,
                fetched_at,
            )
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"Waizao fetch failed for {request.security.symbol}") from exc
        return AdapterResult(
            provider=self.provider,
            adapter_version=self.adapter_version,
            request=request,
            fetched_at=fetched_at,
            raw_records=raw_records,
            bars=bars,
        )

    def _fetch_from_waizao(self, request: DailyBarRequest) -> Iterable[Mapping[str, Any]]:
        token = os.environ.get("WAIZAO_TOKEN", "").strip()
        if not token:
            raise AdapterError("WAIZAO_TOKEN is not set")

        # ponytail: provider exposes only HTTP; replace this endpoint when HTTPS is available.
        payload = {
            "type": "1",
            "code": request.security.local_code,
            "ktype": "101",
            "fq": "0",
            "startDate": request.start_date.isoformat(),
            "endDate": request.end_date.isoformat(),
            "fields": "all",
            "export": "1",
            "token": token,
            "filter": "",
        }
        http_request = UrlRequest(
            _ENDPOINT,
            data=urlencode(payload).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self._timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise AdapterError(f"Waizao request failed with HTTP {exc.code}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AdapterError("Waizao request failed") from exc

        if not isinstance(response_payload, dict):
            raise AdapterError("Waizao returned an invalid response")
        if response_payload.get("code") != 200:
            message = str(response_payload.get("message") or "unknown provider error")
            raise AdapterError(f"Waizao API rejected the request: {message}")
        data = response_payload.get("data")
        if data is None:
            return ()
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise AdapterError("Waizao returned invalid daily-bar rows")
        return data


def _to_contract_row(row: Mapping[str, Any], expected_code: str) -> dict[str, Any]:
    code = str(row.get("code", "")).strip().zfill(6)
    if code != expected_code:
        raise AdapterError(f"Waizao returned unexpected code: {code!r}")
    required = ("tdate", "open", "high", "low", "close", "cjl", "cje")
    missing = [field for field in required if row.get(field) in (None, "")]
    if missing:
        raise AdapterError(f"Waizao row is missing required fields: {', '.join(missing)}")
    return {
        "date": row["tdate"],
        "code": code,
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        # Waizao cjl is lots; the domain volume unit is shares.
        "volume": Decimal(str(row["cjl"])) * Decimal("100"),
        "amount": row["cje"],
    }


if __name__ == "__main__":
    raise SystemExit("Use WaizaoAdapter through the ingestion pipeline")
