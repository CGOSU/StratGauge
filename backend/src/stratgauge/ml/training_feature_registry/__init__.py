"""Re-exports for the training_feature_registry package (split from one module)."""

from __future__ import annotations

from stratgauge.ml.training_feature_registry.core import (
    FeatureModelRegistryError,
)
from stratgauge.ml.training_feature_registry.models import (
    FEATURE_MODEL_LIFECYCLE_VERSION,
    FEATURE_MODEL_REGISTRATION_TYPE,
    FEATURE_MODEL_REGISTRY_VERSION,
    FeatureModelLifecycle,
    FeatureModelLifecycleEvent,
    FeatureModelRegistration,
    _registration_id,
)
from stratgauge.ml.training_feature_registry.models_b import (
    FeatureModelRegistryStore,
)

__all__ = [
    "_registration_id",
    "FEATURE_MODEL_REGISTRY_VERSION",
    "FEATURE_MODEL_REGISTRATION_TYPE",
    "FEATURE_MODEL_LIFECYCLE_VERSION",
    "FeatureModelLifecycle",
    "FeatureModelRegistryError",
    "FeatureModelRegistration",
    "FeatureModelLifecycleEvent",
    "FeatureModelRegistryStore",
]
