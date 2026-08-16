"""Train purpose-isolated LightGBM models from verified feature splits."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    TrainingLabel,
    label_contract_id_for,
)

FEATURE_LIGHTGBM_REPORT_VERSION = "selection-purpose-lightgbm-v1"


FEATURE_LIGHTGBM_EVALUATION_VERSION = "selection-purpose-lightgbm-evaluation-v1"


FEATURE_LIGHTGBM_MODEL_TYPE: Literal["lightgbm"] = "lightgbm"


FEATURE_LIGHTGBM_BOOST_ROUNDS = 50


SplitName = Literal["train", "validation", "test"]


PredictionSplit = Literal["validation", "test"]


_PURPOSE_LABEL_ORDER: dict[TrainingDatasetPurpose, tuple[TrainingLabel, ...]] = {
    TrainingDatasetPurpose.CANDIDATE_SUCCESS: (
        TrainingLabel.NOT_SUCCESS,
        TrainingLabel.SUCCESS,
    ),
    TrainingDatasetPurpose.ANNOTATION_DECISION: (
        TrainingLabel.DISCARD,
        TrainingLabel.WATCH,
        TrainingLabel.PREPARE_BUY,
    ),
    TrainingDatasetPurpose.BUY_TIMING: (
        TrainingLabel.NOT_PURCHASED,
        TrainingLabel.PURCHASED,
    ),
    TrainingDatasetPurpose.SELL_TIMING: (
        TrainingLabel.NOT_SOLD,
        TrainingLabel.SOLD,
    ),
}


class FeatureLightGBMModel(DomainModel):
    """Immutable metadata for one purpose-specific LightGBM artifact."""

    model_version_id: str
    model_type: Literal["lightgbm"] = FEATURE_LIGHTGBM_MODEL_TYPE
    report_version: str = FEATURE_LIGHTGBM_REPORT_VERSION
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    feature_names: tuple[str, ...]
    class_labels: tuple[TrainingLabel, ...]
    train_row_count: int = Field(gt=0)
    train_target_counts: dict[str, int]
    random_seed: int
    minimum_labeled_samples: int = Field(gt=0)
    feature_dataset_manifest_sha256: str
    source_label_dataset_sha256: str
    label_split_manifest_sha256: str
    artifact_sha256: str
    parameters: dict[str, str | int | float]
    row_counts: dict[str, int]
    purged_row_count: int = Field(ge=0)

    @field_validator(
        "model_version_id",
        "label_contract_id",
        "feature_set_version_id",
        "feature_dataset_manifest_sha256",
        "source_label_dataset_sha256",
        "label_split_manifest_sha256",
        "artifact_sha256",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature LightGBM model text must not be empty")
        return normalized

    @field_validator(
        "feature_dataset_manifest_sha256",
        "source_label_dataset_sha256",
        "label_split_manifest_sha256",
        "artifact_sha256",
    )
    @classmethod
    def validate_digests(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("feature LightGBM model digest must be a SHA-256 value")
        return normalized

    @model_validator(mode="after")
    def validate_contract(self) -> FeatureLightGBMModel:
        if self.report_version != FEATURE_LIGHTGBM_REPORT_VERSION:
            raise ValueError("unsupported feature LightGBM report version")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("feature LightGBM label contract does not match purpose")
        labels = _labels_for(self.purpose)
        if self.class_labels != labels:
            raise ValueError("feature LightGBM class labels do not match purpose")
        if set(self.train_target_counts) != {label.value for label in labels}:
            raise ValueError("feature LightGBM target counts do not match labels")
        expected_objective = "multiclass" if len(labels) > 2 else "binary"
        if self.parameters.get("objective") != expected_objective:
            raise ValueError("feature LightGBM objective does not match purpose")
        if len(labels) > 2 and self.parameters.get("num_class") != len(labels):
            raise ValueError("feature LightGBM class count does not match purpose")
        if len(labels) == 2 and "num_class" in self.parameters:
            raise ValueError("binary feature LightGBM must not declare num_class")
        if sum(self.train_target_counts.values()) != self.train_row_count:
            raise ValueError("feature LightGBM target counts do not match train rows")
        if not self.feature_names or tuple(sorted(set(self.feature_names))) != self.feature_names:
            raise ValueError("feature LightGBM feature names must be sorted and non-empty")
        if set(self.row_counts) != {"train", "validation", "test"}:
            raise ValueError("feature LightGBM row counts are incomplete")
        if any(value < 0 for value in self.row_counts.values()):
            raise ValueError("feature LightGBM row counts must be non-negative")
        return self


class FeatureLightGBMMetrics(DomainModel):
    """Metrics for one chronological partition under a multiclass-aware model."""

    split: SplitName
    row_count: int = Field(ge=0)
    coverage: Decimal = Field(ge=0, le=1)
    target_counts: dict[str, int]
    mean_predicted_probabilities: dict[str, Decimal]
    log_loss: float | None = Field(default=None, ge=0)
    normalized_brier_score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_metrics(self) -> FeatureLightGBMMetrics:
        if sum(self.target_counts.values()) != self.row_count:
            raise ValueError("feature LightGBM target counts do not match row count")
        if set(self.target_counts) != set(self.mean_predicted_probabilities):
            raise ValueError("feature LightGBM metric labels do not match")
        if self.row_count == 0 and (self.coverage != 0 or self.log_loss is not None):
            raise ValueError("empty feature LightGBM metrics have invalid coverage or loss")
        if self.row_count > 0 and self.coverage != 1:
            raise ValueError("non-empty feature LightGBM metrics require full coverage")
        return self


class FeatureLightGBMPrediction(DomainModel):
    """Out-of-sample probabilities with purpose and feature lineage retained."""

    split: PredictionSplit
    sample_id: str
    candidate_id: str
    symbol: str
    observation_date: date
    label_available_at: datetime
    target: TrainingLabel
    predicted_label: TrainingLabel
    predicted_probabilities: dict[str, Decimal]
    feature_snapshot_id: str
    source_label_row_sha256: str

    @field_validator("sample_id", "candidate_id", "symbol", "feature_snapshot_id")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature LightGBM prediction identity must not be empty")
        return normalized

    @field_validator("source_label_row_sha256")
    @classmethod
    def validate_source_label_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _is_sha256(normalized):
            raise ValueError("feature LightGBM prediction source must be a SHA-256 digest")
        return normalized

    @field_validator("label_available_at")
    @classmethod
    def validate_label_available_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("feature LightGBM label availability must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_label_timing(self) -> FeatureLightGBMPrediction:
        if self.label_available_at.date() < self.observation_date:
            raise ValueError("feature LightGBM label availability cannot precede observation date")
        return self


class FeatureLightGBMEvaluationEvidence(DomainModel):
    """Evaluation evidence that remains separate from model registration."""

    evaluation_version: str = FEATURE_LIGHTGBM_EVALUATION_VERSION
    model_version_id: str
    model_type: Literal["lightgbm"] = FEATURE_LIGHTGBM_MODEL_TYPE
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    feature_dataset_manifest_sha256: str
    calibrated: Literal[False] = False
    metrics: tuple[FeatureLightGBMMetrics, ...]


class FeatureLightGBMTrainingResult(DomainModel):
    """All immutable outputs from one purpose-specific LightGBM training run."""

    model: FeatureLightGBMModel
    metrics: tuple[FeatureLightGBMMetrics, ...]
    predictions: tuple[FeatureLightGBMPrediction, ...]
    artifact: bytes


def _is_sha256(value: str) -> bool:
    normalized = value.strip().lower()
    return len(normalized) == 64 and all(char in "0123456789abcdef" for char in normalized)


def _labels_for(purpose: TrainingDatasetPurpose) -> tuple[TrainingLabel, ...]:
    return _PURPOSE_LABEL_ORDER[purpose]
