"""Verify one local snapshot root without modifying its evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stratgauge.data_pipeline.store import SnapshotStore, SnapshotStoreError


def main() -> int:
    arguments = _parse_arguments()
    try:
        store = SnapshotStore(arguments.root)
        report = store.verify_fast() if arguments.fast else store.verify()
    except (OSError, SnapshotStoreError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(report.model_dump_json(indent=2))
    return 0 if report.healthy else 1


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/selection-market"))
    parser.add_argument(
        "--fast",
        action="store_true",
        help="只检查 catalog、manifest 和关联文件是否存在，不读取文件内容",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
