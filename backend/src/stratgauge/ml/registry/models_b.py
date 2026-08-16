"""Composed model registry store."""

from __future__ import annotations

from stratgauge.ml.registry._store_base import ModelRegistryBase  # noqa: F401
from stratgauge.ml.registry._store_lifecycle import LifecycleMixin
from stratgauge.ml.registry._store_registration import RegistrationMixin


class ModelRegistryStore(
    RegistrationMixin,
    LifecycleMixin,
):
    """Persist model artifacts once and materialize manual lifecycle events."""
