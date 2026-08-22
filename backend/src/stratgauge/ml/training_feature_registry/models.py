"""Immutable registration evidence for purpose-aware feature LightGBM models."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml._hashing import (
    sha256_json,
)
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    TrainingLabel,
    label_contract_id_for,
    ordered_labels_for,
)
from stratgauge.ml.training_feature_lightgbm import FEATURE_LIGHTGBM_MODEL_TYPE

FEATURE_MODEL_REGISTRY_VERSION: Literal["selection-purpose-feature-registry-v1"] = (
    "selection-purpose-feature-registry-v1"
)


FEATURE_MODEL_REGISTRATION_TYPE: Literal["feature_lightgbm"] = "feature_lightgbm"


FEATURE_MODEL_LIFECYCLE_VERSION: Literal["selection-purpose-feature-lifecycle-v1"] = (
    "selection-purpose-feature-lifecycle-v1"
)


FeatureModelLifecycle = Literal["challenger", "shadow", "retired"]


class FeatureModelRegistration(DomainModel):
    """Immutable challenger registration bound to training and calibration evidence."""

    registry_version: Literal["selection-purpose-feature-registry-v1"] = (
        FEATURE_MODEL_REGISTRY_VERSION
    )
    registration_id: str
    registration_type: Literal["feature_lightgbm"] = FEATURE_MODEL_REGISTRATION_TYPE
    model_version_id: str
    model_type: Literal["lightgbm"] = FEATURE_LIGHTGBM_MODEL_TYPE
    lifecycle: FeatureModelLifecycle = "challenger"
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    feature_names: tuple[str, ...]
    class_labels: tuple[TrainingLabel, ...]
    calibrated: bool = False
    calibration_id: str | None = None
    calibrated_at: datetime | None = None
    calibration_date_start: date | None = None
    calibration_date_end: date | None = None
    training_model_sha256: str
    training_runtime_artifact_sha256: str
    training_metrics_sha256: str
    training_predictions_sha256: str
    training_manifest_sha256: str
    calibration_sha256: str | None = None
    calibration_metrics_sha256: str | None = None
    calibrated_predictions_sha256: str | None = None
    calibration_manifest_sha256: str | None = None
    evaluation_sha256: str
    feature_dataset_manifest_sha256: str
    source_label_dataset_sha256: str
    label_split_manifest_sha256: str
    registered_at: datetime
    registered_by: str

    @field_validator(
        "registration_id",
        "model_version_id",
        "label_contract_id",
        "feature_set_version_id",
        "registered_by",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature model registration text must not be empty")
        return normalized

    @field_validator(
        "training_model_sha256",
        "training_runtime_artifact_sha256",
        "training_metrics_sha256",
        "training_predictions_sha256",
        "training_manifest_sha256",
        "evaluation_sha256",
        "feature_dataset_manifest_sha256",
        "source_label_dataset_sha256",
        "label_split_manifest_sha256",
        "calibration_sha256",
        "calibration_metrics_sha256",
        "calibrated_predictions_sha256",
        "calibration_manifest_sha256",
    )
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not _is_sha256(normalized):
            raise ValueError("feature model registration digest must be a SHA-256 value")
        return normalized

    @field_validator("registered_at", "calibrated_at")
    @classmethod
    def validate_aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("feature model registration timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> FeatureModelRegistration:
        if self.registry_version != FEATURE_MODEL_REGISTRY_VERSION:
            raise ValueError("unsupported feature model registry version")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("feature model registration label contract does not match purpose")
        if self.registration_id != _registration_id(self.model_version_id, self.calibration_id):
            raise ValueError("feature model registration identity does not match model identity")
        if not self.feature_names or tuple(sorted(set(self.feature_names))) != self.feature_names:
            raise ValueError(
                "feature model registration feature names must be sorted and non-empty"
            )
        if self.class_labels != ordered_labels_for(self.purpose):
            raise ValueError("feature model registration classes do not match purpose")
        calibration_values = (
            self.calibration_id,
            self.calibrated_at,
            self.calibration_date_start,
            self.calibration_date_end,
            self.calibration_sha256,
            self.calibration_metrics_sha256,
            self.calibrated_predictions_sha256,
            self.calibration_manifest_sha256,
        )
        if self.calibrated:
            if any(value is None for value in calibration_values):
                raise ValueError("calibrated registration is missing calibration evidence")
            if self.calibrated_at is None or self.calibrated_at > self.registered_at:
                raise ValueError("registration cannot precede calibration time")
            if (
                self.calibration_date_start is None
                or self.calibration_date_end is None
                or self.calibration_date_end < self.calibration_date_start
            ):
                raise ValueError("feature model calibration dates are reversed")
        elif any(value is not None for value in calibration_values):
            raise ValueError("uncalibrated registration must not contain calibration evidence")
        return self


class FeatureModelLifecycleEvent(DomainModel):
    """One manually authorized, append-only purpose-model lifecycle event."""

    lifecycle_version: Literal["selection-purpose-feature-lifecycle-v1"] = (
        FEATURE_MODEL_LIFECYCLE_VERSION
    )
    event_id: str
    registration_id: str
    from_lifecycle: FeatureModelLifecycle
    to_lifecycle: FeatureModelLifecycle
    changed_at: datetime
    reason: str
    operator: str

    @field_validator("event_id", "registration_id", "reason", "operator")
    @classmethod
    def validate_event_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature model lifecycle event text must not be empty")
        return normalized

    @field_validator("changed_at")
    @classmethod
    def validate_changed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("feature model lifecycle event timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_transition(self) -> FeatureModelLifecycleEvent:
        if self.lifecycle_version != FEATURE_MODEL_LIFECYCLE_VERSION:
            raise ValueError("unsupported feature model lifecycle version")
        if self.from_lifecycle == self.to_lifecycle:
            raise ValueError("feature model lifecycle event must change state")
        if (self.from_lifecycle, self.to_lifecycle) not in {
            ("challenger", "shadow"),
            ("shadow", "retired"),
            ("challenger", "retired"),
        }:
            raise ValueError("invalid feature model lifecycle transition")
        return self


def _is_sha256(value: str) -> bool:
    normalized = value.strip().lower()
    return len(normalized) == 64 and all(char in "0123456789abcdef" for char in normalized)


def _registration_id(model_version_id: str, calibration_id: str | None) -> str:
    identity = {
        "calibration_id": calibration_id,
        "model_version_id": model_version_id,
        "registry_version": FEATURE_MODEL_REGISTRY_VERSION,
    }
    return f"feature-registration-{sha256_json(identity)[:20]}"
