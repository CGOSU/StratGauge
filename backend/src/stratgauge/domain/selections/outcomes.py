"""Objective outcome and forward performance models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.domain.selections.enums import (
    SelectionOutcomeStatus,
    SelectionPerformanceStatus,
)

DEFAULT_OUTCOME_POLICY_ID = "outcome-fixed-10-net-positive-v1"
DEFAULT_PERFORMANCE_HORIZONS = (1, 3, 5, 10)
DEFAULT_SELECTION_PERFORMANCE_POLICY_ID = "selection-performance-t1-t3-t5-t10-v1"


class SelectionOutcome(DomainModel):
    """A fixed-horizon market outcome, separate from any human label."""

    candidate_id: str
    batch_id: str
    selection_date: date
    status: SelectionOutcomeStatus
    evaluated_at: datetime
    horizon_sessions: int = Field(gt=0)
    entry_session: date | None = None
    exit_session: date | None = None
    entry_price: Decimal | None = None
    exit_price: Decimal | None = None
    gross_return: Decimal | None = None
    net_return: Decimal | None = None
    reason: str
    tracking_policy_id: str = "legacy-unknown"
    outcome_policy_id: str = "legacy-unknown"
    cost_schedule_id: str = "unspecified"
    position_notional: Decimal | None = Field(default=None, gt=0)
    source_snapshot_ids: tuple[str, ...] = ()
    maturity_task_id: str | None = None

    @field_validator(
        "candidate_id",
        "batch_id",
        "reason",
        "tracking_policy_id",
        "outcome_policy_id",
        "cost_schedule_id",
    )
    @classmethod
    def validate_outcome_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value.strip()

    @field_validator("evaluated_at")
    @classmethod
    def validate_evaluated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluated_at must include a timezone")
        return value

    @field_validator("maturity_task_id")
    @classmethod
    def validate_maturity_task_id(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @field_validator(
        "entry_price",
        "exit_price",
        "gross_return",
        "net_return",
        "position_notional",
        mode="before",
    )
    @classmethod
    def validate_outcome_decimals(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise ValueError("outcome value must be a decimal number") from exc
        if not result.is_finite():
            raise ValueError("outcome value must be finite")
        return result

    @field_validator("source_snapshot_ids")
    @classmethod
    def validate_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not snapshot_id.strip() for snapshot_id in value):
            raise ValueError("source snapshot identifiers must not be empty")
        return tuple(snapshot_id.strip() for snapshot_id in value)

    @model_validator(mode="after")
    def validate_trade_fields(self) -> SelectionOutcome:
        trade_statuses = {
            SelectionOutcomeStatus.WIN,
            SelectionOutcomeStatus.LOSS,
            SelectionOutcomeStatus.FLAT,
        }
        has_trade = self.status in trade_statuses
        trade_fields = (
            self.entry_session,
            self.exit_session,
            self.entry_price,
            self.exit_price,
            self.gross_return,
            self.net_return,
        )
        if has_trade and any(value is None for value in trade_fields):
            raise ValueError("completed outcomes require entry, exit and return fields")
        if self.entry_session is not None and self.exit_session is not None:
            if self.exit_session <= self.entry_session:
                raise ValueError("exit_session must follow entry_session")
            if self.evaluated_at.date() < self.exit_session:
                raise ValueError("evaluated_at cannot precede the exit session")
        return self


class SelectionPerformancePoint(DomainModel):
    """One forward performance observation at a requested trading horizon."""

    horizon_sessions: int = Field(gt=0)
    session_date: date | None = None
    close_price: Decimal | None = None
    return_from_anchor: Decimal | None = None
    maximum_rise: Decimal | None = None
    maximum_drawdown: Decimal | None = None
    target_achieved: bool | None = None

    @field_validator(
        "close_price",
        "return_from_anchor",
        "maximum_rise",
        "maximum_drawdown",
        mode="before",
    )
    @classmethod
    def validate_performance_decimals(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise ValueError("performance value must be a decimal number") from exc
        if not result.is_finite():
            raise ValueError("performance value must be finite")
        return result

    @model_validator(mode="after")
    def validate_point_completeness(self) -> SelectionPerformancePoint:
        values = (
            self.session_date,
            self.close_price,
            self.return_from_anchor,
            self.maximum_rise,
            self.maximum_drawdown,
        )
        if self.target_achieved is not None and any(value is None for value in values):
            raise ValueError("target_achieved requires a complete performance point")
        if self.session_date is None and any(value is not None for value in values):
            raise ValueError("performance values require a session date")
        return self


class SelectionPerformance(DomainModel):
    """Immutable forward performance evidence separate from fixed-horizon outcomes."""

    candidate_id: str
    batch_id: str
    selection_date: date
    status: SelectionPerformanceStatus
    evaluated_at: datetime
    performance_policy_id: str
    anchor_session: date | None = None
    anchor_price: Decimal | None = None
    target_return: Decimal | None = None
    points: tuple[SelectionPerformancePoint, ...] = ()
    reason: str
    source_snapshot_ids: tuple[str, ...] = ()
    maturity_task_id: str | None = None

    @field_validator(
        "candidate_id",
        "batch_id",
        "performance_policy_id",
        "reason",
    )
    @classmethod
    def validate_performance_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("performance value must not be empty")
        return normalized

    @field_validator("evaluated_at")
    @classmethod
    def validate_performance_evaluated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("performance evaluated_at must include a timezone")
        return value

    @field_validator("anchor_price", "target_return", mode="before")
    @classmethod
    def validate_performance_decimal(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise ValueError("performance value must be a decimal number") from exc
        if not result.is_finite():
            raise ValueError("performance value must be finite")
        return result

    @field_validator("maturity_task_id")
    @classmethod
    def normalize_performance_task_id(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @field_validator("source_snapshot_ids")
    @classmethod
    def validate_performance_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not snapshot_id.strip() for snapshot_id in value):
            raise ValueError("performance source snapshot identifiers must not be empty")
        return tuple(snapshot_id.strip() for snapshot_id in value)

    @model_validator(mode="after")
    def validate_performance_structure(self) -> SelectionPerformance:
        if self.status is SelectionPerformanceStatus.RESOLVED:
            if self.anchor_session is None or self.anchor_price is None:
                raise ValueError("resolved performance requires an anchor")
            if any(point.session_date is None for point in self.points):
                raise ValueError("resolved performance requires complete points")
        if self.anchor_price is not None and self.anchor_price <= 0:
            raise ValueError("anchor_price must be positive")
        if self.target_return is not None and self.target_return < 0:
            raise ValueError("target_return must not be negative")
        return self
