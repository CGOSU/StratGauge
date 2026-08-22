"""Contracts for purpose-specific point-in-time feature snapshot evidence."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    label_contract_id_for,
)

TRAINING_FEATURE_SNAPSHOT_REPORT_VERSION = "selection-training-feature-snapshot-report-v1"

TrainingFeatureSnapshotAsOfSource = Literal["observation_at", "batch_exported_at"]

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class TrainingFeatureSnapshotBuildFailure(DomainModel):
    """One label row that could not produce a safe feature snapshot."""

    sample_id: str
    candidate_id: str
    symbol: str
    observation_date: date
    source_label_row_sha256: str
    reason: str
    as_of: datetime | None = None
    as_of_source: TrainingFeatureSnapshotAsOfSource | None = None

    @field_validator("sample_id", "candidate_id", "symbol", "reason")
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("training feature snapshot failure text must not be empty")
        return normalized

    @field_validator("source_label_row_sha256")
    @classmethod
    def validate_source_label_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256_PATTERN.fullmatch(normalized):
            raise ValueError("source_label_row_sha256 must be a lowercase SHA-256 digest")
        return normalized

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("training feature snapshot as_of must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_as_of_lineage(self) -> TrainingFeatureSnapshotBuildFailure:
        if self.as_of is None and self.as_of_source is not None:
            raise ValueError("as_of_source requires as_of")
        if self.as_of is not None and self.as_of_source is None:
            raise ValueError("as_of requires as_of_source")
        return self


class TrainingFeatureSnapshotMapping(DomainModel):
    """One label-to-feature mapping retaining its complete point-in-time lineage."""

    sample_id: str
    source_label_row_sha256: str
    candidate_id: str
    batch_id: str
    symbol: str
    observation_date: date
    observation_at: datetime | None = None
    as_of: datetime
    as_of_source: TrainingFeatureSnapshotAsOfSource
    feature_snapshot_id: str
    source_snapshot_ids: tuple[str, ...]

    @field_validator(
        "sample_id",
        "source_label_row_sha256",
        "candidate_id",
        "batch_id",
        "symbol",
        "feature_snapshot_id",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("training feature snapshot mapping text must not be empty")
        return normalized

    @field_validator("source_label_row_sha256")
    @classmethod
    def validate_source_label_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256_PATTERN.fullmatch(normalized):
            raise ValueError("source_label_row_sha256 must be a lowercase SHA-256 digest")
        return normalized

    @field_validator("as_of", "observation_at")
    @classmethod
    def validate_aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("training feature snapshot times must include a timezone")
        return value

    @field_validator("source_snapshot_ids")
    @classmethod
    def validate_source_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not snapshot_id.strip() for snapshot_id in value):
            raise ValueError("training feature snapshot mappings require source snapshot IDs")
        normalized = tuple(snapshot_id.strip() for snapshot_id in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("training feature snapshot mappings contain duplicate source IDs")
        return normalized

    @model_validator(mode="after")
    def validate_timing(self) -> TrainingFeatureSnapshotMapping:
        if self.as_of.date() > self.observation_date:
            raise ValueError("feature snapshot as_of cannot follow observation date")
        if self.observation_at is not None and self.as_of > self.observation_at:
            raise ValueError("feature snapshot as_of cannot follow observation time")
        if self.as_of_source == "observation_at":
            if self.observation_at is None or self.as_of != self.observation_at:
                raise ValueError("observation_at mappings must use observation_at as_of")
        elif self.observation_at is not None:
            raise ValueError("batch-export mappings cannot carry observation_at")
        return self


class TrainingFeatureSnapshotBuildReport(DomainModel):
    """Immutable accounting for one purpose-specific feature snapshot build."""

    report_version: str = TRAINING_FEATURE_SNAPSHOT_REPORT_VERSION
    report_id: str
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    source_label_dataset_sha256: str
    source_label_row_count: int = Field(ge=0)
    snapshot_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    source_market_snapshot_ids: tuple[str, ...]
    source_benchmark_snapshot_ids: tuple[str, ...] = ()
    source_adjustment_factor_snapshot_ids: tuple[str, ...] = ()
    source_benchmark_adjustment_factor_snapshot_ids: tuple[str, ...] = ()
    source_snapshot_ids: tuple[str, ...]
    mappings: tuple[TrainingFeatureSnapshotMapping, ...] = ()
    failures: tuple[TrainingFeatureSnapshotBuildFailure, ...] = ()

    @field_validator("report_id", "feature_set_version_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("training feature snapshot report text must not be empty")
        return normalized

    @field_validator("source_label_dataset_sha256")
    @classmethod
    def validate_source_label_dataset_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256_PATTERN.fullmatch(normalized):
            raise ValueError("source_label_dataset_sha256 must be a lowercase SHA-256 digest")
        return normalized

    @field_validator(
        "source_market_snapshot_ids",
        "source_benchmark_snapshot_ids",
        "source_adjustment_factor_snapshot_ids",
        "source_benchmark_adjustment_factor_snapshot_ids",
        "source_snapshot_ids",
    )
    @classmethod
    def validate_snapshot_id_groups(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(snapshot_id.strip() for snapshot_id in value)
        if any(not snapshot_id for snapshot_id in normalized):
            raise ValueError("training feature snapshot source IDs must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("training feature snapshot source IDs must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_report(self) -> TrainingFeatureSnapshotBuildReport:
        if self.report_version != TRAINING_FEATURE_SNAPSHOT_REPORT_VERSION:
            raise ValueError("unsupported training feature snapshot report version")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("training feature snapshot report contract does not match purpose")
        if not self.source_market_snapshot_ids:
            raise ValueError("training feature snapshot report requires market source IDs")
        if self.source_label_row_count != self.snapshot_count + self.failure_count:
            raise ValueError("training feature snapshot report row counts do not reconcile")
        if self.snapshot_count != len(self.mappings):
            raise ValueError(
                "training feature snapshot report snapshot count does not match mappings"
            )
        if self.failure_count != len(self.failures):
            raise ValueError(
                "training feature snapshot report failure count does not match failures"
            )
        sample_ids = tuple(mapping.sample_id for mapping in self.mappings) + tuple(
            failure.sample_id for failure in self.failures
        )
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("training feature snapshot report contains duplicate sample IDs")
        expected_source_ids = _unique_ids(
            (
                *self.source_market_snapshot_ids,
                *self.source_benchmark_snapshot_ids,
                *self.source_adjustment_factor_snapshot_ids,
                *self.source_benchmark_adjustment_factor_snapshot_ids,
            )
        )
        if self.source_snapshot_ids != expected_source_ids:
            raise ValueError("training feature snapshot report source IDs are incomplete")
        return self


def _unique_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


__all__ = [
    "TRAINING_FEATURE_SNAPSHOT_REPORT_VERSION",
    "TrainingFeatureSnapshotAsOfSource",
    "TrainingFeatureSnapshotBuildFailure",
    "TrainingFeatureSnapshotBuildReport",
    "TrainingFeatureSnapshotMapping",
]
