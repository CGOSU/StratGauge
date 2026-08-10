"""Backtest policies and reproducible result contracts."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AmbiguousBarrierPolicy(StrEnum):
    CONSERVATIVE_STOP = "conservative_stop"
    UNRESOLVED = "unresolved"


class EntryFillEvidence(StrEnum):
    DAILY_RANGE_TOUCH = "daily_range_touch"
    OPEN_ONLY = "open_only"


class EntryExecutionPolicy(BaseModel):
    """Frozen limit-entry assumptions used by the daily-bar backtester."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str = "entry-limit-chase-v1"
    validity_sessions: int = Field(default=3, gt=0)
    chase_limit: Decimal = Field(default=Decimal("0.01"), ge=0)
    fill_evidence: EntryFillEvidence = EntryFillEvidence.DAILY_RANGE_TOUCH

    @field_validator("policy_id")
    @classmethod
    def validate_policy_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("entry policy ID must not be empty")
        return normalized

    @field_validator("chase_limit", mode="before")
    @classmethod
    def normalize_chase_limit(cls, value: Decimal | str | int | float) -> Decimal:
        return Decimal(str(value))


class BacktestPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_policy: EntryExecutionPolicy = Field(default_factory=EntryExecutionPolicy)
    slippage: Decimal = Field(default=Decimal("0"), ge=0)
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0)
    minimum_commission: Decimal = Field(default=Decimal("5"), ge=0)
    stamp_duty: Decimal = Field(default=Decimal("0.0005"), ge=0)
    board_lot_size: int = Field(default=100, gt=0)
    ambiguous_barrier: AmbiguousBarrierPolicy = AmbiguousBarrierPolicy.CONSERVATIVE_STOP
    cost_schedule_id: str = "a-share-main-board-cost-v1"

    @field_validator(
        "slippage",
        "commission_rate",
        "minimum_commission",
        "stamp_duty",
        mode="before",
    )
    @classmethod
    def normalize_decimal(cls, value: Decimal | str | int | float) -> Decimal:
        return Decimal(str(value))


class Trade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: str
    security_id: str
    symbol: str
    entry_session: str
    entry_price: Decimal
    exit_session: str
    exit_price: Decimal
    exit_reason: str
    gross_return: Decimal
    net_return: Decimal
    total_cost: Decimal
    ambiguous: bool = False
    source_snapshot_id: str
    strategy_version_id: str
    cost_schedule_id: str
    entry_policy_id: str = "entry-limit-chase-v1"


class UnfilledIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: str
    symbol: str
    reason: str
    source_snapshot_id: str


class BacktestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    source_snapshot_ids: tuple[str, ...]
    feature_set_version_ids: tuple[str, ...]
    strategy_version_ids: tuple[str, ...]
    cost_schedule_id: str
    entry_policy_id: str = "entry-limit-chase-v1"
    trades: tuple[Trade, ...] = ()
    unfilled: tuple[UnfilledIntent, ...] = ()
    unresolved_signal_ids: tuple[str, ...] = ()
