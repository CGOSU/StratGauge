"""Export one batch's append-only human reviews as an immutable JSONL dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stratgauge.backtest.selection_dataset import build_annotation_dataset, write_annotation_dataset
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError


def main() -> int:
    arguments = _parse_arguments()
    try:
        rows = build_annotation_dataset(
            SelectionImportStore(arguments.root),
            arguments.batch_id,
        )
        write_annotation_dataset(arguments.output, rows)
    except (FileExistsError, OSError, SelectionStoreError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(
        json.dumps(
            {
                "batch_id": arguments.batch_id,
                "output": str(arguments.output),
                "row_count": len(rows),
                "schema_version": "selection-annotation-dataset-v1",
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
