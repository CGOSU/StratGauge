"""Maturity task ledger models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.domain.selections.outcomes import DEFAULT_SELECTION_PERFORMANCE_POLICY_ID


class SelectionMaturityTaskStatus(StrEnum):
    """Persisted state of one explicit selection maturity task."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class SelectionMaturityTaskEvent(DomainModel):
    """Append-only audit record for a maturity-task state transition."""

    event_id: str
    task_id: str
    event_type: Literal["queued", "running", "attempt_result", "requeue"]
    occurred_at: datetime
    attempt_count: int = Field(ge=0)
    status: SelectionMaturityTaskStatus
    outcomes_written: int = Field(ge=0)
    rejected_candidates: tuple[tuple[str, str], ...] = ()
    market_snapshot_ids: tuple[str, ...] = ()
    failure_reason: str | None = None
    retryable: bool = False
    reason: str | None = None

    @field_validator("event_id", "task_id")
    @classmethod
    def validate_task_event_ids(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("maturity task event identifier must not be empty")
        return normalized

    @field_validator("occurred_at")
    @classmethod
    def validate_task_event_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("maturity task event time must include a timezone")
        return value

    @field_validator("failure_reason", "reason")
    @classmethod
    def normalize_task_event_text(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @field_validator("rejected_candidates")
    @classmethod
    def normalize_rejected_candidates(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        return tuple((candidate_id.strip(), reason.strip()) for candidate_id, reason in value)

    @field_validator("market_snapshot_ids")
    @classmethod
    def normalize_task_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not snapshot_id.strip() for snapshot_id in value):
            raise ValueError("maturity task snapshot identifiers must not be empty")
        return tuple(snapshot_id.strip() for snapshot_id in value)


class SelectionMaturityTask(DomainModel):
    """Immutable task configuration plus its current materialized ledger state."""

    task_id: str
    batch_id: str
    provider: Literal["tushare", "akshare", "baostock", "waizao"]
    market_data_mode: Literal["provider", "approved_snapshots"] = "provider"
    universe_snapshot_id: str | None = None
    input_market_snapshot_ids: tuple[str, ...] = ()
    evaluated_at: datetime
    target_evaluation_date: date
    tracking_policy_id: str
    performance_policy_id: str = DEFAULT_SELECTION_PERFORMANCE_POLICY_ID
    performance_target_return: Decimal | None = None
    horizon_sessions: int = Field(gt=0)
    position_notional: Decimal = Field(gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    timeout_seconds: float = Field(default=900, gt=0, le=86400)
    status: SelectionMaturityTaskStatus = SelectionMaturityTaskStatus.QUEUED
    attempt_count: int = Field(default=0, ge=0)
    outcomes_written: int = Field(default=0, ge=0)
    rejected_candidates: tuple[tuple[str, str], ...] = ()
    market_snapshot_ids: tuple[str, ...] = ()
    failure_reason: str | None = None
    retryable: bool = False
    created_at: datetime
    updated_at: datetime
    events: tuple[SelectionMaturityTaskEvent, ...] = ()

    @field_validator(
        "task_id",
        "batch_id",
        "provider",
        "market_data_mode",
        "tracking_policy_id",
        "performance_policy_id",
    )
    @classmethod
    def validate_task_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("maturity task value must not be empty")
        return normalized

    @field_validator("evaluated_at", "created_at", "updated_at")
    @classmethod
    def validate_task_times(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("maturity task timestamps must include a timezone")
        return value

    @field_validator("performance_target_return", mode="before")
    @classmethod
    def validate_performance_target_return(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise ValueError("performance_target_return must be a decimal number") from exc
        if not result.is_finite() or result < 0:
            raise ValueError("performance_target_return must be finite and non-negative")
        return result

    @field_validator("position_notional", mode="before")
    @classmethod
    def validate_task_notional(cls, value: Any) -> Decimal:
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise ValueError("position_notional must be a decimal number") from exc
        if not result.is_finite() or result <= 0:
            raise ValueError("position_notional must be positive and finite")
        return result

    @field_validator("timeout_seconds")
    @classmethod
    def validate_task_timeout(cls, value: float) -> float:
        if value <= 0 or value > 86400:
            raise ValueError("timeout_seconds must be positive and no greater than 86400")
        return value

    @field_validator("rejected_candidates")
    @classmethod
    def normalize_task_rejections(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        return tuple((candidate_id.strip(), reason.strip()) for candidate_id, reason in value)

    @field_validator("input_market_snapshot_ids", "market_snapshot_ids")
    @classmethod
    def normalize_task_snapshots(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not snapshot_id.strip() for snapshot_id in value):
            raise ValueError("maturity task snapshot identifiers must not be empty")
        return tuple(snapshot_id.strip() for snapshot_id in value)

    @model_validator(mode="after")
    def validate_task_dates(self) -> SelectionMaturityTask:
        if self.target_evaluation_date != self.evaluated_at.date():
            raise ValueError("target_evaluation_date must match evaluated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.market_data_mode == "approved_snapshots":
            if not self.universe_snapshot_id:
                raise ValueError("snapshot-backed maturity task requires a universe snapshot")
            if not self.input_market_snapshot_ids:
                raise ValueError("snapshot-backed maturity task requires market snapshots")
        elif self.universe_snapshot_id is not None or self.input_market_snapshot_ids:
            raise ValueError("provider maturity task cannot carry market snapshot inputs")
        return self
