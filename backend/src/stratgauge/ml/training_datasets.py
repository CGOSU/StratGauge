"""Prepare leakage-safe, purpose-specific label datasets for later model training."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from stratgauge.backtest.selection_dataset import (
    ANNOTATION_DATASET_SCHEMA_VERSION,
    DATASET_SCHEMA_VERSION,
    SelectionAnnotationDatasetRow,
    SelectionDatasetRow,
    read_annotation_dataset,
    read_selection_dataset,
)
from stratgauge.backtest.selection_ml_datasets import (
    BUY_TIMING_DATASET_SCHEMA_VERSION,
    SELL_TIMING_DATASET_SCHEMA_VERSION,
    SelectionBuyTimingRow,
    SelectionSellTimingRow,
    read_buy_timing_dataset,
    read_sell_timing_dataset,
)
from stratgauge.domain.models import DomainModel
from stratgauge.domain.selections import SelectionOutcomeStatus

TRAINING_LABEL_DATASET_SCHEMA_VERSION = "selection-training-label-dataset-v1"
TRAINING_LABEL_SPLIT_MANIFEST_VERSION = "selection-training-label-split-v1"
TRAINING_LABEL_READINESS_REPORT_VERSION = "selection-training-label-readiness-v1"


class TrainingDatasetPurpose(StrEnum):
    """The one target a prepared training dataset is allowed to represent."""

    CANDIDATE_SUCCESS = "candidate_success"
    ANNOTATION_DECISION = "annotation_decision"
    BUY_TIMING = "buy_timing"
    SELL_TIMING = "sell_timing"


class TrainingLabel(StrEnum):
    """All v1 labels, constrained again by the purpose-specific contract."""

    SUCCESS = "success"
    NOT_SUCCESS = "not_success"
    DISCARD = "discard"
    WATCH = "watch"
    PREPARE_BUY = "prepare_buy"
    PURCHASED = "purchased"
    NOT_PURCHASED = "not_purchased"
    SOLD = "sold"
    NOT_SOLD = "not_sold"


_LABEL_CONTRACT_IDS = {
    TrainingDatasetPurpose.CANDIDATE_SUCCESS: "candidate-success-label-v1",
    TrainingDatasetPurpose.ANNOTATION_DECISION: "annotation-decision-label-v1",
    TrainingDatasetPurpose.BUY_TIMING: "buy-timing-label-v1",
    TrainingDatasetPurpose.SELL_TIMING: "sell-timing-label-v1",
}
_ALLOWED_LABELS = {
    TrainingDatasetPurpose.CANDIDATE_SUCCESS: frozenset(
        {TrainingLabel.SUCCESS, TrainingLabel.NOT_SUCCESS}
    ),
    TrainingDatasetPurpose.ANNOTATION_DECISION: frozenset(
        {TrainingLabel.DISCARD, TrainingLabel.WATCH, TrainingLabel.PREPARE_BUY}
    ),
    TrainingDatasetPurpose.BUY_TIMING: frozenset(
        {TrainingLabel.PURCHASED, TrainingLabel.NOT_PURCHASED}
    ),
    TrainingDatasetPurpose.SELL_TIMING: frozenset({TrainingLabel.SOLD, TrainingLabel.NOT_SOLD}),
}
_COMPLETED_OUTCOME_STATUSES = frozenset(
    {SelectionOutcomeStatus.WIN, SelectionOutcomeStatus.LOSS, SelectionOutcomeStatus.FLAT}
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class SelectionTrainingLabelRow(DomainModel):
    """One resolved target with source evidence and label-availability timing."""

    schema_version: str = TRAINING_LABEL_DATASET_SCHEMA_VERSION
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    sample_id: str
    source_schema_version: str
    source_row_sha256: str
    source_reference_id: str
    candidate_id: str
    batch_id: str
    symbol: str
    observation_date: date
    label_available_at: datetime
    target: TrainingLabel
    tracking_policy_id: str | None = None
    outcome_policy_id: str | None = None
    window_sessions: int | None = Field(default=None, gt=0)
    source_snapshot_ids: tuple[str, ...] = ()

    @field_validator(
        "schema_version",
        "label_contract_id",
        "sample_id",
        "source_schema_version",
        "source_reference_id",
        "candidate_id",
        "batch_id",
        "symbol",
    )
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("training label text must not be empty")
        return normalized

    @field_validator("tracking_policy_id", "outcome_policy_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @field_validator("source_row_sha256")
    @classmethod
    def validate_source_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256_PATTERN.fullmatch(normalized):
            raise ValueError("source_row_sha256 must be a lowercase SHA-256 digest")
        return normalized

    @field_validator("label_available_at")
    @classmethod
    def validate_label_available_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("label_available_at must include a timezone")
        return value

    @field_validator("source_snapshot_ids")
    @classmethod
    def validate_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not snapshot_id.strip() for snapshot_id in value):
            raise ValueError("source snapshot identifiers must not be empty")
        return tuple(snapshot_id.strip() for snapshot_id in value)

    @model_validator(mode="after")
    def validate_contract_and_timing(self) -> SelectionTrainingLabelRow:
        if self.schema_version != TRAINING_LABEL_DATASET_SCHEMA_VERSION:
            raise ValueError("unsupported training label dataset schema")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("training label contract does not match purpose")
        if self.target not in allowed_labels_for(self.purpose):
            raise ValueError("training label does not match purpose")
        if self.label_available_at.date() < self.observation_date:
            raise ValueError("label_available_at cannot precede observation_date")
        if self.purpose is TrainingDatasetPurpose.CANDIDATE_SUCCESS:
            if self.tracking_policy_id is None or self.outcome_policy_id is None:
                raise ValueError("candidate success labels require outcome policy lineage")
            if self.window_sessions is not None:
                raise ValueError("candidate success labels cannot have a timing window")
        elif self.purpose is TrainingDatasetPurpose.ANNOTATION_DECISION:
            if self.tracking_policy_id is not None or self.outcome_policy_id is not None:
                raise ValueError("annotation decision labels cannot carry outcome policy lineage")
            if self.window_sessions is not None:
                raise ValueError("annotation decision labels cannot have a timing window")
        elif self.window_sessions is None:
            raise ValueError("timing labels require window_sessions")
        elif self.tracking_policy_id is not None or self.outcome_policy_id is not None:
            raise ValueError("timing labels cannot carry outcome policy lineage")
        return self


class PreparedTrainingLabelDataset(DomainModel):
    """One normalized target dataset before chronological label-availability purging."""

    purpose: TrainingDatasetPurpose
    label_contract_id: str
    source_schema_version: str
    source_file_sha256: str
    source_row_count: int = Field(ge=0)
    labeled_row_count: int = Field(ge=0)
    skipped_unlabeled_row_count: int = Field(ge=0)
    filtered_policy_row_count: int = Field(ge=0)
    tracking_policy_id: str | None = None
    outcome_policy_id: str | None = None
    window_sessions: int | None = Field(default=None, gt=0)
    rows: tuple[SelectionTrainingLabelRow, ...]

    @field_validator("label_contract_id", "source_schema_version", "source_file_sha256")
    @classmethod
    def validate_metadata_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("training label metadata must not be empty")
        return normalized

    @field_validator("source_file_sha256")
    @classmethod
    def validate_file_hash(cls, value: str) -> str:
        normalized = value.lower()
        if not _SHA256_PATTERN.fullmatch(normalized):
            raise ValueError("source_file_sha256 must be a lowercase SHA-256 digest")
        return normalized

    @model_validator(mode="after")
    def validate_preparation(self) -> PreparedTrainingLabelDataset:
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("prepared label contract does not match purpose")
        if self.labeled_row_count != len(self.rows):
            raise ValueError("labeled_row_count does not match prepared rows")
        if (
            self.source_row_count
            != self.labeled_row_count
            + self.skipped_unlabeled_row_count
            + self.filtered_policy_row_count
        ):
            raise ValueError("prepared row counts do not account for every source row")
        if any(row.purpose is not self.purpose for row in self.rows):
            raise ValueError("prepared dataset contains a different purpose")
        if any(row.label_contract_id != self.label_contract_id for row in self.rows):
            raise ValueError("prepared dataset contains a different label contract")
        if self.purpose is TrainingDatasetPurpose.CANDIDATE_SUCCESS:
            if self.tracking_policy_id is None or self.outcome_policy_id is None:
                if self.rows:
                    raise ValueError(
                        "candidate success preparation requires outcome policy lineage"
                    )
            if self.window_sessions is not None:
                raise ValueError("candidate success preparation cannot have a timing window")
        elif self.purpose is TrainingDatasetPurpose.ANNOTATION_DECISION:
            if any(
                value is not None
                for value in (self.tracking_policy_id, self.outcome_policy_id, self.window_sessions)
            ):
                raise ValueError("annotation decision preparation has unsupported metadata")
        elif self.window_sessions is None and self.rows:
            raise ValueError("timing preparation requires a timing window")
        _validate_unique_training_rows(self.rows)
        return self


class TrainingLabelDatasetSplit(DomainModel):
    """Chronological partitions plus rows purged for unavailable labels."""

    prepared: PreparedTrainingLabelDataset
    train_rows: tuple[SelectionTrainingLabelRow, ...]
    validation_rows: tuple[SelectionTrainingLabelRow, ...]
    test_rows: tuple[SelectionTrainingLabelRow, ...]
    purged_rows: tuple[SelectionTrainingLabelRow, ...]
    train_end: date
    validation_end: date
    label_available_through: datetime

    @field_validator("label_available_through")
    @classmethod
    def validate_cutoff(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("label_available_through must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_split(self) -> TrainingLabelDatasetSplit:
        if self.train_end >= self.validation_end:
            raise ValueError("train_end must precede validation_end")
        if self.label_available_through.date() < self.validation_end:
            raise ValueError("label_available_through cannot precede validation_end")
        partitions = self.train_rows + self.validation_rows + self.test_rows + self.purged_rows
        if len(partitions) != len(self.prepared.rows) or set(partitions) != set(self.prepared.rows):
            raise ValueError("training label partitions do not match prepared rows")
        _validate_unique_training_rows(partitions)
        return self


class TrainingLabelSplitManifest(DomainModel):
    """Immutable provenance for a purpose-specific chronological label split."""

    manifest_version: str = TRAINING_LABEL_SPLIT_MANIFEST_VERSION
    dataset_schema_version: str = TRAINING_LABEL_DATASET_SCHEMA_VERSION
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    source_schema_version: str
    source_file_sha256: str
    prepared_dataset_sha256: str
    train_end: date
    validation_end: date
    label_available_through: datetime
    source_row_count: int = Field(ge=0)
    skipped_unlabeled_row_count: int = Field(ge=0)
    filtered_policy_row_count: int = Field(ge=0)
    row_counts: dict[str, int]
    tracking_policy_id: str | None = None
    outcome_policy_id: str | None = None
    window_sessions: int | None = Field(default=None, gt=0)


class TrainingLabelReadinessReport(DomainModel):
    """A non-mutating readiness assessment for one already prepared label split."""

    report_version: str = TRAINING_LABEL_READINESS_REPORT_VERSION
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    prepared_dataset_sha256: str
    minimum_labeled_samples: int = Field(gt=0)
    source_row_count: int = Field(ge=0)
    labeled_row_count: int = Field(ge=0)
    accepted_row_count: int = Field(ge=0)
    skipped_unlabeled_row_count: int = Field(ge=0)
    filtered_policy_row_count: int = Field(ge=0)
    purged_future_label_count: int = Field(ge=0)
    row_counts: dict[str, int]
    target_counts: dict[str, int]
    train_target_counts: dict[str, int]
    duplicate_sample_count: int = Field(ge=0)
    duplicate_source_row_count: int = Field(ge=0)
    missing_source_snapshot_count: int = Field(ge=0)
    observation_date_from: date | None = None
    observation_date_to: date | None = None
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    eligible: bool = False


def label_contract_id_for(purpose: TrainingDatasetPurpose) -> str:
    """Return the immutable v1 target contract for one model purpose."""

    return _LABEL_CONTRACT_IDS[purpose]


def allowed_labels_for(purpose: TrainingDatasetPurpose) -> frozenset[TrainingLabel]:
    """Return the labels that a purpose-specific dataset may contain."""

    return _ALLOWED_LABELS[purpose]


def prepare_training_label_dataset(
    content: bytes,
    *,
    purpose: TrainingDatasetPurpose | str,
    tracking_policy_id: str | None = None,
) -> PreparedTrainingLabelDataset:
    """Normalize one raw research dataset into one target-only training contract."""

    resolved_purpose = TrainingDatasetPurpose(purpose)
    source_file_sha256 = _sha256(content)
    if resolved_purpose is TrainingDatasetPurpose.CANDIDATE_SUCCESS:
        return _prepare_candidate_success_labels(
            read_selection_dataset(content),
            source_file_sha256=source_file_sha256,
            requested_tracking_policy_id=tracking_policy_id,
        )
    if tracking_policy_id is not None:
        raise ValueError("tracking_policy_id is only valid for candidate success labels")
    if resolved_purpose is TrainingDatasetPurpose.ANNOTATION_DECISION:
        return _prepare_annotation_decision_labels(
            read_annotation_dataset(content),
            source_file_sha256=source_file_sha256,
        )
    if resolved_purpose is TrainingDatasetPurpose.BUY_TIMING:
        return _prepare_buy_timing_labels(
            read_buy_timing_dataset(content),
            source_file_sha256=source_file_sha256,
        )
    return _prepare_sell_timing_labels(
        read_sell_timing_dataset(content),
        source_file_sha256=source_file_sha256,
    )


def split_training_label_dataset(
    prepared: PreparedTrainingLabelDataset,
    *,
    train_end: date,
    validation_end: date,
    label_available_through: datetime,
) -> TrainingLabelDatasetSplit:
    """Split by observation date and purge labels unavailable at each boundary."""

    if train_end >= validation_end:
        raise ValueError("train_end must precede validation_end")
    _require_aware(label_available_through, "label_available_through")
    if label_available_through.date() < validation_end:
        raise ValueError("label_available_through cannot precede validation_end")
    partitions: dict[str, list[SelectionTrainingLabelRow]] = {
        "train": [],
        "validation": [],
        "test": [],
        "purged": [],
    }
    for row in sorted(prepared.rows, key=lambda item: (item.observation_date, item.sample_id)):
        partition, cutoff_date = _partition_for_observation(
            row.observation_date,
            train_end=train_end,
            validation_end=validation_end,
            label_available_through=label_available_through,
        )
        destination = partition if row.label_available_at.date() <= cutoff_date else "purged"
        partitions[destination].append(row)
    return TrainingLabelDatasetSplit(
        prepared=prepared,
        train_rows=tuple(partitions["train"]),
        validation_rows=tuple(partitions["validation"]),
        test_rows=tuple(partitions["test"]),
        purged_rows=tuple(partitions["purged"]),
        train_end=train_end,
        validation_end=validation_end,
        label_available_through=label_available_through,
    )


def audit_training_label_split(
    split: TrainingLabelDatasetSplit,
    *,
    minimum_labeled_samples: int = 100,
) -> TrainingLabelReadinessReport:
    """Audit the resolved partitions before any feature join or model fitting."""

    if minimum_labeled_samples <= 0:
        raise ValueError("minimum_labeled_samples must be positive")
    accepted_rows = split.train_rows + split.validation_rows + split.test_rows
    duplicate_sample_count = _duplicate_count(row.sample_id for row in accepted_rows)
    duplicate_source_row_count = _duplicate_count(row.source_row_sha256 for row in accepted_rows)
    temporal_violation_count = _label_availability_violation_count(split)
    missing_source_snapshot_count = sum(
        not row.source_snapshot_ids
        for row in accepted_rows
        if row.purpose is TrainingDatasetPurpose.CANDIDATE_SUCCESS
    )
    blockers: list[str] = []
    warnings: list[str] = []
    if not split.train_rows:
        blockers.append("training split contains no labels")
    if not split.validation_rows:
        blockers.append("validation split contains no labels")
    if len(accepted_rows) < minimum_labeled_samples:
        blockers.append(
            f"accepted labels {len(accepted_rows)} are below minimum {minimum_labeled_samples}"
        )
    train_targets = {row.target for row in split.train_rows}
    if len(train_targets) < 2:
        blockers.append("training labels do not contain at least two classes")
    if duplicate_sample_count:
        blockers.append(f"prepared split contains {duplicate_sample_count} duplicate sample IDs")
    if duplicate_source_row_count:
        blockers.append(
            f"prepared split contains {duplicate_source_row_count} duplicate source rows"
        )
    if temporal_violation_count:
        blockers.append(f"prepared split contains {temporal_violation_count} unavailable labels")
    if missing_source_snapshot_count:
        blockers.append(
            f"candidate success labels lack {missing_source_snapshot_count} source snapshots"
        )
    if split.purged_rows:
        warnings.append(f"{len(split.purged_rows)} labels were purged by availability cutoff")
    if split.prepared.skipped_unlabeled_row_count:
        warnings.append(
            f"{split.prepared.skipped_unlabeled_row_count} source rows have no resolved label"
        )
    if split.prepared.filtered_policy_row_count:
        warnings.append(
            f"source rows outside the selected policy: {split.prepared.filtered_policy_row_count}"
        )
    observation_dates = [row.observation_date for row in accepted_rows]
    return TrainingLabelReadinessReport(
        purpose=split.prepared.purpose,
        label_contract_id=split.prepared.label_contract_id,
        prepared_dataset_sha256=_prepared_dataset_sha256(split.prepared.rows),
        minimum_labeled_samples=minimum_labeled_samples,
        source_row_count=split.prepared.source_row_count,
        labeled_row_count=split.prepared.labeled_row_count,
        accepted_row_count=len(accepted_rows),
        skipped_unlabeled_row_count=split.prepared.skipped_unlabeled_row_count,
        filtered_policy_row_count=split.prepared.filtered_policy_row_count,
        purged_future_label_count=len(split.purged_rows),
        row_counts={
            "train": len(split.train_rows),
            "validation": len(split.validation_rows),
            "test": len(split.test_rows),
            "purged": len(split.purged_rows),
        },
        target_counts=dict(sorted(Counter(row.target.value for row in accepted_rows).items())),
        train_target_counts=dict(
            sorted(Counter(row.target.value for row in split.train_rows).items())
        ),
        duplicate_sample_count=duplicate_sample_count,
        duplicate_source_row_count=duplicate_source_row_count,
        missing_source_snapshot_count=missing_source_snapshot_count,
        observation_date_from=min(observation_dates) if observation_dates else None,
        observation_date_to=max(observation_dates) if observation_dates else None,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        eligible=not blockers,
    )


def read_training_label_dataset(content: bytes) -> tuple[SelectionTrainingLabelRow, ...]:
    """Read one purpose-specific prepared JSONL file with its schema pinned."""

    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("training label dataset is not valid UTF-8") from exc
    rows: list[SelectionTrainingLabelRow] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("training label row must be a JSON object")
            if payload.get("schema_version") != TRAINING_LABEL_DATASET_SCHEMA_VERSION:
                raise ValueError("unsupported training label dataset schema")
            rows.append(SelectionTrainingLabelRow.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid training label row at line {line_number}: {exc}") from exc
    _validate_unique_training_rows(rows)
    return tuple(rows)


def serialize_training_label_dataset(rows: Iterable[SelectionTrainingLabelRow]) -> str:
    """Serialize target-only rows as deterministic UTF-8 JSONL."""

    return "".join(f"{row.model_dump_json()}\n" for row in rows)


def build_training_label_split_manifest(
    split: TrainingLabelDatasetSplit,
) -> TrainingLabelSplitManifest:
    """Build the immutable manifest that describes a prepared label split."""

    prepared = split.prepared
    return TrainingLabelSplitManifest(
        purpose=prepared.purpose,
        label_contract_id=prepared.label_contract_id,
        source_schema_version=prepared.source_schema_version,
        source_file_sha256=prepared.source_file_sha256,
        prepared_dataset_sha256=_prepared_dataset_sha256(prepared.rows),
        train_end=split.train_end,
        validation_end=split.validation_end,
        label_available_through=split.label_available_through,
        source_row_count=prepared.source_row_count,
        skipped_unlabeled_row_count=prepared.skipped_unlabeled_row_count,
        filtered_policy_row_count=prepared.filtered_policy_row_count,
        row_counts={
            "train": len(split.train_rows),
            "validation": len(split.validation_rows),
            "test": len(split.test_rows),
            "purged": len(split.purged_rows),
        },
        tracking_policy_id=prepared.tracking_policy_id,
        outcome_policy_id=prepared.outcome_policy_id,
        window_sessions=prepared.window_sessions,
    )


def write_training_label_split(
    output_dir: str | Path,
    split: TrainingLabelDatasetSplit,
    report: TrainingLabelReadinessReport,
) -> TrainingLabelSplitManifest:
    """Write all split evidence atomically without replacing an earlier run."""

    manifest = build_training_label_split_manifest(split)
    destination = Path(output_dir)
    outputs = {
        "train.jsonl": serialize_training_label_dataset(split.train_rows),
        "validation.jsonl": serialize_training_label_dataset(split.validation_rows),
        "test.jsonl": serialize_training_label_dataset(split.test_rows),
        "purged.jsonl": serialize_training_label_dataset(split.purged_rows),
        "manifest.json": manifest.model_dump_json(indent=2) + "\n",
        "readiness.json": report.model_dump_json(indent=2) + "\n",
    }
    destination.mkdir(parents=True, exist_ok=True)
    paths = tuple(destination / name for name in outputs)
    if any(path.exists() for path in paths):
        raise FileExistsError(f"training label split output already exists: {destination}")
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
    return manifest


def _prepare_candidate_success_labels(
    rows: Sequence[SelectionDatasetRow],
    *,
    source_file_sha256: str,
    requested_tracking_policy_id: str | None,
) -> PreparedTrainingLabelDataset:
    policies = {
        row.tracking_policy_id
        for row in rows
        if row.outcome_status in _COMPLETED_OUTCOME_STATUSES and row.tracking_policy_id is not None
    }
    if requested_tracking_policy_id is None and len(policies) > 1:
        raise ValueError("candidate success dataset contains multiple tracking policies")
    resolved_tracking_policy_id = requested_tracking_policy_id or next(iter(policies), None)
    prepared_rows: list[SelectionTrainingLabelRow] = []
    skipped_unlabeled_row_count = 0
    filtered_policy_row_count = 0
    for row in rows:
        if row.outcome_status not in _COMPLETED_OUTCOME_STATUSES:
            skipped_unlabeled_row_count += 1
            continue
        if (
            resolved_tracking_policy_id is not None
            and row.tracking_policy_id != resolved_tracking_policy_id
        ):
            filtered_policy_row_count += 1
            continue
        if row.tracking_policy_id is None or row.outcome_policy_id is None:
            raise ValueError("candidate success row lacks outcome policy lineage")
        outcome_evaluated_at = _parse_aware_datetime(
            row.outcome_evaluated_at,
            "candidate success outcome_evaluated_at",
        )
        target = (
            TrainingLabel.SUCCESS
            if row.outcome_status is SelectionOutcomeStatus.WIN
            else TrainingLabel.NOT_SUCCESS
        )
        prepared_rows.append(
            _label_row(
                purpose=TrainingDatasetPurpose.CANDIDATE_SUCCESS,
                source_schema_version=DATASET_SCHEMA_VERSION,
                source_row=row,
                source_reference_id=f"{row.candidate_id}:{row.tracking_policy_id}",
                candidate_id=row.candidate_id,
                batch_id=row.batch_id,
                symbol=row.symbol,
                observation_date=_parse_date(
                    row.selection_date, "candidate success selection_date"
                ),
                label_available_at=outcome_evaluated_at,
                target=target,
                tracking_policy_id=row.tracking_policy_id,
                outcome_policy_id=row.outcome_policy_id,
                source_snapshot_ids=row.source_snapshot_ids,
                sample_identity={
                    "candidate_id": row.candidate_id,
                    "batch_id": row.batch_id,
                    "tracking_policy_id": row.tracking_policy_id,
                    "outcome_policy_id": row.outcome_policy_id,
                },
            )
        )
    outcome_policies = {row.outcome_policy_id for row in prepared_rows}
    if len(outcome_policies) > 1:
        raise ValueError("candidate success dataset contains multiple outcome policies")
    if prepared_rows and resolved_tracking_policy_id is None:
        raise ValueError("candidate success dataset has no tracking policy")
    return PreparedTrainingLabelDataset(
        purpose=TrainingDatasetPurpose.CANDIDATE_SUCCESS,
        label_contract_id=label_contract_id_for(TrainingDatasetPurpose.CANDIDATE_SUCCESS),
        source_schema_version=DATASET_SCHEMA_VERSION,
        source_file_sha256=source_file_sha256,
        source_row_count=len(rows),
        labeled_row_count=len(prepared_rows),
        skipped_unlabeled_row_count=skipped_unlabeled_row_count,
        filtered_policy_row_count=filtered_policy_row_count,
        tracking_policy_id=resolved_tracking_policy_id,
        outcome_policy_id=next(iter(outcome_policies), None),
        rows=_sorted_rows(prepared_rows),
    )


def _prepare_annotation_decision_labels(
    rows: Sequence[SelectionAnnotationDatasetRow],
    *,
    source_file_sha256: str,
) -> PreparedTrainingLabelDataset:
    prepared_rows = tuple(
        _label_row(
            purpose=TrainingDatasetPurpose.ANNOTATION_DECISION,
            source_schema_version=ANNOTATION_DATASET_SCHEMA_VERSION,
            source_row=row,
            source_reference_id=row.annotation_id,
            candidate_id=row.candidate_id,
            batch_id=row.batch_id,
            symbol=row.symbol,
            observation_date=_parse_aware_datetime(
                row.annotation_reviewed_at,
                "annotation decision annotation_reviewed_at",
            ).date(),
            label_available_at=_parse_aware_datetime(
                row.annotation_recorded_at,
                "annotation decision annotation_recorded_at",
            ),
            target=TrainingLabel(row.decision.value),
            sample_identity={"annotation_id": row.annotation_id},
        )
        for row in rows
    )
    return PreparedTrainingLabelDataset(
        purpose=TrainingDatasetPurpose.ANNOTATION_DECISION,
        label_contract_id=label_contract_id_for(TrainingDatasetPurpose.ANNOTATION_DECISION),
        source_schema_version=ANNOTATION_DATASET_SCHEMA_VERSION,
        source_file_sha256=source_file_sha256,
        source_row_count=len(rows),
        labeled_row_count=len(prepared_rows),
        skipped_unlabeled_row_count=0,
        filtered_policy_row_count=0,
        rows=_sorted_rows(prepared_rows),
    )


def _prepare_buy_timing_labels(
    rows: Sequence[SelectionBuyTimingRow],
    *,
    source_file_sha256: str,
) -> PreparedTrainingLabelDataset:
    window_sessions = _single_window_sessions(rows, TrainingDatasetPurpose.BUY_TIMING)
    prepared_rows: list[SelectionTrainingLabelRow] = []
    skipped_unlabeled_row_count = 0
    for row in rows:
        if row.purchased_within_window is None:
            skipped_unlabeled_row_count += 1
            continue
        if row.purchased_within_window:
            if row.fill_id is None or row.filled_at is None:
                raise ValueError("purchased buy timing label lacks fill evidence")
            if row.filled_at > row.evaluated_at:
                raise ValueError("purchased buy timing fill is after evaluation cutoff")
            label_available_at = row.filled_at
            target = TrainingLabel.PURCHASED
        else:
            if row.evaluated_at.date() < row.observation_end:
                raise ValueError(
                    "not purchased buy timing label has an incomplete observation window"
                )
            label_available_at = row.evaluated_at
            target = TrainingLabel.NOT_PURCHASED
        prepared_rows.append(
            _label_row(
                purpose=TrainingDatasetPurpose.BUY_TIMING,
                source_schema_version=BUY_TIMING_DATASET_SCHEMA_VERSION,
                source_row=row,
                source_reference_id=row.annotation_id,
                candidate_id=row.candidate_id,
                batch_id=row.batch_id,
                symbol=row.symbol,
                observation_date=row.annotation_reviewed_at.date(),
                label_available_at=label_available_at,
                target=target,
                window_sessions=row.window_sessions,
                sample_identity={"annotation_id": row.annotation_id},
            )
        )
    return PreparedTrainingLabelDataset(
        purpose=TrainingDatasetPurpose.BUY_TIMING,
        label_contract_id=label_contract_id_for(TrainingDatasetPurpose.BUY_TIMING),
        source_schema_version=BUY_TIMING_DATASET_SCHEMA_VERSION,
        source_file_sha256=source_file_sha256,
        source_row_count=len(rows),
        labeled_row_count=len(prepared_rows),
        skipped_unlabeled_row_count=skipped_unlabeled_row_count,
        filtered_policy_row_count=0,
        window_sessions=window_sessions,
        rows=_sorted_rows(prepared_rows),
    )


def _prepare_sell_timing_labels(
    rows: Sequence[SelectionSellTimingRow],
    *,
    source_file_sha256: str,
) -> PreparedTrainingLabelDataset:
    window_sessions = _single_window_sessions(rows, TrainingDatasetPurpose.SELL_TIMING)
    prepared_rows: list[SelectionTrainingLabelRow] = []
    skipped_unlabeled_row_count = 0
    for row in rows:
        if row.sold_within_window is None:
            skipped_unlabeled_row_count += 1
            continue
        if row.sold_within_window:
            if not row.sell_fill_ids:
                raise ValueError("sold sell timing label lacks fill evidence")
            target = TrainingLabel.SOLD
        else:
            if row.evaluated_at.date() < row.sell_window_end:
                raise ValueError("not sold sell timing label has an incomplete observation window")
            target = TrainingLabel.NOT_SOLD
        prepared_rows.append(
            _label_row(
                purpose=TrainingDatasetPurpose.SELL_TIMING,
                source_schema_version=SELL_TIMING_DATASET_SCHEMA_VERSION,
                source_row=row,
                source_reference_id=f"{row.candidate_id}:{row.exposure_session.isoformat()}",
                candidate_id=row.candidate_id,
                batch_id=row.batch_id,
                symbol=row.symbol,
                observation_date=row.exposure_session,
                label_available_at=row.evaluated_at,
                target=target,
                window_sessions=row.window_sessions,
                sample_identity={
                    "candidate_id": row.candidate_id,
                    "exposure_session": row.exposure_session.isoformat(),
                    "window_sessions": row.window_sessions,
                },
            )
        )
    return PreparedTrainingLabelDataset(
        purpose=TrainingDatasetPurpose.SELL_TIMING,
        label_contract_id=label_contract_id_for(TrainingDatasetPurpose.SELL_TIMING),
        source_schema_version=SELL_TIMING_DATASET_SCHEMA_VERSION,
        source_file_sha256=source_file_sha256,
        source_row_count=len(rows),
        labeled_row_count=len(prepared_rows),
        skipped_unlabeled_row_count=skipped_unlabeled_row_count,
        filtered_policy_row_count=0,
        window_sessions=window_sessions,
        rows=_sorted_rows(prepared_rows),
    )


def _label_row(
    *,
    purpose: TrainingDatasetPurpose,
    source_schema_version: str,
    source_row: BaseModel,
    source_reference_id: str,
    candidate_id: str,
    batch_id: str,
    symbol: str,
    observation_date: date,
    label_available_at: datetime,
    target: TrainingLabel,
    sample_identity: dict[str, str | int],
    tracking_policy_id: str | None = None,
    outcome_policy_id: str | None = None,
    window_sessions: int | None = None,
    source_snapshot_ids: tuple[str, ...] = (),
) -> SelectionTrainingLabelRow:
    """Create a deterministic row while retaining the full source-row digest."""

    _require_aware(label_available_at, "label_available_at")
    source_row_sha256 = _source_row_sha256(source_row)
    sample_id = _stable_identifier(purpose, sample_identity)
    return SelectionTrainingLabelRow(
        purpose=purpose,
        label_contract_id=label_contract_id_for(purpose),
        sample_id=sample_id,
        source_schema_version=source_schema_version,
        source_row_sha256=source_row_sha256,
        source_reference_id=source_reference_id,
        candidate_id=candidate_id,
        batch_id=batch_id,
        symbol=symbol,
        observation_date=observation_date,
        label_available_at=label_available_at,
        target=target,
        tracking_policy_id=tracking_policy_id,
        outcome_policy_id=outcome_policy_id,
        window_sessions=window_sessions,
        source_snapshot_ids=source_snapshot_ids,
    )


def _partition_for_observation(
    observation_date: date,
    *,
    train_end: date,
    validation_end: date,
    label_available_through: datetime,
) -> tuple[str, date]:
    if observation_date <= train_end:
        return "train", train_end
    if observation_date <= validation_end:
        return "validation", validation_end
    return "test", label_available_through.date()


def _label_availability_violation_count(split: TrainingLabelDatasetSplit) -> int:
    checks = (
        (split.train_rows, split.train_end),
        (split.validation_rows, split.validation_end),
        (split.test_rows, split.label_available_through.date()),
    )
    return sum(
        row.label_available_at.date() > cutoff_date for rows, cutoff_date in checks for row in rows
    )


def _single_window_sessions(
    rows: Sequence[SelectionBuyTimingRow] | Sequence[SelectionSellTimingRow],
    purpose: TrainingDatasetPurpose,
) -> int | None:
    windows = {row.window_sessions for row in rows}
    if len(windows) > 1:
        raise ValueError(f"{purpose.value} dataset contains multiple window sessions")
    return next(iter(windows), None)


def _parse_aware_datetime(value: str | None, name: str) -> datetime:
    if value is None:
        raise ValueError(f"{name} is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} is not a valid ISO datetime") from exc
    _require_aware(parsed, name)
    return parsed


def _parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} is not a valid ISO date") from exc


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


def _source_row_sha256(row: BaseModel) -> str:
    return _sha256(_canonical_json(row.model_dump(mode="json")))


def _prepared_dataset_sha256(rows: Iterable[SelectionTrainingLabelRow]) -> str:
    return _sha256(serialize_training_label_dataset(rows).encode("utf-8"))


def _stable_identifier(
    purpose: TrainingDatasetPurpose,
    identity: dict[str, str | int],
) -> str:
    payload = {"purpose": purpose.value, **identity}
    return f"training-{purpose.value}-{_sha256(_canonical_json(payload))[:20]}"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sorted_rows(
    rows: Iterable[SelectionTrainingLabelRow],
) -> tuple[SelectionTrainingLabelRow, ...]:
    return tuple(sorted(rows, key=lambda row: (row.observation_date, row.sample_id)))


def _validate_unique_training_rows(rows: Iterable[SelectionTrainingLabelRow]) -> None:
    materialized = tuple(rows)
    duplicate_samples = _duplicate_count(row.sample_id for row in materialized)
    if duplicate_samples:
        raise ValueError("training label dataset contains duplicate sample IDs")
    duplicate_sources = _duplicate_count(row.source_row_sha256 for row in materialized)
    if duplicate_sources:
        raise ValueError("training label dataset contains duplicate source rows")


def _duplicate_count(values: Iterable[str]) -> int:
    materialized = tuple(values)
    return len(materialized) - len(set(materialized))
