"""Append one strategy lifecycle transition."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.domain.models import StrategyLifecycle
from stratgauge.strategies.store import StrategyStoreError, StrategyVersionStore


def main() -> int:
    arguments = _parse_arguments()
    try:
        strategy = StrategyVersionStore(arguments.root).transition(
            arguments.strategy_version_id,
            arguments.to_lifecycle,
            changed_at=arguments.changed_at,
            reason=arguments.reason,
        )
    except (OSError, StrategyStoreError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(strategy.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--strategy-version-id", required=True)
    parser.add_argument("--to", dest="to_lifecycle", type=StrategyLifecycle, required=True)
    parser.add_argument("--changed-at", type=_parse_aware_datetime, required=True)
    parser.add_argument("--reason", required=True)
    return parser.parse_args()


def _parse_aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("datetime must use ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone offset")
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
