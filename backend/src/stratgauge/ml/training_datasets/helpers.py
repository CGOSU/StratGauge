"""Prepare leakage-safe, purpose-specific label datasets for later model training."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from stratgauge.backtest.selection_dataset import (
    DATASET_SCHEMA_VERSION,
    SelectionDatasetRow,
    read_annotation_dataset,
    read_selection_dataset,
)
from stratgauge.backtest.selection_ml_datasets import (
    read_buy_timing_dataset,
    read_sell_timing_dataset,
)
from stratgauge.domain.selections import SelectionOutcomeStatus
from stratgauge.ml._artifacts import write_immutable_bundle
from stratgauge.ml._hashing import sha256_bytes
from stratgauge.ml.training_datasets.helpers_b import (
    _label_availability_violation_count,
    _label_row,
    _parse_aware_datetime,
    _parse_date,
    _partition_for_observation,
    _prepare_annotation_decision_labels,
    _prepare_buy_timing_labels,
    _prepare_sell_timing_labels,
    _prepared_dataset_sha256,
    _require_aware,
    _sorted_rows,
)
from stratgauge.ml.training_datasets.models import (
    _COMPLETED_OUTCOME_STATUSES,
    TRAINING_LABEL_DATASET_SCHEMA_VERSION,
    PreparedTrainingLabelDataset,
    SelectionTrainingLabelRow,
    TrainingDatasetPurpose,
    TrainingLabel,
    TrainingLabelDatasetSplit,
    TrainingLabelReadinessReport,
    TrainingLabelSplitManifest,
    _duplicate_count,
    _validate_unique_training_rows,
    label_contract_id_for,
    serialize_training_label_dataset,
)


def prepare_training_label_dataset(
    content: bytes,
    *,
    purpose: TrainingDatasetPurpose | str,
    tracking_policy_id: str | None = None,
) -> PreparedTrainingLabelDataset:
    """Normalize one raw research dataset into one target-only training contract."""

    resolved_purpose = TrainingDatasetPurpose(purpose)
    source_file_sha256 = sha256_bytes(content)
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
    write_immutable_bundle(
        destination,
        outputs,
        already_exists_message=f"training label split output already exists: {destination}",
    )
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
