"""Provider-independent market-data contracts and row normalization."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from stratgauge.domain.models import DailyBar, Security

SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


class AdapterError(RuntimeError):
    """A provider or normalization failure with provider context."""


class RetryableAdapterError(AdapterError):
    """A provider failure that is safe to try again later."""


def is_retryable_provider_message(message: str) -> bool:
    """Recognize provider-declared transient failures without retrying bad data."""

    normalized = message.strip().lower()
    transient_terms = (
        "rate limit",
        "rate-limit",
        "too many request",
        "too many requests",
        "throttl",
        "限流",
        "请求过于频繁",
        "频率限制",
        "temporarily unavailable",
        "service unavailable",
        "服务不可用",
        "network",
        "connection",
        "remote disconnected",
        "connection reset",
        "connection refused",
        "timeout",
        "timed out",
        "超时",
    )
    if any(term in normalized for term in transient_terms):
        return True
    return any(
        f"http {status}" in normalized or f"status {status}" in normalized
        for status in (408, 425, 429, 500, 502, 503, 504)
    )


def is_retryable_adapter_exception(error: BaseException) -> bool:
    """Recognize only explicit transport, timeout, and rate-limit failures."""

    if isinstance(error, RetryableAdapterError):
        return True
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return True
    return is_retryable_provider_message(str(error))


class DailyBarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    security: Security
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_range(self) -> DailyBarRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


@dataclass(frozen=True)
class AdapterResult:
    provider: str
    adapter_version: str
    request: DailyBarRequest
    fetched_at: datetime
    raw_records: tuple[Mapping[str, Any], ...]
    bars: tuple[DailyBar, ...]


class MarketDataAdapter(Protocol):
    """Common interface consumed by the ingestion pipeline."""

    provider: str
    adapter_version: str

    def fetch_daily_bars(self, request: DailyBarRequest) -> AdapterResult: ...


Fetcher = Callable[[DailyBarRequest], Iterable[Mapping[str, Any]]]
Clock = Callable[[], datetime]


_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "session_date": ("date", "日期", "交易日期"),
    "provider_symbol": ("code", "代码", "证券代码"),
    "open": ("open", "开盘"),
    "high": ("high", "最高"),
    "low": ("low", "最低"),
    "close": ("close", "收盘"),
    "previous_close": ("preclose", "previous_close", "前收盘", "昨收"),
    "volume": ("volume", "成交量"),
    "amount": ("amount", "成交额"),
    "trade_status": ("tradestatus", "trade_status", "交易状态"),
    "suspended": ("suspended", "停牌"),
    "tradable": ("tradable", "可交易"),
}


def _row_value(row: Mapping[str, Any], field: str) -> Any:
    for alias in _COLUMN_ALIASES[field]:
        if alias in row:
            return row[alias]
    normalized = {str(key).strip().lower(): value for key, value in row.items()}
    for alias in _COLUMN_ALIASES[field]:
        value = normalized.get(alias.lower())
        if value is not None:
            return value
    return None


def _required(row: Mapping[str, Any], field: str, provider: str) -> Any:
    value = _row_value(row, field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise AdapterError(f"{provider} row is missing required field: {field}")
    return value


def _parse_date(value: Any, provider: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise AdapterError(f"{provider} returned invalid session date: {value!r}") from exc


def _parse_decimal(value: Any, field: str, provider: str) -> Decimal:
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise AdapterError(f"{provider} returned invalid {field}: {value!r}") from exc
    if not result.is_finite():
        raise AdapterError(f"{provider} returned non-finite {field}: {value!r}")
    return result


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "否", "正常", "交易"}


def _event_time(session_date: date) -> datetime:
    return datetime.combine(session_date, time(15, 0), tzinfo=SHANGHAI)


def normalize_daily_rows(
    rows: Iterable[Mapping[str, Any]],
    request: DailyBarRequest,
    provider: str,
    adapter_version: str,
    fetched_at: datetime,
) -> tuple[DailyBar, ...]:
    """Normalize provider-shaped rows without allowing provider names into domain code."""

    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise AdapterError("fetched_at must include a timezone")

    normalized: list[DailyBar] = []
    for raw in rows:
        session_date = _parse_date(_required(raw, "session_date", provider), provider)
        if not request.start_date <= session_date <= request.end_date:
            raise AdapterError(
                f"{provider} returned {session_date.isoformat()} outside requested range"
            )
        provider_symbol = str(_row_value(raw, "provider_symbol") or request.security.local_code)
        suspended = _parse_bool(_row_value(raw, "suspended"), default=False)
        trade_status = _row_value(raw, "trade_status")
        if trade_status is not None:
            suspended = str(trade_status).strip() in {"0", "停牌", "suspended"}
        tradable = _parse_bool(_row_value(raw, "tradable"), default=not suspended)
        normalized.append(
            DailyBar(
                security_id=request.security.security_id,
                symbol=request.security.symbol,
                session_date=session_date,
                provider=provider,
                provider_symbol=provider_symbol,
                open=_parse_decimal(_required(raw, "open", provider), "open", provider),
                high=_parse_decimal(_required(raw, "high", provider), "high", provider),
                low=_parse_decimal(_required(raw, "low", provider), "low", provider),
                close=_parse_decimal(_required(raw, "close", provider), "close", provider),
                previous_close=(
                    None
                    if _row_value(raw, "previous_close") in (None, "")
                    else _parse_decimal(
                        _row_value(raw, "previous_close"), "previous_close", provider
                    )
                ),
                volume=_parse_decimal(_required(raw, "volume", provider), "volume", provider),
                amount=_parse_decimal(_required(raw, "amount", provider), "amount", provider),
                event_time=_event_time(session_date),
                available_at=max(fetched_at, _event_time(session_date)),
                ingested_at=max(fetched_at, _event_time(session_date)),
                suspended=suspended,
                tradable=tradable and not suspended,
                quality_state="pending",
            )
        )
    return tuple(normalized)


def now_utc() -> datetime:
    return datetime.now(UTC)
