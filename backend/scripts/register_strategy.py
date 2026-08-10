"""Register one immutable strategy version."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stratgauge.domain.models import StrategyVersion
from stratgauge.strategies.store import StrategyStoreError, StrategyVersionStore


def main() -> int:
    arguments = _parse_arguments()
    try:
        strategy = StrategyVersion.model_validate_json(arguments.input.read_text(encoding="utf-8"))
        stored = StrategyVersionStore(arguments.root).create(strategy)
    except (OSError, StrategyStoreError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(stored.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--input", type=Path, required=True)
    arguments = parser.parse_args()
    if not arguments.input.is_file():
        parser.error(f"--input does not exist: {arguments.input}")
    return arguments


if __name__ == "__main__":
    raise SystemExit(main())
