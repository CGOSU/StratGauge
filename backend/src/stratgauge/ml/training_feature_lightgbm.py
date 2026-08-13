"""Train purpose-isolated LightGBM models from verified feature splits."""

from __future__ import annotations

import hashlib
import json
import math
import platform
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    TrainingLabel,
    label_contract_id_for,
)
from stratgauge.ml.training_feature_baseline import TrainingFeatureSplitInput
from stratgauge.ml.training_feature_join import SelectionTrainingFeatureRow

FEATURE_LIGHTGBM_REPORT_VERSION = "selection-purpose-lightgbm-v1"
FEATURE_LIGHTGBM_EVALUATION_VERSION = "selection-purpose-lightgbm-evaluation-v1"
FEATURE_LIGHTGBM_MODEL_TYPE: Literal["lightgbm"] = "lightgbm"
FEATURE_LIGHTGBM_BOOST_ROUNDS = 50
SplitName = Literal["train", "validation", "test"]
PredictionSplit = Literal["validation", "test"]

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


class FeatureLightGBMUnavailableError(RuntimeError):
    """The optional LightGBM or NumPy dependency is unavailable."""


class FeatureLightGBMModel(DomainModel):
    """Immutable metadata for one purpose-specific LightGBM artifact."""

    model_version_id: str
    model_type: Literal["lightgbm"] = FEATURE_LIGHTGBM_MODEL_TYPE
    report_version: str = FEATURE_LIGHTGBM_REPORT_VERSION
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    feature_names: tuple[str, ...]
    class_labels: tuple[TrainingLabel, ...]
    train_row_count: int = Field(gt=0)
    train_target_counts: dict[str, int]
    random_seed: int
    minimum_labeled_samples: int = Field(gt=0)
    feature_dataset_manifest_sha256: str
    source_label_dataset_sha256: str
    label_split_manifest_sha256: str
    artifact_sha256: str
    parameters: dict[str, str | int | float]
    row_counts: dict[str, int]
    purged_row_count: int = Field(ge=0)

    @field_validator(
        "model_version_id",
        "label_contract_id",
        "feature_set_version_id",
        "feature_dataset_manifest_sha256",
        "source_label_dataset_sha256",
        "label_split_manifest_sha256",
        "artifact_sha256",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature LightGBM model text must not be empty")
        return normalized

    @field_validator(
        "feature_dataset_manifest_sha256",
        "source_label_dataset_sha256",
        "label_split_manifest_sha256",
        "artifact_sha256",
    )
    @classmethod
    def validate_digests(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("feature LightGBM model digest must be a SHA-256 value")
        return normalized

    @model_validator(mode="after")
    def validate_contract(self) -> FeatureLightGBMModel:
        if self.report_version != FEATURE_LIGHTGBM_REPORT_VERSION:
            raise ValueError("unsupported feature LightGBM report version")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("feature LightGBM label contract does not match purpose")
        labels = _labels_for(self.purpose)
        if self.class_labels != labels:
            raise ValueError("feature LightGBM class labels do not match purpose")
        if set(self.train_target_counts) != {label.value for label in labels}:
            raise ValueError("feature LightGBM target counts do not match labels")
        expected_objective = "multiclass" if len(labels) > 2 else "binary"
        if self.parameters.get("objective") != expected_objective:
            raise ValueError("feature LightGBM objective does not match purpose")
        if len(labels) > 2 and self.parameters.get("num_class") != len(labels):
            raise ValueError("feature LightGBM class count does not match purpose")
        if len(labels) == 2 and "num_class" in self.parameters:
            raise ValueError("binary feature LightGBM must not declare num_class")
        if sum(self.train_target_counts.values()) != self.train_row_count:
            raise ValueError("feature LightGBM target counts do not match train rows")
        if not self.feature_names or tuple(sorted(set(self.feature_names))) != self.feature_names:
            raise ValueError("feature LightGBM feature names must be sorted and non-empty")
        if set(self.row_counts) != {"train", "validation", "test"}:
            raise ValueError("feature LightGBM row counts are incomplete")
        if any(value < 0 for value in self.row_counts.values()):
            raise ValueError("feature LightGBM row counts must be non-negative")
        return self


class FeatureLightGBMMetrics(DomainModel):
    """Metrics for one chronological partition under a multiclass-aware model."""

    split: SplitName
    row_count: int = Field(ge=0)
    coverage: Decimal = Field(ge=0, le=1)
    target_counts: dict[str, int]
    mean_predicted_probabilities: dict[str, Decimal]
    log_loss: float | None = Field(default=None, ge=0)
    normalized_brier_score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_metrics(self) -> FeatureLightGBMMetrics:
        if sum(self.target_counts.values()) != self.row_count:
            raise ValueError("feature LightGBM target counts do not match row count")
        if set(self.target_counts) != set(self.mean_predicted_probabilities):
            raise ValueError("feature LightGBM metric labels do not match")
        if self.row_count == 0 and (self.coverage != 0 or self.log_loss is not None):
            raise ValueError("empty feature LightGBM metrics have invalid coverage or loss")
        if self.row_count > 0 and self.coverage != 1:
            raise ValueError("non-empty feature LightGBM metrics require full coverage")
        return self


class FeatureLightGBMPrediction(DomainModel):
    """Out-of-sample probabilities with purpose and feature lineage retained."""

    split: PredictionSplit
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

    @field_validator("sample_id", "candidate_id", "symbol", "feature_snapshot_id")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature LightGBM prediction identity must not be empty")
        return normalized

    @field_validator("source_label_row_sha256")
    @classmethod
    def validate_source_label_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _is_sha256(normalized):
            raise ValueError("feature LightGBM prediction source must be a SHA-256 digest")
        return normalized

    @field_validator("label_available_at")
    @classmethod
    def validate_label_available_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("feature LightGBM label availability must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_label_timing(self) -> FeatureLightGBMPrediction:
        if self.label_available_at.date() < self.observation_date:
            raise ValueError("feature LightGBM label availability cannot precede observation date")
        return self


class FeatureLightGBMEvaluationEvidence(DomainModel):
    """Evaluation evidence that remains separate from model registration."""

    evaluation_version: str = FEATURE_LIGHTGBM_EVALUATION_VERSION
    model_version_id: str
    model_type: Literal["lightgbm"] = FEATURE_LIGHTGBM_MODEL_TYPE
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    feature_dataset_manifest_sha256: str
    calibrated: Literal[False] = False
    metrics: tuple[FeatureLightGBMMetrics, ...]


class FeatureLightGBMTrainingResult(DomainModel):
    """All immutable outputs from one purpose-specific LightGBM training run."""

    model: FeatureLightGBMModel
    metrics: tuple[FeatureLightGBMMetrics, ...]
    predictions: tuple[FeatureLightGBMPrediction, ...]
    artifact: bytes


def train_feature_lightgbm(
    split_input: TrainingFeatureSplitInput,
    *,
    random_seed: int = 0,
    minimum_labeled_samples: int = 100,
) -> FeatureLightGBMTrainingResult:
    """Fit only on train feature rows and evaluate validation/test rows."""

    _validate_training_input(split_input, minimum_labeled_samples)
    lightgbm = _load_lightgbm()
    numpy = _load_numpy()
    labels = _labels_for(split_input.manifest.purpose)
    feature_names = split_input.manifest.feature_names
    train_rows = split_input.train_rows
    parameters = _parameters(labels, random_seed)
    dataset = lightgbm.Dataset(
        numpy.asarray(_matrix(train_rows, feature_names), dtype=float),
        label=[labels.index(row.target) for row in train_rows],
        feature_name=list(feature_names),
        free_raw_data=False,
    )
    booster = lightgbm.train(
        {key: value for key, value in parameters.items() if key != "num_boost_round"},
        dataset,
        num_boost_round=FEATURE_LIGHTGBM_BOOST_ROUNDS,
    )
    artifact = booster.model_to_string().encode("utf-8")
    artifact_sha256 = _sha256(artifact)
    model_identity = {
        "artifact_sha256": artifact_sha256,
        "class_labels": [label.value for label in labels],
        "feature_dataset_manifest_sha256": split_input.manifest_sha256,
        "feature_names": feature_names,
        "feature_set_version_id": split_input.manifest.feature_set_version_id,
        "label_contract_id": split_input.manifest.label_contract_id,
        "label_split_manifest_sha256": split_input.manifest.label_split_manifest_sha256,
        "model_type": FEATURE_LIGHTGBM_MODEL_TYPE,
        "parameters": parameters,
        "purpose": split_input.manifest.purpose.value,
        "random_seed": random_seed,
        "source_label_dataset_sha256": split_input.manifest.source_label_dataset_sha256,
        "train_target_counts": {
            label.value: sum(row.target is label for row in train_rows) for label in labels
        },
    }
    model = FeatureLightGBMModel(
        model_version_id=f"feature-lightgbm-{_sha256_json(model_identity)[:20]}",
        purpose=split_input.manifest.purpose,
        label_contract_id=split_input.manifest.label_contract_id,
        feature_set_version_id=split_input.manifest.feature_set_version_id,
        feature_names=feature_names,
        class_labels=labels,
        train_row_count=len(train_rows),
        train_target_counts={
            label.value: sum(row.target is label for row in train_rows) for label in labels
        },
        random_seed=random_seed,
        minimum_labeled_samples=minimum_labeled_samples,
        feature_dataset_manifest_sha256=split_input.manifest_sha256,
        source_label_dataset_sha256=split_input.manifest.source_label_dataset_sha256,
        label_split_manifest_sha256=split_input.manifest.label_split_manifest_sha256,
        artifact_sha256=artifact_sha256,
        parameters=parameters,
        row_counts={
            "train": len(split_input.train_rows),
            "validation": len(split_input.validation_rows),
            "test": len(split_input.test_rows),
        },
        purged_row_count=len(split_input.purged_rows),
    )
    metrics: list[FeatureLightGBMMetrics] = []
    predictions: list[FeatureLightGBMPrediction] = []
    for split_name, rows in (
        ("train", split_input.train_rows),
        ("validation", split_input.validation_rows),
        ("test", split_input.test_rows),
    ):
        probabilities = _predict_probabilities(booster, rows, feature_names, labels, numpy)
        metrics.append(_metrics(cast(SplitName, split_name), rows, probabilities, labels))
        if split_name != "train":
            predictions.extend(
                _predictions(
                    cast(PredictionSplit, split_name),
                    rows,
                    probabilities,
                    labels,
                )
            )
    return FeatureLightGBMTrainingResult(
        model=model,
        metrics=tuple(metrics),
        predictions=tuple(predictions),
        artifact=artifact,
    )


def write_feature_lightgbm_result(
    output_dir: str | Path,
    result: FeatureLightGBMTrainingResult,
) -> None:
    """Write LightGBM artifacts and lineage evidence without overwriting."""

    model_content = (result.model.model_dump_json(indent=2) + "\n").encode("utf-8")
    metrics_content = (
        FeatureLightGBMEvaluationEvidence(
            model_version_id=result.model.model_version_id,
            purpose=result.model.purpose,
            label_contract_id=result.model.label_contract_id,
            feature_set_version_id=result.model.feature_set_version_id,
            feature_dataset_manifest_sha256=result.model.feature_dataset_manifest_sha256,
            metrics=result.metrics,
        ).model_dump_json(indent=2)
        + "\n"
    ).encode("utf-8")
    predictions_content = "".join(
        f"{prediction.model_dump_json()}\n" for prediction in result.predictions
    ).encode("utf-8")
    outputs = {
        "model.json": model_content,
        "model.txt": result.artifact,
        "metrics.json": metrics_content,
        "predictions.jsonl": predictions_content,
        "manifest.json": (
            json.dumps(
                {
                    "report_version": FEATURE_LIGHTGBM_REPORT_VERSION,
                    "model_type": FEATURE_LIGHTGBM_MODEL_TYPE,
                    "purpose": result.model.purpose.value,
                    "label_contract_id": result.model.label_contract_id,
                    "feature_set_version_id": result.model.feature_set_version_id,
                    "feature_names": result.model.feature_names,
                    "class_labels": [label.value for label in result.model.class_labels],
                    "feature_dataset_manifest_sha256": (
                        result.model.feature_dataset_manifest_sha256
                    ),
                    "source_label_dataset_sha256": result.model.source_label_dataset_sha256,
                    "label_split_manifest_sha256": result.model.label_split_manifest_sha256,
                    "artifact_sha256": result.model.artifact_sha256,
                    "model_sha256": _sha256(model_content),
                    "metrics_sha256": _sha256(metrics_content),
                    "predictions_sha256": _sha256(predictions_content),
                    "training_parameters": result.model.parameters,
                    "environment": {
                        "python_implementation": platform.python_implementation(),
                        "python_version": platform.python_version(),
                    },
                    "row_counts": result.model.row_counts,
                    "purged_row_count": result.model.purged_row_count,
                    "model_version_id": result.model.model_version_id,
                    "calibrated": False,
                    "purged_rows_excluded": True,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8"),
    }
    _write_feature_lightgbm_outputs(Path(output_dir), outputs)


def _write_feature_lightgbm_outputs(
    destination: Path,
    outputs: dict[str, bytes],
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    paths = tuple(destination / name for name in outputs)
    if any(path.exists() for path in paths):
        raise FileExistsError(f"feature LightGBM output already exists: {destination}")
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


def _validate_training_input(
    split_input: TrainingFeatureSplitInput,
    minimum_labeled_samples: int,
) -> None:
    if minimum_labeled_samples <= 0:
        raise ValueError("minimum_labeled_samples must be positive")
    if split_input.report.failure_count:
        raise ValueError("feature join contains failures; incomplete coverage cannot train")
    partitions = {
        "train": split_input.train_rows,
        "validation": split_input.validation_rows,
        "test": split_input.test_rows,
    }
    if {name: len(rows) for name, rows in partitions.items()} != {
        name: split_input.manifest.row_counts[name] for name in partitions
    }:
        raise ValueError("feature rows do not match feature manifest")
    if any(
        len(partitions[name]) != split_input.manifest.label_row_counts[name] for name in partitions
    ):
        raise ValueError("feature rows do not match label lineage")
    if split_input.report.joined_row_count != sum(len(rows) for rows in partitions.values()):
        raise ValueError("feature rows do not match join report")
    if not split_input.train_rows:
        raise ValueError("feature LightGBM training split contains no rows")
    if not split_input.validation_rows:
        raise ValueError("feature LightGBM validation split contains no rows")
    rows = split_input.train_rows + split_input.validation_rows + split_input.test_rows
    if len(rows) < minimum_labeled_samples:
        raise ValueError(f"feature labels {len(rows)} are below minimum {minimum_labeled_samples}")
    labels = _labels_for(split_input.manifest.purpose)
    train_targets = Counter(row.target for row in split_input.train_rows)
    if any(train_targets[label] == 0 for label in labels):
        raise ValueError("feature LightGBM training labels must contain every purpose class")
    if split_input.purged_rows:
        purged_ids = {row.sample_id for row in split_input.purged_rows}
        if any(row.sample_id in purged_ids for row in rows):
            raise ValueError("purged labels must not appear in feature training rows")


def _parameters(
    labels: Sequence[TrainingLabel],
    random_seed: int,
) -> dict[str, str | int | float]:
    multiclass = len(labels) > 2
    parameters: dict[str, str | int | float] = {
        "objective": "multiclass" if multiclass else "binary",
        "metric": "multi_logloss" if multiclass else "binary_logloss",
        "verbosity": -1,
        "seed": random_seed,
        "feature_fraction_seed": random_seed,
        "bagging_seed": random_seed,
        "data_random_seed": random_seed,
        "deterministic": "true",
        "force_col_wise": "true",
        "num_threads": 1,
        "learning_rate": 0.05,
        "num_leaves": 15,
        "min_data_in_leaf": 10,
        "feature_fraction": 1.0,
        "bagging_fraction": 1.0,
        "bagging_freq": 0,
        "num_boost_round": FEATURE_LIGHTGBM_BOOST_ROUNDS,
    }
    if multiclass:
        parameters["num_class"] = len(labels)
    return parameters


def _load_lightgbm() -> Any:
    try:
        return import_module("lightgbm")
    except (ImportError, OSError) as exc:
        raise FeatureLightGBMUnavailableError(
            "LightGBM is unavailable; run `uv sync --extra ml` before training"
        ) from exc


def _load_numpy() -> Any:
    try:
        return import_module("numpy")
    except (ImportError, OSError) as exc:
        raise FeatureLightGBMUnavailableError(
            "NumPy is unavailable; run `uv sync --extra ml` before training"
        ) from exc


def _matrix(
    rows: Sequence[SelectionTrainingFeatureRow],
    feature_names: Sequence[str],
) -> list[list[float]]:
    return [[float(row.features[name]) for name in feature_names] for row in rows]


def _predict_probabilities(
    booster: Any,
    rows: Sequence[SelectionTrainingFeatureRow],
    feature_names: Sequence[str],
    labels: Sequence[TrainingLabel],
    numpy: Any,
) -> tuple[tuple[Decimal, ...], ...]:
    if not rows:
        return ()
    raw = booster.predict(numpy.asarray(_matrix(rows, feature_names), dtype=float))
    values = raw.tolist() if hasattr(raw, "tolist") else raw
    class_count = len(labels)
    if class_count == 2:
        probability_rows: list[Sequence[Any]] = _binary_probability_rows(values, len(rows))
    else:
        probability_rows = _multiclass_probability_rows(values, len(rows), class_count)
    return tuple(_normalize_probability_row(row, class_count) for row in probability_rows)


def _binary_probability_rows(values: Any, row_count: int) -> list[Sequence[Any]]:
    if len(values) != row_count:
        raise ValueError("LightGBM binary prediction count does not match rows")
    rows: list[Sequence[Any]] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            if len(value) != 2:
                raise ValueError("LightGBM binary prediction row has invalid class count")
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
        raise ValueError("LightGBM multiclass prediction shape does not match rows")
    if any(len(row) != class_count for row in rows):
        raise ValueError("LightGBM multiclass prediction row has invalid class count")
    return rows


def _normalize_probability_row(values: Sequence[Any], class_count: int) -> tuple[Decimal, ...]:
    probabilities: list[Decimal] = []
    for value in values:
        try:
            probability = Decimal(str(float(value)))
        except (TypeError, ValueError):
            raise ValueError("LightGBM produced an invalid probability") from None
        if not probability.is_finite() or probability < 0 or probability > 1:
            raise ValueError("LightGBM produced an invalid probability")
        probabilities.append(probability)
    if len(probabilities) != class_count:
        raise ValueError("LightGBM produced an invalid class probability vector")
    total = sum(probabilities, Decimal("0"))
    if total <= 0 or abs(total - Decimal("1")) > Decimal("1e-6"):
        raise ValueError("LightGBM class probabilities must sum to one")
    return tuple(probability / total for probability in probabilities)


def _metrics(
    split: SplitName,
    rows: Sequence[SelectionTrainingFeatureRow],
    probabilities: Sequence[Sequence[Decimal]],
    labels: Sequence[TrainingLabel],
) -> FeatureLightGBMMetrics:
    target_counts = {label.value: 0 for label in labels}
    mean_probabilities = {label.value: Decimal("0") for label in labels}
    for row in rows:
        target_counts[row.target.value] += 1
    if not rows:
        return FeatureLightGBMMetrics(
            split=split,
            row_count=0,
            coverage=Decimal("0"),
            target_counts=target_counts,
            mean_predicted_probabilities=mean_probabilities,
        )
    for probability_row in probabilities:
        for index, label in enumerate(labels):
            mean_probabilities[label.value] += probability_row[index]
    mean_probabilities = {
        name: value / Decimal(len(rows)) for name, value in mean_probabilities.items()
    }
    target_indices = [labels.index(row.target) for row in rows]
    log_loss = -sum(
        math.log(max(float(probabilities[index][target_index]), 1e-15))
        for index, target_index in enumerate(target_indices)
    ) / len(rows)
    brier = sum(
        sum(
            (float(probability) - int(class_index == target_index)) ** 2
            for class_index, probability in enumerate(probability_row)
        )
        / len(labels)
        for probability_row, target_index in zip(probabilities, target_indices, strict=True)
    ) / len(rows)
    return FeatureLightGBMMetrics(
        split=split,
        row_count=len(rows),
        coverage=Decimal("1"),
        target_counts=target_counts,
        mean_predicted_probabilities=mean_probabilities,
        log_loss=log_loss,
        normalized_brier_score=brier,
    )


def _predictions(
    split: PredictionSplit,
    rows: Sequence[SelectionTrainingFeatureRow],
    probabilities: Sequence[Sequence[Decimal]],
    labels: Sequence[TrainingLabel],
) -> tuple[FeatureLightGBMPrediction, ...]:
    return tuple(
        FeatureLightGBMPrediction(
            split=split,
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
        for row, probability in zip(rows, probabilities, strict=True)
    )


def _labels_for(purpose: TrainingDatasetPurpose) -> tuple[TrainingLabel, ...]:
    return _PURPOSE_LABEL_ORDER[purpose]


def _is_sha256(value: str) -> bool:
    normalized = value.strip().lower()
    return len(normalized) == 64 and all(char in "0123456789abcdef" for char in normalized)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
