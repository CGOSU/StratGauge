"""Re-exports for the training_feature_shadow package (split from one module)."""

from __future__ import annotations

from stratgauge.ml.training_feature_shadow.core import (
    FeatureShadowError,
)
from stratgauge.ml.training_feature_shadow.helpers import (
    feature_shadow_input_sha256,
    read_feature_shadow_inputs,
    score_feature_shadow_batch,
    serialize_feature_shadow_inputs,
)
from stratgauge.ml.training_feature_shadow.models import (
    FEATURE_SHADOW_REPORT_VERSION,
    FEATURE_SHADOW_SCORE_VERSION,
    FeatureShadowCalibrationMode,
    FeatureShadowFailure,
    FeatureShadowInput,
    FeatureShadowReport,
    FeatureShadowScore,
    FeatureShadowScoreStore,
)

__all__ = [
    "FEATURE_SHADOW_SCORE_VERSION",
    "FEATURE_SHADOW_REPORT_VERSION",
    "FeatureShadowCalibrationMode",
    "FeatureShadowError",
    "FeatureShadowInput",
    "FeatureShadowScore",
    "FeatureShadowFailure",
    "FeatureShadowReport",
    "FeatureShadowScoreStore",
    "read_feature_shadow_inputs",
    "serialize_feature_shadow_inputs",
    "feature_shadow_input_sha256",
    "score_feature_shadow_batch",
]
