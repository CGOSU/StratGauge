"""Purpose-aware, purged walk-forward validation for feature LightGBM models."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    TrainingLabel,
    label_contract_id_for,
)
from stratgauge.ml.training_feature_baseline import TrainingFeatureSplitInput
from stratgauge.ml.training_feature_lightgbm import FeatureLightGBMModel
from stratgauge.ml.training_feature_lightgbm_calibration import (
    FeatureCalibrationOutput,
    FeatureLightGBMOutput,
)
from stratgauge.ml.training_feature_registry import (
    FeatureModelLifecycle,
    FeatureModelRegistration,
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


class FeatureWalkForwardUnavailableError(RuntimeError):
    """The optional LightGBM or NumPy dependency is unavailable."""


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


def validate_feature_walk_forward(
    split_input: TrainingFeatureSplitInput,
    *,
    registration: FeatureModelRegistration,
    training_output: FeatureLightGBMOutput,
    calibration_output: FeatureCalibrationOutput | None,
    folds: Sequence[FeatureWalkForwardFoldSpec],
    evaluated_at: datetime,
) -> FeatureWalkForwardReport:
    """Run train-only LightGBM folds over the non-test feature partitions."""

    _require_aware(evaluated_at, "evaluated_at")
    _validate_registration_context(
        split_input,
        registration,
        training_output,
        calibration_output,
        evaluated_at,
    )
    ordered_folds = _validate_fold_inputs(split_input, folds)
    development_rows = split_input.train_rows + split_input.validation_rows
    test_rows = split_input.test_rows
    purged_ids = {row.sample_id for row in split_input.purged_rows}
    if any(row.sample_id in purged_ids for row in development_rows + test_rows):
        raise ValueError("purged labels must not appear in feature walk-forward rows")
    for row in development_rows + test_rows:
        if row.feature_available_at > evaluated_at:
            raise ValueError("feature availability follows evaluated_at")
    for fold in ordered_folds:
        validation_rows = _validation_rows(development_rows, fold)
        for row in validation_rows:
            if row.label_available_at > evaluated_at:
                raise ValueError(
                    f"validation label is unavailable at evaluated_at: {row.sample_id}"
                )
        train_candidates = _train_candidates(development_rows, fold)
        for row in train_candidates:
            if row.label_available_at > evaluated_at:
                raise ValueError(f"training label is unavailable at evaluated_at: {row.sample_id}")

    lightgbm = _load_lightgbm()
    numpy = _load_numpy()
    labels = _labels_for(split_input.manifest.purpose)
    model = training_output.model
    fold_reports = tuple(
        _evaluate_fold(
            development_rows,
            fold,
            labels=labels,
            model=model,
            lightgbm=lightgbm,
            numpy=numpy,
        )
        for fold in ordered_folds
    )
    first_test_date = min((row.observation_date for row in test_rows), default=None)
    report = FeatureWalkForwardReport(
        report_id="pending",
        registration_id=registration.registration_id,
        registration_lifecycle=registration.lifecycle,
        model_version_id=model.model_version_id,
        purpose=model.purpose,
        label_contract_id=model.label_contract_id,
        feature_set_version_id=model.feature_set_version_id,
        feature_names=model.feature_names,
        class_labels=model.class_labels,
        registered_at=registration.registered_at,
        evaluated_at=evaluated_at,
        registered_model_sha256=registration.training_model_sha256,
        registered_training_manifest_sha256=registration.training_manifest_sha256,
        feature_dataset_manifest_sha256=split_input.manifest_sha256,
        source_label_dataset_sha256=split_input.manifest.source_label_dataset_sha256,
        label_split_manifest_sha256=split_input.manifest.label_split_manifest_sha256,
        final_test_start=first_test_date,
        final_test_row_count=len(test_rows),
        final_test_feature_partition_sha256=split_input.manifest.partition_sha256["test"],
        final_test_label_partition_sha256=split_input.manifest.label_partition_sha256["test"],
        purged_row_count=len(split_input.purged_rows),
        purged_partition_sha256=split_input.manifest.partition_sha256["purged"],
        registered_calibrated=registration.calibrated,
        registered_calibration_id=registration.calibration_id,
        registered_calibrated_at=registration.calibrated_at,
        folds=fold_reports,
    )
    report_data = report.model_dump(mode="json")
    report_data.pop("report_id")
    report_id = f"feature-walk-forward-{_sha256_json(report_data)[:20]}"
    return report.model_copy(update={"report_id": report_id})


def _validate_registration_context(
    split_input: TrainingFeatureSplitInput,
    registration: FeatureModelRegistration,
    training_output: FeatureLightGBMOutput,
    calibration_output: FeatureCalibrationOutput | None,
    evaluated_at: datetime,
) -> None:
    model = training_output.model
    if model.model_type != FEATURE_WALK_FORWARD_MODEL_TYPE:
        raise ValueError("feature walk-forward requires a LightGBM registration")
    if registration.model_version_id != model.model_version_id:
        raise ValueError("registration model does not match training evidence")
    if registration.purpose is not split_input.manifest.purpose:
        raise ValueError("registration purpose does not match feature split")
    if model.purpose is not split_input.manifest.purpose:
        raise ValueError("training model purpose does not match feature split")
    if registration.label_contract_id != model.label_contract_id:
        raise ValueError("registration label contract does not match training evidence")
    if registration.feature_set_version_id != model.feature_set_version_id:
        raise ValueError("registration feature set does not match training evidence")
    if registration.feature_names != model.feature_names:
        raise ValueError("registration feature names do not match training evidence")
    if registration.class_labels != model.class_labels:
        raise ValueError("registration class labels do not match training evidence")
    expected = (
        (
            "label_contract_id",
            registration.label_contract_id,
            split_input.manifest.label_contract_id,
        ),
        (
            "feature_set_version_id",
            registration.feature_set_version_id,
            split_input.manifest.feature_set_version_id,
        ),
        ("feature_names", registration.feature_names, split_input.manifest.feature_names),
        (
            "class_labels",
            registration.class_labels,
            _labels_for(split_input.manifest.purpose),
        ),
        (
            "feature_dataset_manifest_sha256",
            registration.feature_dataset_manifest_sha256,
            split_input.manifest_sha256,
        ),
        (
            "source_label_dataset_sha256",
            registration.source_label_dataset_sha256,
            split_input.manifest.source_label_dataset_sha256,
        ),
        (
            "label_split_manifest_sha256",
            registration.label_split_manifest_sha256,
            split_input.manifest.label_split_manifest_sha256,
        ),
    )
    for name, actual, expected_value in expected:
        if actual != expected_value:
            raise ValueError(f"feature walk-forward registration does not match split: {name}")
    if model.feature_names != split_input.manifest.feature_names:
        raise ValueError("training model feature names do not match feature split")
    if model.class_labels != _labels_for(split_input.manifest.purpose):
        raise ValueError("training model class labels do not match purpose")
    if model.feature_dataset_manifest_sha256 != split_input.manifest_sha256:
        raise ValueError("training model feature manifest does not match feature split")
    if training_output.manifest_sha256 != registration.training_manifest_sha256:
        raise ValueError("training manifest hash does not match registration")
    if training_output.model_sha256 != registration.training_model_sha256:
        raise ValueError("training model hash does not match registration")
    if model.artifact_sha256 != registration.training_runtime_artifact_sha256:
        raise ValueError("training runtime artifact hash does not match registration")
    if training_output.metrics_sha256 != registration.training_metrics_sha256:
        raise ValueError("training metrics hash does not match registration")
    if training_output.predictions_sha256 != registration.training_predictions_sha256:
        raise ValueError("training predictions hash does not match registration")
    if registration.calibrated:
        if calibration_output is None:
            raise ValueError("calibrated registration is missing calibration evidence")
        if registration.calibration_id != calibration_output.model.calibration_id:
            raise ValueError("calibration identity does not match registration")
        if registration.calibrated_at != calibration_output.model.calibrated_at:
            raise ValueError("calibration time does not match registration")
        calibration_hashes = (
            (
                "calibration_sha256",
                registration.calibration_sha256,
                calibration_output.calibration_sha256,
            ),
            (
                "calibration_metrics_sha256",
                registration.calibration_metrics_sha256,
                calibration_output.metrics_sha256,
            ),
            (
                "calibrated_predictions_sha256",
                registration.calibrated_predictions_sha256,
                calibration_output.calibrated_predictions_sha256,
            ),
            (
                "calibration_manifest_sha256",
                registration.calibration_manifest_sha256,
                calibration_output.manifest_sha256,
            ),
        )
        for name, registered_hash, actual_hash in calibration_hashes:
            if registered_hash != actual_hash:
                raise ValueError(f"calibration hash does not match registration: {name}")
    elif calibration_output is not None:
        raise ValueError("uncalibrated registration has unexpected calibration evidence")
    if registration.registered_at > evaluated_at:
        raise ValueError("evaluated_at cannot precede registration time")
    if registration.calibrated and registration.calibrated_at is not None:
        if registration.calibrated_at > evaluated_at:
            raise ValueError("evaluated_at cannot precede calibration time")


def _validate_fold_inputs(
    split_input: TrainingFeatureSplitInput,
    folds: Sequence[FeatureWalkForwardFoldSpec],
) -> tuple[FeatureWalkForwardFoldSpec, ...]:
    if not folds:
        raise ValueError("feature walk-forward requires at least one fold")
    fold_ids = tuple(fold.fold_id for fold in folds)
    if len(fold_ids) != len(set(fold_ids)):
        raise ValueError("feature walk-forward fold IDs must be unique")
    ordered = tuple(sorted(folds, key=lambda fold: (fold.validation_start, fold.fold_id)))
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current.validation_start <= previous.validation_end:
            raise ValueError("feature walk-forward validation windows must not overlap")
    development_rows = split_input.train_rows + split_input.validation_rows
    test_start = min((row.observation_date for row in split_input.test_rows), default=None)
    development_start = min((row.observation_date for row in development_rows), default=None)
    development_end = max((row.observation_date for row in development_rows), default=None)
    if development_start is None or development_end is None:
        raise ValueError("feature walk-forward development partitions contain no rows")
    for fold in ordered:
        if test_start is not None and fold.train_end >= test_start:
            raise ValueError("feature walk-forward training overlaps final test period")
        if test_start is not None and fold.validation_end >= test_start:
            raise ValueError("feature walk-forward validation overlaps final test period")
        if fold.validation_start < development_start:
            raise ValueError("feature walk-forward validation starts before development data")
        if fold.train_start is not None and fold.train_start > development_end:
            raise ValueError("feature walk-forward train_start follows development data")
        if not _validation_rows(development_rows, fold):
            raise ValueError(f"feature walk-forward fold has no validation rows: {fold.fold_id}")
        candidates = _train_candidates(development_rows, fold)
        if not candidates:
            raise ValueError(f"feature walk-forward fold has no training rows: {fold.fold_id}")
        usable = tuple(
            row for row in candidates if row.label_available_at.date() < fold.validation_start
        )
        if not usable:
            raise ValueError(
                "feature walk-forward fold has no labels available before validation: "
                f"{fold.fold_id}"
            )
    return ordered


def _evaluate_fold(
    development_rows: Sequence[Any],
    fold: FeatureWalkForwardFoldSpec,
    *,
    labels: tuple[TrainingLabel, ...],
    model: FeatureLightGBMModel,
    lightgbm: Any,
    numpy: Any,
) -> FeatureWalkForwardFoldReport:
    candidates = _train_candidates(development_rows, fold)
    train_rows = tuple(
        row for row in candidates if row.label_available_at.date() < fold.validation_start
    )
    validation_rows = _validation_rows(development_rows, fold)
    target_counts = {label.value: 0 for label in labels}
    for row in train_rows:
        target_counts[row.target.value] += 1
    if any(target_counts[label.value] == 0 for label in labels):
        raise ValueError(
            f"feature walk-forward training labels have one or more missing classes: {fold.fold_id}"
        )
    parameters = dict(model.parameters)
    raw_boost_rounds = parameters.pop("num_boost_round", None)
    if not isinstance(raw_boost_rounds, (int, float)) or isinstance(raw_boost_rounds, bool):
        raise ValueError("registered LightGBM parameters require a positive num_boost_round")
    boost_rounds = int(raw_boost_rounds)
    if boost_rounds <= 0:
        raise ValueError("registered LightGBM num_boost_round must be positive")
    dataset = lightgbm.Dataset(
        numpy.asarray(_matrix(train_rows, model.feature_names), dtype=float),
        label=[labels.index(row.target) for row in train_rows],
        feature_name=list(model.feature_names),
        free_raw_data=False,
    )
    booster = lightgbm.train(parameters, dataset, num_boost_round=boost_rounds)
    probabilities = _predict_probabilities(
        booster,
        validation_rows,
        model.feature_names,
        labels,
        numpy,
    )
    metrics = _metrics(validation_rows, probabilities, labels)
    predictions = tuple(
        FeatureWalkForwardPrediction(
            fold_id=fold.fold_id,
            sample_id=row.sample_id,
            candidate_id=row.candidate_id,
            symbol=row.symbol,
            observation_date=row.observation_date,
            label_available_at=row.label_available_at,
            target=row.target,
            predicted_label=labels[
                max(range(len(labels)), key=lambda index: (probability[index], -index))
            ],
            predicted_probabilities={
                label.value: probability[index] for index, label in enumerate(labels)
            },
            feature_snapshot_id=row.feature_snapshot_id,
            source_label_row_sha256=row.source_label_row_sha256,
        )
        for row, probability in zip(validation_rows, probabilities, strict=True)
    )
    return FeatureWalkForwardFoldReport(
        fold_id=fold.fold_id,
        train_start=fold.train_start,
        train_end=fold.train_end,
        validation_start=fold.validation_start,
        validation_end=fold.validation_end,
        train_candidate_row_count=len(candidates),
        train_row_count=len(train_rows),
        validation_row_count=len(validation_rows),
        purged_train_row_count=len(candidates) - len(train_rows),
        train_target_counts=target_counts,
        metrics=metrics,
        predictions=predictions,
    )


def _train_candidates(
    rows: Sequence[Any],
    fold: FeatureWalkForwardFoldSpec,
) -> tuple[Any, ...]:
    return tuple(
        row
        for row in rows
        if (fold.train_start is None or row.observation_date >= fold.train_start)
        and row.observation_date <= fold.train_end
    )


def _validation_rows(
    rows: Sequence[Any],
    fold: FeatureWalkForwardFoldSpec,
) -> tuple[Any, ...]:
    return tuple(
        row for row in rows if fold.validation_start <= row.observation_date <= fold.validation_end
    )


def _metrics(
    rows: Sequence[Any],
    probabilities: Sequence[Sequence[Decimal]],
    labels: tuple[TrainingLabel, ...],
) -> FeatureWalkForwardMetrics:
    if not rows or len(rows) != len(probabilities):
        raise ValueError("feature walk-forward validation probabilities do not match rows")
    target_counts = {label.value: 0 for label in labels}
    mean_probabilities = {label.value: Decimal("0") for label in labels}
    correct = 0
    log_loss = Decimal("0")
    brier = Decimal("0")
    for row, probability_row in zip(rows, probabilities, strict=True):
        target_counts[row.target.value] += 1
        target_index = labels.index(row.target)
        predicted_index = max(
            range(len(labels)), key=lambda index: (probability_row[index], -index)
        )
        correct += predicted_index == target_index
        for index, label in enumerate(labels):
            mean_probabilities[label.value] += probability_row[index]
            brier += (probability_row[index] - int(index == target_index)) ** 2
        log_loss -= Decimal(str(math.log(max(float(probability_row[target_index]), 1e-15))))
    mean_probabilities = {
        name: value / Decimal(len(rows)) for name, value in mean_probabilities.items()
    }
    return FeatureWalkForwardMetrics(
        row_count=len(rows),
        coverage=Decimal("1"),
        target_counts=target_counts,
        mean_predicted_probabilities=mean_probabilities,
        log_loss=float(log_loss / Decimal(len(rows))),
        normalized_brier_score=float(brier / Decimal(len(rows) * len(labels))),
        accuracy=Decimal(correct) / Decimal(len(rows)),
    )


def _predict_probabilities(
    booster: Any,
    rows: Sequence[Any],
    feature_names: Sequence[str],
    labels: tuple[TrainingLabel, ...],
    numpy: Any,
) -> tuple[tuple[Decimal, ...], ...]:
    raw = booster.predict(numpy.asarray(_matrix(rows, feature_names), dtype=float))
    values = raw.tolist() if hasattr(raw, "tolist") else raw
    if len(labels) == 2:
        rows_of_values = _binary_probability_rows(values, len(rows))
    else:
        rows_of_values = _multiclass_probability_rows(values, len(rows), len(labels))
    return tuple(_normalize_probability_row(row, len(labels)) for row in rows_of_values)


def _binary_probability_rows(values: Any, row_count: int) -> list[Sequence[Any]]:
    if len(values) != row_count:
        raise ValueError("feature walk-forward binary prediction count does not match rows")
    rows: list[Sequence[Any]] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            if len(value) != 2:
                raise ValueError("feature walk-forward binary prediction has invalid classes")
            rows.append((value[0], value[1]))
        else:
            rows.append((1 - value, value))
    return rows


def _multiclass_probability_rows(
    values: Any,
    row_count: int,
    class_count: int,
) -> list[Sequence[Any]]:
    if len(values) == row_count and all(isinstance(row, (list, tuple)) for row in values):
        rows: list[Sequence[Any]] = [tuple(row) for row in values]
    elif len(values) == row_count * class_count:
        rows = [
            tuple(values[index : index + class_count])
            for index in range(0, len(values), class_count)
        ]
    else:
        raise ValueError("feature walk-forward multiclass prediction shape does not match rows")
    if any(len(row) != class_count for row in rows):
        raise ValueError("feature walk-forward multiclass prediction has invalid classes")
    return rows


def _normalize_probability_row(values: Sequence[Any], class_count: int) -> tuple[Decimal, ...]:
    probabilities: list[Decimal] = []
    for value in values:
        try:
            probability = Decimal(str(float(value)))
        except (TypeError, ValueError):
            raise ValueError("feature walk-forward produced an invalid probability") from None
        if not probability.is_finite() or probability < 0 or probability > 1:
            raise ValueError("feature walk-forward produced an invalid probability")
        probabilities.append(probability)
    if len(probabilities) != class_count:
        raise ValueError("feature walk-forward produced an invalid probability vector")
    total = sum(probabilities, Decimal("0"))
    if total <= 0 or abs(total - Decimal("1")) > Decimal("1e-6"):
        raise ValueError("feature walk-forward probabilities must sum to one")
    return tuple(probability / total for probability in probabilities)


def _matrix(rows: Sequence[Any], feature_names: Sequence[str]) -> list[list[float]]:
    return [[float(row.features[name]) for name in feature_names] for row in rows]


def _labels_for(purpose: TrainingDatasetPurpose) -> tuple[TrainingLabel, ...]:
    return _PURPOSE_LABEL_ORDER[purpose]


def _labels_for_target(target: TrainingLabel) -> tuple[TrainingLabel, ...]:
    for labels in _PURPOSE_LABEL_ORDER.values():
        if target in labels:
            return labels
    raise ValueError(f"unsupported training target: {target}")


def _load_lightgbm() -> Any:
    try:
        return import_module("lightgbm")
    except (ImportError, OSError) as exc:
        raise FeatureWalkForwardUnavailableError(
            "LightGBM is unavailable; run `uv sync --extra ml` before walk-forward validation"
        ) from exc


def _load_numpy() -> Any:
    try:
        return import_module("numpy")
    except (ImportError, OSError) as exc:
        raise FeatureWalkForwardUnavailableError(
            "NumPy is unavailable; run `uv sync --extra ml` before walk-forward validation"
        ) from exc


def _validate_report_identity(report: FeatureWalkForwardReport) -> None:
    data = report.model_dump(mode="json")
    report_id = data.pop("report_id")
    expected = f"feature-walk-forward-{_sha256_json(data)[:20]}"
    if report_id != expected:
        raise ValueError("feature walk-forward report identity does not match content")


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(
        character in normalized for character in ("/", "\\", "\0", "\r", "\n")
    ):
        raise ValueError("feature walk-forward report identifier contains an invalid path")
    return normalized


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _sha256_json(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
