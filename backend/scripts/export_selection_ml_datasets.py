"""Export the four purpose-specific selection research datasets."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from stratgauge.backtest.selection_dataset import (
    build_annotation_dataset,
    build_selection_dataset,
    write_annotation_dataset,
    write_selection_dataset,
)
from stratgauge.backtest.selection_ml_datasets import (
    build_buy_timing_dataset,
    build_sell_timing_dataset,
    write_buy_timing_dataset,
    write_sell_timing_dataset,
)
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore
from stratgauge.data_pipeline.selection_store import SelectionImportStore


def main() -> int:
    arguments = _parse_arguments()
    sessions = _read_sessions(arguments.sessions_file)
    evaluated_at = arguments.evaluated_at
    output_dir = arguments.output_dir
    paths = {
        "selection": output_dir / "selection-dataset.jsonl",
        "annotation": output_dir / "annotation-dataset.jsonl",
        "buy_timing": output_dir / "buy-timing-dataset.jsonl",
        "sell_timing": output_dir / "sell-timing-dataset.jsonl",
    }
    existing = tuple(path for path in paths.values() if path.exists())
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
        selection_rows = build_selection_dataset(selection_store, arguments.batch_id)
        annotation_rows = build_annotation_dataset(selection_store, arguments.batch_id)
        buy_rows = build_buy_timing_dataset(
            selection_store,
            portfolio_store,
            arguments.batch_id,
            trading_sessions=sessions,
            evaluated_at=evaluated_at,
            window_sessions=arguments.buy_window_sessions,
        )
        sell_rows = build_sell_timing_dataset(
            selection_store,
            portfolio_store,
            arguments.batch_id,
            trading_sessions=sessions,
            evaluated_at=evaluated_at,
            window_sessions=arguments.sell_window_sessions,
        )
        write_selection_dataset(paths["selection"], selection_rows)
        write_annotation_dataset(paths["annotation"], annotation_rows)
        write_buy_timing_dataset(paths["buy_timing"], buy_rows)
        write_sell_timing_dataset(paths["sell_timing"], sell_rows)
    except (FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(
        json.dumps(
            {
                "batch_id": arguments.batch_id,
                "evaluated_at": evaluated_at.isoformat(),
                "output_dir": str(output_dir),
                "row_counts": {
                    "selection": len(selection_rows),
                    "annotation": len(annotation_rows),
                    "buy_timing": len(buy_rows),
                    "sell_timing": len(sell_rows),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--selection-root", type=Path, default=Path("data/selection"))
    parser.add_argument("--portfolio-root", type=Path, default=Path("data"))
    parser.add_argument("--sessions-file", type=Path, required=True)
    parser.add_argument("--evaluated-at", type=_parse_aware_datetime, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--buy-window-sessions", type=_positive_int, default=3)
    parser.add_argument("--sell-window-sessions", type=_positive_int, default=1)
    return parser.parse_args()


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
