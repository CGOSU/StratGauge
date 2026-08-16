"""Prepare leakage-safe, purpose-specific label datasets for later model training."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.domain.selections import SelectionOutcomeStatus

TRAINING_LABEL_DATASET_SCHEMA_VERSION = "selection-training-label-dataset-v2"


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
    observation_at: datetime | None = None
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

    @field_validator("label_available_at", "observation_at")
    @classmethod
    def validate_label_times(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("training label timestamps must include a timezone")
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
        if self.observation_at is not None:
            if self.observation_at.date() < self.observation_date:
                raise ValueError("observation_at cannot precede observation_date")
            if self.label_available_at < self.observation_at:
                raise ValueError("label_available_at cannot precede observation_at")
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


def serialize_training_label_dataset(rows: Iterable[SelectionTrainingLabelRow]) -> str:
    """Serialize target-only rows as deterministic UTF-8 JSONL."""

    return "".join(f"{row.model_dump_json()}\n" for row in rows)


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
