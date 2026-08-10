"""Dependency-free isotonic calibration for LightGBM probabilities."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml.evaluation import ModelEvaluationEvidence

CALIBRATION_VERSION = "selection-calibration-v1"
LIGHTGBM_REPORT_VERSION = "selection-lightgbm-v1"
CalibrationSplit = Literal["validation"]
PredictionSplit = Literal["validation", "test"]


class CalibrationError(ValueError):
    """Probability calibration input or output is invalid."""


class CalibrationPrediction(DomainModel):
    """One prediction emitted by a model training run."""

    split: PredictionSplit
    candidate_id: str
    symbol: str
    selection_date: date
    label: Literal["win", "loss"]
    predicted_probability: Decimal = Field(ge=0, le=1)
    rank: int = Field(gt=0)
    feature_snapshot_id: str

    @field_validator("candidate_id", "symbol", "feature_snapshot_id")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("calibration prediction text must not be empty")
        return value.strip()


class IsotonicCalibrationBin(DomainModel):
    """One monotonic probability interval fitted by PAVA."""

    lower_probability: Decimal = Field(ge=0, le=1)
    upper_probability: Decimal = Field(ge=0, le=1)
    calibrated_probability: Decimal = Field(ge=0, le=1)
    sample_count: int = Field(gt=0)
    positive_count: int = Field(ge=0)


class CalibratedModel(DomainModel):
    """An immutable, explicitly scoped isotonic calibration artifact."""

    calibration_id: str
    calibration_version: str = CALIBRATION_VERSION
    model_type: Literal["lightgbm_isotonic"] = "lightgbm_isotonic"
    base_model_version_id: str
    feature_set_version_id: str
    tracking_policy_id: str
    method: Literal["isotonic_pava"] = "isotonic_pava"
    calibrated: Literal[True] = True
    calibrated_at: datetime
    calibration_split: CalibrationSplit = "validation"
    calibration_row_count: int = Field(gt=0)
    calibration_positive_count: int = Field(gt=0)
    calibration_date_start: date
    calibration_date_end: date
    test_row_count: int = Field(ge=0)
    source_prediction_sha256: str
    source_model_manifest_sha256: str
    bins: tuple[IsotonicCalibrationBin, ...]

    @field_validator(
        "calibration_id",
        "base_model_version_id",
        "feature_set_version_id",
        "tracking_policy_id",
        "source_prediction_sha256",
        "source_model_manifest_sha256",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("calibration model text must not be empty")
        return value.strip()

    @field_validator("calibrated_at")
    @classmethod
    def validate_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("calibrated_at must include a timezone")
        return value


class CalibrationMetrics(DomainModel):
    """Raw and calibrated probability metrics for one split."""

    split: PredictionSplit
    row_count: int = Field(ge=0)
    positive_count: int = Field(ge=0)
    raw_log_loss: Decimal | None = Field(default=None, ge=0)
    calibrated_log_loss: Decimal | None = Field(default=None, ge=0)
    raw_brier_score: Decimal | None = Field(default=None, ge=0)
    calibrated_brier_score: Decimal | None = Field(default=None, ge=0)


class CalibratedPrediction(DomainModel):
    """A raw model probability paired with its calibrated value."""

    split: PredictionSplit
    candidate_id: str
    symbol: str
    selection_date: date
    label: Literal["win", "loss"]
    predicted_probability: Decimal = Field(ge=0, le=1)
    calibrated_probability: Decimal = Field(ge=0, le=1)
    rank: int = Field(gt=0)
    feature_snapshot_id: str


@dataclass(frozen=True)
class CalibrationResult:
    model: CalibratedModel
    metrics: tuple[CalibrationMetrics, ...]
    predictions: tuple[CalibratedPrediction, ...]
    source_prediction_sha256: str
    source_model_manifest_sha256: str


def read_calibration_predictions(content: bytes) -> tuple[CalibrationPrediction, ...]:
    """Read model prediction JSONL and reject duplicate split/candidate rows."""

    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise CalibrationError("prediction input is not valid UTF-8") from exc
    predictions: list[CalibrationPrediction] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("prediction row must be a JSON object")
            predictions.append(CalibrationPrediction.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CalibrationError(f"invalid prediction row at line {line_number}: {exc}") from exc
    if not predictions:
        raise CalibrationError("prediction input contains no rows")
    keys = tuple((row.split, row.candidate_id) for row in predictions)
    if len(keys) != len(set(keys)):
        raise CalibrationError("prediction input contains duplicate split/candidate rows")
    return tuple(predictions)


def read_lightgbm_manifest(content: bytes) -> dict[str, object]:
    """Validate the minimum manifest contract required for calibration."""

    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CalibrationError("LightGBM manifest is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise CalibrationError("LightGBM manifest must be a JSON object")
    if payload.get("report_version") != LIGHTGBM_REPORT_VERSION:
        raise CalibrationError("calibration requires a LightGBM training manifest")
    for field in ("model_version_id", "feature_set_version_id", "tracking_policy_id"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise CalibrationError(f"LightGBM manifest field is missing: {field}")
    return payload


def calibrate_predictions(
    predictions: Sequence[CalibrationPrediction],
    *,
    model_manifest: Mapping[str, object],
    source_prediction_sha256: str,
    source_model_manifest_sha256: str,
    calibrated_at: datetime,
    minimum_validation_samples: int = 100,
) -> CalibrationResult:
    """Fit PAVA on validation only and apply it to validation/test predictions."""

    if calibrated_at.tzinfo is None or calibrated_at.utcoffset() is None:
        raise CalibrationError("calibrated_at must include a timezone")
    if minimum_validation_samples <= 0:
        raise CalibrationError("minimum_validation_samples must be positive")
    if not source_prediction_sha256.strip() or not source_model_manifest_sha256.strip():
        raise CalibrationError("calibration source hashes must not be empty")
    manifest = read_lightgbm_manifest(
        json.dumps(model_manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    validation = tuple(row for row in predictions if row.split == "validation")
    test = tuple(row for row in predictions if row.split == "test")
    if len(validation) < minimum_validation_samples:
        raise CalibrationError(
            f"validation samples are insufficient: {len(validation)} < {minimum_validation_samples}"
        )
    if len({row.label for row in validation}) < 2:
        raise CalibrationError("validation labels must contain two classes")
    if len({row.predicted_probability for row in validation}) < 2:
        raise CalibrationError("validation predictions must contain two distinct probabilities")
    if test and min(row.selection_date for row in test) <= max(
        row.selection_date for row in validation
    ):
        raise CalibrationError("test predictions must follow the validation period")

    bins = _fit_isotonic(validation)
    calibrated_predictions = tuple(
        _calibrated_prediction(row, bins) for row in (*validation, *test)
    )
    calibration_date_start = min(row.selection_date for row in validation)
    calibration_date_end = max(row.selection_date for row in validation)
    base_model_version_id = _manifest_text(manifest, "model_version_id")
    feature_set_version_id = _manifest_text(manifest, "feature_set_version_id")
    tracking_policy_id = _manifest_text(manifest, "tracking_policy_id")
    identity = {
        "base_model_version_id": base_model_version_id,
        "bins": [item.model_dump(mode="json") for item in bins],
        "calibrated_at": calibrated_at.isoformat(),
        "calibration_date_end": calibration_date_end.isoformat(),
        "calibration_date_start": calibration_date_start.isoformat(),
        "calibration_version": CALIBRATION_VERSION,
        "feature_set_version_id": feature_set_version_id,
        "source_model_manifest_sha256": source_model_manifest_sha256,
        "source_prediction_sha256": source_prediction_sha256,
        "tracking_policy_id": tracking_policy_id,
    }
    calibration_id = f"calibration-{_sha256(_canonical_json(identity))[:20]}"
    metrics = tuple(
        _metrics(split, tuple(row for row in calibrated_predictions if row.split == split))
        for split in ("validation", "test")
    )
    model = CalibratedModel(
        calibration_id=calibration_id,
        base_model_version_id=base_model_version_id,
        feature_set_version_id=feature_set_version_id,
        tracking_policy_id=tracking_policy_id,
        calibrated_at=calibrated_at,
        calibration_row_count=len(validation),
        calibration_positive_count=sum(row.label == "win" for row in validation),
        calibration_date_start=calibration_date_start,
        calibration_date_end=calibration_date_end,
        test_row_count=len(test),
        source_prediction_sha256=source_prediction_sha256,
        source_model_manifest_sha256=source_model_manifest_sha256,
        bins=bins,
    )
    return CalibrationResult(
        model=model,
        metrics=metrics,
        predictions=calibrated_predictions,
        source_prediction_sha256=source_prediction_sha256,
        source_model_manifest_sha256=source_model_manifest_sha256,
    )


def write_calibration_result(output_dir: str | Path, result: CalibrationResult) -> None:
    """Write calibration artifacts without overwriting an existing run."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    evaluation = ModelEvaluationEvidence(
        model_version_id=result.model.calibration_id,
        model_type=result.model.model_type,
        feature_set_version_id=result.model.feature_set_version_id,
        tracking_policy_id=result.model.tracking_policy_id,
        source_file_sha256=result.source_prediction_sha256,
        calibrated=True,
        metrics=_evaluation_metrics(result.metrics),
    )
    outputs = {
        "calibration.json": (result.model.model_dump_json(indent=2) + "\n").encode("utf-8"),
        "metrics.json": (evaluation.model_dump_json(indent=2) + "\n").encode("utf-8"),
        "calibrated_predictions.jsonl": "".join(
            f"{prediction.model_dump_json()}\n" for prediction in result.predictions
        ).encode("utf-8"),
        "manifest.json": (
            json.dumps(
                {
                    "calibration_version": CALIBRATION_VERSION,
                    "calibration_id": result.model.calibration_id,
                    "base_model_version_id": result.model.base_model_version_id,
                    "source_prediction_sha256": result.source_prediction_sha256,
                    "source_model_manifest_sha256": result.source_model_manifest_sha256,
                    "calibrated": True,
                    "calibration_split": "validation",
                    "calibration_row_count": result.model.calibration_row_count,
                    "test_row_count": result.model.test_row_count,
                    "calibrated_at": result.model.calibrated_at.isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8"),
    }
    paths = tuple(destination / name for name in outputs)
    if any(path.exists() for path in paths):
        raise FileExistsError(f"calibration output already exists: {destination}")
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


@dataclass
class _IsotonicBlock:
    lower_probability: Decimal
    upper_probability: Decimal
    positive_count: int
    sample_count: int

    @property
    def calibrated_probability(self) -> Decimal:
        return Decimal(self.positive_count) / Decimal(self.sample_count)


def _fit_isotonic(rows: Sequence[CalibrationPrediction]) -> tuple[IsotonicCalibrationBin, ...]:
    blocks: list[_IsotonicBlock] = []
    for row in sorted(rows, key=lambda item: (item.predicted_probability, item.candidate_id)):
        blocks.append(
            _IsotonicBlock(
                lower_probability=row.predicted_probability,
                upper_probability=row.predicted_probability,
                positive_count=int(row.label == "win"),
                sample_count=1,
            )
        )
        while len(blocks) >= 2 and (
            blocks[-2].calibrated_probability > blocks[-1].calibrated_probability
        ):
            right = blocks.pop()
            left = blocks.pop()
            blocks.append(
                _IsotonicBlock(
                    lower_probability=left.lower_probability,
                    upper_probability=right.upper_probability,
                    positive_count=left.positive_count + right.positive_count,
                    sample_count=left.sample_count + right.sample_count,
                )
            )
    return tuple(
        IsotonicCalibrationBin(
            lower_probability=block.lower_probability,
            upper_probability=block.upper_probability,
            calibrated_probability=block.calibrated_probability,
            sample_count=block.sample_count,
            positive_count=block.positive_count,
        )
        for block in blocks
    )


def _calibrated_prediction(
    row: CalibrationPrediction,
    bins: Sequence[IsotonicCalibrationBin],
) -> CalibratedPrediction:
    calibrated = next(
        (
            item.calibrated_probability
            for item in bins
            if row.predicted_probability <= item.upper_probability
        ),
        bins[-1].calibrated_probability,
    )
    return CalibratedPrediction(
        split=row.split,
        candidate_id=row.candidate_id,
        symbol=row.symbol,
        selection_date=row.selection_date,
        label=row.label,
        predicted_probability=row.predicted_probability,
        calibrated_probability=calibrated,
        rank=row.rank,
        feature_snapshot_id=row.feature_snapshot_id,
    )


def _metrics(
    split: PredictionSplit,
    rows: Sequence[CalibratedPrediction],
) -> CalibrationMetrics:
    if not rows:
        return CalibrationMetrics(split=split, row_count=0, positive_count=0)
    labels = tuple(row.label == "win" for row in rows)
    raw = tuple(row.predicted_probability for row in rows)
    calibrated = tuple(row.calibrated_probability for row in rows)
    return CalibrationMetrics(
        split=split,
        row_count=len(rows),
        positive_count=sum(labels),
        raw_log_loss=_log_loss(raw, labels),
        calibrated_log_loss=_log_loss(calibrated, labels),
        raw_brier_score=_brier(raw, labels),
        calibrated_brier_score=_brier(calibrated, labels),
    )


def _log_loss(probabilities: Sequence[Decimal], labels: Sequence[bool]) -> Decimal:
    values = tuple(
        -math.log(
            min(max(float(probability), 1e-15), 1 - 1e-15)
            if label
            else 1 - min(max(float(probability), 1e-15), 1 - 1e-15)
        )
        for probability, label in zip(probabilities, labels, strict=True)
    )
    return Decimal(str(sum(values) / len(values))) if values else Decimal("0")


def _brier(probabilities: Sequence[Decimal], labels: Sequence[bool]) -> Decimal:
    if not probabilities:
        return Decimal("0")
    value = sum(
        (probability - Decimal(int(label))) ** 2
        for probability, label in zip(probabilities, labels, strict=True)
    ) / Decimal(len(probabilities))
    return value


def _evaluation_metrics(metrics: Sequence[CalibrationMetrics]) -> dict[str, Decimal | None]:
    values: dict[str, Decimal | None] = {}
    for metric in metrics:
        values[f"{metric.split}_raw_log_loss"] = metric.raw_log_loss
        values[f"{metric.split}_calibrated_log_loss"] = metric.calibrated_log_loss
        values[f"{metric.split}_raw_brier_score"] = metric.raw_brier_score
        values[f"{metric.split}_calibrated_brier_score"] = metric.calibrated_brier_score
    return values


def _manifest_text(manifest: Mapping[str, object], field_name: str) -> str:
    value = manifest.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise CalibrationError(f"LightGBM manifest field is missing: {field_name}")
    return value.strip()


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
