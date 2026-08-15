"""Rebuild the selection DuckDB read index from immutable JSON evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError


def main() -> int:
    arguments = _parse_arguments()
    try:
        stats = SelectionImportStore(arguments.root).rebuild_selection_index()
    except (OSError, SelectionStoreError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(
        json.dumps(
            {
                "root": str(arguments.root),
                "index": "tdx/selection-index.duckdb",
                "counts": {
                    "outcomes": stats.outcomes,
                    "performances": stats.performances,
                    "lifecycle_events": stats.lifecycle_events,
                    "reusable_tag_events": stats.reusable_tag_events,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/selection"))
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
