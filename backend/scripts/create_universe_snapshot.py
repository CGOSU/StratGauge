"""Create an immutable point-in-time universe snapshot."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_pipeline.universe import (
    UniverseSnapshotStore,
    build_universe_snapshot,
)
from stratgauge.domain.models import Exchange, Security, canonical_symbol


def main() -> int:
    arguments = _parse_arguments()
    try:
        securities = tuple(_parse_security(value) for value in arguments.symbol)
        snapshot = build_universe_snapshot(
            as_of=arguments.as_of,
            source=arguments.source,
            source_snapshot_ids=arguments.source_snapshot_id,
            securities=securities,
        )
        stored = UniverseSnapshotStore(arguments.root).write(
            snapshot,
            SnapshotStore(arguments.market_root),
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(stored.model_dump_json(indent=2))
    return 0


def _parse_security(value: str) -> Security:
    normalized = value.strip().upper()
    if normalized.count(".") != 1:
        raise ValueError("--symbol must use CODE.EXCHANGE")
    local_code, exchange_name = normalized.split(".")
    exchange = Exchange(exchange_name)
    symbol = canonical_symbol(local_code, exchange)
    return Security(
        security_id=f"universe-{symbol}",
        local_code=local_code,
        symbol=symbol,
        exchange=exchange,
    )


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--market-root", type=Path, default=Path("data/selection-market"))
    parser.add_argument("--as-of", type=_parse_aware_datetime, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-snapshot-id", action="append", required=True)
    parser.add_argument("--symbol", action="append", required=True)
    return parser.parse_args()


def _parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--as-of must include a timezone offset")
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
