"""Historical, time-aware analysis for one selected candidate."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from math import ceil

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.backtest.selection_outcomes import latest_selection_outcomes
from stratgauge.backtest.selection_stats import (
    DEFAULT_MINIMUM_EVIDENCE_SAMPLES,
    SelectionOutcomeStats,
    summarize_selection_outcomes,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.domain.selections import SelectionOutcome, SelectionOutcomeStatus


class SelectionAnalysisError(RuntimeError):
    """Historical candidate analysis cannot be produced safely."""


class EntryPriceStats(BaseModel):
    """Descriptive prices from completed historical entries; not a recommendation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_count: int = Field(ge=0)
    minimum: Decimal | None = None
    lower_quartile: Decimal | None = None
    median: Decimal | None = None
    upper_quartile: Decimal | None = None
    maximum: Decimal | None = None
    average: Decimal | None = None


class SelectionCandidateAnalysis(BaseModel):
    """Time-point cohort statistics and historical entry prices for one candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str
    candidate_id: str
    symbol: str
    reference_at: datetime
    formula_id: str | None
    formula_name: str
    formula_version: str | None
    tracking_policy_id: str | None
    available_tracking_policy_ids: tuple[str, ...] = ()
    cohort_batch_count: int = Field(ge=0)
    cohort_candidate_count: int = Field(ge=0)
    conditional_stats: SelectionOutcomeStats
    entry_price_stats: EntryPriceStats

    @field_validator("reference_at")
    @classmethod
    def require_aware_reference_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reference_at must include a timezone")
        return value


def analyze_selection_candidate(
    store: SelectionImportStore,
    *,
    batch_id: str,
    candidate_id: str,
    tracking_policy_id: str | None = None,
    minimum_evidence_samples: int = DEFAULT_MINIMUM_EVIDENCE_SAMPLES,
) -> SelectionCandidateAnalysis:
    """Analyze prior same-formula outcomes visible when the batch was exported."""

    if minimum_evidence_samples <= 0:
        raise ValueError("minimum_evidence_samples must be positive")
    try:
        imported = store.load_import(batch_id)
    except SelectionStoreError as exc:
        raise SelectionAnalysisError(str(exc)) from exc
    candidate = next(
        (item for item in imported.candidates if item.candidate_id == candidate_id),
        None,
    )
    if candidate is None:
        raise SelectionAnalysisError(
            f"candidate does not belong to selection batch: {candidate_id}"
        )

    reference_at = imported.batch.exported_at
    prior_outcomes = _load_prior_outcomes(
        store,
        current_batch_id=batch_id,
        selection_date=imported.batch.selection_date,
        reference_at=reference_at,
        formula_id=imported.batch.formula_id,
        formula_name=imported.batch.formula_name,
        formula_version=imported.batch.formula_version,
    )
    available_policy_ids = tuple(sorted({outcome.tracking_policy_id for outcome in prior_outcomes}))
    selected_policy_id = _resolve_policy_id(
        tracking_policy_id,
        available_policy_ids,
    )
    scoped_outcomes = tuple(
        outcome
        for outcome in prior_outcomes
        if selected_policy_id is None or outcome.tracking_policy_id == selected_policy_id
    )
    latest = latest_selection_outcomes(scoped_outcomes)
    return SelectionCandidateAnalysis(
        batch_id=batch_id,
        candidate_id=candidate_id,
        symbol=candidate.symbol,
        reference_at=reference_at,
        formula_id=imported.batch.formula_id,
        formula_name=imported.batch.formula_name,
        formula_version=imported.batch.formula_version,
        tracking_policy_id=selected_policy_id,
        available_tracking_policy_ids=available_policy_ids,
        cohort_batch_count=len({outcome.batch_id for outcome in latest}),
        cohort_candidate_count=len({outcome.candidate_id for outcome in latest}),
        conditional_stats=summarize_selection_outcomes(
            latest,
            minimum_evidence_samples=minimum_evidence_samples,
        ),
        entry_price_stats=_summarize_entry_prices(latest),
    )


def _load_prior_outcomes(
    store: SelectionImportStore,
    *,
    current_batch_id: str,
    selection_date: date,
    reference_at: datetime,
    formula_id: str | None,
    formula_name: str,
    formula_version: str | None,
) -> tuple[SelectionOutcome, ...]:
    outcomes: list[SelectionOutcome] = []
    try:
        manifests = store.list_manifests(include_archived=True)
        for manifest in manifests:
            imported = store.load_import(manifest.batch_id)
            batch = imported.batch
            if batch.batch_id == current_batch_id or batch.selection_date >= selection_date:
                continue
            if batch.exported_at > reference_at or not _same_formula(
                batch.formula_id,
                batch.formula_name,
                batch.formula_version,
                formula_id,
                formula_name,
                formula_version,
            ):
                continue
            for historical_candidate in imported.candidates:
                visible = tuple(
                    outcome
                    for outcome in store.list_outcomes(historical_candidate.candidate_id)
                    if outcome.evaluated_at <= reference_at
                )
                if visible:
                    outcomes.extend(visible)
    except SelectionStoreError as exc:
        raise SelectionAnalysisError(str(exc)) from exc
    return tuple(outcomes)


def _same_formula(
    left_id: str | None,
    left_name: str,
    left_version: str | None,
    right_id: str | None,
    right_name: str,
    right_version: str | None,
) -> bool:
    if left_id is not None and right_id is not None:
        return left_id == right_id
    return left_name == right_name and left_version == right_version


def _resolve_policy_id(
    requested: str | None,
    available: tuple[str, ...],
) -> str | None:
    if requested is not None:
        normalized = requested.strip()
        if not normalized:
            raise ValueError("tracking_policy_id must not be empty")
        return normalized
    if len(available) > 1:
        raise SelectionAnalysisError(
            "multiple tracking policies are available; tracking_policy_id is required"
        )
    return available[0] if available else None


def _summarize_entry_prices(outcomes: Iterable[SelectionOutcome]) -> EntryPriceStats:
    completed_statuses = {
        SelectionOutcomeStatus.WIN,
        SelectionOutcomeStatus.LOSS,
        SelectionOutcomeStatus.FLAT,
    }
    values = tuple(
        sorted(
            outcome.entry_price
            for outcome in outcomes
            if outcome.status in completed_statuses and outcome.entry_price is not None
        )
    )
    if not values:
        return EntryPriceStats(sample_count=0)
    return EntryPriceStats(
        sample_count=len(values),
        minimum=values[0],
        lower_quartile=_nearest_rank(values, 1, 4),
        median=_median(values),
        upper_quartile=_nearest_rank(values, 3, 4),
        maximum=values[-1],
        average=sum(values, Decimal("0")) / Decimal(len(values)),
    )


def _nearest_rank(values: tuple[Decimal, ...], numerator: int, denominator: int) -> Decimal:
    index = ceil(len(values) * numerator / denominator) - 1
    return values[max(0, min(index, len(values) - 1))]


def _median(values: tuple[Decimal, ...]) -> Decimal:
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / Decimal("2")
