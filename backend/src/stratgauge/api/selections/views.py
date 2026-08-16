"""View models for the selection API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stratgauge.application.selection_management import SelectionApiError as SelectionApiError
from stratgauge.backtest.selection_stats import SelectionOutcomeStats
from stratgauge.domain.selections import (
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionBatch,
    SelectionCandidate,
    SelectionCandidateExclusion,
    SelectionCandidateReviewState,
    SelectionFormula,
    SelectionLifecycleEvent,
    SelectionOutcome,
    SelectionPerformance,
)


class SelectionFormulaView(BaseModel):
    model_config = ConfigDict(frozen=True)

    formula: SelectionFormula


class SelectionManagedFormulaView(BaseModel):
    model_config = ConfigDict(frozen=True)

    formula: SelectionFormula
    active: bool
    lifecycle: tuple[SelectionLifecycleEvent, ...]


class SelectionManagedBatchView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch: SelectionBatch
    active: bool
    label_count: int = Field(ge=0)
    annotation_count: int = Field(default=0, ge=0)
    exclusion_count: int = Field(default=0, ge=0)
    outcome_count: int = Field(ge=0)
    lifecycle: tuple[SelectionLifecycleEvent, ...]


class SelectionMatureView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_id: str
    provider: str
    outcomes_written: int = Field(ge=0)
    performances_written: int = Field(default=0, ge=0)
    rejected_candidates: tuple[tuple[str, str], ...]
    market_snapshot_ids: tuple[str, ...]


class SelectionCandidateView(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: SelectionCandidate
    labels: tuple[ManualSelectionLabel, ...] = ()
    annotations: tuple[SelectionAnnotation, ...] = ()
    exclusions: tuple[SelectionCandidateExclusion, ...] = ()
    latest_annotation: SelectionAnnotation | None = None
    latest_exclusion: SelectionCandidateExclusion | None = None
    review_state: SelectionCandidateReviewState = SelectionCandidateReviewState.UNREVIEWED
    excluded: bool = False
    outcomes: tuple[SelectionOutcome, ...] = ()
    performances: tuple[SelectionPerformance, ...] = ()


class SelectionObservationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch: SelectionBatch
    candidate: SelectionCandidateView


class SelectionBatchView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch: SelectionBatch
    candidates: tuple[SelectionCandidateView, ...]


class SelectionManagedBatchDetailView(BaseModel):
    model_config = ConfigDict(frozen=True)

    view: SelectionBatchView
    active: bool
    lifecycle: tuple[SelectionLifecycleEvent, ...]


class SelectionStatsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_id: str
    candidate_count: int = Field(ge=0)
    pending: int = Field(ge=0)
    latest_outcome_count: int = Field(ge=0)
    summary: SelectionOutcomeStats


class SelectionManagementStatsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    pending: int = Field(ge=0)
    sample_count: int = Field(ge=0)
    tracking_policy_ids: tuple[str, ...] = ()
    summary: SelectionOutcomeStats


class SelectionBatchPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[SelectionBatch, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    total: int = Field(ge=0)
    has_next: bool


class SelectionObservationPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[SelectionObservationView, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    total: int = Field(ge=0)
    has_next: bool


class SelectionManagedBatchPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[SelectionManagedBatchView, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    total: int = Field(ge=0)
    has_next: bool


__all__ = [
    "SelectionFormulaView",
    "SelectionManagedFormulaView",
    "SelectionManagedBatchView",
    "SelectionMatureView",
    "SelectionCandidateView",
    "SelectionObservationView",
    "SelectionBatchView",
    "SelectionManagedBatchDetailView",
    "SelectionStatsView",
    "SelectionManagementStatsView",
    "SelectionBatchPage",
    "SelectionObservationPage",
    "SelectionManagedBatchPage",
]
