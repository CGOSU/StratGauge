"""Join prepared labels to point-in-time feature snapshots without leakage."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.features.contract import FeatureSnapshot
from stratgauge.features.store import FeatureSnapshotStore, FeatureStoreError
from stratgauge.ml.training_datasets import (
    SelectionTrainingLabelRow,
    TrainingDatasetPurpose,
    TrainingLabel,
    TrainingLabelDatasetSplit,
    TrainingLabelReadinessReport,
    TrainingLabelSplitManifest,
)

TRAINING_FEATURE_DATASET_SCHEMA_VERSION = "selection-purpose-feature-dataset-v1"


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


class TrainingFeatureDatasetSplit(DomainModel):
    """Joined feature partitions with purged labels kept out of training."""

    report: TrainingFeatureJoinReport
    label_split_manifest: TrainingLabelSplitManifest
    label_readiness: TrainingLabelReadinessReport
    train_rows: tuple[SelectionTrainingFeatureRow, ...]
    validation_rows: tuple[SelectionTrainingFeatureRow, ...]
    test_rows: tuple[SelectionTrainingFeatureRow, ...]
    purged_rows: tuple[SelectionTrainingLabelRow, ...]


def join_training_features(
    label_split: TrainingLabelDatasetSplit,
    *,
    feature_store: FeatureSnapshotStore,
    feature_snapshot_ids: Sequence[str],
    feature_set_version_id: str,
    label_split_manifest: TrainingLabelSplitManifest,
    label_readiness: TrainingLabelReadinessReport,
) -> TrainingFeatureDatasetSplit:
    """Join only safe snapshots explicitly supplied by the caller."""

    prepared = label_split.prepared
    if prepared.purpose is not label_split_manifest.purpose:
        raise ValueError("feature join purpose does not match label split manifest")
    if prepared.label_contract_id != label_split_manifest.label_contract_id:
        raise ValueError("feature join label contract does not match split manifest")
    if feature_set_version_id.strip() == "":
        raise ValueError("feature_set_version_id must not be empty")
    _validate_snapshot_ids(feature_snapshot_ids)
    snapshots = _load_snapshots(feature_store, feature_snapshot_ids)
    by_key = _index_safe_snapshots(snapshots, feature_set_version_id)
    joined: dict[str, list[SelectionTrainingFeatureRow]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    failures: list[tuple[str, str]] = []
    for name, rows in (
        ("train", label_split.train_rows),
        ("validation", label_split.validation_rows),
        ("test", label_split.test_rows),
    ):
        for row in rows:
            try:
                snapshot = _resolve_snapshot(row, by_key)
                joined[name].append(_feature_row(row, snapshot))
            except ValueError as exc:
                failures.append((row.sample_id, str(exc)))
    report = TrainingFeatureJoinReport(
        purpose=prepared.purpose,
        label_contract_id=prepared.label_contract_id,
        feature_set_version_id=feature_set_version_id,
        source_label_dataset_sha256=prepared.source_file_sha256,
        source_label_row_count=len(prepared.rows),
        joined_row_count=sum(len(rows) for rows in joined.values()),
        purged_row_count=len(label_split.purged_rows),
        failure_count=len(failures),
        joined_feature_snapshot_ids=tuple(
            sorted({row.feature_snapshot_id for rows in joined.values() for row in rows})
        ),
        failures=tuple(failures),
    )
    return TrainingFeatureDatasetSplit(
        report=report,
        label_split_manifest=label_split_manifest,
        label_readiness=label_readiness,
        train_rows=tuple(joined["train"]),
        validation_rows=tuple(joined["validation"]),
        test_rows=tuple(joined["test"]),
        purged_rows=label_split.purged_rows,
    )


def serialize_training_feature_dataset(
    rows: Iterable[SelectionTrainingFeatureRow],
) -> str:
    """Serialize joined feature rows as deterministic JSONL."""

    return "".join(f"{row.model_dump_json()}\n" for row in rows)


def write_training_feature_split(
    output_dir: str | Path,
    split: TrainingFeatureDatasetSplit,
) -> None:
    """Write joined feature partitions and join report without overwriting."""

    destination = Path(output_dir)
    outputs = {
        "train.jsonl": serialize_training_feature_dataset(split.train_rows),
        "validation.jsonl": serialize_training_feature_dataset(split.validation_rows),
        "test.jsonl": serialize_training_feature_dataset(split.test_rows),
        "purged-labels.jsonl": "".join(f"{row.model_dump_json()}\n" for row in split.purged_rows),
        "join-report.json": split.report.model_dump_json(indent=2) + "\n",
    }
    destination.mkdir(parents=True, exist_ok=True)
    paths = tuple(destination / name for name in outputs)
    if any(path.exists() for path in paths):
        raise FileExistsError(f"training feature output already exists: {destination}")
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


def _load_snapshots(
    store: FeatureSnapshotStore,
    snapshot_ids: Sequence[str],
) -> tuple[FeatureSnapshot, ...]:
    snapshots: list[FeatureSnapshot] = []
    for snapshot_id in snapshot_ids:
        try:
            snapshots.append(store.load(snapshot_id))
        except FeatureStoreError as exc:
            raise ValueError(str(exc)) from exc
    return tuple(snapshots)


def _index_safe_snapshots(
    snapshots: Sequence[FeatureSnapshot],
    feature_set_version_id: str,
) -> dict[tuple[str, date], tuple[FeatureSnapshot, ...]]:
    result: dict[tuple[str, date], list[FeatureSnapshot]] = {}
    for snapshot in snapshots:
        if snapshot.feature_set_version_id != feature_set_version_id:
            raise ValueError(
                f"feature snapshot uses a different feature set: {snapshot.feature_snapshot_id}"
            )
        key = (snapshot.symbol, snapshot.selection_date)
        result.setdefault(key, []).append(snapshot)
    return {key: tuple(values) for key, values in result.items()}


def _resolve_snapshot(
    row: SelectionTrainingLabelRow,
    by_key: dict[tuple[str, date], tuple[FeatureSnapshot, ...]],
) -> FeatureSnapshot:
    candidates = tuple(
        snapshot
        for snapshot in by_key.get((row.symbol, row.observation_date), ())
        if _snapshot_is_safe(row, snapshot)
    )
    if not candidates:
        raise ValueError("no safe feature snapshot for label observation")
    latest_as_of = max(snapshot.as_of for snapshot in candidates)
    latest = tuple(snapshot for snapshot in candidates if snapshot.as_of == latest_as_of)
    if len(latest) != 1:
        raise ValueError("multiple feature snapshots share the safe latest as_of")
    return latest[0]


def _snapshot_is_safe(row: SelectionTrainingLabelRow, snapshot: FeatureSnapshot) -> bool:
    if snapshot.as_of.date() > row.observation_date:
        return False
    return row.observation_at is None or snapshot.as_of <= row.observation_at


def _feature_row(
    row: SelectionTrainingLabelRow,
    snapshot: FeatureSnapshot,
) -> SelectionTrainingFeatureRow:
    return SelectionTrainingFeatureRow(
        purpose=row.purpose,
        label_contract_id=row.label_contract_id,
        sample_id=row.sample_id,
        source_label_row_sha256=row.source_row_sha256,
        candidate_id=row.candidate_id,
        batch_id=row.batch_id,
        symbol=row.symbol,
        observation_date=row.observation_date,
        observation_at=row.observation_at,
        label_available_at=row.label_available_at,
        target=row.target,
        feature_set_version_id=snapshot.feature_set_version_id,
        feature_snapshot_id=snapshot.feature_snapshot_id,
        feature_as_of=snapshot.as_of,
        feature_available_at=snapshot.available_at,
        input_session_start=snapshot.input_session_start,
        input_session_end=snapshot.input_session_end,
        source_snapshot_ids=snapshot.source_snapshot_ids,
        features=snapshot.values,
    )


def _validate_snapshot_ids(values: Sequence[str]) -> None:
    if not values:
        raise ValueError("feature_snapshot_ids must not be empty")
    normalized = tuple(value.strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError("feature snapshot identifiers must not be empty")
    if len(normalized) != len(set(normalized)):
        raise ValueError("feature snapshot identifiers must be unique")
