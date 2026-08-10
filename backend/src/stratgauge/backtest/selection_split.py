"""Deterministic date-based train/validation/test splits for selection datasets."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel, ConfigDict, Field

from stratgauge.backtest.selection_dataset import SelectionDatasetRow

SPLIT_MANIFEST_VERSION = "selection-dataset-split-v1"
DATASET_SCHEMA_VERSION = "selection-dataset-v1"


class SelectionDatasetSplitManifest(BaseModel):
    """Reproducibility record for one deterministic dataset split."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: str = SPLIT_MANIFEST_VERSION
    dataset_schema_version: str = DATASET_SCHEMA_VERSION
    source_file_sha256: str
    train_end: date
    validation_end: date
    row_counts: dict[str, int] = Field(default_factory=dict)


def split_selection_dataset(
    rows: Iterable[SelectionDatasetRow],
    *,
    train_end: date,
    validation_end: date,
) -> dict[str, tuple[SelectionDatasetRow, ...]]:
    """Split rows by selection date without shuffling or mixing future rows."""

    if train_end >= validation_end:
        raise ValueError("train_end must precede validation_end")
    buckets: dict[str, list[SelectionDatasetRow]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    ordered = sorted(rows, key=lambda row: (row.selection_date, row.candidate_id))
    for row in ordered:
        if row.schema_version != DATASET_SCHEMA_VERSION:
            raise ValueError(f"unsupported dataset schema: {row.schema_version}")
        selection_date = date.fromisoformat(row.selection_date)
        if selection_date <= train_end:
            buckets["train"].append(row)
        elif selection_date <= validation_end:
            buckets["validation"].append(row)
        else:
            buckets["test"].append(row)
    return {name: tuple(items) for name, items in buckets.items()}


def write_selection_dataset_split(
    source_path: str | Path,
    output_dir: str | Path,
    *,
    train_end: date,
    validation_end: date,
) -> SelectionDatasetSplitManifest:
    """Read one JSONL file and write non-overwriting split files plus a manifest."""

    source = Path(source_path)
    destination = Path(output_dir)
    content = source.read_bytes()
    rows = _read_dataset_rows(content)
    splits = split_selection_dataset(
        rows,
        train_end=train_end,
        validation_end=validation_end,
    )
    manifest = SelectionDatasetSplitManifest(
        source_file_sha256=hashlib.sha256(content).hexdigest(),
        train_end=train_end,
        validation_end=validation_end,
        row_counts={name: len(items) for name, items in splits.items()},
    )
    destination.mkdir(parents=True, exist_ok=True)
    paths = {name: destination / f"{name}.jsonl" for name in splits}
    manifest_path = destination / "manifest.json"
    if any(path.exists() for path in (*paths.values(), manifest_path)):
        raise FileExistsError(f"dataset split output already exists: {destination}")
    try:
        for name, split_rows in splits.items():
            _atomic_write(
                paths[name],
                "".join(f"{row.model_dump_json()}\n" for row in split_rows),
            )
        _atomic_write(manifest_path, manifest.model_dump_json(indent=2))
    except OSError:
        for path in (*paths.values(), manifest_path):
            path.unlink(missing_ok=True)
        raise
    return manifest


def _read_dataset_rows(content: bytes) -> tuple[SelectionDatasetRow, ...]:
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
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError) as exc:
            raise ValueError(f"invalid dataset row at line {line_number}: {exc}") from exc
    return tuple(rows)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.replace(path)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
