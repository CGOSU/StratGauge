"""Purpose-aware validation-only calibration for feature LightGBM outputs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path

from stratgauge.ml._hashing import sha256_bytes, sha256_json
from stratgauge.ml.isotonic import IsotonicBin, fit_isotonic_bins
from stratgauge.ml.training_datasets import (
    TrainingLabel,
)
from stratgauge.ml.training_feature_lightgbm import (
    FEATURE_LIGHTGBM_EVALUATION_VERSION,
    FEATURE_LIGHTGBM_MODEL_TYPE,
    FEATURE_LIGHTGBM_REPORT_VERSION,
    FeatureLightGBMEvaluationEvidence,
    FeatureLightGBMModel,
    FeatureLightGBMPrediction,
)
from stratgauge.ml.training_feature_lightgbm_calibration.core import (
    FeatureCalibrationError,
)
from stratgauge.ml.training_feature_lightgbm_calibration.helpers_b import (
    _bin_identity,
    _calibrate_prediction,
    _manifest_sequence,
    _metrics,
    _read_json_object,
    _read_predictions,
    _require_manifest_equal,
    _validate_calibration_inputs,
    _validate_prediction_evidence,
    _write_outputs,
)
from stratgauge.ml.training_feature_lightgbm_calibration.models import (
    FEATURE_CALIBRATION_EVALUATION_VERSION,
    FEATURE_CALIBRATION_VERSION,
    FeatureCalibratedModel,
    FeatureCalibratedPrediction,
    FeatureCalibrationBin,
    FeatureCalibrationEvaluationEvidence,
    FeatureCalibrationOutput,
    FeatureCalibrationResult,
    FeatureLightGBMOutput,
    _is_sha256,
    _labels_for,
)


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
