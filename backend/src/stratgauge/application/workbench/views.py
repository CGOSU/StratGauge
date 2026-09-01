"""Workbench view models and read error."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stratgauge.domain.portfolio import (
    ManualFill,
    ManualOrder,
    ManualPositionTrackingCase,
    PortfolioPosition,
)
from stratgauge.domain.selections import (
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionBatch,
    SelectionCandidate,
    SelectionCandidateExclusion,
    SelectionCandidateReviewState,
    SelectionOutcome,
    SelectionPerformance,
)


class WorkbenchReadError(RuntimeError):
    """A workbench projection cannot be safely read."""

    def __init__(self, code: str, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class MarketEvidenceStatus(StrEnum):
    """Availability state for values shown in the workbench."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INCOMPLETE = "incomplete"


class MarketEvidence(BaseModel):
    """Latest approved close evidence, never a value from an import file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: MarketEvidenceStatus
    snapshot_id: str | None = None
    provider: str | None = None
    session_date: date | None = None
    available_at: datetime | None = None
    close: Decimal | None = None
    previous_close: Decimal | None = None
    change_percent: Decimal | None = None
    volume: Decimal | None = None
    amount: Decimal | None = None
    reason: str | None = None


class WorkbenchObservationPerformance(BaseModel):
    """Performance from the current continuous observation period to the latest close."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: MarketEvidenceStatus
    started_at: datetime | None = None
    start_snapshot_id: str | None = None
    start_session_date: date | None = None
    start_available_at: datetime | None = None
    start_close: Decimal | None = None
    change_percent: Decimal | None = None
    reason: str | None = None


class WorkbenchSignalView(BaseModel):
    """One immutable selection candidate projected as a strategy signal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    batch: SelectionBatch
    candidate: SelectionCandidate
    labels: tuple[ManualSelectionLabel, ...] = ()
    annotations: tuple[SelectionAnnotation, ...] = ()
    latest_annotation: SelectionAnnotation | None = None
    exclusions: tuple[SelectionCandidateExclusion, ...] = ()
    latest_exclusion: SelectionCandidateExclusion | None = None
    review_state: SelectionCandidateReviewState = SelectionCandidateReviewState.UNREVIEWED
    excluded: bool = False
    outcomes: tuple[SelectionOutcome, ...] = ()
    performances: tuple[SelectionPerformance, ...] = ()
    market: MarketEvidence
    observation_performance: WorkbenchObservationPerformance | None = None
    confirmed_buy: bool = False
    confirmed_buy_fill_ids: tuple[str, ...] = ()


class WorkbenchSummary(BaseModel):
    """Action counts for the current workbench scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    signal_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    unreviewed_count: int = Field(ge=0)
    reviewed_count: int = Field(ge=0)
    watching_count: int = Field(ge=0)
    preparing_count: int = Field(ge=0)
    confirmed_buy_count: int = Field(ge=0)


class WorkbenchTimelineEvent(BaseModel):
    """A source-preserving event in one stock's research lifecycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    event_type: Literal[
        "signal",
        "annotation",
        "exclusion",
        "order",
        "fill",
        "review",
        "outcome",
        "performance",
    ]
    occurred_at: datetime
    source_id: str
    title: str
    detail: str


class WorkbenchStockDetail(BaseModel):
    """Stock-centric read model shared by every stock entry point."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    name: str | None = None
    as_of: datetime
    source: Literal["signal", "manual_discovery", "mixed"]
    market: MarketEvidence
    position: PortfolioPosition | None = None
    tracking_cases: tuple[ManualPositionTrackingCase, ...] = ()
    signals: tuple[WorkbenchSignalView, ...] = ()
    timeline: tuple[WorkbenchTimelineEvent, ...] = ()


class WorkbenchPositionView(BaseModel):
    """A derived open position with explicit market-evidence boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position: PortfolioPosition
    as_of: datetime
    source: Literal["signal", "manual_discovery", "mixed"]
    selection_candidate_ids: tuple[str, ...] = ()
    holding_days: int = Field(ge=0)
    market: MarketEvidence
    unrealized_profit: Decimal | None = None
    unrealized_return: Decimal | None = None
    unrealized_reason: str | None = None


class WorkbenchTradeView(BaseModel):
    """Confirmed order/fill evidence; signals and outcomes are not trades."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order: ManualOrder
    fills: tuple[ManualFill, ...]
    source: Literal["signal", "manual_discovery", "mixed"]


class WorkbenchHorizonMetric(BaseModel):
    """Aggregated objective evidence for one forward horizon."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    horizon_sessions: int = Field(gt=0)
    sample_count: int = Field(ge=0)
    average_return: Decimal | None = None
    average_maximum_rise: Decimal | None = None
    average_maximum_drawdown: Decimal | None = None
    target_3_percent_rate: Decimal | None = None
    target_5_percent_rate: Decimal | None = None


class WorkbenchCohortMetrics(BaseModel):
    """A cohort with explicit size, resolved count and exclusions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cohort: Literal["all_signals", "human_bullish", "confirmed_trades"]
    sample_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    horizons: tuple[WorkbenchHorizonMetric, ...] = ()


class WorkbenchStrategyView(BaseModel):
    """Objective strategy-version statistics for the workbench."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    formula_id: str | None
    formula_name: str
    formula_version: str | None
    signal_count: int = Field(ge=0)
    cohorts: tuple[WorkbenchCohortMetrics, ...]


class WorkbenchAnalyticsView(BaseModel):
    """Cross-strategy objective cohort comparison."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    cohorts: tuple[WorkbenchCohortMetrics, ...]
