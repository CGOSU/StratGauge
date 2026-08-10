"""Immutable domain models used by the first implementation slice."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Exchange(StrEnum):
    """Supported mainland China exchanges."""

    SH = "SH"
    SZ = "SZ"
    BJ = "BJ"


class SignalTiming(StrEnum):
    """When a strategy is allowed to create a signal."""

    AFTER_CLOSE = "after_close"


class StrategyLifecycle(StrEnum):
    """Lifecycle states for an immutable strategy version."""

    DRAFT = "draft"
    VALIDATED = "validated"
    SHADOW = "shadow"
    ACTIVE = "active"
    RETIRED = "retired"


class ModelLifecycle(StrEnum):
    """Lifecycle states for an immutable model registration."""

    CHALLENGER = "challenger"
    SHADOW = "shadow"
    ACTIVE = "active"
    RETIRED = "retired"


class ActionState(StrEnum):
    """User-facing recommendation state."""

    OBSERVE = "observe"
    WAIT_ENTRY = "wait_entry"
    ENTRY_VALID = "entry_valid"
    HOLD = "hold"
    REDUCE = "reduce"
    EXIT = "exit"


class DomainModel(BaseModel):
    """Base model that prevents accidental mutation and unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _require_id(value: str) -> str:
    if not value:
        raise ValueError("identifier must not be empty")
    return value


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError("value must be a decimal number") from exc
    if not result.is_finite():
        raise ValueError("value must be finite")
    return result


def canonical_symbol(local_code: str, exchange: Exchange | str) -> str:
    """Build a display symbol only from explicitly verified exchange metadata."""

    code = local_code.strip().upper()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("local_code must contain exactly six digits")
    try:
        resolved_exchange = Exchange(exchange)
    except ValueError as exc:
        raise ValueError("exchange must be SH, SZ or BJ") from exc
    return f"{code}.{resolved_exchange.value}"


class Security(DomainModel):
    security_id: str
    local_code: str
    symbol: str
    exchange: Exchange
    name: str | None = None
    listed_at: date | None = None
    listed: bool = True
    delisted_at: date | None = None

    @field_validator("security_id")
    @classmethod
    def validate_security_id(cls, value: str) -> str:
        return _require_id(value)

    @field_validator("local_code")
    @classmethod
    def validate_local_code(cls, value: str) -> str:
        code = value.strip().upper()
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("local_code must contain exactly six digits")
        return code

    @model_validator(mode="after")
    def validate_identity(self) -> Security:
        if self.symbol != canonical_symbol(self.local_code, self.exchange):
            raise ValueError("symbol must match local_code and exchange")
        if self.listed_at is not None and self.listed_at < date.min:
            raise ValueError("listed_at is invalid")
        if self.delisted_at is not None and self.delisted_at < date.min:
            raise ValueError("delisted_at is invalid")
        if (
            self.listed_at is not None
            and self.delisted_at is not None
            and self.delisted_at < self.listed_at
        ):
            raise ValueError("delisted_at must not precede listed_at")
        return self


class DailyBar(DomainModel):
    security_id: str
    symbol: str
    session_date: date
    provider: str
    provider_symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    previous_close: Decimal | None = None
    volume: Decimal
    amount: Decimal
    event_time: datetime
    available_at: datetime
    ingested_at: datetime
    suspended: bool = False
    tradable: bool = True
    quality_state: str = "pending"

    @field_validator(
        "security_id",
        "provider",
        "provider_symbol",
        "quality_state",
    )
    @classmethod
    def validate_text_fields(cls, value: str) -> str:
        return _require_id(value)

    @field_validator(
        "open",
        "high",
        "low",
        "close",
        "previous_close",
        "volume",
        "amount",
        mode="before",
    )
    @classmethod
    def validate_decimal_fields(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        return _decimal(value)

    @field_validator("event_time", "available_at", "ingested_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_market_values(self) -> DailyBar:
        if min(self.open, self.high, self.low, self.close) < 0:
            raise ValueError("prices must not be negative")
        if not (self.low <= self.open <= self.high):
            raise ValueError("open must be inside the daily range")
        if not (self.low <= self.close <= self.high):
            raise ValueError("close must be inside the daily range")
        if self.previous_close is not None and self.previous_close < 0:
            raise ValueError("previous_close must not be negative")
        if self.volume < 0 or self.amount < 0:
            raise ValueError("volume and amount must not be negative")
        if self.available_at < self.event_time:
            raise ValueError("available_at cannot precede event_time")
        if self.ingested_at < self.available_at:
            raise ValueError("ingested_at cannot precede available_at")
        if self.suspended and self.tradable:
            raise ValueError("suspended security cannot be tradable")
        return self


class AdjustmentFactor(DomainModel):
    """One provider adjustment factor kept separate from raw daily prices."""

    security_id: str
    symbol: str
    session_date: date
    provider: str
    provider_symbol: str
    factor: Decimal
    event_time: datetime
    available_at: datetime
    ingested_at: datetime
    quality_state: str = "pending"

    @field_validator("security_id", "provider", "provider_symbol", "quality_state")
    @classmethod
    def validate_text_fields(cls, value: str) -> str:
        return _require_id(value)

    @field_validator("factor", mode="before")
    @classmethod
    def validate_factor(cls, value: Any) -> Decimal:
        return _decimal(value)

    @field_validator("event_time", "available_at", "ingested_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_values(self) -> AdjustmentFactor:
        if self.factor <= 0:
            raise ValueError("adjustment factor must be positive")
        if self.available_at < self.event_time:
            raise ValueError("available_at cannot precede event_time")
        if self.ingested_at < self.available_at:
            raise ValueError("ingested_at cannot precede available_at")
        return self


class StrategyVersion(DomainModel):
    strategy_version_id: str
    strategy_id: str
    version: int = Field(gt=0)
    name: str
    feature_set_version: str
    lifecycle: StrategyLifecycle = StrategyLifecycle.DRAFT
    signal_timing: SignalTiming = SignalTiming.AFTER_CLOSE
    definition: dict[str, Any] = Field(default_factory=dict)

    @field_validator("strategy_version_id", "strategy_id", "feature_set_version")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        return _require_id(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value:
            raise ValueError("name must not be empty")
        return value


class Signal(DomainModel):
    signal_id: str
    security_id: str
    symbol: str
    as_of: date
    earliest_execution: date
    latest_execution: date
    strategy_version_id: str
    feature_set_version: str
    source_snapshot_id: str
    entry_lower: Decimal | None = None
    entry_upper: Decimal | None = None
    stop_price: Decimal | None = None
    target_price: Decimal | None = None
    max_holding_sessions: int | None = Field(default=None, gt=0)
    rejection_reason: str | None = None

    @field_validator(
        "signal_id",
        "security_id",
        "strategy_version_id",
        "feature_set_version",
        "source_snapshot_id",
    )
    @classmethod
    def validate_ids(cls, value: str) -> str:
        return _require_id(value)

    @field_validator(
        "entry_lower",
        "entry_upper",
        "stop_price",
        "target_price",
        mode="before",
    )
    @classmethod
    def validate_entry_prices(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        return _decimal(value)

    @model_validator(mode="after")
    def validate_execution_window(self) -> Signal:
        if self.earliest_execution <= self.as_of:
            raise ValueError("after-close signals must execute after as_of")
        if self.latest_execution < self.earliest_execution:
            raise ValueError("latest_execution must not precede earliest_execution")
        if (self.entry_lower is None) != (self.entry_upper is None):
            raise ValueError("entry_lower and entry_upper must be provided together")
        if (
            self.entry_lower is not None
            and self.entry_upper is not None
            and self.entry_lower > self.entry_upper
        ):
            raise ValueError("entry_lower must not exceed entry_upper")
        for price in (self.entry_lower, self.entry_upper, self.stop_price, self.target_price):
            if price is not None and price < 0:
                raise ValueError("signal prices must not be negative")
        return self


class Recommendation(DomainModel):
    recommendation_id: str
    security_id: str
    symbol: str
    as_of: date
    intended_session: date
    action_state: ActionState
    strategy_version_id: str
    feature_set_version: str
    source_snapshot_id: str
    model_version_id: str | None = None
    probability: Decimal | None = None
    expected_net_return: Decimal | None = None
    risk_metric: Decimal | None = None
    sample_size: int = Field(default=0, ge=0)
    reasons: tuple[str, ...] = ()

    @field_validator(
        "recommendation_id",
        "security_id",
        "strategy_version_id",
        "feature_set_version",
        "source_snapshot_id",
    )
    @classmethod
    def validate_ids(cls, value: str) -> str:
        return _require_id(value)

    @field_validator("model_version_id")
    @classmethod
    def validate_optional_model_id(cls, value: str | None) -> str | None:
        return None if value is None else _require_id(value)

    @field_validator("probability", "expected_net_return", "risk_metric", mode="before")
    @classmethod
    def validate_metrics(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        return _decimal(value)

    @model_validator(mode="after")
    def validate_recommendation(self) -> Recommendation:
        if self.intended_session <= self.as_of:
            raise ValueError("intended_session must follow as_of")
        if self.probability is not None and not 0 <= self.probability <= 1:
            raise ValueError("probability must be between zero and one")
        return self
