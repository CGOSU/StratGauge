"""Purpose-aware immutable comparison evidence for feature models."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Literal

from stratgauge.ml.training_feature_comparison.core import (
    FeatureComparisonError,
)
from stratgauge.ml.training_feature_comparison.models import (
    ComparisonDirection,
    FeatureComparisonMetricRule,
    FeatureComparisonModelEvidence,
    FeatureMetricComparison,
)
from stratgauge.ml.training_feature_walk_forward import (
    FeatureWalkForwardReport,
)


def _compare_metric(
    rule: FeatureComparisonMetricRule,
    challenger_metrics: Mapping[str, Decimal],
    reference_metrics: Mapping[str, Decimal] | None,
) -> FeatureMetricComparison:
    challenger_value = challenger_metrics.get(rule.metric_name)
    reference_value = None
    reasons: list[str] = []
    if challenger_value is None:
        return FeatureMetricComparison(
            metric_name=rule.metric_name,
            direction=rule.direction,
            challenger_value=None,
            reference_value=(
                None if reference_metrics is None else reference_metrics.get(rule.metric_name)
            ),
            minimum_value=rule.minimum_value,
            maximum_value=rule.maximum_value,
            max_relative_regression=rule.max_relative_regression,
            relative_regression=None,
            passed=False,
            reason="challenger aggregate metric is missing",
        )
    if rule.minimum_value is not None and challenger_value < rule.minimum_value:
        reasons.append("challenger is below minimum")
    if rule.maximum_value is not None and challenger_value > rule.maximum_value:
        reasons.append("challenger is above maximum")
    relative_regression = None
    if reference_metrics is not None:
        reference_value = reference_metrics.get(rule.metric_name)
        if reference_value is None:
            return FeatureMetricComparison(
                metric_name=rule.metric_name,
                direction=rule.direction,
                challenger_value=challenger_value,
                reference_value=None,
                minimum_value=rule.minimum_value,
                maximum_value=rule.maximum_value,
                max_relative_regression=rule.max_relative_regression,
                relative_regression=None,
                passed=False,
                reason="reference aggregate metric is missing",
            )
        relative_regression = _relative_regression(
            challenger_value,
            reference_value,
            rule.direction,
        )
        if relative_regression is None:
            reasons.append("reference metric is zero; relative regression is undefined")
        elif relative_regression > rule.max_relative_regression:
            reasons.append("challenger relative regression exceeds policy")
    elif rule.max_relative_regression > 0:
        raise FeatureComparisonError(f"reference aggregate metric is required: {rule.metric_name}")
    return FeatureMetricComparison(
        metric_name=rule.metric_name,
        direction=rule.direction,
        challenger_value=challenger_value,
        reference_value=reference_value,
        minimum_value=rule.minimum_value,
        maximum_value=rule.maximum_value,
        max_relative_regression=rule.max_relative_regression,
        relative_regression=relative_regression,
        passed=not reasons,
        reason="; ".join(reasons) if reasons else "passed",
    )


def _relative_regression(
    challenger: Decimal,
    reference: Decimal,
    direction: ComparisonDirection,
) -> Decimal | None:
    unfavorable = challenger - reference if direction == "minimize" else reference - challenger
    if unfavorable <= 0:
        return Decimal("0")
    denominator = abs(reference)
    if denominator == 0:
        return None
    return unfavorable / denominator


def _calibration_gate(
    challenger: FeatureComparisonModelEvidence,
    reference: FeatureComparisonModelEvidence | None,
    required: bool,
) -> tuple[bool, str | None]:
    if not required:
        return True, None
    if not challenger.registration.calibrated:
        return False, "challenger registration is not calibrated"
    if reference is not None and not reference.registration.calibrated:
        return False, "reference registration is not calibrated"
    return True, None


def _weighted_metric(
    report: FeatureWalkForwardReport,
    field_name: Literal["log_loss", "normalized_brier_score", "accuracy", "coverage"],
    total_rows: int,
) -> Decimal:
    value = sum(
        Decimal(str(getattr(fold.metrics, field_name))) * Decimal(fold.metrics.row_count)
        for fold in report.folds
    )
    return value / Decimal(total_rows)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeatureComparisonError(f"{name} must include a timezone")


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise FeatureComparisonError(f"{name} must not be empty")
