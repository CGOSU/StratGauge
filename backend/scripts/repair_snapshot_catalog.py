"""Repair missing local snapshot catalog rows from immutable manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stratgauge.data_pipeline.store import SnapshotStore, SnapshotStoreError


def main() -> int:
    arguments = _parse_arguments()
    try:
        repaired = SnapshotStore(arguments.root).repair_catalog()
    except (OSError, SnapshotStoreError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"repaired_snapshot_ids": repaired}, ensure_ascii=False, indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/selection-market"))
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
