"""Optional, deterministic LightGBM training for selection feature rows."""

from __future__ import annotations

import hashlib
import json
import math
import platform
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, field_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml.evaluation import ModelEvaluationEvidence
from stratgauge.ml.selection_training import (
    SelectionFeatureRow,
    SplitName,
    split_feature_dataset,
)

LIGHTGBM_REPORT_VERSION = "selection-lightgbm-v1"
LightGBMLabel = Literal["win", "loss"]


class LightGBMUnavailableError(RuntimeError):
    """The optional LightGBM dependency is not installed."""


class LightGBMModel(DomainModel):
    """Metadata for one uncalibrated LightGBM candidate model."""

    model_version_id: str
    model_type: Literal["lightgbm"] = "lightgbm"
    report_version: str = LIGHTGBM_REPORT_VERSION
    feature_set_version_id: str
    tracking_policy_id: str
    positive_label: LightGBMLabel = "win"
    feature_names: tuple[str, ...]
    train_row_count: int = Field(gt=0)
    train_positive_count: int = Field(ge=0)
    random_seed: int
    source_file_sha256: str
    artifact_sha256: str
    parameters: dict[str, str | int | float]

    @field_validator(
        "model_version_id",
        "feature_set_version_id",
        "tracking_policy_id",
        "source_file_sha256",
        "artifact_sha256",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("LightGBM model text must not be empty")
        return value.strip()


class LightGBMMetrics(DomainModel):
    """Metrics for one chronological LightGBM data partition."""

    split: SplitName
    row_count: int = Field(ge=0)
    positive_count: int = Field(ge=0)
    coverage: Decimal = Field(ge=0, le=1)
    predicted_probability: Decimal | None = Field(default=None, ge=0, le=1)
    log_loss: float | None = Field(default=None, ge=0)
    brier_score: float | None = Field(default=None, ge=0)


class LightGBMPrediction(DomainModel):
    """One out-of-sample LightGBM probability for audit and ranking inspection."""

    split: Literal["validation", "test"]
    candidate_id: str
    symbol: str
    selection_date: date
    label: LightGBMLabel
    predicted_probability: Decimal = Field(ge=0, le=1)
    rank: int = Field(gt=0)
    feature_snapshot_id: str


@dataclass(frozen=True)
class LightGBMTrainingResult:
    model: LightGBMModel
    metrics: tuple[LightGBMMetrics, ...]
    predictions: tuple[LightGBMPrediction, ...]
    source_file_sha256: str
    train_end: date
    validation_end: date
    row_counts: dict[str, int]
    artifact: bytes


def train_lightgbm(
    rows: Sequence[SelectionFeatureRow],
    *,
    train_end: date,
    validation_end: date,
    source_file_sha256: str,
    random_seed: int = 0,
    expected_feature_set_version_id: str | None = None,
    expected_tracking_policy_id: str | None = None,
) -> LightGBMTrainingResult:
    """Train a fixed-configuration, uncalibrated chronological LightGBM model."""

    lightgbm = _load_lightgbm()
    numpy = _load_numpy()
    all_rows = tuple(rows)
    if not all_rows:
        raise ValueError("LightGBM dataset contains no rows")
    if not source_file_sha256.strip():
        raise ValueError("LightGBM source file hash must not be empty")
    if train_end >= validation_end:
        raise ValueError("train_end must precede validation_end")
    _validate_scope(
        all_rows,
        expected_feature_set_version_id=expected_feature_set_version_id,
        expected_tracking_policy_id=expected_tracking_policy_id,
    )
    feature_names = tuple(sorted(all_rows[0].features))
    if any(tuple(sorted(row.features)) != feature_names for row in all_rows):
        raise ValueError("LightGBM dataset rows have different feature columns")
    splits = split_feature_dataset(
        all_rows,
        train_end=train_end,
        validation_end=validation_end,
    )
    train_rows = splits["train"]
    if not train_rows:
        raise ValueError("training split contains no rows")
    positive_count = sum(row.label == "win" for row in train_rows)
    if positive_count == 0 or positive_count == len(train_rows):
        raise ValueError("training labels must contain at least two classes")

    parameters: dict[str, str | int | float] = {
        "objective": "binary",
        "metric": "binary_logloss",
        "verbosity": -1,
        "seed": random_seed,
        "feature_fraction_seed": random_seed,
        "bagging_seed": random_seed,
        "data_random_seed": random_seed,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": 1,
        "learning_rate": 0.05,
        "num_leaves": 15,
        "min_data_in_leaf": 10,
        "feature_fraction": 1.0,
        "bagging_fraction": 1.0,
        "bagging_freq": 0,
        "num_boost_round": 50,
    }
    dataset = lightgbm.Dataset(
        numpy.asarray(_matrix(train_rows, feature_names), dtype=float),
        label=[1 if row.label == "win" else 0 for row in train_rows],
        feature_name=list(feature_names),
        free_raw_data=False,
    )
    booster = lightgbm.train(
        {key: value for key, value in parameters.items() if key != "num_boost_round"},
        dataset,
        num_boost_round=int(parameters["num_boost_round"]),
    )
    artifact = booster.model_to_string().encode("utf-8")
    artifact_sha256 = _sha256(artifact)
    feature_set_version_id = train_rows[0].feature_set_version_id
    tracking_policy_id = train_rows[0].tracking_policy_id
    model_identity = {
        "artifact_sha256": artifact_sha256,
        "feature_set_version_id": feature_set_version_id,
        "parameters": parameters,
        "report_version": LIGHTGBM_REPORT_VERSION,
        "source_file_sha256": source_file_sha256,
        "tracking_policy_id": tracking_policy_id,
        "train_end": train_end.isoformat(),
        "validation_end": validation_end.isoformat(),
    }
    model = LightGBMModel(
        model_version_id=f"lightgbm-{_sha256(_canonical_json(model_identity))[:20]}",
        feature_set_version_id=feature_set_version_id,
        tracking_policy_id=tracking_policy_id,
        feature_names=feature_names,
        train_row_count=len(train_rows),
        train_positive_count=positive_count,
        random_seed=random_seed,
        source_file_sha256=source_file_sha256,
        artifact_sha256=artifact_sha256,
        parameters=parameters,
    )
    metrics: list[LightGBMMetrics] = []
    predictions: list[LightGBMPrediction] = []
    for split_name in ("train", "validation", "test"):
        split_rows = splits[split_name]
        probabilities = _predict(booster, split_rows, feature_names, numpy)
        metrics.append(_metrics(split_name, split_rows, probabilities))
        if split_name == "train":
            continue
        predictions.extend(
            _predictions(cast(Literal["validation", "test"], split_name), split_rows, probabilities)
        )
    return LightGBMTrainingResult(
        model=model,
        metrics=tuple(metrics),
        predictions=tuple(predictions),
        source_file_sha256=source_file_sha256,
        train_end=train_end,
        validation_end=validation_end,
        row_counts={name: len(split_rows) for name, split_rows in splits.items()},
        artifact=artifact,
    )


def write_lightgbm_result(output_dir: str | Path, result: LightGBMTrainingResult) -> None:
    """Write the LightGBM artifact and audit files without overwriting."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    evaluation = ModelEvaluationEvidence(
        model_version_id=result.model.model_version_id,
        model_type=result.model.model_type,
        feature_set_version_id=result.model.feature_set_version_id,
        tracking_policy_id=result.model.tracking_policy_id,
        source_file_sha256=result.source_file_sha256,
        calibrated=False,
        metrics=_evaluation_metrics(result.metrics),
    )
    outputs: dict[str, bytes] = {
        "model.json": (result.model.model_dump_json(indent=2) + "\n").encode("utf-8"),
        "model.txt": result.artifact,
        "metrics.json": (evaluation.model_dump_json(indent=2) + "\n").encode("utf-8"),
        "predictions.jsonl": "".join(
            f"{prediction.model_dump_json()}\n" for prediction in result.predictions
        ).encode("utf-8"),
        "manifest.json": (
            json.dumps(
                {
                    "report_version": LIGHTGBM_REPORT_VERSION,
                    "source_file_sha256": result.source_file_sha256,
                    "artifact_sha256": result.model.artifact_sha256,
                    "training_parameters": result.model.parameters,
                    "environment": {
                        "python_implementation": platform.python_implementation(),
                        "python_version": platform.python_version(),
                    },
                    "feature_set_version_id": result.model.feature_set_version_id,
                    "tracking_policy_id": result.model.tracking_policy_id,
                    "train_end": result.train_end.isoformat(),
                    "validation_end": result.validation_end.isoformat(),
                    "row_counts": result.row_counts,
                    "model_version_id": result.model.model_version_id,
                    "calibrated": False,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8"),
    }
    paths = tuple(destination / name for name in outputs)
    if any(path.exists() for path in paths):
        raise FileExistsError(f"LightGBM output already exists: {destination}")
    temporary_paths: list[Path] = []
    try:
        for name, content in outputs.items():
            path = destination / name
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


def _load_lightgbm() -> Any:
    try:
        return import_module("lightgbm")
    except (ImportError, OSError) as exc:
        raise LightGBMUnavailableError(
            "LightGBM is unavailable; run `uv sync --extra ml` before training"
        ) from exc


def _load_numpy() -> Any:
    try:
        return import_module("numpy")
    except (ImportError, OSError) as exc:
        raise LightGBMUnavailableError(
            "NumPy is unavailable; run `uv sync --extra ml` before training"
        ) from exc


def _validate_scope(
    rows: Sequence[SelectionFeatureRow],
    *,
    expected_feature_set_version_id: str | None,
    expected_tracking_policy_id: str | None,
) -> None:
    keys = tuple((row.candidate_id, row.selection_date) for row in rows)
    if len(keys) != len(set(keys)):
        raise ValueError("LightGBM dataset contains duplicate candidate/date rows")
    feature_sets = {row.feature_set_version_id for row in rows}
    if len(feature_sets) != 1:
        raise ValueError("LightGBM dataset contains multiple feature-set versions")
    policies = {row.tracking_policy_id for row in rows}
    if len(policies) != 1:
        raise ValueError("LightGBM dataset contains multiple tracking policies")
    if (
        expected_feature_set_version_id is not None
        and next(iter(feature_sets)) != expected_feature_set_version_id
    ):
        raise ValueError("LightGBM dataset does not match expected feature-set version")
    if (
        expected_tracking_policy_id is not None
        and next(iter(policies)) != expected_tracking_policy_id
    ):
        raise ValueError("LightGBM dataset does not match expected tracking policy")


def _matrix(rows: Sequence[SelectionFeatureRow], feature_names: Sequence[str]) -> list[list[float]]:
    return [[float(row.features[name]) for name in feature_names] for row in rows]


def _predict(
    booster: Any,
    rows: Sequence[SelectionFeatureRow],
    feature_names: Sequence[str],
    numpy: Any,
) -> tuple[Decimal, ...]:
    if not rows:
        return ()
    values = booster.predict(numpy.asarray(_matrix(rows, feature_names), dtype=float))
    probabilities = tuple(Decimal(str(float(value))) for value in values)
    if any(not value.is_finite() or not 0 <= value <= 1 for value in probabilities):
        raise ValueError("LightGBM produced an invalid probability")
    return probabilities


def _metrics(
    split: SplitName,
    rows: Sequence[SelectionFeatureRow],
    probabilities: Sequence[Decimal],
) -> LightGBMMetrics:
    if not rows:
        return LightGBMMetrics(split=split, row_count=0, positive_count=0, coverage=Decimal("0"))
    labels = tuple(row.label == "win" for row in rows)
    probability_values = tuple(float(value) for value in probabilities)
    log_loss = -sum(
        math.log(
            min(max(probability, 1e-15), 1 - 1e-15)
            if label
            else 1 - min(max(probability, 1e-15), 1 - 1e-15)
        )
        for probability, label in zip(probability_values, labels, strict=True)
    ) / len(labels)
    brier = sum(
        (probability - int(label)) ** 2
        for probability, label in zip(probability_values, labels, strict=True)
    ) / len(labels)
    return LightGBMMetrics(
        split=split,
        row_count=len(rows),
        positive_count=sum(labels),
        coverage=Decimal("1"),
        predicted_probability=sum(probabilities, Decimal("0")) / Decimal(len(probabilities)),
        log_loss=log_loss,
        brier_score=brier,
    )


def _predictions(
    split: Literal["validation", "test"],
    rows: Sequence[SelectionFeatureRow],
    probabilities: Sequence[Decimal],
) -> tuple[LightGBMPrediction, ...]:
    ranked = sorted(
        zip(rows, probabilities, strict=True),
        key=lambda item: (-item[1], item[0].candidate_id),
    )
    return tuple(
        LightGBMPrediction(
            split=split,
            candidate_id=row.candidate_id,
            symbol=row.symbol,
            selection_date=row.selection_date,
            label=row.label,
            predicted_probability=probability,
            rank=rank,
            feature_snapshot_id=row.feature_snapshot_id,
        )
        for rank, (row, probability) in enumerate(ranked, start=1)
    )


def _evaluation_metrics(metrics: Sequence[LightGBMMetrics]) -> dict[str, Decimal | None]:
    values: dict[str, Decimal | None] = {}
    for metric in metrics:
        values[f"{metric.split}_coverage"] = metric.coverage
        values[f"{metric.split}_predicted_probability"] = metric.predicted_probability
        values[f"{metric.split}_log_loss"] = (
            None if metric.log_loss is None else Decimal(str(metric.log_loss))
        )
        values[f"{metric.split}_brier_score"] = (
            None if metric.brier_score is None else Decimal(str(metric.brier_score))
        )
    return values


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
