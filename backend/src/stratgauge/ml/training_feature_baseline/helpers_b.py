"""Train a feature-lineage-aware constant distribution baseline."""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal

from stratgauge.ml.training_datasets import (
    PredictionSplit,
    TrainingLabel,
)
from stratgauge.ml.training_datasets import (
    TrainingSplitName as SplitName,
)
from stratgauge.ml.training_feature_baseline.models import (
    FeatureBaselineMetrics,
    FeatureBaselinePrediction,
)
from stratgauge.ml.training_feature_join import (
    SelectionTrainingFeatureRow,
)


def _metrics(
    split: SplitName,
    rows: Sequence[SelectionTrainingFeatureRow],
    probabilities: dict[str, Decimal],
    labels: tuple[TrainingLabel, ...],
) -> FeatureBaselineMetrics:
    counts = {label.value: 0 for label in labels}
    for row in rows:
        counts[row.target.value] += 1
    if not rows:
        return FeatureBaselineMetrics(
            split=split,
            row_count=0,
            coverage=Decimal("0"),
            target_counts=counts,
            predicted_probabilities=probabilities,
        )
    values = {label.value: float(probabilities[label.value]) for label in labels}
    log_loss = -sum(math.log(max(values[row.target.value], 1e-15)) for row in rows) / len(rows)
    brier = sum(
        sum((values[label.value] - int(row.target == label)) ** 2 for label in labels) / 2
        for row in rows
    ) / len(rows)
    return FeatureBaselineMetrics(
        split=split,
        row_count=len(rows),
        coverage=Decimal("1"),
        target_counts=counts,
        predicted_probabilities=probabilities,
        log_loss=log_loss,
        normalized_brier_score=brier,
    )


def _predictions(
    split: PredictionSplit,
    rows: Sequence[SelectionTrainingFeatureRow],
    probabilities: dict[str, Decimal],
    labels: tuple[TrainingLabel, ...],
) -> tuple[FeatureBaselinePrediction, ...]:
    predicted = max(labels, key=lambda label: (probabilities[label.value], -labels.index(label)))
    return tuple(
        FeatureBaselinePrediction(
            split=split,
            sample_id=row.sample_id,
            target=row.target,
            predicted_label=predicted,
            predicted_probabilities=probabilities,
            feature_snapshot_id=row.feature_snapshot_id,
            source_label_row_sha256=row.source_label_row_sha256,
        )
        for row in rows
    )
