"""Purged walk-forward validation for the dependency-free baseline."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.backtest.selection_dataset import SelectionDatasetRow
from stratgauge.domain.models import DomainModel
from stratgauge.ml._hashing import sha256_json
from stratgauge.ml.selection_training import SelectionFeatureRow
from stratgauge.ml.walk_forward_outcomes import _index_outcomes, _outcome_metrics

WALK_FORWARD_REPORT_VERSION = "selection-walk-forward-v2"


class WalkForwardFoldSpec(DomainModel):
    """Explicit chronological boundaries for one validation fold."""

    fold_id: str
    train_start: date | None = None
    train_end: date
    validation_start: date
    validation_end: date
    market_state: str | None = None

    @field_validator("fold_id")
    @classmethod
    def validate_fold_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("fold_id must not be empty")
        return value.strip()

    @model_validator(mode="after")
    def validate_boundaries(self) -> WalkForwardFoldSpec:
        if self.train_start is not None and self.train_start > self.train_end:
            raise ValueError("train_start must not follow train_end")
        if self.train_end >= self.validation_start:
            raise ValueError("train_end must precede validation_start")
        if self.validation_start > self.validation_end:
            raise ValueError("validation_start must not follow validation_end")
        if self.market_state is not None and not self.market_state.strip():
            raise ValueError("market_state must not be empty")
        return self


class WalkForwardFoldReport(DomainModel):
    """Metrics and data accounting for one purged fold."""

    fold_id: str
    train_start: date | None
    train_end: date
    validation_start: date
    validation_end: date
    market_state: str | None
    train_row_count: int = Field(ge=0)
    validation_row_count: int = Field(ge=0)
    purged_train_row_count: int = Field(ge=0)
    missing_feature_rate: Decimal = Field(ge=0, le=1)
    execution_rate: Decimal | None = Field(default=None, ge=0, le=1)
    predicted_probability: Decimal = Field(ge=0, le=1)
    log_loss: float = Field(ge=0)
    brier_score: float = Field(ge=0)
    ranking_auc: Decimal | None = Field(default=None, ge=0, le=1)
    average_net_return: Decimal | None = None
    max_drawdown: Decimal | None = Field(default=None, ge=0)
    turnover: Decimal | None = Field(default=None, ge=0)
    outcome_coverage: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    completed_outcome_count: int = Field(default=0, ge=0)
    no_entry_count: int = Field(default=0, ge=0)
    unresolved_count: int = Field(default=0, ge=0)
    market_state_average_net_return: Decimal | None = None


class WalkForwardReport(DomainModel):
    """Content-addressed walk-forward report with no final-test usage."""

    report_id: str
    report_version: Literal["selection-walk-forward-v2"] = "selection-walk-forward-v2"
    model_type: Literal["constant_probability"] = "constant_probability"
    source_file_sha256: str
    feature_set_version_id: str
    tracking_policy_id: str
    final_test_start: date | None = None
    final_test_row_count: int = Field(ge=0)
    final_test_used: bool = False
    outcome_source_file_sha256: str | None = None
    market_state_average_net_returns: dict[str, Decimal | None] = Field(default_factory=dict)
    folds: tuple[WalkForwardFoldReport, ...]


def validate_walk_forward(
    rows: Iterable[SelectionFeatureRow],
    *,
    folds: Sequence[WalkForwardFoldSpec],
    source_file_sha256: str,
    final_test_start: date | None = None,
    outcomes: Iterable[SelectionDatasetRow] = (),
    outcome_source_file_sha256: str | None = None,
) -> WalkForwardReport:
    """Evaluate chronological folds and purge labels not known at validation start."""

    all_rows = tuple(rows)
    if not all_rows:
        raise ValueError("walk-forward dataset contains no rows")
    _validate_unique_rows(all_rows)
    if not source_file_sha256.strip():
        raise ValueError("walk-forward source file hash must not be empty")
    if not folds:
        raise ValueError("walk-forward requires at least one fold")
    feature_sets = {row.feature_set_version_id for row in all_rows}
    if len(feature_sets) != 1:
        raise ValueError("walk-forward dataset contains multiple feature-set versions")
    policies = {row.tracking_policy_id for row in all_rows}
    if len(policies) != 1:
        raise ValueError("walk-forward dataset contains multiple tracking policies")
    ordered_folds = tuple(sorted(folds, key=lambda fold: (fold.validation_start, fold.fold_id)))
    for previous, current in zip(ordered_folds, ordered_folds[1:], strict=False):
        if current.validation_start <= previous.validation_end:
            raise ValueError("walk-forward validation windows must not overlap")
    if final_test_start is not None:
        for fold in ordered_folds:
            if fold.train_end >= final_test_start or fold.validation_end >= final_test_start:
                raise ValueError("walk-forward fold overlaps the final test period")
    outcome_rows = tuple(outcomes)
    if outcome_rows and not outcome_source_file_sha256:
        raise ValueError("outcome source file hash is required when outcomes are provided")
    if outcome_source_file_sha256 is not None and not outcome_source_file_sha256.strip():
        raise ValueError("outcome source file hash must not be empty")
    outcome_map = _index_outcomes(
        outcome_rows,
        expected_tracking_policy_id=next(iter(policies)),
    )
    evaluation_rows = (
        tuple(row for row in all_rows if row.selection_date < final_test_start)
        if final_test_start is not None
        else all_rows
    )
    final_test_row_count = len(all_rows) - len(evaluation_rows)
    if not evaluation_rows:
        raise ValueError("walk-forward dataset contains no pre-test rows")

    reports = tuple(
        _evaluate_fold(evaluation_rows, fold, outcome_map=outcome_map) for fold in ordered_folds
    )
    market_state_average_net_returns = _market_state_averages(reports)
    identity = {
        "folds": [fold.model_dump(mode="json") for fold in reports],
        "feature_set_version_id": next(iter(feature_sets)),
        "final_test_row_count": final_test_row_count,
        "final_test_start": final_test_start.isoformat() if final_test_start else None,
        "market_state_average_net_returns": {
            state: None if value is None else str(value)
            for state, value in market_state_average_net_returns.items()
        },
        "outcome_source_file_sha256": outcome_source_file_sha256,
        "report_version": WALK_FORWARD_REPORT_VERSION,
        "source_file_sha256": source_file_sha256,
        "tracking_policy_id": next(iter(policies)),
    }
    digest = sha256_json(identity)
    return WalkForwardReport(
        report_id=f"walk-forward-{digest[:20]}",
        source_file_sha256=source_file_sha256,
        feature_set_version_id=next(iter(feature_sets)),
        tracking_policy_id=next(iter(policies)),
        final_test_start=final_test_start,
        final_test_row_count=final_test_row_count,
        outcome_source_file_sha256=outcome_source_file_sha256,
        market_state_average_net_returns=market_state_average_net_returns,
        folds=reports,
    )


def _evaluate_fold(
    rows: tuple[SelectionFeatureRow, ...],
    fold: WalkForwardFoldSpec,
    *,
    outcome_map: dict[tuple[str, date], SelectionDatasetRow],
) -> WalkForwardFoldReport:
    train_candidates = tuple(
        row
        for row in rows
        if (fold.train_start is None or row.selection_date >= fold.train_start)
        and row.selection_date <= fold.train_end
    )
    train_rows = tuple(
        row for row in train_candidates if row.label_evaluated_at.date() < fold.validation_start
    )
    purged_count = len(train_candidates) - len(train_rows)
    validation_rows = tuple(
        row for row in rows if fold.validation_start <= row.selection_date <= fold.validation_end
    )
    if not train_rows:
        raise ValueError(f"walk-forward fold has no usable training rows: {fold.fold_id}")
    if not validation_rows:
        raise ValueError(f"walk-forward fold has no validation rows: {fold.fold_id}")
    positive_count = sum(row.label == "win" for row in train_rows)
    if positive_count in {0, len(train_rows)}:
        raise ValueError(f"walk-forward fold training labels have one class: {fold.fold_id}")
    probability = Decimal(positive_count) / Decimal(len(train_rows))
    probability_float = min(max(float(probability), 1e-15), 1 - 1e-15)
    validation_labels = tuple(row.label == "win" for row in validation_rows)
    log_loss = -sum(
        math.log(probability_float if label else 1 - probability_float)
        for label in validation_labels
    ) / len(validation_labels)
    brier = sum(
        (probability_float - (1 if label else 0)) ** 2 for label in validation_labels
    ) / len(validation_labels)
    outcome_metrics = _outcome_metrics(validation_rows, outcome_map)
    return WalkForwardFoldReport(
        fold_id=fold.fold_id,
        train_start=fold.train_start,
        train_end=fold.train_end,
        validation_start=fold.validation_start,
        validation_end=fold.validation_end,
        market_state=fold.market_state,
        train_row_count=len(train_rows),
        validation_row_count=len(validation_rows),
        purged_train_row_count=purged_count,
        missing_feature_rate=Decimal("0"),
        execution_rate=outcome_metrics.execution_rate,
        predicted_probability=probability,
        log_loss=log_loss,
        brier_score=brier,
        ranking_auc=Decimal("0.5") if len(set(validation_labels)) == 2 else None,
        average_net_return=outcome_metrics.average_net_return,
        max_drawdown=outcome_metrics.max_drawdown,
        turnover=outcome_metrics.turnover,
        outcome_coverage=outcome_metrics.coverage,
        completed_outcome_count=outcome_metrics.completed_count,
        no_entry_count=outcome_metrics.no_entry_count,
        unresolved_count=outcome_metrics.unresolved_count,
        market_state_average_net_return=outcome_metrics.average_net_return,
    )


def _validate_unique_rows(rows: tuple[SelectionFeatureRow, ...]) -> None:
    keys = tuple((row.candidate_id, row.selection_date) for row in rows)
    if len(keys) != len(set(keys)):
        raise ValueError("walk-forward dataset contains duplicate candidate/date rows")


def _market_state_averages(
    reports: Sequence[WalkForwardFoldReport],
) -> dict[str, Decimal | None]:
    grouped: dict[str, list[tuple[Decimal, int]]] = {}
    for report in reports:
        if report.market_state is None:
            continue
        average = report.market_state_average_net_return
        grouped.setdefault(report.market_state, []).append(
            (
                average if average is not None else Decimal("0"),
                report.completed_outcome_count if average is not None else 0,
            )
        )
    return {
        state: (
            sum((value * count for value, count in values), Decimal("0"))
            / Decimal(sum(count for _, count in values))
            if sum(count for _, count in values)
            else None
        )
        for state, values in sorted(grouped.items())
    }
