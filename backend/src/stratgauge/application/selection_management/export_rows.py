"""Dataset row builders for selection exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stratgauge.backtest.selection_dataset import (
    SelectionAnnotationDatasetRow,
    SelectionDatasetRow,
)
from stratgauge.domain.selections import (
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionBatch,
    SelectionCandidate,
    SelectionOutcome,
)

if TYPE_CHECKING:
    pass


def selection_dataset_row(
    batch: SelectionBatch,
    candidate: SelectionCandidate,
    label: ManualSelectionLabel | None,
    outcome: SelectionOutcome | None,
) -> SelectionDatasetRow:
    return SelectionDatasetRow(
        candidate_id=candidate.candidate_id,
        batch_id=batch.batch_id,
        symbol=candidate.symbol,
        selection_date=batch.selection_date.isoformat(),
        source_filename=batch.source_filename,
        formula_id=batch.formula_id,
        formula_name=batch.formula_name,
        formula_version=batch.formula_version,
        manual_decision=label.decision if label else None,
        manual_labeled_at=label.labeled_at.isoformat() if label else None,
        tracking_policy_id=outcome.tracking_policy_id if outcome else None,
        outcome_policy_id=outcome.outcome_policy_id if outcome else None,
        cost_schedule_id=outcome.cost_schedule_id if outcome else None,
        horizon_sessions=outcome.horizon_sessions if outcome else None,
        position_notional=(
            str(outcome.position_notional)
            if outcome and outcome.position_notional is not None
            else None
        ),
        outcome_status=outcome.status if outcome else None,
        outcome_evaluated_at=outcome.evaluated_at.isoformat() if outcome else None,
        net_return=(
            str(outcome.net_return) if outcome and outcome.net_return is not None else None
        ),
        source_snapshot_ids=outcome.source_snapshot_ids if outcome else (),
    )


def annotation_dataset_row(
    batch: SelectionBatch,
    candidate: SelectionCandidate,
    annotation: SelectionAnnotation,
    outcome: SelectionOutcome | None,
) -> SelectionAnnotationDatasetRow:
    return SelectionAnnotationDatasetRow(
        annotation_id=annotation.annotation_id,
        candidate_id=candidate.candidate_id,
        batch_id=batch.batch_id,
        symbol=candidate.symbol,
        selection_date=batch.selection_date.isoformat(),
        source_filename=batch.source_filename,
        formula_id=batch.formula_id,
        formula_name=batch.formula_name,
        formula_version=batch.formula_version,
        annotation_reviewed_at=annotation.reviewed_at.isoformat(),
        annotation_recorded_at=annotation.recorded_at.isoformat(),
        overall_score=annotation.overall_score,
        outlook=annotation.outlook,
        decision=annotation.decision,
        position_tags=annotation.position_tags,
        pattern_tags=annotation.pattern_tags,
        price_volume_tags=annotation.price_volume_tags,
        sector_tags=annotation.sector_tags,
        taxonomy_version=annotation.taxonomy_version,
        annotation_note=annotation.note,
        tracking_policy_id=outcome.tracking_policy_id if outcome else None,
        outcome_policy_id=outcome.outcome_policy_id if outcome else None,
        cost_schedule_id=outcome.cost_schedule_id if outcome else None,
        horizon_sessions=outcome.horizon_sessions if outcome else None,
        position_notional=(
            str(outcome.position_notional)
            if outcome and outcome.position_notional is not None
            else None
        ),
        outcome_status=outcome.status if outcome else None,
        outcome_evaluated_at=outcome.evaluated_at.isoformat() if outcome else None,
        net_return=(
            str(outcome.net_return) if outcome and outcome.net_return is not None else None
        ),
        source_snapshot_ids=outcome.source_snapshot_ids if outcome else (),
    )


def latest_outcome_after_annotation(
    outcomes: tuple[SelectionOutcome, ...],
    annotation: SelectionAnnotation,
) -> SelectionOutcome | None:
    future = tuple(outcome for outcome in outcomes if outcome.evaluated_at > annotation.recorded_at)
    return future[-1] if future else None
