"""Export the four purpose-specific selection research datasets."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from stratgauge.backtest.selection_dataset import (
    SelectionMLDatasetFile,
    SelectionMLDatasetManifest,
    build_annotation_dataset_for_batches,
    build_selection_dataset_for_batches,
    order_selection_batch_ids,
    serialize_annotation_dataset,
    serialize_selection_dataset,
    serialize_selection_ml_dataset_manifest,
)
from stratgauge.backtest.selection_ml_datasets import (
    build_buy_timing_dataset_for_batches,
    build_sell_timing_dataset_for_batches,
    serialize_buy_timing_dataset,
    serialize_sell_timing_dataset,
)
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore
from stratgauge.data_pipeline.selection_store import (
    SelectionImportStore,
    SelectionStoreError,
)

_BatchSelectionMode = Literal["single_batch", "batch_ids", "all_batches", "date_range"]
_DatasetName = Literal["selection", "annotation", "buy_timing", "sell_timing"]


@dataclass(frozen=True)
class _BatchSelection:
    batch_ids: tuple[str, ...]
    selection_date_from: date
    selection_date_to: date
    mode: _BatchSelectionMode
    requested_selection_date_from: date | None = None
    requested_selection_date_to: date | None = None


def main() -> int:
    arguments = _parse_arguments()
    sessions = _read_sessions(arguments.sessions_file)
    evaluated_at = arguments.evaluated_at
    output_dir = arguments.output_dir
    paths: dict[_DatasetName, Path] = {
        "selection": output_dir / "selection-dataset.jsonl",
        "annotation": output_dir / "annotation-dataset.jsonl",
        "buy_timing": output_dir / "buy-timing-dataset.jsonl",
        "sell_timing": output_dir / "sell-timing-dataset.jsonl",
    }
    manifest_path = output_dir / "manifest.json"
    is_single_batch = arguments.batch_ids is not None and len(arguments.batch_ids) == 1
    target_paths = (*paths.values(), manifest_path)
    existing = tuple(path for path in target_paths if path.exists())
    if existing:
        print(
            json.dumps(
                {
                    "error": "dataset output already exists",
                    "paths": [str(path) for path in existing],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    try:
        selection_store = SelectionImportStore(arguments.selection_root)
        portfolio_store = ManualPortfolioStore(arguments.portfolio_root)
        batch_selection = _select_batches(selection_store, arguments)
        selection_rows = build_selection_dataset_for_batches(
            selection_store,
            batch_selection.batch_ids,
        )
        annotation_rows = build_annotation_dataset_for_batches(
            selection_store,
            batch_selection.batch_ids,
        )
        buy_rows = build_buy_timing_dataset_for_batches(
            selection_store,
            portfolio_store,
            batch_selection.batch_ids,
            trading_sessions=sessions,
            evaluated_at=evaluated_at,
            window_sessions=arguments.buy_window_sessions,
        )
        sell_rows = build_sell_timing_dataset_for_batches(
            selection_store,
            portfolio_store,
            batch_selection.batch_ids,
            trading_sessions=sessions,
            evaluated_at=evaluated_at,
            window_sessions=arguments.sell_window_sessions,
        )
        serialized: dict[_DatasetName, str] = {
            "selection": serialize_selection_dataset(selection_rows),
            "annotation": serialize_annotation_dataset(annotation_rows),
            "buy_timing": serialize_buy_timing_dataset(buy_rows),
            "sell_timing": serialize_sell_timing_dataset(sell_rows),
        }
        contents = {paths[name]: content for name, content in serialized.items()}
        if not is_single_batch:
            manifest = SelectionMLDatasetManifest(
                selection_mode=batch_selection.mode,
                batch_ids=batch_selection.batch_ids,
                selection_date_from=batch_selection.selection_date_from,
                selection_date_to=batch_selection.selection_date_to,
                requested_selection_date_from=batch_selection.requested_selection_date_from,
                requested_selection_date_to=batch_selection.requested_selection_date_to,
                evaluated_at=evaluated_at,
                buy_window_sessions=arguments.buy_window_sessions,
                sell_window_sessions=arguments.sell_window_sessions,
                files=tuple(
                    SelectionMLDatasetFile(
                        dataset=name,
                        path=paths[name].name,
                        row_count=_row_count(serialized[name]),
                        sha256=_sha256_text(serialized[name]),
                    )
                    for name in serialized
                ),
            )
            contents[manifest_path] = serialize_selection_ml_dataset_manifest(manifest)
        _write_bundle(contents)
    except (FileExistsError, OSError, SelectionStoreError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    row_counts = {name: _row_count(content) for name, content in serialized.items()}
    if is_single_batch:
        payload: dict[str, object] = {
            "batch_id": batch_selection.batch_ids[0],
            "evaluated_at": evaluated_at.isoformat(),
            "output_dir": str(output_dir),
            "row_counts": row_counts,
        }
    else:
        payload = {
            "selection_mode": batch_selection.mode,
            "batch_ids": list(batch_selection.batch_ids),
            "selection_date_from": batch_selection.selection_date_from.isoformat(),
            "selection_date_to": batch_selection.selection_date_to.isoformat(),
            "evaluated_at": evaluated_at.isoformat(),
            "output_dir": str(output_dir),
            "manifest": str(manifest_path),
            "row_counts": row_counts,
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    batch_group = parser.add_mutually_exclusive_group(required=True)
    batch_group.add_argument(
        "--batch-id",
        action="append",
        dest="batch_ids",
        help="one batch ID; repeat for an explicit multi-batch export",
    )
    batch_group.add_argument(
        "--all-batches",
        action="store_true",
        help="export all historical batches, including archived batches",
    )
    batch_group.add_argument(
        "--start-date",
        "--selection-date-from",
        dest="start_date",
        type=_parse_date,
        help="inclusive selection-date range start",
    )
    parser.add_argument(
        "--end-date",
        "--selection-date-to",
        dest="end_date",
        type=_parse_date,
        help="inclusive selection-date range end",
    )
    parser.add_argument("--selection-root", type=Path, default=Path("data/selection"))
    parser.add_argument("--portfolio-root", type=Path, default=Path("data"))
    parser.add_argument("--sessions-file", type=Path, required=True)
    parser.add_argument("--evaluated-at", type=_parse_aware_datetime, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--buy-window-sessions", type=_positive_int, default=3)
    parser.add_argument("--sell-window-sessions", type=_positive_int, default=1)
    return parser.parse_args()


def _select_batches(
    store: SelectionImportStore,
    arguments: argparse.Namespace,
) -> _BatchSelection:
    if arguments.end_date is not None and arguments.start_date is None:
        raise ValueError("--end-date requires --start-date")
    if arguments.batch_ids is not None:
        if arguments.start_date is not None or arguments.end_date is not None:
            raise ValueError("date range cannot be combined with --batch-id")
        ordered = order_selection_batch_ids(store, arguments.batch_ids)
        mode: _BatchSelectionMode = "single_batch" if len(ordered) == 1 else "batch_ids"
        return _make_batch_selection(store, ordered, mode=mode)
    if arguments.all_batches:
        if arguments.end_date is not None:
            raise ValueError("--end-date cannot be combined with --all-batches")
        batch_ids = tuple(
            manifest.batch_id for manifest in store.list_manifests(include_archived=True)
        )
        if not batch_ids:
            raise ValueError("no selection batches match the requested scope")
        return _make_batch_selection(store, batch_ids, mode="all_batches")
    if arguments.start_date is None or arguments.end_date is None:
        raise ValueError("date-range export requires --start-date and --end-date")
    if arguments.end_date < arguments.start_date:
        raise ValueError("selection date range is invalid")
    batch_ids = tuple(
        manifest.batch_id
        for manifest in store.list_manifests(include_archived=True)
        if arguments.start_date
        <= store.load_import(manifest.batch_id).batch.selection_date
        <= arguments.end_date
    )
    if not batch_ids:
        raise ValueError("no selection batches match the requested scope")
    return _make_batch_selection(
        store,
        batch_ids,
        mode="date_range",
        requested_selection_date_from=arguments.start_date,
        requested_selection_date_to=arguments.end_date,
    )


def _make_batch_selection(
    store: SelectionImportStore,
    batch_ids: tuple[str, ...],
    *,
    mode: _BatchSelectionMode,
    requested_selection_date_from: date | None = None,
    requested_selection_date_to: date | None = None,
) -> _BatchSelection:
    ordered = order_selection_batch_ids(store, batch_ids)
    selection_dates = tuple(
        store.load_import(batch_id).batch.selection_date for batch_id in ordered
    )
    if not selection_dates:
        raise ValueError("no selection batches match the requested scope")
    return _BatchSelection(
        batch_ids=ordered,
        selection_date_from=min(selection_dates),
        selection_date_to=max(selection_dates),
        mode=mode,
        requested_selection_date_from=requested_selection_date_from,
        requested_selection_date_to=requested_selection_date_to,
    )


def _row_count(content: str) -> int:
    return sum(1 for line in content.splitlines() if line.strip())


def _sha256_text(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _write_bundle(contents: Mapping[Path, str]) -> None:
    """Create all bundle files exclusively and remove newly-created files on failure."""

    created: list[Path] = []
    try:
        for destination, content in contents.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                handle = destination.open("x", encoding="utf-8", newline="\n")
            except FileExistsError as exc:
                raise FileExistsError(f"dataset already exists: {destination}") from exc
            created.append(destination)
            with handle:
                handle.write(content)
    except Exception:
        for destination in reversed(created):
            try:
                destination.unlink()
            except OSError:
                pass
        raise


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone offset")
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _read_sessions(path: Path) -> tuple[date, ...]:
    try:
        values = tuple(
            date.fromisoformat(line.strip())
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid sessions file: {path}") from exc
    if not values or values != tuple(sorted(set(values))):
        raise ValueError("sessions file must contain unique sorted dates")
    return values


if __name__ == "__main__":
    raise SystemExit(main())
