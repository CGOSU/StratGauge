"""Join prepared labels to point-in-time feature snapshots without leakage."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml._hashing import (
    sha256_bytes,
)
from stratgauge.ml.training_datasets import (
    SelectionTrainingLabelRow,
    TrainingDatasetPurpose,
    TrainingLabel,
    TrainingLabelReadinessReport,
    TrainingLabelSplitManifest,
    allowed_labels_for,
    label_contract_id_for,
)
from stratgauge.ml.training_datasets.models import (
    serialize_training_label_dataset,
)

TRAINING_FEATURE_DATASET_SCHEMA_VERSION = "selection-purpose-feature-dataset-v1"


TRAINING_FEATURE_SPLIT_MANIFEST_VERSION = "selection-purpose-feature-split-v1"


class SelectionTrainingFeatureRow(DomainModel):
    """One purpose-specific target joined to a safe feature snapshot."""

    schema_version: str = TRAINING_FEATURE_DATASET_SCHEMA_VERSION
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    sample_id: str
    source_label_row_sha256: str
    candidate_id: str
    batch_id: str
    symbol: str
    observation_date: date
    observation_at: datetime | None = None
    label_available_at: datetime
    target: TrainingLabel
    feature_set_version_id: str
    feature_snapshot_id: str
    feature_as_of: datetime
    feature_available_at: datetime
    input_session_start: date
    input_session_end: date
    source_snapshot_ids: tuple[str, ...]
    features: dict[str, Decimal]

    @field_validator(
        "schema_version",
        "label_contract_id",
        "sample_id",
        "source_label_row_sha256",
        "candidate_id",
        "batch_id",
        "symbol",
        "feature_set_version_id",
        "feature_snapshot_id",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("training feature row text must not be empty")
        return normalized

    @field_validator(
        "observation_at", "label_available_at", "feature_as_of", "feature_available_at"
    )
    @classmethod
    def validate_aware_times(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("training feature timestamps must include a timezone")
        return value

    @field_validator("source_label_row_sha256")
    @classmethod
    def validate_label_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("source_label_row_sha256 must be a SHA-256 digest")
        return normalized

    @field_validator("source_snapshot_ids")
    @classmethod
    def validate_source_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("training feature rows require source snapshot IDs")
        return tuple(item.strip() for item in value)

    @field_validator("features")
    @classmethod
    def validate_features(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        if not value or any(not name.strip() for name in value):
            raise ValueError("training feature rows require named features")
        if any(not item.is_finite() for item in value.values()):
            raise ValueError("training feature values must be finite")
        return {name.strip(): item for name, item in value.items()}

    @model_validator(mode="after")
    def validate_temporal_join(self) -> SelectionTrainingFeatureRow:
        if self.schema_version != TRAINING_FEATURE_DATASET_SCHEMA_VERSION:
            raise ValueError("unsupported training feature dataset schema")
        if self.feature_as_of.date() > self.observation_date:
            raise ValueError("feature as_of cannot follow observation date")
        if self.observation_at is not None and self.feature_as_of > self.observation_at:
            raise ValueError("feature as_of cannot follow precise observation time")
        if self.feature_available_at > self.feature_as_of:
            raise ValueError("feature available_at cannot follow feature as_of")
        if self.input_session_end >= self.observation_date:
            raise ValueError("feature input sessions must precede observation date")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("training feature label contract does not match purpose")
        if self.target not in allowed_labels_for(self.purpose):
            raise ValueError("training feature target does not match purpose")
        return self


class TrainingFeatureJoinReport(DomainModel):
    """Immutable accounting for a purpose-specific label/feature join."""

    report_version: str = "selection-training-feature-join-v1"
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    source_label_dataset_sha256: str
    source_label_row_count: int = Field(ge=0)
    joined_row_count: int = Field(ge=0)
    purged_row_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    joined_feature_snapshot_ids: tuple[str, ...]
    failures: tuple[tuple[str, str], ...] = ()

    @model_validator(mode="after")
    def validate_accounting(self) -> TrainingFeatureJoinReport:
        if self.report_version != "selection-training-feature-join-v1":
            raise ValueError("unsupported training feature join report version")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("training feature join report contract does not match purpose")
        if self.failure_count != len(self.failures):
            raise ValueError("training feature join report failure count does not match failures")
        if (
            self.source_label_row_count
            != self.joined_row_count + self.failure_count + self.purged_row_count
        ):
            raise ValueError("training feature join report row counts do not reconcile")
        if len(self.joined_feature_snapshot_ids) != len(set(self.joined_feature_snapshot_ids)):
            raise ValueError("training feature join report contains duplicate snapshots")
        return self


class TrainingFeatureSplitManifest(DomainModel):
    """Immutable hashes and schema metadata for one joined feature split."""

    manifest_version: str = TRAINING_FEATURE_SPLIT_MANIFEST_VERSION
    dataset_schema_version: str = TRAINING_FEATURE_DATASET_SCHEMA_VERSION
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    label_split_manifest_sha256: str
    label_readiness_sha256: str
    source_label_dataset_sha256: str
    prepared_label_dataset_sha256: str
    row_counts: dict[str, int]
    label_row_counts: dict[str, int]
    partition_sha256: dict[str, str]
    label_partition_sha256: dict[str, str]
    feature_names: tuple[str, ...]
    join_report_sha256: str

    @model_validator(mode="after")
    def validate_manifest(self) -> TrainingFeatureSplitManifest:
        if self.manifest_version != TRAINING_FEATURE_SPLIT_MANIFEST_VERSION:
            raise ValueError("unsupported training feature split manifest version")
        if self.dataset_schema_version != TRAINING_FEATURE_DATASET_SCHEMA_VERSION:
            raise ValueError("unsupported training feature split dataset schema")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("training feature manifest contract does not match purpose")
        _validate_digest_fields(
            self.label_split_manifest_sha256,
            self.label_readiness_sha256,
            self.source_label_dataset_sha256,
            self.prepared_label_dataset_sha256,
            self.join_report_sha256,
        )
        expected_partitions = {"train", "validation", "test", "purged"}
        if set(self.row_counts) != expected_partitions:
            raise ValueError("training feature manifest row counts are incomplete")
        if set(self.label_row_counts) != expected_partitions:
            raise ValueError("training feature manifest label row counts are incomplete")
        if set(self.partition_sha256) != expected_partitions:
            raise ValueError("training feature manifest partition hashes are incomplete")
        if set(self.label_partition_sha256) != expected_partitions:
            raise ValueError("training feature manifest label partition hashes are incomplete")
        if any(value < 0 for value in (*self.row_counts.values(), *self.label_row_counts.values())):
            raise ValueError("training feature manifest row counts must be non-negative")
        if tuple(sorted(set(self.feature_names))) != self.feature_names:
            raise ValueError("training feature manifest feature names must be sorted and unique")
        _validate_digest_fields(
            *self.partition_sha256.values(), *self.label_partition_sha256.values()
        )
        return self


class TrainingFeatureDatasetSplit(DomainModel):
    """Joined feature partitions with purged labels kept out of training."""

    report: TrainingFeatureJoinReport
    label_split_manifest: TrainingLabelSplitManifest
    label_readiness: TrainingLabelReadinessReport
    train_rows: tuple[SelectionTrainingFeatureRow, ...]
    validation_rows: tuple[SelectionTrainingFeatureRow, ...]
    test_rows: tuple[SelectionTrainingFeatureRow, ...]
    purged_rows: tuple[SelectionTrainingLabelRow, ...]
    label_train_rows: tuple[SelectionTrainingLabelRow, ...]
    label_validation_rows: tuple[SelectionTrainingLabelRow, ...]
    label_test_rows: tuple[SelectionTrainingLabelRow, ...]

    @model_validator(mode="after")
    def validate_lineage(self) -> TrainingFeatureDatasetSplit:
        if self.report.purpose is not self.label_split_manifest.purpose:
            raise ValueError("feature split purpose does not match label split manifest")
        if self.report.label_contract_id != self.label_split_manifest.label_contract_id:
            raise ValueError("feature split contract does not match label split manifest")
        if self.label_readiness.purpose is not self.report.purpose:
            raise ValueError("feature split purpose does not match readiness report")
        if self.label_readiness.label_contract_id != self.report.label_contract_id:
            raise ValueError("feature split contract does not match readiness report")
        _validate_label_rows(
            self.label_train_rows,
            self.report.purpose,
            self.report.label_contract_id,
            "train",
        )
        _validate_label_rows(
            self.label_validation_rows,
            self.report.purpose,
            self.report.label_contract_id,
            "validation",
        )
        _validate_label_rows(
            self.label_test_rows,
            self.report.purpose,
            self.report.label_contract_id,
            "test",
        )
        _validate_label_rows(
            self.purged_rows,
            self.report.purpose,
            self.report.label_contract_id,
            "purged",
        )
        _validate_feature_label_alignment(self.train_rows, self.label_train_rows, "train")
        _validate_feature_label_alignment(
            self.validation_rows, self.label_validation_rows, "validation"
        )
        _validate_feature_label_alignment(self.test_rows, self.label_test_rows, "test")
        label_rows = (
            self.label_train_rows
            + self.label_validation_rows
            + self.label_test_rows
            + self.purged_rows
        )
        if self.report.source_label_row_count != len(label_rows):
            raise ValueError("feature split label rows do not match join report")
        feature_names = tuple(
            sorted(
                {
                    name
                    for row in self.train_rows + self.validation_rows + self.test_rows
                    for name in row.features
                }
            )
        )
        if self.train_rows + self.validation_rows + self.test_rows and any(
            tuple(sorted(row.features)) != feature_names
            for row in self.train_rows + self.validation_rows + self.test_rows
        ):
            raise ValueError("feature split rows do not use one feature column set")
        return self

    @property
    def manifest(self) -> TrainingFeatureSplitManifest:
        return build_training_feature_split_manifest(self)


def _validate_digest_fields(*values: str) -> None:
    if any(
        len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
        for value in values
    ):
        raise ValueError("training feature manifest contains an invalid SHA-256 digest")


def _validate_feature_label_alignment(
    feature_rows: Sequence[SelectionTrainingFeatureRow],
    label_rows: Sequence[SelectionTrainingLabelRow],
    partition: str,
) -> None:
    labels_by_sample = {row.sample_id: row for row in label_rows}
    feature_sample_ids = tuple(row.sample_id for row in feature_rows)
    if len(feature_sample_ids) != len(set(feature_sample_ids)):
        raise ValueError(f"{partition} feature rows contain duplicate sample IDs")
    for feature_row in feature_rows:
        label_row = labels_by_sample.get(feature_row.sample_id)
        if label_row is None:
            raise ValueError(f"{partition} feature row has no matching label row")
        if feature_row.source_label_row_sha256 != label_row.source_row_sha256:
            raise ValueError(f"{partition} feature row source hash does not match label row")
        if feature_row.target is not label_row.target:
            raise ValueError(f"{partition} feature row target does not match label row")
        if feature_row.candidate_id != label_row.candidate_id:
            raise ValueError(f"{partition} feature row candidate does not match label row")
        if feature_row.observation_date != label_row.observation_date:
            raise ValueError(f"{partition} feature row date does not match label row")
        if feature_row.observation_at != label_row.observation_at:
            raise ValueError(f"{partition} feature row time does not match label row")
        if feature_row.label_available_at != label_row.label_available_at:
            raise ValueError(f"{partition} feature row availability does not match label row")


def _validate_label_rows(
    rows: Sequence[SelectionTrainingLabelRow],
    purpose: TrainingDatasetPurpose,
    label_contract_id: str,
    partition: str,
) -> None:
    sample_ids = tuple(row.sample_id for row in rows)
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError(f"{partition} label rows contain duplicate sample IDs")
    for row in rows:
        if row.purpose is not purpose:
            raise ValueError(f"{partition} label row has a different purpose")
        if row.label_contract_id != label_contract_id:
            raise ValueError(f"{partition} label row has a different label contract")


def build_training_feature_split_manifest(
    split: TrainingFeatureDatasetSplit,
) -> TrainingFeatureSplitManifest:
    """Build hashes for every joined partition and its join report."""

    partitions = {
        "train": serialize_training_feature_dataset(split.train_rows).encode("utf-8"),
        "validation": serialize_training_feature_dataset(split.validation_rows).encode("utf-8"),
        "test": serialize_training_feature_dataset(split.test_rows).encode("utf-8"),
        "purged": serialize_training_label_dataset(split.purged_rows).encode("utf-8"),
    }
    label_partitions = {
        "train": serialize_training_label_dataset(split.label_train_rows).encode("utf-8"),
        "validation": serialize_training_label_dataset(split.label_validation_rows).encode("utf-8"),
        "test": serialize_training_label_dataset(split.label_test_rows).encode("utf-8"),
        "purged": partitions["purged"],
    }
    feature_names = tuple(
        sorted(
            {
                name
                for row in split.train_rows + split.validation_rows + split.test_rows
                for name in row.features
            }
        )
    )
    return TrainingFeatureSplitManifest(
        purpose=split.report.purpose,
        label_contract_id=split.report.label_contract_id,
        feature_set_version_id=split.report.feature_set_version_id,
        label_split_manifest_sha256=sha256_bytes(
            _serialize_label_split_manifest(split.label_split_manifest).encode("utf-8")
        ),
        label_readiness_sha256=sha256_bytes(
            _serialize_label_readiness(split.label_readiness).encode("utf-8")
        ),
        source_label_dataset_sha256=split.report.source_label_dataset_sha256,
        prepared_label_dataset_sha256=split.label_readiness.prepared_dataset_sha256,
        row_counts={name: len(content.splitlines()) for name, content in partitions.items()},
        label_row_counts={
            name: len(content.splitlines()) for name, content in label_partitions.items()
        },
        partition_sha256={name: sha256_bytes(content) for name, content in partitions.items()},
        label_partition_sha256={
            name: sha256_bytes(content) for name, content in label_partitions.items()
        },
        feature_names=feature_names,
        join_report_sha256=sha256_bytes(_serialize_join_report(split.report).encode("utf-8")),
    )


def _serialize_join_report(report: TrainingFeatureJoinReport) -> str:
    return report.model_dump_json(indent=2) + "\n"


def _serialize_label_readiness(report: TrainingLabelReadinessReport) -> str:
    return report.model_dump_json(indent=2) + "\n"


def _serialize_label_split_manifest(manifest: TrainingLabelSplitManifest) -> str:
    return manifest.model_dump_json(indent=2) + "\n"


def serialize_training_feature_dataset(
    rows: Iterable[SelectionTrainingFeatureRow],
) -> str:
    """Serialize joined feature rows as deterministic JSONL."""

    return "".join(f"{row.model_dump_json()}\n" for row in rows)
