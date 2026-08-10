"""Tushare Pro adapters for unadjusted daily bars and reference data."""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any, cast

from stratgauge.data_sources.base import (
    AdapterError,
    AdapterResult,
    Clock,
    DailyBarRequest,
    Fetcher,
    MarketDataAdapter,
    RetryableAdapterError,
    is_retryable_adapter_exception,
    normalize_daily_rows,
    now_utc,
)


class TushareAdapter(MarketDataAdapter):
    """Fetch Tushare Pro ``daily`` data using an environment token."""

    provider = "tushare"
    adapter_version = "0.1"

    def __init__(
        self,
        fetcher: Fetcher | None = None,
        clock: Clock = now_utc,
    ) -> None:
        self._fetcher = fetcher or self._fetch_from_tushare
        self._clock = clock

    def fetch_daily_bars(self, request: DailyBarRequest) -> AdapterResult:
        fetched_at = self._clock()
        try:
            raw_records = tuple(self._fetcher(request))
            contract_records = tuple(_to_contract_row(row) for row in raw_records)
            bars = normalize_daily_rows(
                contract_records,
                request,
                self.provider,
                self.adapter_version,
                fetched_at,
            )
        except AdapterError as exc:
            if is_retryable_adapter_exception(exc):
                raise RetryableAdapterError(
                    f"Tushare transient fetch failure for {request.security.symbol}: {exc}"
                ) from exc
            raise
        except Exception as exc:
            error_type = (
                RetryableAdapterError if is_retryable_adapter_exception(exc) else AdapterError
            )
            raise error_type(f"Tushare fetch failed for {request.security.symbol}") from exc
        return AdapterResult(
            provider=self.provider,
            adapter_version=self.adapter_version,
            request=request,
            fetched_at=fetched_at,
            raw_records=raw_records,
            bars=bars,
        )

    @staticmethod
    def _fetch_from_tushare(request: DailyBarRequest) -> Iterable[Mapping[str, Any]]:
        try:
            tushare = importlib.import_module("tushare")
        except ImportError as exc:
            raise AdapterError(
                "Tushare is not installed; install the optional data-source dependency"
            ) from exc
        token = os.environ.get("TUSHARE_TOKEN", "").strip()
        if not token:
            raise AdapterError("TUSHARE_TOKEN is not set")
        try:
            client = tushare.pro_api(token)
            frame = client.daily(
                ts_code=request.security.symbol,
                start_date=request.start_date.strftime("%Y%m%d"),
                end_date=request.end_date.strftime("%Y%m%d"),
            )
            return cast(Iterable[Mapping[str, Any]], frame.to_dict(orient="records"))
        except AdapterError:
            raise
        except Exception as exc:
            if is_retryable_adapter_exception(exc):
                raise RetryableAdapterError("Tushare request failed") from exc
            raise AdapterError("Tushare request failed") from exc


def _to_contract_row(row: Mapping[str, Any]) -> dict[str, Any]:
    required = ("trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount")
    missing = [field for field in required if row.get(field) in (None, "")]
    if missing:
        raise AdapterError(f"Tushare row is missing required fields: {', '.join(missing)}")
    return {
        "date": row["trade_date"],
        "code": row["ts_code"],
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "previous_close": row.get("pre_close"),
        # Tushare daily.vol is lots and daily.amount is thousand CNY.
        "volume": Decimal(str(row["vol"])) * Decimal("100"),
        "amount": Decimal(str(row["amount"])) * Decimal("1000"),
    }
