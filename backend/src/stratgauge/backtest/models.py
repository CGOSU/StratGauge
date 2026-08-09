"""Backtest policies and reproducible result contracts."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AmbiguousBarrierPolicy(StrEnum):
    CONSERVATIVE_STOP = "conservative_stop"
    UNRESOLVED = "unresolved"


class BacktestPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chase_limit: Decimal = Field(default=Decimal("0.01"), ge=0)
    slippage: Decimal = Field(default=Decimal("0"), ge=0)
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0)
    minimum_commission: Decimal = Field(default=Decimal("5"), ge=0)
    stamp_duty: Decimal = Field(default=Decimal("0.0005"), ge=0)
    ambiguous_barrier: AmbiguousBarrierPolicy = AmbiguousBarrierPolicy.CONSERVATIVE_STOP
    cost_schedule_id: str = "phase0-default"

    @field_validator(
        "chase_limit",
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
    trades: tuple[Trade, ...] = ()
    unfilled: tuple[UnfilledIntent, ...] = ()
    unresolved_signal_ids: tuple[str, ...] = ()
