"""Purpose-aware validation-only calibration for feature LightGBM outputs."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from stratgauge.ml.isotonic import IsotonicBin, apply_isotonic_bin
from stratgauge.ml.training_datasets import (
    TrainingLabel,
)
from stratgauge.ml.training_feature_lightgbm import (
    FEATURE_LIGHTGBM_MODEL_TYPE,
    FEATURE_LIGHTGBM_REPORT_VERSION,
    FeatureLightGBMEvaluationEvidence,
    FeatureLightGBMModel,
    FeatureLightGBMPrediction,
)
from stratgauge.ml.training_feature_lightgbm_calibration.core import (
    FeatureCalibrationError,
)
from stratgauge.ml.training_feature_lightgbm_calibration.models import (
    FeatureCalibratedPrediction,
    FeatureCalibrationMetrics,
    _is_sha256,
    _labels_for,
)


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
