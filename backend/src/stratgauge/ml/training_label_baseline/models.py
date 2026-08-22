"""Train dependency-free constant label distributions for prepared ML targets."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml.training_datasets import (
    PredictionSplit,
    TrainingDatasetPurpose,
    TrainingLabel,
    TrainingLabelDatasetSplit,
    TrainingLabelReadinessReport,
    TrainingLabelSplitManifest,
    TrainingSplitName,
    ordered_labels_for,
)

LABEL_BASELINE_REPORT_VERSION = "selection-label-baseline-v1"


LABEL_BASELINE_EVALUATION_VERSION = "selection-label-baseline-evaluation-v1"


LABEL_BASELINE_MODEL_TYPE: Literal["constant_label_distribution"] = "constant_label_distribution"


SplitName = TrainingSplitName


class TrainingLabelSplitInput(DomainModel):
    """Prepared split files and the two reports required before fitting."""

    split: TrainingLabelDatasetSplit
    manifest: TrainingLabelSplitManifest
    readiness: TrainingLabelReadinessReport
    split_manifest_sha256: str

    @field_validator("split_manifest_sha256")
    @classmethod
    def validate_manifest_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("split_manifest_sha256 must be a SHA-256 digest")
        return normalized


class LabelBaselineModel(DomainModel):
    """Immutable metadata and class prior for one purpose-specific baseline."""

    model_version_id: str
    model_type: Literal["constant_label_distribution"] = LABEL_BASELINE_MODEL_TYPE
    report_version: str = LABEL_BASELINE_REPORT_VERSION
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    class_labels: tuple[TrainingLabel, ...]
    probabilities: dict[str, Decimal]
    train_row_count: int = Field(gt=0)
    train_target_counts: dict[str, int]
    random_seed: int
    minimum_labeled_samples: int = Field(gt=0)
    source_file_sha256: str
    prepared_dataset_sha256: str
    split_manifest_sha256: str
    train_end: date
    validation_end: date
    label_available_through: datetime
    row_counts: dict[str, int]

    @field_validator(
        "model_version_id",
        "label_contract_id",
        "source_file_sha256",
        "prepared_dataset_sha256",
        "split_manifest_sha256",
    )
    @classmethod
    def validate_model_text(cls, value: str) -> str:
        normalized = value.strip().lower() if "sha256" in value else value.strip()
        if not normalized:
            raise ValueError("label baseline model text must not be empty")
        return normalized

    @field_validator("label_available_through")
    @classmethod
    def validate_model_cutoff(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("label baseline cutoff must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_distribution(self) -> LabelBaselineModel:
        expected_labels = ordered_labels_for(self.purpose)
        if self.class_labels != expected_labels:
            raise ValueError("label baseline class labels do not match purpose")
        if set(self.probabilities) != {label.value for label in expected_labels}:
            raise ValueError("label baseline probabilities do not match class labels")
        if any(
            not probability.is_finite() or probability < 0 or probability > 1
            for probability in self.probabilities.values()
        ):
            raise ValueError("label baseline probabilities must be finite and bounded")
        if abs(sum(self.probabilities.values(), Decimal("0")) - Decimal("1")) > Decimal("1e-20"):
            raise ValueError("label baseline probabilities must sum to one")
        if sum(self.train_target_counts.values()) != self.train_row_count:
            raise ValueError("label baseline target counts do not match train row count")
        if set(self.train_target_counts) != {label.value for label in expected_labels}:
            raise ValueError("label baseline target counts do not match class labels")
        return self


class LabelBaselineMetrics(DomainModel):
    """One partition's metrics under the fixed train class distribution."""

    split: SplitName
    row_count: int = Field(ge=0)
    coverage: Decimal = Field(ge=0, le=1)
    target_counts: dict[str, int]
    predicted_probabilities: dict[str, Decimal]
    log_loss: float | None = Field(default=None, ge=0)
    normalized_brier_score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_metric_counts(self) -> LabelBaselineMetrics:
        if sum(self.target_counts.values()) != self.row_count:
            raise ValueError("label baseline metric target counts do not match row count")
        if set(self.target_counts) != set(self.predicted_probabilities):
            raise ValueError("label baseline metric classes do not match")
        if self.row_count == 0:
            if self.coverage != 0 or self.log_loss is not None:
                raise ValueError("empty label baseline metrics must have zero coverage and no loss")
        elif self.coverage != 1:
            raise ValueError("non-empty label baseline metrics must have full coverage")
        return self


class LabelBaselinePrediction(DomainModel):
    """One validation/test prediction retained for audit and future ranking review."""

    split: PredictionSplit
    sample_id: str
    candidate_id: str
    batch_id: str
    symbol: str
    observation_date: date
    label_available_at: datetime
    target: TrainingLabel
    predicted_label: TrainingLabel
    predicted_probabilities: dict[str, Decimal]
    source_row_sha256: str

    @field_validator("label_available_at")
    @classmethod
    def validate_prediction_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("label baseline prediction time must include a timezone")
        return value


class LabelBaselineEvaluationEvidence(DomainModel):
    """Purpose-aware evaluation evidence kept separate from feature-model evidence."""

    evaluation_version: str = LABEL_BASELINE_EVALUATION_VERSION
    model_version_id: str
    model_type: Literal["constant_label_distribution"] = LABEL_BASELINE_MODEL_TYPE
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    source_file_sha256: str
    prepared_dataset_sha256: str
    split_manifest_sha256: str
    metrics: tuple[LabelBaselineMetrics, ...]


class LabelBaselineTrainingResult(DomainModel):
    """All immutable outputs from one constant label-distribution training run."""

    model: LabelBaselineModel
    metrics: tuple[LabelBaselineMetrics, ...]
    predictions: tuple[LabelBaselinePrediction, ...]
    source_file_sha256: str
    prepared_dataset_sha256: str
    split_manifest_sha256: str
    row_counts: dict[str, int]
