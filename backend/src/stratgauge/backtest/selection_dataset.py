"""Build a time-stamped, outcome-aware dataset from selection storage."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from stratgauge.backtest.selection_outcomes import latest_selection_outcomes
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    SelectionAnnotation,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
    SelectionOutcome,
    SelectionOutcomeStatus,
    SelectionPatternTag,
    SelectionPositionTag,
    SelectionPriceVolumeTag,
    SelectionSectorTag,
)

DATASET_SCHEMA_VERSION = "selection-dataset-v2"
ANNOTATION_DATASET_SCHEMA_VERSION = "selection-annotation-dataset-v1"
SELECTION_ML_DATASET_MANIFEST_SCHEMA_VERSION = "selection-ml-datasets-manifest-v1"
_SELECTION_ML_DATASET_NAMES = ("selection", "annotation", "buy_timing", "sell_timing")


class SelectionMLDatasetFile(BaseModel):
    """One immutable JSONL output referenced by an ML dataset bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: Literal["selection", "annotation", "buy_timing", "sell_timing"]
    path: str
    row_count: int = Field(ge=0)
    sha256: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("dataset file path must not be empty")
        return normalized

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("dataset file sha256 must be a lowercase SHA-256 digest")
        return normalized


class SelectionMLDatasetManifest(BaseModel):
    """Immutable provenance for one single- or multi-batch dataset export."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SELECTION_ML_DATASET_MANIFEST_SCHEMA_VERSION
    selection_mode: Literal["single_batch", "batch_ids", "all_batches", "date_range"]
    batch_ids: tuple[str, ...]
    selection_date_from: date
    selection_date_to: date
    requested_selection_date_from: date | None = None
    requested_selection_date_to: date | None = None
    include_archived_batches: bool = True
    evaluated_at: datetime
    buy_window_sessions: int = Field(gt=0)
    sell_window_sessions: int = Field(gt=0)
    files: tuple[SelectionMLDatasetFile, ...]

    @field_validator("batch_ids")
    @classmethod
    def validate_batch_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if not normalized or any(not item for item in normalized):
            raise ValueError("dataset manifest requires batch IDs")
        if len(set(normalized)) != len(normalized):
            raise ValueError("dataset manifest batch IDs must be unique")
        return normalized

    @field_validator("evaluated_at")
    @classmethod
    def validate_evaluated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("dataset manifest evaluated_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> SelectionMLDatasetManifest:
        if self.selection_date_to < self.selection_date_from:
            raise ValueError("dataset manifest selection date range is invalid")
        requested_dates = (
            self.requested_selection_date_from,
            self.requested_selection_date_to,
        )
        if (requested_dates[0] is None) != (requested_dates[1] is None):
            raise ValueError("dataset manifest requested date range must be complete")
        if (
            requested_dates[0] is not None
            and requested_dates[1] is not None
            and requested_dates[1] < requested_dates[0]
        ):
            raise ValueError("dataset manifest requested date range is invalid")
        if self.selection_mode == "date_range" and requested_dates[0] is None:
            raise ValueError("date-range dataset manifest requires requested dates")
        if self.selection_mode != "date_range" and requested_dates[0] is not None:
            raise ValueError("only date-range dataset manifests may have requested dates")
        if tuple(item.dataset for item in self.files) != _SELECTION_ML_DATASET_NAMES:
            raise ValueError("dataset manifest must list all four datasets in order")
        if len({item.path for item in self.files}) != len(self.files):
            raise ValueError("dataset manifest file paths must be unique")
        return self


class SelectionAnnotationDatasetRow(BaseModel):
    """One human-review observation joined only to outcomes known afterward."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = ANNOTATION_DATASET_SCHEMA_VERSION
    annotation_id: str
    candidate_id: str
    batch_id: str
    source: Literal["tongdaxin", "manual"] | None = None
    symbol: str
    selection_date: str
    source_filename: str | None
    formula_id: str | None
    formula_name: str
    formula_version: str | None
    annotation_reviewed_at: str
    annotation_recorded_at: str
    overall_score: int
    outlook: SelectionAnnotationOutlook
    decision: SelectionAnnotationDecision
    position_tags: tuple[SelectionPositionTag, ...]
    pattern_tags: tuple[SelectionPatternTag, ...]
    price_volume_tags: tuple[SelectionPriceVolumeTag, ...]
    sector_tags: tuple[SelectionSectorTag, ...]
    taxonomy_version: str
    annotation_note: str | None
    tracking_policy_id: str | None
    outcome_policy_id: str | None = None
    cost_schedule_id: str | None
    horizon_sessions: int | None
    position_notional: str | None
    outcome_status: SelectionOutcomeStatus | None
    outcome_evaluated_at: str | None
    net_return: str | None
    source_snapshot_ids: tuple[str, ...]


class SelectionDatasetRow(BaseModel):
    """One versioned candidate/outcome observation for later time-split training."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = DATASET_SCHEMA_VERSION
    candidate_id: str
    batch_id: str
    source: Literal["tongdaxin", "manual"] | None = None
    symbol: str
    selection_date: str
    source_filename: str | None
    formula_id: str | None
    formula_name: str
    formula_version: str | None
    manual_decision: ManualSelectionDecision | None
    manual_labeled_at: str | None
    tracking_policy_id: str | None
    outcome_policy_id: str | None = None
    cost_schedule_id: str | None
    horizon_sessions: int | None
    position_notional: str | None
    outcome_status: SelectionOutcomeStatus | None
    outcome_evaluated_at: str | None
    net_return: str | None
    source_snapshot_ids: tuple[str, ...]


def build_selection_dataset(
    store: SelectionImportStore,
    batch_id: str,
) -> tuple[SelectionDatasetRow, ...]:
    """Build rows without dropping pending, no-entry or unresolved observations."""

    imported = store.load_import(batch_id)
    rows: list[SelectionDatasetRow] = []
    for candidate in imported.candidates:
        labels = store.list_labels(candidate.candidate_id)
        latest_label = labels[-1] if labels else None
        outcomes = latest_selection_outcomes(store.list_outcomes(candidate.candidate_id))
        observations: Iterable[SelectionOutcome | None] = outcomes or (None,)
        for outcome in observations:
            rows.append(
                SelectionDatasetRow(
                    candidate_id=candidate.candidate_id,
                    batch_id=batch_id,
                    source=imported.batch.source,
                    symbol=candidate.symbol,
                    selection_date=imported.batch.selection_date.isoformat(),
                    source_filename=imported.batch.source_filename,
                    formula_id=imported.batch.formula_id,
                    formula_name=imported.batch.formula_name,
                    formula_version=imported.batch.formula_version,
                    manual_decision=latest_label.decision if latest_label else None,
                    manual_labeled_at=(
                        latest_label.labeled_at.isoformat() if latest_label else None
                    ),
                    tracking_policy_id=(outcome.tracking_policy_id if outcome else None),
                    outcome_policy_id=(outcome.outcome_policy_id if outcome else None),
                    cost_schedule_id=(outcome.cost_schedule_id if outcome else None),
                    horizon_sessions=(outcome.horizon_sessions if outcome else None),
                    position_notional=(
                        str(outcome.position_notional)
                        if outcome and outcome.position_notional is not None
                        else None
                    ),
                    outcome_status=outcome.status if outcome else None,
                    outcome_evaluated_at=(outcome.evaluated_at.isoformat() if outcome else None),
                    net_return=(
                        str(outcome.net_return)
                        if outcome and outcome.net_return is not None
                        else None
                    ),
                    source_snapshot_ids=outcome.source_snapshot_ids if outcome else (),
                )
            )
    return tuple(rows)


def order_selection_batch_ids(
    store: SelectionImportStore,
    batch_ids: Iterable[str],
) -> tuple[str, ...]:
    """Validate and deterministically order batch IDs by selection date then ID."""

    normalized = tuple(item.strip() for item in batch_ids)
    if not normalized or any(not item for item in normalized):
        raise ValueError("batch IDs must not be empty")
    if len(set(normalized)) != len(normalized):
        raise ValueError("batch IDs must be unique")
    loaded = tuple((batch_id, store.load_import(batch_id)) for batch_id in normalized)
    return tuple(
        batch_id
        for batch_id, imported in sorted(
            loaded,
            key=lambda item: (item[1].batch.selection_date, item[0]),
        )
    )


def build_annotation_dataset(
    store: SelectionImportStore,
    batch_id: str,
) -> tuple[SelectionAnnotationDatasetRow, ...]:
    """Build one row per annotation while enforcing a future-outcome boundary."""

    imported = store.load_import(batch_id)
    rows: list[SelectionAnnotationDatasetRow] = []
    for candidate in imported.candidates:
        outcomes = latest_selection_outcomes(store.list_outcomes(candidate.candidate_id))
        for annotation in store.list_annotations(candidate.candidate_id):
            outcome = _latest_outcome_after_annotation(outcomes, annotation)
            rows.append(
                SelectionAnnotationDatasetRow(
                    annotation_id=annotation.annotation_id,
                    candidate_id=candidate.candidate_id,
                    batch_id=batch_id,
                    source=imported.batch.source,
                    symbol=candidate.symbol,
                    selection_date=imported.batch.selection_date.isoformat(),
                    source_filename=imported.batch.source_filename,
                    formula_id=imported.batch.formula_id,
                    formula_name=imported.batch.formula_name,
                    formula_version=imported.batch.formula_version,
                    annotation_reviewed_at=annotation.reviewed_at.isoformat(),
                    annotation_recorded_at=annotation.recorded_at.isoformat(),
                    overall_score=annotation.overall_score,
                    outlook=annotation.outlook,
                    decision=annotation.decision,
                    position_tags=annotation.position_tags,
                    pattern_tags=annotation.pattern_tags,
                    price_volume_tags=annotation.price_volume_tags,
                    sector_tags=annotation.sector_tags,
                    taxonomy_version=annotation.taxonomy_version,
                    annotation_note=annotation.note,
                    tracking_policy_id=(outcome.tracking_policy_id if outcome else None),
                    outcome_policy_id=(outcome.outcome_policy_id if outcome else None),
                    cost_schedule_id=(outcome.cost_schedule_id if outcome else None),
                    horizon_sessions=(outcome.horizon_sessions if outcome else None),
                    position_notional=(
                        str(outcome.position_notional)
                        if outcome and outcome.position_notional is not None
                        else None
                    ),
                    outcome_status=outcome.status if outcome else None,
                    outcome_evaluated_at=(outcome.evaluated_at.isoformat() if outcome else None),
                    net_return=(
                        str(outcome.net_return)
                        if outcome and outcome.net_return is not None
                        else None
                    ),
                    source_snapshot_ids=outcome.source_snapshot_ids if outcome else (),
                )
            )
    return tuple(sorted(rows, key=lambda row: (row.annotation_recorded_at, row.annotation_id)))


def build_selection_dataset_for_batches(
    store: SelectionImportStore,
    batch_ids: Iterable[str],
) -> tuple[SelectionDatasetRow, ...]:
    """Build the candidate dataset in stable selection-batch order."""

    ordered_batch_ids = order_selection_batch_ids(store, batch_ids)
    return tuple(
        row for batch_id in ordered_batch_ids for row in build_selection_dataset(store, batch_id)
    )


def build_annotation_dataset_for_batches(
    store: SelectionImportStore,
    batch_ids: Iterable[str],
) -> tuple[SelectionAnnotationDatasetRow, ...]:
    """Build the annotation dataset in stable selection-batch order."""

    ordered_batch_ids = order_selection_batch_ids(store, batch_ids)
    return tuple(
        row for batch_id in ordered_batch_ids for row in build_annotation_dataset(store, batch_id)
    )


def read_selection_dataset(content: bytes) -> tuple[SelectionDatasetRow, ...]:
    """Parse UTF-8 JSONL and reject unknown or malformed dataset rows."""

    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("dataset is not valid UTF-8") from exc
    rows: list[SelectionDatasetRow] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("dataset row must be a JSON object")
            if payload.get("schema_version") != DATASET_SCHEMA_VERSION:
                raise ValueError("unsupported dataset schema")
            rows.append(SelectionDatasetRow.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid dataset row at line {line_number}: {exc}") from exc
    return tuple(rows)


def read_annotation_dataset(content: bytes) -> tuple[SelectionAnnotationDatasetRow, ...]:
    """Parse the versioned one-row-per-annotation JSONL dataset."""

    return _read_dataset(content, ANNOTATION_DATASET_SCHEMA_VERSION, SelectionAnnotationDatasetRow)


def serialize_selection_dataset(rows: Iterable[SelectionDatasetRow]) -> str:
    """Serialize dataset rows as newline-delimited JSON."""

    return "".join(f"{row.model_dump_json()}\n" for row in rows)


def serialize_annotation_dataset(rows: Iterable[SelectionAnnotationDatasetRow]) -> str:
    """Serialize annotation rows as newline-delimited JSON."""

    return "".join(f"{row.model_dump_json()}\n" for row in rows)


def read_selection_ml_dataset_manifest(content: bytes) -> SelectionMLDatasetManifest:
    """Parse and validate a dataset bundle manifest."""

    try:
        return SelectionMLDatasetManifest.model_validate_json(content)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid selection ML dataset manifest: {exc}") from exc


def serialize_selection_ml_dataset_manifest(manifest: SelectionMLDatasetManifest) -> str:
    """Serialize a dataset bundle manifest deterministically."""

    return manifest.model_dump_json(indent=2) + "\n"


def write_selection_ml_dataset_manifest(
    path: str | Path,
    manifest: SelectionMLDatasetManifest,
) -> None:
    """Write a dataset bundle manifest without overwriting existing evidence."""

    _write_dataset(path, serialize_selection_ml_dataset_manifest(manifest))


def write_annotation_dataset(
    path: str | Path,
    rows: Iterable[SelectionAnnotationDatasetRow],
) -> None:
    """Write annotation JSONL without overwriting an existing dataset."""

    _write_dataset(path, serialize_annotation_dataset(rows))


def write_selection_dataset(path: str | Path, rows: Iterable[SelectionDatasetRow]) -> None:
    """Write newline-delimited JSON without overwriting an existing dataset."""

    _write_dataset(path, serialize_selection_dataset(rows))


def _read_dataset[
    RowT: BaseModel,
](content: bytes, schema_version: str, row_type: type[RowT]) -> tuple[RowT, ...]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("dataset is not valid UTF-8") from exc
    rows: list[RowT] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("dataset row must be a JSON object")
            if payload.get("schema_version") != schema_version:
                raise ValueError("unsupported dataset schema")
            rows.append(row_type.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid dataset row at line {line_number}: {exc}") from exc
    return tuple(rows)


def _write_dataset(path: str | Path, content: str) -> None:
    destination = Path(path)
    if destination.exists():
        raise FileExistsError(f"dataset already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _latest_outcome_after_annotation(
    outcomes: tuple[SelectionOutcome, ...],
    annotation: SelectionAnnotation,
) -> SelectionOutcome | None:
    future = tuple(outcome for outcome in outcomes if outcome.evaluated_at > annotation.recorded_at)
    return future[-1] if future else None
