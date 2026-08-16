"""Re-exports for the training_feature_comparison package (split from one module)."""

from __future__ import annotations

from stratgauge.ml.training_feature_comparison.core import (
    FeatureComparisonError,
)
from stratgauge.ml.training_feature_comparison.helpers import (
    aggregate_feature_walk_forward_metrics,
    compare_feature_model_evidence,
    compare_registered_feature_models,
)
from stratgauge.ml.training_feature_comparison.models import (
    FEATURE_COMPARISON_REPORT_VERSION,
    ComparisonDirection,
    FeatureComparisonMetricRule,
    FeatureComparisonModelEvidence,
    FeatureComparisonPolicy,
    FeatureComparisonReport,
    FeatureComparisonStore,
    FeatureMetricComparison,
)

__all__ = [
    "FEATURE_COMPARISON_REPORT_VERSION",
    "ComparisonDirection",
    "FeatureComparisonError",
    "FeatureComparisonMetricRule",
    "FeatureComparisonPolicy",
    "FeatureMetricComparison",
    "FeatureComparisonReport",
    "FeatureComparisonModelEvidence",
    "FeatureComparisonStore",
    "compare_registered_feature_models",
    "compare_feature_model_evidence",
    "aggregate_feature_walk_forward_metrics",
]
