"""BaoStock adapter. Third-party imports stay inside this module."""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Mapping
from typing import Any

from stratgauge.data_sources.base import (
    AdapterError,
    AdapterResult,
    Clock,
    DailyBarRequest,
    Fetcher,
    MarketDataAdapter,
    RetryableAdapterError,
    is_retryable_adapter_exception,
    is_retryable_provider_message,
    normalize_daily_rows,
    now_utc,
)


class BaoStockAdapter(MarketDataAdapter):
    provider = "baostock"
    adapter_version = "0.1"

    def __init__(self, fetcher: Fetcher | None = None, clock: Clock = now_utc) -> None:
        self._fetcher = fetcher or self._fetch_from_baostock
        self._clock = clock

    def fetch_daily_bars(self, request: DailyBarRequest) -> AdapterResult:
        fetched_at = self._clock()
        try:
            raw_records = tuple(self._fetcher(request))
            bars = normalize_daily_rows(
                raw_records,
                request,
                self.provider,
                self.adapter_version,
                fetched_at,
            )
        except AdapterError as exc:
            if is_retryable_adapter_exception(exc):
                raise RetryableAdapterError(
                    f"BaoStock transient fetch failure for {request.security.symbol}: {exc}"
                ) from exc
            raise
        except Exception as exc:
            error_type = (
                RetryableAdapterError if is_retryable_adapter_exception(exc) else AdapterError
            )
            raise error_type(f"BaoStock fetch failed for {request.security.symbol}") from exc
        return AdapterResult(
            provider=self.provider,
            adapter_version=self.adapter_version,
            request=request,
            fetched_at=fetched_at,
            raw_records=raw_records,
            bars=bars,
        )

    @staticmethod
    def _fetch_from_baostock(request: DailyBarRequest) -> Iterable[Mapping[str, Any]]:
        try:
            bs = importlib.import_module("baostock")
        except ImportError as exc:
            raise AdapterError(
                "BaoStock is not installed; install the optional data-source dependency"
            ) from exc

        login = bs.login()
        if getattr(login, "error_code", "0") != "0":
            message = str(getattr(login, "error_msg", login))
            error_type = (
                RetryableAdapterError if is_retryable_provider_message(message) else AdapterError
            )
            raise error_type(f"BaoStock login failed: {message}")
        try:
            code = f"{request.security.exchange.value.lower()}.{request.security.local_code}"
            result = bs.query_history_k_data_plus(
                code,
                "date,code,open,high,low,close,preclose,volume,amount,tradestatus",
                start_date=request.start_date.isoformat(),
                end_date=request.end_date.isoformat(),
                frequency="d",
                adjustflag="3",
            )
            if getattr(result, "error_code", "0") != "0":
                message = str(getattr(result, "error_msg", result))
                error_type = (
                    RetryableAdapterError
                    if is_retryable_provider_message(message)
                    else AdapterError
                )
                raise error_type(f"BaoStock query failed: {message}")
            rows: list[dict[str, Any]] = []
            while result.next():
                rows.append(dict(zip(result.fields, result.get_row_data(), strict=True)))
            return rows
        finally:
            bs.logout()
