"""Create deterministic train/validation/test JSONL lists from a selection dataset."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from stratgauge.backtest.selection_split import write_selection_dataset_split


def main() -> int:
    arguments = _parse_arguments()
    manifest = write_selection_dataset_split(
        arguments.input,
        arguments.output_dir,
        train_end=arguments.train_end,
        validation_end=arguments.validation_end,
    )
    print(manifest.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--train-end", type=_parse_date, required=True)
    parser.add_argument("--validation-end", type=_parse_date, required=True)
    arguments = parser.parse_args()
    if arguments.train_end >= arguments.validation_end:
        parser.error("--train-end must precede --validation-end")
    return arguments


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


if __name__ == "__main__":
    raise SystemExit(main())
