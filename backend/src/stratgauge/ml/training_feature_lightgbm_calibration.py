"""Purpose-aware validation-only calibration for feature LightGBM outputs."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml._hashing import sha256_bytes, sha256_json
from stratgauge.ml.isotonic import IsotonicBin, apply_isotonic_bin, fit_isotonic_bins
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    TrainingLabel,
    label_contract_id_for,
)
from stratgauge.ml.training_feature_lightgbm import (
    FEATURE_LIGHTGBM_EVALUATION_VERSION,
    FEATURE_LIGHTGBM_MODEL_TYPE,
    FEATURE_LIGHTGBM_REPORT_VERSION,
    FeatureLightGBMEvaluationEvidence,
    FeatureLightGBMModel,
    FeatureLightGBMPrediction,
)

FEATURE_CALIBRATION_VERSION = "selection-purpose-lightgbm-calibration-v1"
FEATURE_CALIBRATION_EVALUATION_VERSION = "selection-purpose-lightgbm-calibration-evaluation-v1"


class FeatureCalibrationError(ValueError):
    """Feature LightGBM calibration input or output is invalid."""


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
    calibration_split: Literal["validation"] = "validation"
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
        expected_labels = _labels_for(self.purpose)
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

    split: Literal["validation", "test"]
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

    split: Literal["validation", "test"]
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


def read_feature_calibration_output(output_dir: str | Path) -> FeatureCalibrationOutput:
    """Read and verify every artifact in one purpose-aware calibration directory."""

    directory = Path(output_dir)
    try:
        calibration_content = (directory / "calibration.json").read_bytes()
        metrics_content = (directory / "metrics.json").read_bytes()
        predictions_content = (directory / "calibrated_predictions.jsonl").read_bytes()
        manifest_content = (directory / "manifest.json").read_bytes()
        model = FeatureCalibratedModel.model_validate_json(calibration_content)
        metrics = FeatureCalibrationEvaluationEvidence.model_validate_json(metrics_content)
        predictions = _read_calibrated_predictions(predictions_content)
        manifest = _read_json_object(manifest_content, "feature calibration manifest")
    except (OSError, TypeError, ValueError) as exc:
        raise FeatureCalibrationError(
            f"invalid feature calibration output: {directory}: {exc}"
        ) from exc

    calibration_sha256 = sha256_bytes(calibration_content)
    metrics_sha256 = sha256_bytes(metrics_content)
    calibrated_predictions_sha256 = sha256_bytes(predictions_content)
    manifest_sha256 = sha256_bytes(manifest_content)
    _validate_calibration_output(
        model,
        metrics,
        predictions,
        manifest,
        calibration_sha256=calibration_sha256,
        metrics_sha256=metrics_sha256,
        calibrated_predictions_sha256=calibrated_predictions_sha256,
    )
    return FeatureCalibrationOutput(
        model=model,
        metrics=metrics,
        predictions=predictions,
        manifest=manifest,
        calibration_sha256=calibration_sha256,
        metrics_sha256=metrics_sha256,
        calibrated_predictions_sha256=calibrated_predictions_sha256,
        manifest_sha256=manifest_sha256,
    )


def read_feature_lightgbm_output(output_dir: str | Path) -> FeatureLightGBMOutput:
    """Read and verify all model, evaluation, prediction and manifest evidence."""

    directory = Path(output_dir)
    try:
        model_content = (directory / "model.json").read_bytes()
        artifact_content = (directory / "model.txt").read_bytes()
        metrics_content = (directory / "metrics.json").read_bytes()
        predictions_content = (directory / "predictions.jsonl").read_bytes()
        manifest_content = (directory / "manifest.json").read_bytes()
        model = FeatureLightGBMModel.model_validate_json(model_content)
        metrics = FeatureLightGBMEvaluationEvidence.model_validate_json(metrics_content)
        manifest = _read_json_object(manifest_content, "feature LightGBM manifest")
        predictions = _read_predictions(predictions_content)
    except (OSError, TypeError, ValueError) as exc:
        raise FeatureCalibrationError(
            f"invalid feature LightGBM output: {directory}: {exc}"
        ) from exc

    model_sha256 = sha256_bytes(model_content)
    metrics_sha256 = sha256_bytes(metrics_content)
    predictions_sha256 = sha256_bytes(predictions_content)
    manifest_sha256 = sha256_bytes(manifest_content)
    _validate_output_manifest(
        model,
        metrics,
        predictions,
        manifest,
        artifact_content=artifact_content,
        model_sha256=model_sha256,
        metrics_sha256=metrics_sha256,
        predictions_sha256=predictions_sha256,
    )
    return FeatureLightGBMOutput(
        model=model,
        metrics=metrics,
        predictions=predictions,
        manifest=manifest,
        model_sha256=model_sha256,
        metrics_sha256=metrics_sha256,
        predictions_sha256=predictions_sha256,
        manifest_sha256=manifest_sha256,
    )


def _read_calibrated_predictions(content: bytes) -> tuple[FeatureCalibratedPrediction, ...]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise FeatureCalibrationError("feature calibrated predictions are not UTF-8") from exc
    predictions: list[FeatureCalibratedPrediction] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("calibrated prediction row must be a JSON object")
            predictions.append(FeatureCalibratedPrediction.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise FeatureCalibrationError(
                f"invalid feature calibrated prediction at line {line_number}: {exc}"
            ) from exc
    if not predictions:
        raise FeatureCalibrationError("feature calibrated predictions contain no rows")
    return tuple(predictions)


def calibrate_feature_lightgbm(
    model: FeatureLightGBMModel,
    predictions: Sequence[FeatureLightGBMPrediction],
    *,
    source_prediction_sha256: str,
    source_model_sha256: str,
    source_manifest_sha256: str,
    calibrated_at: datetime,
    minimum_validation_samples: int = 100,
) -> FeatureCalibrationResult:
    """Fit validation-only PAVA and apply it to validation/test predictions."""

    _validate_calibration_inputs(
        model,
        predictions,
        source_prediction_sha256=source_prediction_sha256,
        source_model_sha256=source_model_sha256,
        source_manifest_sha256=source_manifest_sha256,
        calibrated_at=calibrated_at,
        minimum_validation_samples=minimum_validation_samples,
    )
    validation = tuple(row for row in predictions if row.split == "validation")
    test = tuple(row for row in predictions if row.split == "test")
    labels = _labels_for(model.purpose)
    positive_label = labels[-1]
    bins_by_label: dict[TrainingLabel, tuple[IsotonicBin, ...]] = {
        positive_label: fit_isotonic_bins(
            tuple(row.predicted_probabilities[positive_label.value] for row in validation),
            tuple(row.target is positive_label for row in validation),
        )
    }
    if len(labels) > 2:
        bins_by_label.update(
            {
                label: fit_isotonic_bins(
                    tuple(row.predicted_probabilities[label.value] for row in validation),
                    tuple(row.target is label for row in validation),
                )
                for label in labels[:-1]
            }
        )
    calibrated_predictions = tuple(
        _calibrate_prediction(row, labels, bins_by_label) for row in (*validation, *test)
    )
    target_counts = {label.value: 0 for label in labels}
    for row in validation:
        target_counts[row.target.value] += 1
    identity = {
        "base_model_version_id": model.model_version_id,
        "bins": {
            label.value: [_bin_identity(item) for item in bins_by_label[label]]
            for label in bins_by_label
        },
        "calibrated_at": calibrated_at.isoformat(),
        "calibration_date_end": max(row.observation_date for row in validation).isoformat(),
        "calibration_date_start": min(row.observation_date for row in validation).isoformat(),
        "calibration_version": FEATURE_CALIBRATION_VERSION,
        "feature_names": model.feature_names,
        "feature_set_version_id": model.feature_set_version_id,
        "label_contract_id": model.label_contract_id,
        "purpose": model.purpose.value,
        "source_manifest_sha256": source_manifest_sha256,
        "source_model_sha256": source_model_sha256,
        "source_prediction_sha256": source_prediction_sha256,
    }
    calibration_id = f"feature-calibration-{sha256_json(identity)[:20]}"
    metrics = tuple(
        _metrics(split, tuple(row for row in calibrated_predictions if row.split == split), labels)
        for split in ("validation", "test")
    )
    calibration_model = FeatureCalibratedModel(
        calibration_id=calibration_id,
        base_model_version_id=model.model_version_id,
        purpose=model.purpose,
        label_contract_id=model.label_contract_id,
        feature_set_version_id=model.feature_set_version_id,
        feature_names=model.feature_names,
        calibrated_at=calibrated_at,
        calibration_row_count=len(validation),
        calibration_target_counts=target_counts,
        calibration_date_start=min(row.observation_date for row in validation),
        calibration_date_end=max(row.observation_date for row in validation),
        test_row_count=len(test),
        source_prediction_sha256=source_prediction_sha256,
        source_model_sha256=source_model_sha256,
        source_manifest_sha256=source_manifest_sha256,
        bins=tuple(
            FeatureCalibrationBin(
                label=label,
                lower_probability=item.lower_probability,
                upper_probability=item.upper_probability,
                calibrated_probability=item.calibrated_probability,
                sample_count=item.sample_count,
                positive_count=item.positive_count,
            )
            for label in bins_by_label
            for item in bins_by_label[label]
        ),
    )
    return FeatureCalibrationResult(
        model=calibration_model,
        metrics=metrics,
        predictions=calibrated_predictions,
        source_prediction_sha256=source_prediction_sha256,
        source_model_sha256=source_model_sha256,
        source_manifest_sha256=source_manifest_sha256,
    )


def write_feature_calibration_result(
    output_dir: str | Path,
    result: FeatureCalibrationResult,
) -> None:
    """Write calibrated artifacts and hashes without overwriting prior evidence."""

    calibration_content = (result.model.model_dump_json(indent=2) + "\n").encode("utf-8")
    metrics_content = (
        FeatureCalibrationEvaluationEvidence(
            model_version_id=result.model.calibration_id,
            purpose=result.model.purpose,
            label_contract_id=result.model.label_contract_id,
            feature_set_version_id=result.model.feature_set_version_id,
            source_prediction_sha256=result.source_prediction_sha256,
            source_model_sha256=result.source_model_sha256,
            source_manifest_sha256=result.source_manifest_sha256,
            metrics=result.metrics,
        ).model_dump_json(indent=2)
        + "\n"
    ).encode("utf-8")
    predictions_content = "".join(
        f"{prediction.model_dump_json()}\n" for prediction in result.predictions
    ).encode("utf-8")
    outputs = {
        "calibration.json": calibration_content,
        "metrics.json": metrics_content,
        "calibrated_predictions.jsonl": predictions_content,
        "manifest.json": (
            json.dumps(
                {
                    "calibration_version": FEATURE_CALIBRATION_VERSION,
                    "calibration_id": result.model.calibration_id,
                    "base_model_version_id": result.model.base_model_version_id,
                    "purpose": result.model.purpose.value,
                    "label_contract_id": result.model.label_contract_id,
                    "feature_set_version_id": result.model.feature_set_version_id,
                    "feature_names": result.model.feature_names,
                    "source_prediction_sha256": result.source_prediction_sha256,
                    "source_model_sha256": result.source_model_sha256,
                    "source_manifest_sha256": result.source_manifest_sha256,
                    "calibration_sha256": sha256_bytes(calibration_content),
                    "metrics_sha256": sha256_bytes(metrics_content),
                    "calibrated_predictions_sha256": sha256_bytes(predictions_content),
                    "calibrated": True,
                    "calibration_split": "validation",
                    "calibration_row_count": result.model.calibration_row_count,
                    "test_row_count": result.model.test_row_count,
                    "calibrated_at": result.model.calibrated_at.isoformat(),
                    "calibration_date_start": result.model.calibration_date_start.isoformat(),
                    "calibration_date_end": result.model.calibration_date_end.isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8"),
    }
    _write_outputs(Path(output_dir), outputs)


def _validate_calibration_output(
    model: FeatureCalibratedModel,
    metrics: FeatureCalibrationEvaluationEvidence,
    predictions: Sequence[FeatureCalibratedPrediction],
    manifest: Mapping[str, object],
    *,
    calibration_sha256: str,
    metrics_sha256: str,
    calibrated_predictions_sha256: str,
) -> None:
    if manifest.get("calibration_version") != FEATURE_CALIBRATION_VERSION:
        raise FeatureCalibrationError("unsupported feature calibration manifest version")
    for name, expected in (
        ("calibration_id", model.calibration_id),
        ("base_model_version_id", model.base_model_version_id),
        ("purpose", model.purpose.value),
        ("label_contract_id", model.label_contract_id),
        ("feature_set_version_id", model.feature_set_version_id),
        ("source_prediction_sha256", model.source_prediction_sha256),
        ("source_model_sha256", model.source_model_sha256),
        ("source_manifest_sha256", model.source_manifest_sha256),
        ("calibration_sha256", calibration_sha256),
        ("metrics_sha256", metrics_sha256),
        ("calibrated_predictions_sha256", calibrated_predictions_sha256),
    ):
        _require_manifest_equal(manifest, name, expected)
    if manifest.get("calibrated") is not True or manifest.get("calibration_split") != "validation":
        raise FeatureCalibrationError("feature calibration manifest is not validation-only")
    if _manifest_sequence(manifest, "feature_names") != model.feature_names:
        raise FeatureCalibrationError("feature calibration manifest feature columns do not match")
    if manifest.get("calibration_row_count") != model.calibration_row_count:
        raise FeatureCalibrationError("feature calibration manifest row count does not match")
    if manifest.get("test_row_count") != model.test_row_count:
        raise FeatureCalibrationError("feature calibration manifest test count does not match")
    if manifest.get("calibrated_at") != model.calibrated_at.isoformat():
        raise FeatureCalibrationError("feature calibration manifest timestamp does not match")
    if manifest.get("calibration_date_start") != model.calibration_date_start.isoformat():
        raise FeatureCalibrationError("feature calibration manifest start date does not match")
    if manifest.get("calibration_date_end") != model.calibration_date_end.isoformat():
        raise FeatureCalibrationError("feature calibration manifest end date does not match")
    if (
        metrics.evaluation_version != FEATURE_CALIBRATION_EVALUATION_VERSION
        or metrics.model_version_id != model.calibration_id
        or metrics.purpose is not model.purpose
        or metrics.label_contract_id != model.label_contract_id
        or metrics.feature_set_version_id != model.feature_set_version_id
        or metrics.source_prediction_sha256 != model.source_prediction_sha256
        or metrics.source_model_sha256 != model.source_model_sha256
        or metrics.source_manifest_sha256 != model.source_manifest_sha256
        or not metrics.calibrated
    ):
        raise FeatureCalibrationError("feature calibration evaluation evidence does not match")
    _validate_calibrated_prediction_evidence(model, metrics, predictions)


def _validate_calibrated_prediction_evidence(
    model: FeatureCalibratedModel,
    metrics: FeatureCalibrationEvaluationEvidence,
    predictions: Sequence[FeatureCalibratedPrediction],
) -> None:
    labels = _labels_for(model.purpose)
    expected_count = model.calibration_row_count + model.test_row_count
    if len(predictions) != expected_count:
        raise FeatureCalibrationError("feature calibrated prediction count does not match model")
    sample_ids = tuple(row.sample_id for row in predictions)
    if len(sample_ids) != len(set(sample_ids)):
        raise FeatureCalibrationError("feature calibrated predictions contain duplicate sample IDs")
    split_counts = {"validation": 0, "test": 0}
    target_counts = {
        "validation": {label.value: 0 for label in labels},
        "test": {label.value: 0 for label in labels},
    }
    validation_dates: list[date] = []
    test_dates: list[date] = []
    for row in predictions:
        if set(row.predicted_probabilities) != {label.value for label in labels}:
            raise FeatureCalibrationError("feature calibrated prediction classes do not match")
        if row.label not in labels or row.predicted_label not in labels:
            raise FeatureCalibrationError("feature calibrated prediction label does not match")
        if row.label_available_at > model.calibrated_at:
            raise FeatureCalibrationError(
                "feature calibrated prediction label availability follows calibration time"
            )
        if not row.feature_snapshot_id.strip() or not _is_sha256(row.source_label_row_sha256):
            raise FeatureCalibrationError("feature calibrated prediction lineage is incomplete")
        (validation_dates if row.split == "validation" else test_dates).append(row.observation_date)
        split_counts[row.split] += 1
        target_counts[row.split][row.label.value] += 1
    if test_dates and validation_dates and min(test_dates) <= max(validation_dates):
        raise FeatureCalibrationError(
            "feature calibrated test predictions overlap validation period"
        )
    if split_counts != {"validation": model.calibration_row_count, "test": model.test_row_count}:
        raise FeatureCalibrationError("feature calibrated prediction partitions do not match")
    metrics_by_split = {metric.split: metric for metric in metrics.metrics}
    if set(metrics_by_split) != {"validation", "test"}:
        raise FeatureCalibrationError("feature calibration metrics are incomplete")
    for split in ("validation", "test"):
        metric = metrics_by_split[split]
        if metric.row_count != split_counts[split] or metric.target_counts != target_counts[split]:
            raise FeatureCalibrationError("feature calibration metric counts do not match")


def _validate_output_manifest(
    model: FeatureLightGBMModel,
    metrics: FeatureLightGBMEvaluationEvidence,
    predictions: Sequence[FeatureLightGBMPrediction],
    manifest: Mapping[str, object],
    *,
    artifact_content: bytes,
    model_sha256: str,
    metrics_sha256: str,
    predictions_sha256: str,
) -> None:
    if manifest.get("report_version") != FEATURE_LIGHTGBM_REPORT_VERSION:
        raise FeatureCalibrationError("feature calibration requires a feature LightGBM manifest")
    _require_manifest_equal(manifest, "model_type", FEATURE_LIGHTGBM_MODEL_TYPE)
    for name, expected in (
        ("model_version_id", model.model_version_id),
        ("purpose", model.purpose.value),
        ("label_contract_id", model.label_contract_id),
        ("feature_set_version_id", model.feature_set_version_id),
        ("feature_dataset_manifest_sha256", model.feature_dataset_manifest_sha256),
        ("source_label_dataset_sha256", model.source_label_dataset_sha256),
        ("label_split_manifest_sha256", model.label_split_manifest_sha256),
        ("artifact_sha256", model.artifact_sha256),
        ("model_sha256", model_sha256),
        ("metrics_sha256", metrics_sha256),
        ("predictions_sha256", predictions_sha256),
    ):
        _require_manifest_equal(manifest, name, expected)
    if _manifest_sequence(manifest, "feature_names") != model.feature_names:
        raise FeatureCalibrationError(
            "feature LightGBM manifest feature columns do not match model"
        )
    if _manifest_sequence(manifest, "class_labels") != tuple(
        label.value for label in _labels_for(model.purpose)
    ):
        raise FeatureCalibrationError("feature LightGBM manifest classes do not match purpose")
    if manifest.get("calibrated") is not False or manifest.get("purged_rows_excluded") is not True:
        raise FeatureCalibrationError(
            "feature LightGBM output must be uncalibrated and purged-safe"
        )
    if manifest.get("row_counts") != model.row_counts:
        raise FeatureCalibrationError("feature LightGBM manifest row counts do not match model")
    if manifest.get("purged_row_count") != model.purged_row_count:
        raise FeatureCalibrationError("feature LightGBM manifest purged count does not match model")
    if sha256_bytes(artifact_content) != model.artifact_sha256:
        raise FeatureCalibrationError("feature LightGBM runtime artifact hash does not match model")
    if (
        metrics.evaluation_version != FEATURE_LIGHTGBM_EVALUATION_VERSION
        or metrics.model_version_id != model.model_version_id
        or metrics.model_type != FEATURE_LIGHTGBM_MODEL_TYPE
        or metrics.purpose is not model.purpose
        or metrics.label_contract_id != model.label_contract_id
        or metrics.feature_set_version_id != model.feature_set_version_id
        or metrics.feature_dataset_manifest_sha256 != model.feature_dataset_manifest_sha256
        or metrics.calibrated
    ):
        raise FeatureCalibrationError("feature LightGBM evaluation evidence does not match model")
    _validate_prediction_evidence(model, metrics, predictions)


def _validate_prediction_evidence(
    model: FeatureLightGBMModel,
    metrics: FeatureLightGBMEvaluationEvidence,
    predictions: Sequence[FeatureLightGBMPrediction],
) -> None:
    labels = _labels_for(model.purpose)
    expected_probability_keys = {label.value for label in labels}
    expected_count = model.row_counts["validation"] + model.row_counts["test"]
    if len(predictions) != expected_count:
        raise FeatureCalibrationError("feature LightGBM prediction count does not match manifest")
    sample_ids = tuple(row.sample_id for row in predictions)
    if len(sample_ids) != len(set(sample_ids)):
        raise FeatureCalibrationError("feature LightGBM predictions contain duplicate sample IDs")
    counts_by_split: dict[str, int] = {"validation": 0, "test": 0}
    target_counts_by_split: dict[str, dict[str, int]] = {
        "validation": {label.value: 0 for label in labels},
        "test": {label.value: 0 for label in labels},
    }
    for row in predictions:
        if set(row.predicted_probabilities) != expected_probability_keys:
            raise FeatureCalibrationError(
                "feature LightGBM prediction classes do not match purpose"
            )
        if row.target not in labels or row.predicted_label not in labels:
            raise FeatureCalibrationError(
                "feature LightGBM prediction label does not match purpose"
            )
        if abs(sum(row.predicted_probabilities.values(), Decimal("0")) - Decimal("1")) > Decimal(
            "1e-6"
        ):
            raise FeatureCalibrationError(
                "feature LightGBM prediction probabilities do not sum to one"
            )
        if not row.source_label_row_sha256.strip() or not _is_sha256(row.source_label_row_sha256):
            raise FeatureCalibrationError("feature LightGBM prediction label lineage is invalid")
        counts_by_split[row.split] += 1
        target_counts_by_split[row.split][row.target.value] += 1
    expected_counts = {name: model.row_counts[name] for name in ("validation", "test")}
    if counts_by_split != expected_counts:
        raise FeatureCalibrationError(
            "feature LightGBM prediction partitions do not match manifest"
        )
    metric_by_split = {metric.split: metric for metric in metrics.metrics}
    if set(metric_by_split) != {"train", "validation", "test"}:
        raise FeatureCalibrationError("feature LightGBM evaluation metrics are incomplete")
    if metric_by_split["train"].row_count != model.row_counts["train"]:
        raise FeatureCalibrationError("feature LightGBM train metrics do not match model")
    if metric_by_split["train"].target_counts != model.train_target_counts:
        raise FeatureCalibrationError("feature LightGBM train targets do not match model")
    for split in ("validation", "test"):
        metric = metric_by_split[split]
        if metric.row_count != model.row_counts[split]:
            raise FeatureCalibrationError(
                "feature LightGBM evaluation row count does not match model"
            )
        if metric.target_counts != target_counts_by_split[split]:
            raise FeatureCalibrationError(
                "feature LightGBM evaluation targets do not match predictions"
            )


def _validate_calibration_inputs(
    model: FeatureLightGBMModel,
    predictions: Sequence[FeatureLightGBMPrediction],
    *,
    source_prediction_sha256: str,
    source_model_sha256: str,
    source_manifest_sha256: str,
    calibrated_at: datetime,
    minimum_validation_samples: int,
) -> None:
    if model.model_type != FEATURE_LIGHTGBM_MODEL_TYPE:
        raise FeatureCalibrationError("feature calibration requires a feature LightGBM model")
    if model.report_version != FEATURE_LIGHTGBM_REPORT_VERSION:
        raise FeatureCalibrationError("feature calibration requires a feature LightGBM report")
    if calibrated_at.tzinfo is None or calibrated_at.utcoffset() is None:
        raise FeatureCalibrationError("calibrated_at must include a timezone")
    if minimum_validation_samples <= 0:
        raise FeatureCalibrationError("minimum_validation_samples must be positive")
    if not all(
        _is_sha256(value)
        for value in (source_prediction_sha256, source_model_sha256, source_manifest_sha256)
    ):
        raise FeatureCalibrationError("feature calibration source hashes must be SHA-256 values")
    if not predictions:
        raise FeatureCalibrationError("feature calibration predictions contain no rows")
    expected_count = model.row_counts["validation"] + model.row_counts["test"]
    if len(predictions) != expected_count:
        raise FeatureCalibrationError("feature calibration prediction count does not match model")
    keys = tuple((row.split, row.sample_id) for row in predictions)
    if len(keys) != len(set(keys)):
        raise FeatureCalibrationError("feature calibration predictions contain duplicate rows")
    expected_labels = set(_labels_for(model.purpose))
    validation = tuple(row for row in predictions if row.split == "validation")
    test = tuple(row for row in predictions if row.split == "test")
    if len(validation) < minimum_validation_samples:
        raise FeatureCalibrationError(
            f"validation samples are insufficient: {len(validation)} < {minimum_validation_samples}"
        )
    if {row.target for row in validation} != expected_labels:
        raise FeatureCalibrationError("validation labels must contain every purpose class")
    counts_by_split = {
        "validation": sum(row.split == "validation" for row in predictions),
        "test": sum(row.split == "test" for row in predictions),
    }
    if counts_by_split != {
        "validation": model.row_counts["validation"],
        "test": model.row_counts["test"],
    }:
        raise FeatureCalibrationError(
            "feature calibration prediction partitions do not match model"
        )
    if test and min(row.observation_date for row in test) <= max(
        row.observation_date for row in validation
    ):
        raise FeatureCalibrationError("test predictions must follow the validation period")
    if any(row.label_available_at > calibrated_at for row in predictions):
        raise FeatureCalibrationError("calibrated_at cannot precede prediction label availability")
    for row in predictions:
        if set(row.predicted_probabilities) != expected_labels:
            raise FeatureCalibrationError("prediction class probabilities do not match purpose")
        if abs(sum(row.predicted_probabilities.values(), Decimal("0")) - Decimal("1")) > Decimal(
            "1e-6"
        ):
            raise FeatureCalibrationError("prediction probabilities do not sum to one")
        if not row.feature_snapshot_id.strip() or not _is_sha256(row.source_label_row_sha256):
            raise FeatureCalibrationError("feature calibration prediction lineage is incomplete")


def _calibrate_prediction(
    row: FeatureLightGBMPrediction,
    labels: Sequence[TrainingLabel],
    bins_by_label: Mapping[TrainingLabel, Sequence[IsotonicBin]],
) -> FeatureCalibratedPrediction:
    raw = {label.value: row.predicted_probabilities[label.value] for label in labels}
    if len(labels) == 2:
        positive = labels[-1]
        calibrated_positive = apply_isotonic_bin(raw[positive.value], bins_by_label[positive])
        calibrated_values = {
            labels[0].value: Decimal("1") - calibrated_positive,
            positive.value: calibrated_positive,
        }
    else:
        calibrated_values = {
            label.value: apply_isotonic_bin(raw[label.value], bins_by_label[label])
            for label in labels
        }
        total = sum(calibrated_values.values(), Decimal("0"))
        if total <= 0:
            calibrated_values = raw
        else:
            calibrated_values = {name: value / total for name, value in calibrated_values.items()}
    return FeatureCalibratedPrediction(
        split=row.split,
        sample_id=row.sample_id,
        candidate_id=row.candidate_id,
        symbol=row.symbol,
        observation_date=row.observation_date,
        label_available_at=row.label_available_at,
        label=row.target,
        predicted_label=row.predicted_label,
        predicted_probabilities=raw,
        calibrated_probabilities=calibrated_values,
        feature_snapshot_id=row.feature_snapshot_id,
        source_label_row_sha256=row.source_label_row_sha256,
    )


def _metrics(
    split: Literal["validation", "test"],
    rows: Sequence[FeatureCalibratedPrediction],
    labels: Sequence[TrainingLabel],
) -> FeatureCalibrationMetrics:
    counts = {label.value: 0 for label in labels}
    for row in rows:
        counts[row.label.value] += 1
    if not rows:
        return FeatureCalibrationMetrics(split=split, row_count=0, target_counts=counts)
    raw = tuple(row.predicted_probabilities for row in rows)
    calibrated = tuple(row.calibrated_probabilities for row in rows)
    target_indices = tuple(labels.index(row.label) for row in rows)
    return FeatureCalibrationMetrics(
        split=split,
        row_count=len(rows),
        target_counts=counts,
        raw_log_loss=_multiclass_log_loss(raw, target_indices, labels),
        calibrated_log_loss=_multiclass_log_loss(calibrated, target_indices, labels),
        raw_normalized_brier_score=_normalized_brier(raw, target_indices, labels),
        calibrated_normalized_brier_score=_normalized_brier(calibrated, target_indices, labels),
    )


def _multiclass_log_loss(
    probabilities: Sequence[Mapping[str, Decimal]],
    targets: Sequence[int],
    labels: Sequence[TrainingLabel],
) -> float:
    if not probabilities:
        return 0.0
    return -sum(
        math.log(max(float(row[labels[target].value]), 1e-15))
        for row, target in zip(probabilities, targets, strict=True)
    ) / len(probabilities)


def _normalized_brier(
    probabilities: Sequence[Mapping[str, Decimal]],
    targets: Sequence[int],
    labels: Sequence[TrainingLabel],
) -> float:
    if not probabilities:
        return 0.0
    return sum(
        sum(
            (float(row[label.value]) - int(index == target)) ** 2
            for index, label in enumerate(labels)
        )
        / len(labels)
        for row, target in zip(probabilities, targets, strict=True)
    ) / len(probabilities)


def _labels_for(purpose: TrainingDatasetPurpose) -> tuple[TrainingLabel, ...]:
    return {
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
        TrainingDatasetPurpose.SELL_TIMING: (TrainingLabel.NOT_SOLD, TrainingLabel.SOLD),
    }[purpose]


def _read_predictions(content: bytes) -> tuple[FeatureLightGBMPrediction, ...]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise FeatureCalibrationError("feature LightGBM predictions are not UTF-8") from exc
    predictions: list[FeatureLightGBMPrediction] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("prediction row must be a JSON object")
            predictions.append(FeatureLightGBMPrediction.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise FeatureCalibrationError(
                f"invalid feature LightGBM prediction at line {line_number}: {exc}"
            ) from exc
    return tuple(predictions)


def _read_json_object(content: bytes, name: str) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FeatureCalibrationError(f"{name} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise FeatureCalibrationError(f"{name} must be a JSON object")
    return payload


def _require_manifest_equal(manifest: Mapping[str, object], name: str, expected: object) -> None:
    if manifest.get(name) != expected:
        raise FeatureCalibrationError(f"feature LightGBM manifest field does not match: {name}")


def _manifest_sequence(manifest: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = manifest.get(name)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise FeatureCalibrationError(
            f"feature LightGBM manifest field is not a string list: {name}"
        )
    return tuple(value)


def _bin_identity(item: IsotonicBin) -> dict[str, str | int]:
    return {
        "lower_probability": str(item.lower_probability),
        "upper_probability": str(item.upper_probability),
        "calibrated_probability": str(item.calibrated_probability),
        "sample_count": item.sample_count,
        "positive_count": item.positive_count,
    }


def _write_outputs(destination: Path, outputs: Mapping[str, bytes]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    paths = tuple(destination / name for name in outputs)
    if any(path.exists() for path in paths):
        raise FileExistsError(f"feature calibration output already exists: {destination}")
    temporary_paths: list[Path] = []
    try:
        for path, content in zip(paths, outputs.values(), strict=True):
            temporary = path.with_suffix(f"{path.suffix}.tmp")
            temporary.write_bytes(content)
            temporary_paths.append(temporary)
        for temporary, path in zip(temporary_paths, paths, strict=True):
            temporary.replace(path)
    except OSError:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)
        for path in paths:
            path.unlink(missing_ok=True)
        raise


def _is_sha256(value: str) -> bool:
    normalized = value.strip().lower()
    return len(normalized) == 64 and all(char in "0123456789abcdef" for char in normalized)
