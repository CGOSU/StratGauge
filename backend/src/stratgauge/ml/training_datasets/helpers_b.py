"""Prepare leakage-safe, purpose-specific label datasets for later model training."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime

from pydantic import BaseModel

from stratgauge.backtest.selection_dataset import (
    ANNOTATION_DATASET_SCHEMA_VERSION,
    SelectionAnnotationDatasetRow,
)
from stratgauge.backtest.selection_ml_datasets import (
    BUY_TIMING_DATASET_SCHEMA_VERSION,
    SELL_TIMING_DATASET_SCHEMA_VERSION,
    SelectionBuyTimingRow,
    SelectionSellTimingRow,
)
from stratgauge.ml._hashing import sha256_bytes, sha256_json
from stratgauge.ml.training_datasets.models import (
    PreparedTrainingLabelDataset,
    SelectionTrainingLabelRow,
    TrainingDatasetPurpose,
    TrainingLabel,
    TrainingLabelDatasetSplit,
    label_contract_id_for,
    serialize_training_label_dataset,
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
                observation_at=datetime.combine(
                    row.exposure_session,
                    datetime.min.time(),
                    tzinfo=row.evaluated_at.tzinfo,
                ),
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
    observation_at: datetime | None = None,
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
        observation_at=observation_at,
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
    return sha256_json(row.model_dump(mode="json"))


def _prepared_dataset_sha256(rows: Iterable[SelectionTrainingLabelRow]) -> str:
    return sha256_bytes(serialize_training_label_dataset(rows).encode("utf-8"))


def _stable_identifier(
    purpose: TrainingDatasetPurpose,
    identity: dict[str, str | int],
) -> str:
    payload = {"purpose": purpose.value, **identity}
    return f"training-{purpose.value}-{sha256_json(payload)[:20]}"


def _sorted_rows(
    rows: Iterable[SelectionTrainingLabelRow],
) -> tuple[SelectionTrainingLabelRow, ...]:
    return tuple(sorted(rows, key=lambda row: (row.observation_date, row.sample_id)))


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
            observation_at=_parse_aware_datetime(
                row.annotation_reviewed_at,
                "annotation decision annotation_reviewed_at",
            ),
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
                observation_at=row.annotation_reviewed_at,
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
