"""Purpose-aware, purged walk-forward validation for feature LightGBM models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml._hashing import (
    sha256_json,
)
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    TrainingLabel,
    label_contract_id_for,
)
from stratgauge.ml.training_feature_registry import (
    FeatureModelLifecycle,
)

FEATURE_WALK_FORWARD_REPORT_VERSION = "selection-purpose-feature-walk-forward-v1"


FEATURE_WALK_FORWARD_MODEL_TYPE: Literal["lightgbm"] = "lightgbm"


SplitName = Literal["validation"]


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


class FeatureWalkForwardFoldSpec(DomainModel):
    """Explicit chronological boundaries for one purpose-aware validation fold."""

    fold_id: str
    train_start: date | None = None
    train_end: date
    validation_start: date
    validation_end: date

    @field_validator("fold_id")
    @classmethod
    def validate_fold_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature walk-forward fold_id must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_boundaries(self) -> FeatureWalkForwardFoldSpec:
        if self.train_start is not None and self.train_start > self.train_end:
            raise ValueError("feature walk-forward train_start must not follow train_end")
        if self.train_end >= self.validation_start:
            raise ValueError("feature walk-forward train_end must precede validation_start")
        if self.validation_start > self.validation_end:
            raise ValueError("feature walk-forward validation_start must not follow validation_end")
        return self


class FeatureWalkForwardMetrics(DomainModel):
    """Validation-only multiclass-aware metrics for one fold."""

    split: SplitName = "validation"
    row_count: int = Field(gt=0)
    coverage: Decimal = Field(ge=0, le=1)
    target_counts: dict[str, int]
    mean_predicted_probabilities: dict[str, Decimal]
    log_loss: float = Field(ge=0)
    normalized_brier_score: float = Field(ge=0, le=1)
    accuracy: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_metrics(self) -> FeatureWalkForwardMetrics:
        if self.coverage != 1:
            raise ValueError("feature walk-forward validation requires full coverage")
        if sum(self.target_counts.values()) != self.row_count:
            raise ValueError("feature walk-forward target counts do not match rows")
        if set(self.target_counts) != set(self.mean_predicted_probabilities):
            raise ValueError("feature walk-forward metric labels do not match")
        mean_probability_total = sum(self.mean_predicted_probabilities.values(), Decimal("0"))
        if abs(mean_probability_total - Decimal("1")) > Decimal("1e-6"):
            raise ValueError("feature walk-forward mean probabilities must sum to one")
        return self


class FeatureWalkForwardPrediction(DomainModel):
    """Raw out-of-sample probability for one fold validation row."""

    fold_id: str
    split: SplitName = "validation"
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

    @field_validator(
        "fold_id",
        "sample_id",
        "candidate_id",
        "symbol",
        "feature_snapshot_id",
    )
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature walk-forward prediction identity must not be empty")
        return normalized

    @field_validator("label_available_at")
    @classmethod
    def validate_label_available_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("feature walk-forward label availability must include a timezone")
        return value

    @field_validator("source_label_row_sha256")
    @classmethod
    def validate_source_label_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _is_sha256(normalized):
            raise ValueError("feature walk-forward source must be a SHA-256 digest")
        return normalized

    @model_validator(mode="after")
    def validate_probability_vector(self) -> FeatureWalkForwardPrediction:
        if self.label_available_at.date() < self.observation_date:
            raise ValueError("feature walk-forward label availability cannot precede observation")
        labels = set(self.predicted_probabilities)
        expected = {label.value for label in _labels_for_target(self.target)}
        if labels != expected:
            raise ValueError("feature walk-forward probability labels do not match target purpose")
        if any(
            not value.is_finite() or value < 0 or value > 1
            for value in self.predicted_probabilities.values()
        ):
            raise ValueError("feature walk-forward probabilities must be bounded")
        if abs(sum(self.predicted_probabilities.values(), Decimal("0")) - Decimal("1")) > Decimal(
            "1e-6"
        ):
            raise ValueError("feature walk-forward probabilities must sum to one")
        if self.predicted_label.value not in labels:
            raise ValueError("feature walk-forward predicted label is not in probability vector")
        return self


class FeatureWalkForwardFoldReport(DomainModel):
    """Accounting and validation evidence for one chronological fold."""

    fold_id: str
    train_start: date | None
    train_end: date
    validation_start: date
    validation_end: date
    train_candidate_row_count: int = Field(ge=0)
    train_row_count: int = Field(gt=0)
    validation_row_count: int = Field(gt=0)
    purged_train_row_count: int = Field(ge=0)
    train_target_counts: dict[str, int]
    metrics: FeatureWalkForwardMetrics
    predictions: tuple[FeatureWalkForwardPrediction, ...]

    @model_validator(mode="after")
    def validate_fold_accounting(self) -> FeatureWalkForwardFoldReport:
        if self.train_row_count + self.purged_train_row_count != self.train_candidate_row_count:
            raise ValueError("feature walk-forward fold train accounting does not reconcile")
        if self.validation_row_count != len(self.predictions):
            raise ValueError("feature walk-forward predictions do not match validation rows")
        if self.metrics.row_count != self.validation_row_count:
            raise ValueError("feature walk-forward metrics do not match validation rows")
        if sum(self.train_target_counts.values()) != self.train_row_count:
            raise ValueError("feature walk-forward train targets do not match rows")
        if any(prediction.fold_id != self.fold_id for prediction in self.predictions):
            raise ValueError("feature walk-forward prediction belongs to a different fold")
        return self


class FeatureWalkForwardReport(DomainModel):
    """Immutable four-purpose walk-forward evidence with final test isolation."""

    report_id: str
    report_version: Literal["selection-purpose-feature-walk-forward-v1"] = (
        "selection-purpose-feature-walk-forward-v1"
    )
    model_type: Literal["lightgbm"] = FEATURE_WALK_FORWARD_MODEL_TYPE
    registration_id: str
    registration_lifecycle: FeatureModelLifecycle
    model_version_id: str
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    feature_names: tuple[str, ...]
    class_labels: tuple[TrainingLabel, ...]
    registered_at: datetime
    evaluated_at: datetime
    registered_model_sha256: str
    registered_training_manifest_sha256: str
    feature_dataset_manifest_sha256: str
    source_label_dataset_sha256: str
    label_split_manifest_sha256: str
    final_test_start: date | None
    final_test_row_count: int = Field(ge=0)
    final_test_feature_partition_sha256: str
    final_test_label_partition_sha256: str
    purged_row_count: int = Field(ge=0)
    purged_partition_sha256: str
    registered_calibrated: bool = False
    registered_calibration_id: str | None = None
    registered_calibrated_at: datetime | None = None
    calibration_used: Literal[False] = False
    folds: tuple[FeatureWalkForwardFoldReport, ...]

    @field_validator(
        "report_id",
        "registration_id",
        "model_version_id",
        "label_contract_id",
        "feature_set_version_id",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature walk-forward report text must not be empty")
        return normalized

    @field_validator(
        "registered_model_sha256",
        "registered_training_manifest_sha256",
        "feature_dataset_manifest_sha256",
        "source_label_dataset_sha256",
        "label_split_manifest_sha256",
        "final_test_feature_partition_sha256",
        "final_test_label_partition_sha256",
        "purged_partition_sha256",
    )
    @classmethod
    def validate_digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _is_sha256(normalized):
            raise ValueError("feature walk-forward evidence must be a SHA-256 digest")
        return normalized

    @field_validator("registered_at", "evaluated_at", "registered_calibrated_at")
    @classmethod
    def validate_aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("feature walk-forward timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> FeatureWalkForwardReport:
        if self.report_version != FEATURE_WALK_FORWARD_REPORT_VERSION:
            raise ValueError("unsupported feature walk-forward report version")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("feature walk-forward label contract does not match purpose")
        if self.class_labels != _labels_for(self.purpose):
            raise ValueError("feature walk-forward class labels do not match purpose")
        if not self.feature_names or tuple(sorted(set(self.feature_names))) != self.feature_names:
            raise ValueError("feature walk-forward feature names must be sorted and non-empty")
        if self.registered_at > self.evaluated_at:
            raise ValueError("feature walk-forward evaluation cannot precede registration")
        if self.registered_calibrated:
            if self.registered_calibration_id is None or self.registered_calibrated_at is None:
                raise ValueError("calibrated registration context is incomplete")
            if self.registered_calibrated_at > self.evaluated_at:
                raise ValueError("feature walk-forward evaluation cannot precede calibration")
        elif (
            self.registered_calibration_id is not None or self.registered_calibrated_at is not None
        ):
            raise ValueError("uncalibrated registration context must not contain calibration")
        if self.final_test_row_count == 0 and self.final_test_start is not None:
            raise ValueError("empty final test partition must not have a start date")
        if self.final_test_row_count > 0 and self.final_test_start is None:
            raise ValueError("non-empty final test partition requires a start date")
        if not self.folds:
            raise ValueError("feature walk-forward report requires folds")
        fold_ids = tuple(fold.fold_id for fold in self.folds)
        if len(fold_ids) != len(set(fold_ids)):
            raise ValueError("feature walk-forward report contains duplicate fold IDs")
        expected_labels = {label.value for label in self.class_labels}
        for fold in self.folds:
            if set(fold.train_target_counts) != expected_labels:
                raise ValueError("feature walk-forward train labels do not match purpose")
            if set(fold.metrics.target_counts) != expected_labels:
                raise ValueError("feature walk-forward metric labels do not match purpose")
            if set(fold.metrics.mean_predicted_probabilities) != expected_labels:
                raise ValueError("feature walk-forward mean probabilities do not match purpose")
            for prediction in fold.predictions:
                if prediction.target.value not in expected_labels:
                    raise ValueError("feature walk-forward target does not match purpose")
                if prediction.predicted_label.value not in expected_labels:
                    raise ValueError("feature walk-forward predicted label does not match purpose")
                if set(prediction.predicted_probabilities) != expected_labels:
                    raise ValueError("feature walk-forward probabilities do not match purpose")
        sample_ids = tuple(
            prediction.sample_id for fold in self.folds for prediction in fold.predictions
        )
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("feature walk-forward report contains duplicate predictions")
        return self


class FeatureWalkForwardReportStore:
    """Store immutable purpose-aware walk-forward reports."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.report_dir = self.root / "models" / "feature-registry" / "walk-forward-reports"
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def write(self, report: FeatureWalkForwardReport) -> FeatureWalkForwardReport:
        """Write a report once; identical bytes are an idempotent retry."""

        _validate_report_identity(report)
        content = (report.model_dump_json(indent=2) + "\n").encode("utf-8")
        path = self._path(report.report_id)
        if path.exists():
            try:
                if path.read_bytes() != content:
                    raise ValueError("feature walk-forward report identity has different content")
            except OSError as exc:
                raise ValueError("failed to read existing feature walk-forward report") from exc
            return self.load(report.report_id)
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ValueError("failed to write feature walk-forward report") from exc
        return self.load(report.report_id)

    def load(self, report_id: str) -> FeatureWalkForwardReport:
        """Read and verify one immutable report."""

        normalized = _safe_identifier(report_id)
        path = self._path(normalized)
        try:
            report = FeatureWalkForwardReport.model_validate_json(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"feature walk-forward report does not exist: {normalized}") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"feature walk-forward report is invalid: {normalized}") from exc
        if report.report_id != normalized:
            raise ValueError("feature walk-forward report identity does not match filename")
        _validate_report_identity(report)
        return report

    def _path(self, report_id: str) -> Path:
        return self.report_dir / f"{_safe_identifier(report_id)}.json"


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _labels_for(purpose: TrainingDatasetPurpose) -> tuple[TrainingLabel, ...]:
    return _PURPOSE_LABEL_ORDER[purpose]


def _labels_for_target(target: TrainingLabel) -> tuple[TrainingLabel, ...]:
    for labels in _PURPOSE_LABEL_ORDER.values():
        if target in labels:
            return labels
    raise ValueError(f"unsupported training target: {target}")


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(
        character in normalized for character in ("/", "\\", "\0", "\r", "\n")
    ):
        raise ValueError("feature walk-forward report identifier contains an invalid path")
    return normalized


def _validate_report_identity(report: FeatureWalkForwardReport) -> None:
    data = report.model_dump(mode="json")
    report_id = data.pop("report_id")
    expected = f"feature-walk-forward-{sha256_json(data)[:20]}"
    if report_id != expected:
        raise ValueError("feature walk-forward report identity does not match content")
