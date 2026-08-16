"""Composed feature model registry store."""

from __future__ import annotations

from stratgauge.ml.training_feature_registry._registry_base import FeatureRegistryBase  # noqa: F401
from stratgauge.ml.training_feature_registry._registry_lifecycle import LifecycleMixin
from stratgauge.ml.training_feature_registry._registry_registration import RegistrationMixin


class FeatureModelRegistryStore(
    RegistrationMixin,
    LifecycleMixin,
):
    """Persist purpose-aware model evidence without reusing the legacy registry."""
