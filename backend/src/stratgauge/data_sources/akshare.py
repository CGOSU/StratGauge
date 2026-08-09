"""AKShare adapter. Third-party imports stay inside this module."""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Mapping
from typing import Any, cast

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


class AKShareAdapter(MarketDataAdapter):
    provider = "akshare"
    adapter_version = "0.1"

    def __init__(self, fetcher: Fetcher | None = None, clock: Clock = now_utc) -> None:
        self._fetcher = fetcher or self._fetch_from_akshare
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
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"AKShare fetch failed for {request.security.symbol}") from exc
        return AdapterResult(
            provider=self.provider,
            adapter_version=self.adapter_version,
            request=request,
            fetched_at=fetched_at,
            raw_records=raw_records,
            bars=bars,
        )

    @staticmethod
    def _fetch_from_akshare(request: DailyBarRequest) -> Iterable[Mapping[str, Any]]:
        try:
            ak = importlib.import_module("akshare")
        except ImportError as exc:
            raise AdapterError(
                "AKShare is not installed; install the optional data-source dependency"
            ) from exc

        frame = ak.stock_zh_a_hist(
            symbol=request.security.local_code,
            period="daily",
            start_date=request.start_date.strftime("%Y%m%d"),
            end_date=request.end_date.strftime("%Y%m%d"),
            adjust="",
        )
        return cast(Iterable[Mapping[str, Any]], frame.to_dict(orient="records"))
