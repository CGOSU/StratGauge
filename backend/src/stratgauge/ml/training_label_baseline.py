"""Train dependency-free constant label distributions for prepared ML targets."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml.training_datasets import (
    TRAINING_LABEL_DATASET_SCHEMA_VERSION,
    PreparedTrainingLabelDataset,
    SelectionTrainingLabelRow,
    TrainingDatasetPurpose,
    TrainingLabel,
    TrainingLabelDatasetSplit,
    TrainingLabelReadinessReport,
    TrainingLabelSplitManifest,
    audit_training_label_split,
    build_training_label_split_manifest,
    read_training_label_dataset,
    split_training_label_dataset,
)

LABEL_BASELINE_REPORT_VERSION = "selection-label-baseline-v1"
LABEL_BASELINE_EVALUATION_VERSION = "selection-label-baseline-evaluation-v1"
LABEL_BASELINE_MODEL_TYPE: Literal["constant_label_distribution"] = "constant_label_distribution"
SplitName = Literal["train", "validation", "test"]

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


class TrainingLabelSplitInput(DomainModel):
    """Prepared split files and the two reports required before fitting."""

    split: TrainingLabelDatasetSplit
    manifest: TrainingLabelSplitManifest
    readiness: TrainingLabelReadinessReport
    split_manifest_sha256: str

    @field_validator("split_manifest_sha256")
    @classmethod
    def validate_manifest_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("split_manifest_sha256 must be a SHA-256 digest")
        return normalized


class LabelBaselineModel(DomainModel):
    """Immutable metadata and class prior for one purpose-specific baseline."""

    model_version_id: str
    model_type: Literal["constant_label_distribution"] = LABEL_BASELINE_MODEL_TYPE
    report_version: str = LABEL_BASELINE_REPORT_VERSION
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    class_labels: tuple[TrainingLabel, ...]
    probabilities: dict[str, Decimal]
    train_row_count: int = Field(gt=0)
    train_target_counts: dict[str, int]
    random_seed: int
    minimum_labeled_samples: int = Field(gt=0)
    source_file_sha256: str
    prepared_dataset_sha256: str
    split_manifest_sha256: str
    train_end: date
    validation_end: date
    label_available_through: datetime
    row_counts: dict[str, int]

    @field_validator(
        "model_version_id",
        "label_contract_id",
        "source_file_sha256",
        "prepared_dataset_sha256",
        "split_manifest_sha256",
    )
    @classmethod
    def validate_model_text(cls, value: str) -> str:
        normalized = value.strip().lower() if "sha256" in value else value.strip()
        if not normalized:
            raise ValueError("label baseline model text must not be empty")
        return normalized

    @field_validator("label_available_through")
    @classmethod
    def validate_model_cutoff(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("label baseline cutoff must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_distribution(self) -> LabelBaselineModel:
        expected_labels = _labels_for(self.purpose)
        if self.class_labels != expected_labels:
            raise ValueError("label baseline class labels do not match purpose")
        if set(self.probabilities) != {label.value for label in expected_labels}:
            raise ValueError("label baseline probabilities do not match class labels")
        if any(
            not probability.is_finite() or probability < 0 or probability > 1
            for probability in self.probabilities.values()
        ):
            raise ValueError("label baseline probabilities must be finite and bounded")
        if abs(sum(self.probabilities.values(), Decimal("0")) - Decimal("1")) > Decimal("1e-20"):
            raise ValueError("label baseline probabilities must sum to one")
        if sum(self.train_target_counts.values()) != self.train_row_count:
            raise ValueError("label baseline target counts do not match train row count")
        if set(self.train_target_counts) != {label.value for label in expected_labels}:
            raise ValueError("label baseline target counts do not match class labels")
        return self


class LabelBaselineMetrics(DomainModel):
    """One partition's metrics under the fixed train class distribution."""

    split: SplitName
    row_count: int = Field(ge=0)
    coverage: Decimal = Field(ge=0, le=1)
    target_counts: dict[str, int]
    predicted_probabilities: dict[str, Decimal]
    log_loss: float | None = Field(default=None, ge=0)
    normalized_brier_score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_metric_counts(self) -> LabelBaselineMetrics:
        if sum(self.target_counts.values()) != self.row_count:
            raise ValueError("label baseline metric target counts do not match row count")
        if set(self.target_counts) != set(self.predicted_probabilities):
            raise ValueError("label baseline metric classes do not match")
        if self.row_count == 0:
            if self.coverage != 0 or self.log_loss is not None:
                raise ValueError("empty label baseline metrics must have zero coverage and no loss")
        elif self.coverage != 1:
            raise ValueError("non-empty label baseline metrics must have full coverage")
        return self


class LabelBaselinePrediction(DomainModel):
    """One validation/test prediction retained for audit and future ranking review."""

    split: Literal["validation", "test"]
    sample_id: str
    candidate_id: str
    batch_id: str
    symbol: str
    observation_date: date
    label_available_at: datetime
    target: TrainingLabel
    predicted_label: TrainingLabel
    predicted_probabilities: dict[str, Decimal]
    source_row_sha256: str

    @field_validator("label_available_at")
    @classmethod
    def validate_prediction_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("label baseline prediction time must include a timezone")
        return value


class LabelBaselineEvaluationEvidence(DomainModel):
    """Purpose-aware evaluation evidence kept separate from feature-model evidence."""

    evaluation_version: str = LABEL_BASELINE_EVALUATION_VERSION
    model_version_id: str
    model_type: Literal["constant_label_distribution"] = LABEL_BASELINE_MODEL_TYPE
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    source_file_sha256: str
    prepared_dataset_sha256: str
    split_manifest_sha256: str
    metrics: tuple[LabelBaselineMetrics, ...]


class LabelBaselineTrainingResult(DomainModel):
    """All immutable outputs from one constant label-distribution training run."""

    model: LabelBaselineModel
    metrics: tuple[LabelBaselineMetrics, ...]
    predictions: tuple[LabelBaselinePrediction, ...]
    source_file_sha256: str
    prepared_dataset_sha256: str
    split_manifest_sha256: str
    row_counts: dict[str, int]


def read_training_label_split(output_dir: str | Path) -> TrainingLabelSplitInput:
    """Read and cross-check split files, manifest and readiness evidence."""

    directory = Path(output_dir)
    manifest_path = directory / "manifest.json"
    readiness_path = directory / "readiness.json"
    try:
        manifest_content = manifest_path.read_bytes()
        readiness_content = readiness_path.read_bytes()
        manifest = TrainingLabelSplitManifest.model_validate_json(manifest_content)
        readiness = TrainingLabelReadinessReport.model_validate_json(readiness_content)
        partition_rows = {
            name: read_training_label_dataset((directory / f"{name}.jsonl").read_bytes())
            for name in ("train", "validation", "test", "purged")
        }
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid training label split: {directory}: {exc}") from exc
    _validate_manifest_identity(manifest)
    rows = tuple(
        sorted(
            (
                *partition_rows["train"],
                *partition_rows["validation"],
                *partition_rows["test"],
                *partition_rows["purged"],
            ),
            key=lambda row: (row.observation_date, row.sample_id),
        )
    )
    prepared = PreparedTrainingLabelDataset(
        purpose=manifest.purpose,
        label_contract_id=manifest.label_contract_id,
        source_schema_version=manifest.source_schema_version,
        source_file_sha256=manifest.source_file_sha256,
        source_row_count=manifest.source_row_count,
        labeled_row_count=len(rows),
        skipped_unlabeled_row_count=manifest.skipped_unlabeled_row_count,
        filtered_policy_row_count=manifest.filtered_policy_row_count,
        tracking_policy_id=manifest.tracking_policy_id,
        outcome_policy_id=manifest.outcome_policy_id,
        window_sessions=manifest.window_sessions,
        rows=rows,
    )
    split = TrainingLabelDatasetSplit(
        prepared=prepared,
        train_rows=partition_rows["train"],
        validation_rows=partition_rows["validation"],
        test_rows=partition_rows["test"],
        purged_rows=partition_rows["purged"],
        train_end=manifest.train_end,
        validation_end=manifest.validation_end,
        label_available_through=manifest.label_available_through,
    )
    expected_split = split_training_label_dataset(
        prepared,
        train_end=manifest.train_end,
        validation_end=manifest.validation_end,
        label_available_through=manifest.label_available_through,
    )
    if split != expected_split:
        raise ValueError("training label split files do not match label availability boundaries")
    expected_manifest = build_training_label_split_manifest(expected_split)
    if manifest != expected_manifest:
        raise ValueError("training label split manifest does not match split files")
    _validate_readiness(readiness, expected_split)
    return TrainingLabelSplitInput(
        split=expected_split,
        manifest=manifest,
        readiness=readiness,
        split_manifest_sha256=_sha256(manifest_content),
    )


def train_constant_label_baseline(
    split_input: TrainingLabelSplitInput,
    *,
    random_seed: int = 0,
    minimum_labeled_samples: int = 100,
) -> LabelBaselineTrainingResult:
    """Fit train class priors and evaluate them without reading purged rows."""

    if minimum_labeled_samples <= 0:
        raise ValueError("minimum_labeled_samples must be positive")
    readiness = audit_training_label_split(
        split_input.split,
        minimum_labeled_samples=minimum_labeled_samples,
    )
    if not readiness.eligible:
        raise ValueError("training label split is not eligible: " + "; ".join(readiness.blockers))
    train_rows = split_input.split.train_rows
    labels = _labels_for(split_input.split.prepared.purpose)
    counts = Counter(row.target for row in train_rows)
    if len([label for label in labels if counts[label]]) < 2:
        raise ValueError("training labels must contain at least two classes")
    probabilities = {
        label.value: Decimal(counts[label]) / Decimal(len(train_rows)) for label in labels
    }
    model_identity = {
        "label_contract_id": split_input.manifest.label_contract_id,
        "model_type": LABEL_BASELINE_MODEL_TYPE,
        "prepared_dataset_sha256": split_input.manifest.prepared_dataset_sha256,
        "probabilities": {key: str(value) for key, value in probabilities.items()},
        "purpose": split_input.manifest.purpose.value,
        "random_seed": random_seed,
        "source_file_sha256": split_input.manifest.source_file_sha256,
        "split_manifest_sha256": split_input.split_manifest_sha256,
        "train_end": split_input.manifest.train_end.isoformat(),
        "validation_end": split_input.manifest.validation_end.isoformat(),
    }
    model_version_id = f"label-baseline-{_sha256(_canonical_json(model_identity))[:20]}"
    model = LabelBaselineModel(
        model_version_id=model_version_id,
        purpose=split_input.manifest.purpose,
        label_contract_id=split_input.manifest.label_contract_id,
        class_labels=labels,
        probabilities=probabilities,
        train_row_count=len(train_rows),
        train_target_counts={label.value: counts[label] for label in labels},
        random_seed=random_seed,
        minimum_labeled_samples=minimum_labeled_samples,
        source_file_sha256=split_input.manifest.source_file_sha256,
        prepared_dataset_sha256=split_input.manifest.prepared_dataset_sha256,
        split_manifest_sha256=split_input.split_manifest_sha256,
        train_end=split_input.manifest.train_end,
        validation_end=split_input.manifest.validation_end,
        label_available_through=split_input.manifest.label_available_through,
        row_counts=split_input.manifest.row_counts,
    )
    metrics = tuple(
        _metrics(cast(SplitName, split_name), rows, probabilities, labels)
        for split_name, rows in (
            ("train", split_input.split.train_rows),
            ("validation", split_input.split.validation_rows),
            ("test", split_input.split.test_rows),
        )
    )
    predictions = tuple(
        prediction
        for split_name, rows in (
            ("validation", split_input.split.validation_rows),
            ("test", split_input.split.test_rows),
        )
        for prediction in _predictions(
            cast(Literal["validation", "test"], split_name), rows, probabilities, labels
        )
    )
    return LabelBaselineTrainingResult(
        model=model,
        metrics=metrics,
        predictions=predictions,
        source_file_sha256=model.source_file_sha256,
        prepared_dataset_sha256=model.prepared_dataset_sha256,
        split_manifest_sha256=model.split_manifest_sha256,
        row_counts=model.row_counts,
    )


def write_constant_label_baseline(
    output_dir: str | Path,
    result: LabelBaselineTrainingResult,
) -> None:
    """Write model, evaluation, predictions and manifest without overwriting."""

    destination = Path(output_dir)
    outputs = {
        "model.json": result.model.model_dump_json(indent=2) + "\n",
        "metrics.json": LabelBaselineEvaluationEvidence(
            model_version_id=result.model.model_version_id,
            purpose=result.model.purpose,
            label_contract_id=result.model.label_contract_id,
            source_file_sha256=result.source_file_sha256,
            prepared_dataset_sha256=result.prepared_dataset_sha256,
            split_manifest_sha256=result.split_manifest_sha256,
            metrics=result.metrics,
        ).model_dump_json(indent=2)
        + "\n",
        "predictions.jsonl": "".join(
            f"{prediction.model_dump_json()}\n" for prediction in result.predictions
        ),
        "manifest.json": json.dumps(
            {
                "report_version": LABEL_BASELINE_REPORT_VERSION,
                "model_type": LABEL_BASELINE_MODEL_TYPE,
                "purpose": result.model.purpose.value,
                "label_contract_id": result.model.label_contract_id,
                "source_file_sha256": result.source_file_sha256,
                "prepared_dataset_sha256": result.prepared_dataset_sha256,
                "split_manifest_sha256": result.split_manifest_sha256,
                "training_parameters": {
                    "random_seed": result.model.random_seed,
                    "minimum_labeled_samples": result.model.minimum_labeled_samples,
                    "brier_score": "multiclass squared error divided by 2",
                },
                "class_labels": [label.value for label in result.model.class_labels],
                "train_end": result.model.train_end.isoformat(),
                "validation_end": result.model.validation_end.isoformat(),
                "label_available_through": result.model.label_available_through.isoformat(),
                "row_counts": result.row_counts,
                "model_version_id": result.model.model_version_id,
                "purged_rows_excluded": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    }
    destination.mkdir(parents=True, exist_ok=True)
    paths = tuple(destination / name for name in outputs)
    if any(path.exists() for path in paths):
        raise FileExistsError(f"label baseline output already exists: {destination}")
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


def _metrics(
    split: SplitName,
    rows: Sequence[SelectionTrainingLabelRow],
    probabilities: dict[str, Decimal],
    labels: tuple[TrainingLabel, ...],
) -> LabelBaselineMetrics:
    target_counts = {label.value: 0 for label in labels}
    for row in rows:
        target_counts[row.target.value] += 1
    if not rows:
        return LabelBaselineMetrics(
            split=split,
            row_count=0,
            coverage=Decimal("0"),
            target_counts=target_counts,
            predicted_probabilities=probabilities,
        )
    probability_values = {label.value: float(probabilities[label.value]) for label in labels}
    log_loss = -sum(
        math.log(max(probability_values[row.target.value], 1e-15)) for row in rows
    ) / len(rows)
    brier = sum(
        sum((probability_values[label.value] - int(row.target == label)) ** 2 for label in labels)
        / 2
        for row in rows
    ) / len(rows)
    return LabelBaselineMetrics(
        split=split,
        row_count=len(rows),
        coverage=Decimal("1"),
        target_counts=target_counts,
        predicted_probabilities=probabilities,
        log_loss=log_loss,
        normalized_brier_score=brier,
    )


def _predictions(
    split: Literal["validation", "test"],
    rows: Sequence[SelectionTrainingLabelRow],
    probabilities: dict[str, Decimal],
    labels: tuple[TrainingLabel, ...],
) -> tuple[LabelBaselinePrediction, ...]:
    predicted_label = max(
        labels, key=lambda label: (probabilities[label.value], -labels.index(label))
    )
    return tuple(
        LabelBaselinePrediction(
            split=split,
            sample_id=row.sample_id,
            candidate_id=row.candidate_id,
            batch_id=row.batch_id,
            symbol=row.symbol,
            observation_date=row.observation_date,
            label_available_at=row.label_available_at,
            target=row.target,
            predicted_label=predicted_label,
            predicted_probabilities=probabilities,
            source_row_sha256=row.source_row_sha256,
        )
        for row in rows
    )


def _labels_for(purpose: TrainingDatasetPurpose) -> tuple[TrainingLabel, ...]:
    return _PURPOSE_LABEL_ORDER[purpose]


def _validate_manifest_identity(manifest: TrainingLabelSplitManifest) -> None:
    if manifest.manifest_version != "selection-training-label-split-v1":
        raise ValueError("unsupported training label split manifest version")
    if manifest.dataset_schema_version != TRAINING_LABEL_DATASET_SCHEMA_VERSION:
        raise ValueError("training label split manifest has an unsupported dataset schema")
    for name in ("source_file_sha256", "prepared_dataset_sha256"):
        value = getattr(manifest, name)
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
            raise ValueError(f"training label split manifest has invalid {name}")
    expected_labels = set(_labels_for(manifest.purpose))
    if not manifest.label_contract_id.strip():
        raise ValueError("training label split manifest has no label contract")
    if set(manifest.row_counts) != {"train", "validation", "test", "purged"}:
        raise ValueError("training label split manifest row counts are incomplete")
    if any(value < 0 for value in manifest.row_counts.values()):
        raise ValueError("training label split manifest row counts must be non-negative")
    if not expected_labels:
        raise ValueError("training label purpose has no class labels")


def _validate_readiness(
    readiness: TrainingLabelReadinessReport,
    split: TrainingLabelDatasetSplit,
) -> None:
    expected = audit_training_label_split(
        split,
        minimum_labeled_samples=readiness.minimum_labeled_samples,
    )
    if readiness != expected:
        raise ValueError("training label readiness report does not match split files")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
