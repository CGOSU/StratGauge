"""Join prepared labels to point-in-time feature snapshots without leakage."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from stratgauge.features.contract import FeatureSnapshot
from stratgauge.features.store import FeatureSnapshotStore, FeatureStoreError
from stratgauge.ml.training_datasets import (
    SelectionTrainingLabelRow,
    TrainingLabelDatasetSplit,
    TrainingLabelReadinessReport,
    TrainingLabelSplitManifest,
    serialize_training_label_dataset,
)
from stratgauge.ml.training_feature_join.models import (
    SelectionTrainingFeatureRow,
    TrainingFeatureDatasetSplit,
    TrainingFeatureJoinReport,
    _serialize_join_report,
    _serialize_label_readiness,
    _serialize_label_split_manifest,
    serialize_training_feature_dataset,
)


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
        label_train_rows=label_split.train_rows,
        label_validation_rows=label_split.validation_rows,
        label_test_rows=label_split.test_rows,
    )


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
        "purged-labels.jsonl": serialize_training_label_dataset(split.purged_rows),
        "label-train.jsonl": serialize_training_label_dataset(split.label_train_rows),
        "label-validation.jsonl": serialize_training_label_dataset(split.label_validation_rows),
        "label-test.jsonl": serialize_training_label_dataset(split.label_test_rows),
        "join-report.json": _serialize_join_report(split.report),
        "label-split-manifest.json": _serialize_label_split_manifest(split.label_split_manifest),
        "label-readiness.json": _serialize_label_readiness(split.label_readiness),
        "manifest.json": split.manifest.model_dump_json(indent=2) + "\n",
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
