"""Re-exports for the registry package (split from one module)."""

from __future__ import annotations

from stratgauge.ml.registry.core import (
    ModelRegistryError,
)
from stratgauge.ml.registry.models import (
    MODEL_REGISTRY_VERSION,
    ModelLifecycleEvent,
    RegisteredModel,
    RegisteredModelMetadata,
)
from stratgauge.ml.registry.models_b import (
    ModelRegistryStore,
)

__all__ = [
    "RegisteredModelMetadata",
    "MODEL_REGISTRY_VERSION",
    "ModelRegistryError",
    "RegisteredModel",
    "ModelLifecycleEvent",
    "ModelRegistryStore",
]
