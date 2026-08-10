"""Versioned feature rows and a dependency-free constant baseline trainer."""

from __future__ import annotations

import hashlib
import json
import math
import platform
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.features.contract import FeatureSnapshot
from stratgauge.ml.evaluation import ModelEvaluationEvidence

FEATURE_DATASET_SCHEMA_VERSION = "selection-feature-dataset-v1"
BASELINE_REPORT_VERSION = "selection-baseline-v1"
BaselineLabel = Literal["win", "loss"]
SplitName = Literal["train", "validation", "test"]


class SelectionFeatureRow(DomainModel):
    """One matured, point-in-time feature row eligible for training."""

    schema_version: str = FEATURE_DATASET_SCHEMA_VERSION
    candidate_id: str
    symbol: str
    selection_date: date
    feature_set_version_id: str
    feature_snapshot_id: str
    as_of: datetime
    label_evaluated_at: datetime
    tracking_policy_id: str
    label: BaselineLabel
    source_snapshot_ids: tuple[str, ...]
    features: dict[str, Decimal]

    @field_validator(
        "candidate_id",
        "symbol",
        "feature_set_version_id",
        "feature_snapshot_id",
        "tracking_policy_id",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("feature row text must not be empty")
        return value.strip()

    @field_validator("as_of", "label_evaluated_at")
    @classmethod
    def validate_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("feature row timestamps must include a timezone")
        return value

    @field_validator("source_snapshot_ids")
    @classmethod
    def validate_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("feature row requires source snapshot IDs")
        return tuple(item.strip() for item in value)

    @field_validator("features")
    @classmethod
    def validate_features(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        if not value:
            raise ValueError("feature row must contain features")
        if any(not item.is_finite() for item in value.values()):
            raise ValueError("feature values must be finite")
        return value

    @model_validator(mode="after")
    def validate_temporal_lineage(self) -> SelectionFeatureRow:
        if self.as_of.date() < self.selection_date:
            raise ValueError("as_of cannot precede selection_date")
        if self.label_evaluated_at.date() <= self.selection_date:
            raise ValueError("label_evaluated_at must follow selection_date")
        return self


class BaselineModel(DomainModel):
    """JSON-serializable constant-probability baseline model."""

    model_version_id: str
    model_type: Literal["constant_probability"] = "constant_probability"
    report_version: str = BASELINE_REPORT_VERSION
    feature_set_version_id: str
    tracking_policy_id: str
    positive_label: BaselineLabel = "win"
    positive_probability: Decimal = Field(ge=0, le=1)
    train_row_count: int = Field(gt=0)
    train_positive_count: int = Field(ge=0)
    random_seed: int
    source_file_sha256: str

    @field_validator(
        "model_version_id",
        "feature_set_version_id",
        "tracking_policy_id",
        "source_file_sha256",
    )
    @classmethod
    def validate_model_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("baseline model text must not be empty")
        return value.strip()

    @model_validator(mode="after")
    def validate_counts(self) -> BaselineModel:
        if self.train_positive_count > self.train_row_count:
            raise ValueError("train_positive_count cannot exceed train_row_count")
        return self


class BaselineMetrics(DomainModel):
    """Metrics for one chronological data partition."""

    split: Literal["train", "validation", "test"]
    row_count: int = Field(ge=0)
    positive_count: int = Field(ge=0)
    coverage: Decimal = Field(ge=0, le=1)
    predicted_probability: Decimal = Field(ge=0, le=1)
    log_loss: float | None = Field(default=None, ge=0)
    brier_score: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_metric_counts(self) -> BaselineMetrics:
        if self.positive_count > self.row_count:
            raise ValueError("positive_count cannot exceed row_count")
        if self.row_count == 0 and self.coverage != 0:
            raise ValueError("empty split coverage must be zero")
        return self


class BaselinePrediction(DomainModel):
    """Out-of-sample prediction retained for audit and ranking inspection."""

    split: Literal["validation", "test"]
    candidate_id: str
    symbol: str
    selection_date: date
    label: BaselineLabel
    predicted_probability: Decimal = Field(ge=0, le=1)
    rank: int = Field(gt=0)
    feature_snapshot_id: str


class BaselineTrainingResult(DomainModel):
    """Immutable result of one chronological baseline training run."""

    model: BaselineModel
    metrics: tuple[BaselineMetrics, ...]
    predictions: tuple[BaselinePrediction, ...]
    source_file_sha256: str
    train_end: date
    validation_end: date
    row_counts: dict[str, int]


def feature_row_from_snapshot(
    snapshot: FeatureSnapshot,
    *,
    candidate_id: str,
    tracking_policy_id: str,
    label: BaselineLabel,
    label_evaluated_at: datetime,
) -> SelectionFeatureRow:
    """Attach a matured outcome label to an immutable feature snapshot."""

    return SelectionFeatureRow(
        candidate_id=candidate_id,
        symbol=snapshot.symbol,
        selection_date=snapshot.selection_date,
        feature_set_version_id=snapshot.feature_set_version_id,
        feature_snapshot_id=snapshot.feature_snapshot_id,
        as_of=snapshot.as_of,
        label_evaluated_at=label_evaluated_at,
        tracking_policy_id=tracking_policy_id,
        label=label,
        source_snapshot_ids=snapshot.source_snapshot_ids,
        features=snapshot.values,
    )


def read_feature_dataset(content: bytes) -> tuple[SelectionFeatureRow, ...]:
    """Parse and validate versioned feature JSONL."""

    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("feature dataset is not valid UTF-8") from exc
    rows: list[SelectionFeatureRow] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("feature dataset row must be a JSON object")
            if payload.get("schema_version") != FEATURE_DATASET_SCHEMA_VERSION:
                raise ValueError("unsupported feature dataset schema")
            rows.append(SelectionFeatureRow.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid feature row at line {line_number}: {exc}") from exc
    if not rows:
        raise ValueError("feature dataset contains no rows")
    _validate_unique_rows(rows)
    return tuple(rows)


def serialize_feature_dataset(rows: Iterable[SelectionFeatureRow]) -> str:
    """Serialize feature rows as deterministic newline-delimited JSON."""

    return "".join(f"{row.model_dump_json()}\n" for row in rows)


def write_feature_dataset(path: str | Path, rows: Iterable[SelectionFeatureRow]) -> None:
    """Write feature rows once without overwriting prior evidence."""

    destination = Path(path)
    if destination.exists():
        raise FileExistsError(f"feature dataset already exists: {destination}")
    content = serialize_feature_dataset(rows)
    if not content:
        raise ValueError("cannot write an empty feature dataset")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def split_feature_dataset(
    rows: Iterable[SelectionFeatureRow],
    *,
    train_end: date,
    validation_end: date,
) -> dict[str, tuple[SelectionFeatureRow, ...]]:
    """Split feature rows by selection date, keeping one date in one partition."""

    if train_end >= validation_end:
        raise ValueError("train_end must precede validation_end")
    buckets: dict[str, list[SelectionFeatureRow]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    ordered = sorted(rows, key=lambda row: (row.selection_date, row.candidate_id))
    for row in ordered:
        bucket = (
            "train"
            if row.selection_date <= train_end
            else "validation"
            if row.selection_date <= validation_end
            else "test"
        )
        buckets[bucket].append(row)
    return {name: tuple(items) for name, items in buckets.items()}


def train_constant_baseline(
    rows: Iterable[SelectionFeatureRow],
    *,
    train_end: date,
    validation_end: date,
    source_file_sha256: str,
    random_seed: int = 0,
    expected_feature_set_version_id: str | None = None,
    expected_tracking_policy_id: str | None = None,
    label_field: Literal["label"] = "label",
) -> BaselineTrainingResult:
    """Fit and evaluate the smallest useful chronological baseline."""

    all_rows = tuple(rows)
    _validate_unique_rows(all_rows)
    if label_field != "label":
        raise ValueError(f"unsupported label field: {label_field}")
    feature_sets = {row.feature_set_version_id for row in all_rows}
    if len(feature_sets) != 1:
        raise ValueError("feature dataset contains multiple feature-set versions")
    tracking_policies = {row.tracking_policy_id for row in all_rows}
    if len(tracking_policies) != 1:
        raise ValueError("feature dataset contains multiple tracking policies")
    tracking_policy_id = next(iter(tracking_policies))
    if (
        expected_tracking_policy_id is not None
        and expected_tracking_policy_id != tracking_policy_id
    ):
        raise ValueError("feature dataset does not match expected tracking policy")
    if (
        expected_feature_set_version_id is not None
        and expected_feature_set_version_id not in feature_sets
    ):
        raise ValueError("feature dataset does not match expected feature-set version")
    feature_names = {tuple(sorted(row.features)) for row in all_rows}
    if len(feature_names) != 1:
        raise ValueError("feature dataset rows have different feature columns")
    splits = split_feature_dataset(
        all_rows,
        train_end=train_end,
        validation_end=validation_end,
    )
    train_rows = splits["train"]
    if not train_rows:
        raise ValueError("training split contains no rows")
    if not splits["validation"]:
        raise ValueError("validation split contains no rows")
    positive_count = sum(row.label == "win" for row in train_rows)
    if positive_count == 0 or positive_count == len(train_rows):
        raise ValueError("training labels must contain at least two classes")
    probability = Decimal(positive_count) / Decimal(len(train_rows))
    model_payload = {
        "feature_set_version_id": next(iter(feature_sets)),
        "tracking_policy_id": tracking_policy_id,
        "positive_label": "win",
        "positive_probability": str(probability),
        "random_seed": random_seed,
        "source_file_sha256": source_file_sha256,
        "train_end": train_end.isoformat(),
        "train_positive_count": positive_count,
        "train_row_count": len(train_rows),
        "validation_end": validation_end.isoformat(),
    }
    model_digest = hashlib.sha256(
        json.dumps(model_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    model = BaselineModel(
        model_version_id=f"baseline-{model_digest[:20]}",
        feature_set_version_id=next(iter(feature_sets)),
        tracking_policy_id=tracking_policy_id,
        positive_probability=probability,
        train_row_count=len(train_rows),
        train_positive_count=positive_count,
        random_seed=random_seed,
        source_file_sha256=source_file_sha256,
    )
    metrics = tuple(
        _metrics(cast(SplitName, split_name), split_rows, probability)
        for split_name, split_rows in splits.items()
    )
    predictions = tuple(
        BaselinePrediction(
            split=split_name,
            candidate_id=row.candidate_id,
            symbol=row.symbol,
            selection_date=row.selection_date,
            label=row.label,
            predicted_probability=probability,
            rank=1,
            feature_snapshot_id=row.feature_snapshot_id,
        )
        for split_name in ("validation", "test")
        for row in splits[split_name]
    )
    return BaselineTrainingResult(
        model=model,
        metrics=metrics,
        predictions=predictions,
        source_file_sha256=source_file_sha256,
        train_end=train_end,
        validation_end=validation_end,
        row_counts={name: len(split_rows) for name, split_rows in splits.items()},
    )


def write_baseline_result(
    output_dir: str | Path,
    result: BaselineTrainingResult,
) -> None:
    """Write model, metrics, predictions and manifest without overwriting."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    evaluation = ModelEvaluationEvidence(
        model_version_id=result.model.model_version_id,
        model_type=result.model.model_type,
        feature_set_version_id=result.model.feature_set_version_id,
        tracking_policy_id=result.model.tracking_policy_id,
        source_file_sha256=result.source_file_sha256,
        metrics=_evaluation_metrics(result.metrics),
    )
    outputs = {
        "model.json": result.model.model_dump_json(indent=2) + "\n",
        "metrics.json": evaluation.model_dump_json(indent=2) + "\n",
        "predictions.jsonl": "".join(
            f"{prediction.model_dump_json()}\n" for prediction in result.predictions
        ),
        "manifest.json": json.dumps(
            {
                "report_version": BASELINE_REPORT_VERSION,
                "source_file_sha256": result.source_file_sha256,
                "training_parameters": {
                    "model_type": result.model.model_type,
                    "random_seed": result.model.random_seed,
                    "label_field": "label",
                },
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
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    }
    paths = tuple(destination / name for name in outputs)
    if any(path.exists() for path in paths):
        raise FileExistsError(f"baseline output already exists: {destination}")
    temporary_paths: list[Path] = []
    try:
        for path, content in zip(paths, outputs.values(), strict=True):
            temporary = path.with_suffix(f"{path.suffix}.tmp")
            temporary.write_text(content, encoding="utf-8", newline="\n")
            temporary_paths.append(temporary)
        for temporary, path in zip(temporary_paths, paths, strict=True):
            temporary.replace(path)
    except OSError:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)
        for path in paths:
            path.unlink(missing_ok=True)
        raise


def _evaluation_metrics(
    metrics: Sequence[BaselineMetrics],
) -> dict[str, Decimal | None]:
    values: dict[str, Decimal | None] = {}
    for metric in metrics:
        values[f"{metric.split}_coverage"] = metric.coverage
        values[f"{metric.split}_log_loss"] = (
            None if metric.log_loss is None else Decimal(str(metric.log_loss))
        )
        values[f"{metric.split}_brier_score"] = (
            None if metric.brier_score is None else Decimal(str(metric.brier_score))
        )
    return values


def _metrics(
    split: SplitName,
    rows: Sequence[SelectionFeatureRow],
    probability: Decimal,
) -> BaselineMetrics:
    if split not in {"train", "validation", "test"}:
        raise ValueError(f"unsupported split: {split}")
    positive_count = sum(row.label == "win" for row in rows)
    if not rows:
        return BaselineMetrics(
            split=split,
            row_count=0,
            positive_count=0,
            coverage=Decimal("0"),
            predicted_probability=probability,
        )
    probability_float = min(max(float(probability), 1e-15), 1 - 1e-15)
    log_loss = -sum(
        math.log(probability_float if row.label == "win" else 1 - probability_float) for row in rows
    ) / len(rows)
    brier = sum((probability_float - (1 if row.label == "win" else 0)) ** 2 for row in rows) / len(
        rows
    )
    return BaselineMetrics(
        split=split,
        row_count=len(rows),
        positive_count=positive_count,
        coverage=Decimal("1"),
        predicted_probability=probability,
        log_loss=log_loss,
        brier_score=brier,
    )


def _validate_unique_rows(rows: Sequence[SelectionFeatureRow]) -> None:
    keys = [(row.candidate_id, row.selection_date) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("feature dataset contains duplicate candidate/date rows")
