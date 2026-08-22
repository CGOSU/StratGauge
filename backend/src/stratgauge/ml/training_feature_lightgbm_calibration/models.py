"""Purpose-aware validation-only calibration for feature LightGBM outputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml.training_datasets import (
    PredictionSplit,
    TrainingDatasetPurpose,
    TrainingLabel,
    ValidationSplit,
    label_contract_id_for,
    ordered_labels_for,
)
from stratgauge.ml.training_feature_lightgbm import (
    FeatureLightGBMEvaluationEvidence,
    FeatureLightGBMModel,
    FeatureLightGBMPrediction,
)

FEATURE_CALIBRATION_VERSION = "selection-purpose-lightgbm-calibration-v1"


FEATURE_CALIBRATION_EVALUATION_VERSION = "selection-purpose-lightgbm-calibration-evaluation-v1"


class FeatureCalibrationBin(DomainModel):
    """One one-vs-rest isotonic bin for one purpose class."""

    label: TrainingLabel
    lower_probability: Decimal = Field(ge=0, le=1)
    upper_probability: Decimal = Field(ge=0, le=1)
    calibrated_probability: Decimal = Field(ge=0, le=1)
    sample_count: int = Field(gt=0)
    positive_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> FeatureCalibrationBin:
        if self.lower_probability > self.upper_probability:
            raise ValueError("feature calibration bin bounds are reversed")
        if self.positive_count > self.sample_count:
            raise ValueError("feature calibration positive count exceeds sample count")
        return self


class FeatureCalibratedModel(DomainModel):
    """Immutable calibration metadata bound to one feature LightGBM model."""

    calibration_id: str
    calibration_version: str = FEATURE_CALIBRATION_VERSION
    model_type: Literal["feature_lightgbm_isotonic"] = "feature_lightgbm_isotonic"
    base_model_version_id: str
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    feature_names: tuple[str, ...]
    calibrated: Literal[True] = True
    calibrated_at: datetime
    calibration_split: ValidationSplit = "validation"
    calibration_row_count: int = Field(gt=0)
    calibration_target_counts: dict[str, int]
    calibration_date_start: date
    calibration_date_end: date
    test_row_count: int = Field(ge=0)
    source_prediction_sha256: str
    source_model_sha256: str
    source_manifest_sha256: str
    bins: tuple[FeatureCalibrationBin, ...]

    @field_validator(
        "calibration_id",
        "base_model_version_id",
        "label_contract_id",
        "feature_set_version_id",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature calibration text must not be empty")
        return normalized

    @field_validator(
        "source_prediction_sha256",
        "source_model_sha256",
        "source_manifest_sha256",
    )
    @classmethod
    def validate_digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _is_sha256(normalized):
            raise ValueError("feature calibration source must be a SHA-256 digest")
        return normalized

    @field_validator("calibrated_at")
    @classmethod
    def validate_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("calibrated_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> FeatureCalibratedModel:
        if self.calibration_version != FEATURE_CALIBRATION_VERSION:
            raise ValueError("unsupported feature calibration version")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("feature calibration label contract does not match purpose")
        if self.calibration_row_count != sum(self.calibration_target_counts.values()):
            raise ValueError("feature calibration target counts do not match rows")
        if self.calibration_date_end < self.calibration_date_start:
            raise ValueError("feature calibration dates are reversed")
        if not self.feature_names or tuple(sorted(set(self.feature_names))) != self.feature_names:
            raise ValueError("feature calibration feature names must be sorted and non-empty")
        expected_labels = ordered_labels_for(self.purpose)
        if set(self.calibration_target_counts) != {label.value for label in expected_labels}:
            raise ValueError("feature calibration target counts do not match purpose")
        if any(count <= 0 for count in self.calibration_target_counts.values()):
            raise ValueError("feature calibration validation must contain every purpose class")
        expected_bin_labels = (
            {expected_labels[-1]} if len(expected_labels) == 2 else set(expected_labels)
        )
        if {item.label for item in self.bins} != expected_bin_labels:
            raise ValueError("feature calibration bins do not match purpose classes")
        for label in expected_bin_labels:
            bins = tuple(item for item in self.bins if item.label is label)
            if sum(item.sample_count for item in bins) != self.calibration_row_count:
                raise ValueError("feature calibration bins do not cover validation rows")
            if (
                sum(item.positive_count for item in bins)
                != self.calibration_target_counts[label.value]
            ):
                raise ValueError("feature calibration bins do not match validation targets")
            if tuple(item.lower_probability for item in bins) != tuple(
                sorted(item.lower_probability for item in bins)
            ):
                raise ValueError("feature calibration bins are not ordered")
            if any(
                left.upper_probability > right.lower_probability
                for left, right in zip(bins, bins[1:], strict=False)
            ):
                raise ValueError("feature calibration bins overlap")
        return self


class FeatureCalibratedPrediction(DomainModel):
    """Raw and calibrated class probabilities for validation or test."""

    split: PredictionSplit
    sample_id: str
    candidate_id: str
    symbol: str
    observation_date: date
    label_available_at: datetime
    label: TrainingLabel
    predicted_label: TrainingLabel
    predicted_probabilities: dict[str, Decimal]
    calibrated_probabilities: dict[str, Decimal]
    feature_snapshot_id: str
    source_label_row_sha256: str

    @field_validator("label_available_at")
    @classmethod
    def validate_label_available_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("feature calibration label availability must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_probabilities(self) -> FeatureCalibratedPrediction:
        if self.label_available_at.date() < self.observation_date:
            raise ValueError(
                "feature calibration label availability cannot precede observation date"
            )
        labels = set(self.predicted_probabilities)
        if labels != set(self.calibrated_probabilities):
            raise ValueError("feature calibration probability labels do not match")
        for probabilities in (self.predicted_probabilities, self.calibrated_probabilities):
            if any(
                not value.is_finite() or not 0 <= value <= 1 for value in probabilities.values()
            ):
                raise ValueError("feature calibration probabilities must be bounded")
            if abs(sum(probabilities.values(), Decimal("0")) - Decimal("1")) > Decimal("1e-6"):
                raise ValueError("feature calibration probabilities must sum to one")
        return self


class FeatureCalibrationMetrics(DomainModel):
    """Raw and calibrated multiclass-aware metrics for one split."""

    split: PredictionSplit
    row_count: int = Field(ge=0)
    target_counts: dict[str, int]
    raw_log_loss: float | None = Field(default=None, ge=0)
    calibrated_log_loss: float | None = Field(default=None, ge=0)
    raw_normalized_brier_score: float | None = Field(default=None, ge=0, le=1)
    calibrated_normalized_brier_score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_counts(self) -> FeatureCalibrationMetrics:
        if sum(self.target_counts.values()) != self.row_count:
            raise ValueError("feature calibration metric target counts do not match rows")
        return self


class FeatureCalibrationEvaluationEvidence(DomainModel):
    """Evaluation evidence for one calibrated feature LightGBM artifact."""

    evaluation_version: str = FEATURE_CALIBRATION_EVALUATION_VERSION
    model_version_id: str
    model_type: Literal["feature_lightgbm_isotonic"] = "feature_lightgbm_isotonic"
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    source_prediction_sha256: str
    source_model_sha256: str
    source_manifest_sha256: str
    calibrated: Literal[True] = True
    metrics: tuple[FeatureCalibrationMetrics, ...]


@dataclass(frozen=True)
class FeatureLightGBMOutput:
    """Verified immutable outputs from one feature LightGBM training directory."""

    model: FeatureLightGBMModel
    metrics: FeatureLightGBMEvaluationEvidence
    predictions: tuple[FeatureLightGBMPrediction, ...]
    manifest: Mapping[str, object]
    model_sha256: str
    metrics_sha256: str
    predictions_sha256: str
    manifest_sha256: str


@dataclass(frozen=True)
class FeatureCalibrationResult:
    model: FeatureCalibratedModel
    metrics: tuple[FeatureCalibrationMetrics, ...]
    predictions: tuple[FeatureCalibratedPrediction, ...]
    source_prediction_sha256: str
    source_model_sha256: str
    source_manifest_sha256: str


@dataclass(frozen=True)
class FeatureCalibrationOutput:
    """Verified immutable outputs from one feature calibration directory."""

    model: FeatureCalibratedModel
    metrics: FeatureCalibrationEvaluationEvidence
    predictions: tuple[FeatureCalibratedPrediction, ...]
    manifest: Mapping[str, object]
    calibration_sha256: str
    metrics_sha256: str
    calibrated_predictions_sha256: str
    manifest_sha256: str


def _is_sha256(value: str) -> bool:
    normalized = value.strip().lower()
    return len(normalized) == 64 and all(char in "0123456789abcdef" for char in normalized)
