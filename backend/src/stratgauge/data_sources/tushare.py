"""Tushare Pro adapters for unadjusted daily bars and reference data."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from stratgauge.data_sources.base import (
    AdapterError,
    AdapterResult,
    Clock,
    DailyBarRequest,
    Fetcher,
    MarketAdapterResult,
    MarketDailyRequest,
    MarketDataAdapter,
    MarketFetcher,
    RetryableAdapterError,
    is_retryable_adapter_exception,
    normalize_daily_rows,
    now_utc,
)
from stratgauge.data_sources.tushare_api import query_tushare
from stratgauge.domain.models import DailyBar, Security


class TushareAdapter(MarketDataAdapter):
    """Fetch Tushare Pro ``daily`` data using an environment token."""

    provider = "tushare"
    adapter_version = "0.2"

    def __init__(
        self,
        fetcher: Fetcher | None = None,
        market_fetcher: MarketFetcher | None = None,
        clock: Clock = now_utc,
    ) -> None:
        self._fetcher = fetcher or self._fetch_from_tushare
        self._market_fetcher = market_fetcher or self._fetch_market_from_tushare
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

    def fetch_market_daily(
        self,
        request: MarketDailyRequest,
        securities: Sequence[Security],
    ) -> MarketAdapterResult:
        fetched_at = self._clock()
        security_by_symbol = {security.symbol: security for security in securities}
        if not security_by_symbol:
            raise AdapterError("market request requires securities")
        try:
            provider_records = tuple(self._market_fetcher(request))
            scoped_records: list[Mapping[str, Any]] = []
            bars: list[DailyBar] = []
            for record in provider_records:
                contract_row = _to_contract_row(record)
                symbol = str(contract_row["code"]).strip().upper()
                security = security_by_symbol.get(symbol)
                if security is None:
                    continue
                scoped_records.append(record)
                bars.extend(
                    normalize_daily_rows(
                        [contract_row],
                        DailyBarRequest(
                            security=security,
                            start_date=request.session_date,
                            end_date=request.session_date,
                        ),
                        self.provider,
                        self.adapter_version,
                        fetched_at,
                    )
                )
        except AdapterError as exc:
            if is_retryable_adapter_exception(exc):
                raise RetryableAdapterError(
                    f"Tushare transient market fetch failure for {request.session_date}"
                ) from exc
            raise
        except Exception as exc:
            error_type = (
                RetryableAdapterError if is_retryable_adapter_exception(exc) else AdapterError
            )
            raise error_type("Tushare market fetch failed") from exc
        return MarketAdapterResult(
            provider=self.provider,
            adapter_version=self.adapter_version,
            request=request,
            fetched_at=fetched_at,
            raw_records=tuple(
                sorted(scoped_records, key=lambda record: str(record.get("ts_code", "")))
            ),
            bars=tuple(sorted(bars, key=lambda bar: (bar.symbol, bar.session_date))),
        )

    @staticmethod
    def _fetch_market_from_tushare(request: MarketDailyRequest) -> Iterable[Mapping[str, Any]]:
        return query_tushare(
            "daily",
            {"trade_date": request.session_date.strftime("%Y%m%d")},
        )

    @staticmethod
    def _fetch_from_tushare(request: DailyBarRequest) -> Iterable[Mapping[str, Any]]:
        return query_tushare(
            "daily",
            {
                "ts_code": request.security.symbol,
                "start_date": request.start_date.strftime("%Y%m%d"),
                "end_date": request.end_date.strftime("%Y%m%d"),
            },
        )


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
