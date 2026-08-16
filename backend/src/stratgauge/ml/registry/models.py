"""Immutable local registration and manual lifecycle for selection models."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import field_validator

from stratgauge.domain.models import DomainModel, ModelLifecycle
from stratgauge.ml.lightgbm_training import LightGBMModel
from stratgauge.ml.selection_training import BaselineModel

RegisteredModelMetadata = BaselineModel | LightGBMModel


MODEL_REGISTRY_VERSION = "selection-model-registry-v1"


class RegisteredModel(DomainModel):
    """Immutable model metadata materialized with its lifecycle events."""

    registry_version: Literal["selection-model-registry-v1"] = "selection-model-registry-v1"
    model_version_id: str
    model_type: str
    lifecycle: ModelLifecycle = ModelLifecycle.CHALLENGER
    feature_set_version_id: str
    tracking_policy_id: str
    source_file_sha256: str
    artifact_sha256: str
    runtime_artifact_sha256: str | None = None
    evaluation_sha256: str
    training_manifest_sha256: str
    random_seed: int
    registered_at: datetime
    registered_by: str

    @field_validator(
        "model_version_id",
        "model_type",
        "feature_set_version_id",
        "tracking_policy_id",
        "source_file_sha256",
        "artifact_sha256",
        "evaluation_sha256",
        "training_manifest_sha256",
        "registered_by",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("registered model text must not be empty")
        return value.strip()

    @field_validator("registered_at")
    @classmethod
    def validate_registered_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("registered_at must include a timezone")
        return value


class ModelLifecycleEvent(DomainModel):
    """One append-only, manually attributable model transition."""

    event_id: str
    model_version_id: str
    from_lifecycle: ModelLifecycle
    to_lifecycle: ModelLifecycle
    changed_at: datetime
    reason: str
    operator: str
    evaluation_sha256: str | None = None

    @field_validator("event_id", "model_version_id", "reason", "operator")
    @classmethod
    def validate_event_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model lifecycle event text must not be empty")
        return value.strip()

    @field_validator("changed_at")
    @classmethod
    def validate_changed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("model lifecycle timestamp must include a timezone")
        return value


_ALLOWED_TRANSITIONS: Mapping[ModelLifecycle, frozenset[ModelLifecycle]] = {
    ModelLifecycle.CHALLENGER: frozenset({ModelLifecycle.SHADOW, ModelLifecycle.RETIRED}),
    ModelLifecycle.SHADOW: frozenset({ModelLifecycle.ACTIVE, ModelLifecycle.RETIRED}),
    ModelLifecycle.ACTIVE: frozenset({ModelLifecycle.SHADOW, ModelLifecycle.RETIRED}),
    ModelLifecycle.RETIRED: frozenset(),
}
