"""Export one imported selection batch as immutable JSONL research data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stratgauge.backtest.selection_dataset import build_selection_dataset, write_selection_dataset
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError


def main() -> int:
    arguments = _parse_arguments()
    try:
        rows = build_selection_dataset(
            SelectionImportStore(arguments.root),
            arguments.batch_id,
        )
        write_selection_dataset(arguments.output, rows)
    except (FileExistsError, OSError, SelectionStoreError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(
        json.dumps(
            {
                "batch_id": arguments.batch_id,
                "output": str(arguments.output),
                "row_count": len(rows),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/selection"))
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
