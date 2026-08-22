"""Train a feature-lineage-aware constant distribution baseline."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml.training_datasets import (
    PredictionSplit,
    SelectionTrainingLabelRow,
    TrainingDatasetPurpose,
    TrainingLabel,
    TrainingLabelReadinessReport,
    TrainingLabelSplitManifest,
    TrainingSplitName,
    ordered_labels_for,
)
from stratgauge.ml.training_feature_join import (
    SelectionTrainingFeatureRow,
    TrainingFeatureJoinReport,
    TrainingFeatureSplitManifest,
)

FEATURE_BASELINE_REPORT_VERSION = "selection-purpose-feature-baseline-v1"


FEATURE_BASELINE_EVALUATION_VERSION = "selection-purpose-feature-baseline-evaluation-v1"


FEATURE_BASELINE_MODEL_TYPE: Literal["constant_label_distribution"] = "constant_label_distribution"


SplitName = TrainingSplitName


class TrainingFeatureSplitInput(DomainModel):
    """Verified feature partitions and their immutable connection evidence."""

    manifest: TrainingFeatureSplitManifest
    report: TrainingFeatureJoinReport
    label_split_manifest: TrainingLabelSplitManifest
    label_readiness: TrainingLabelReadinessReport
    label_train_rows: tuple[SelectionTrainingLabelRow, ...]
    label_validation_rows: tuple[SelectionTrainingLabelRow, ...]
    label_test_rows: tuple[SelectionTrainingLabelRow, ...]
    purged_rows: tuple[SelectionTrainingLabelRow, ...]
    train_rows: tuple[SelectionTrainingFeatureRow, ...]
    validation_rows: tuple[SelectionTrainingFeatureRow, ...]
    test_rows: tuple[SelectionTrainingFeatureRow, ...]
    manifest_sha256: str

    @field_validator("manifest_sha256")
    @classmethod
    def validate_manifest_sha256(cls, value: str) -> str:
        return _sha256_text(value, "manifest_sha256")

    @model_validator(mode="after")
    def validate_split(self) -> TrainingFeatureSplitInput:
        if self.manifest.purpose is not self.report.purpose:
            raise ValueError("training feature manifest purpose does not match join report")
        if self.manifest.label_contract_id != self.report.label_contract_id:
            raise ValueError("training feature manifest contract does not match join report")
        if self.manifest.feature_set_version_id != self.report.feature_set_version_id:
            raise ValueError("training feature manifest feature set does not match join report")
        _validate_label_lineage(
            self.label_train_rows,
            self.label_validation_rows,
            self.label_test_rows,
            self.purged_rows,
            self.manifest,
        )
        _validate_rows(self.train_rows, self.manifest, "train")
        _validate_rows(self.validation_rows, self.manifest, "validation")
        _validate_rows(self.test_rows, self.manifest, "test")
        _validate_cross_partition_sample_ids(
            self.train_rows + self.validation_rows + self.test_rows
        )
        _validate_feature_label_pairs(
            self.train_rows,
            self.label_train_rows,
            "train",
        )
        _validate_feature_label_pairs(
            self.validation_rows,
            self.label_validation_rows,
            "validation",
        )
        _validate_feature_label_pairs(self.test_rows, self.label_test_rows, "test")
        return self


class FeatureBaselineModel(DomainModel):
    """A constant target distribution bound to feature data lineage."""

    model_version_id: str
    model_type: Literal["constant_label_distribution"] = FEATURE_BASELINE_MODEL_TYPE
    report_version: str = FEATURE_BASELINE_REPORT_VERSION
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    feature_names: tuple[str, ...]
    class_labels: tuple[TrainingLabel, ...]
    probabilities: dict[str, Decimal]
    train_row_count: int = Field(gt=0)
    train_target_counts: dict[str, int]
    random_seed: int
    minimum_labeled_samples: int = Field(gt=0)
    feature_dataset_manifest_sha256: str
    source_label_dataset_sha256: str
    label_split_manifest_sha256: str
    row_counts: dict[str, int]

    @field_validator(
        "model_version_id",
        "label_contract_id",
        "feature_set_version_id",
        "feature_dataset_manifest_sha256",
        "source_label_dataset_sha256",
        "label_split_manifest_sha256",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature baseline model text must not be empty")
        return normalized.lower() if "sha256" in normalized else normalized

    @model_validator(mode="after")
    def validate_distribution(self) -> FeatureBaselineModel:
        labels = ordered_labels_for(self.purpose)
        if self.class_labels != labels:
            raise ValueError("feature baseline class labels do not match purpose")
        if set(self.probabilities) != {label.value for label in labels}:
            raise ValueError("feature baseline probabilities do not match labels")
        if sum(self.train_target_counts.values()) != self.train_row_count:
            raise ValueError("feature baseline counts do not match train rows")
        if set(self.train_target_counts) != {label.value for label in labels}:
            raise ValueError("feature baseline counts do not match labels")
        if any(
            not value.is_finite() or value < 0 or value > 1 for value in self.probabilities.values()
        ):
            raise ValueError("feature baseline probabilities must be finite and bounded")
        if abs(sum(self.probabilities.values(), Decimal("0")) - Decimal("1")) > Decimal("1e-20"):
            raise ValueError("feature baseline probabilities must sum to one")
        if not self.feature_names:
            raise ValueError("feature baseline requires feature names")
        return self


class FeatureBaselineMetrics(DomainModel):
    """Partition metrics for a fixed class-distribution feature baseline."""

    split: SplitName
    row_count: int = Field(ge=0)
    coverage: Decimal = Field(ge=0, le=1)
    target_counts: dict[str, int]
    predicted_probabilities: dict[str, Decimal]
    log_loss: float | None = Field(default=None, ge=0)
    normalized_brier_score: float | None = Field(default=None, ge=0, le=1)


class FeatureBaselinePrediction(DomainModel):
    """Out-of-sample prediction retaining its feature/label lineage."""

    split: PredictionSplit
    sample_id: str
    target: TrainingLabel
    predicted_label: TrainingLabel
    predicted_probabilities: dict[str, Decimal]
    feature_snapshot_id: str
    source_label_row_sha256: str


class FeatureBaselineEvaluationEvidence(DomainModel):
    """Evaluation evidence for a feature-lineage-aware baseline."""

    evaluation_version: str = FEATURE_BASELINE_EVALUATION_VERSION
    model_version_id: str
    model_type: Literal["constant_label_distribution"] = FEATURE_BASELINE_MODEL_TYPE
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    feature_dataset_manifest_sha256: str
    metrics: tuple[FeatureBaselineMetrics, ...]


class FeatureBaselineTrainingResult(DomainModel):
    """All immutable output from fitting one feature-aware baseline."""

    model: FeatureBaselineModel
    metrics: tuple[FeatureBaselineMetrics, ...]
    predictions: tuple[FeatureBaselinePrediction, ...]


def _validate_cross_partition_sample_ids(rows: Sequence[SelectionTrainingFeatureRow]) -> None:
    samples = tuple(row.sample_id for row in rows)
    if len(samples) != len(set(samples)):
        raise ValueError("training feature rows contain duplicate sample IDs")


def _validate_feature_label_pairs(
    feature_rows: Sequence[SelectionTrainingFeatureRow],
    label_rows: Sequence[SelectionTrainingLabelRow],
    partition: str,
) -> None:
    labels = {row.sample_id: row for row in label_rows}
    for row in feature_rows:
        label = labels.get(row.sample_id)
        if label is None:
            raise ValueError(f"{partition} feature row has no matching label row")
        if row.source_label_row_sha256 != label.source_row_sha256:
            raise ValueError(f"{partition} feature row source hash does not match label row")
        if row.target is not label.target:
            raise ValueError(f"{partition} feature row target does not match label row")


def _validate_label_lineage(
    train_rows: Sequence[SelectionTrainingLabelRow],
    validation_rows: Sequence[SelectionTrainingLabelRow],
    test_rows: Sequence[SelectionTrainingLabelRow],
    purged_rows: Sequence[SelectionTrainingLabelRow],
    manifest: TrainingFeatureSplitManifest,
) -> None:
    partitions = {
        "train": train_rows,
        "validation": validation_rows,
        "test": test_rows,
        "purged": purged_rows,
    }
    if {name: len(rows) for name, rows in partitions.items()} != manifest.label_row_counts:
        raise ValueError("training feature label row counts do not match manifest")
    samples = tuple(row.sample_id for rows in partitions.values() for row in rows)
    if len(samples) != len(set(samples)):
        raise ValueError("training feature label rows contain duplicate sample IDs")
    for name, rows in partitions.items():
        for row in rows:
            if row.purpose is not manifest.purpose:
                raise ValueError(f"{name} label row has a different purpose")
            if row.label_contract_id != manifest.label_contract_id:
                raise ValueError(f"{name} label row has a different label contract")


def _validate_rows(
    rows: Sequence[SelectionTrainingFeatureRow],
    manifest: TrainingFeatureSplitManifest,
    partition: str,
) -> None:
    expected_names = manifest.feature_names
    for row in rows:
        if row.purpose is not manifest.purpose:
            raise ValueError(f"{partition} feature row has a different purpose")
        if row.label_contract_id != manifest.label_contract_id:
            raise ValueError(f"{partition} feature row has a different label contract")
        if row.feature_set_version_id != manifest.feature_set_version_id:
            raise ValueError(f"{partition} feature row has a different feature set")
        if tuple(sorted(row.features)) != expected_names:
            raise ValueError(f"{partition} feature rows have different feature columns")


def _sha256_text(value: str, name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError(f"{name} must be a SHA-256 digest")
    return normalized
