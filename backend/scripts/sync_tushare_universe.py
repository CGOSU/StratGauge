"""Fetch Tushare stock_basic and create an immutable point-in-time universe."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.data_pipeline.universe import UniverseSnapshotStore, build_universe_snapshot
from stratgauge.data_sources.tushare_reference import (
    TUSHARE_MAIN_BOARD_MARKET,
    TushareReferenceClient,
    TushareReferenceStore,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        client = TushareReferenceClient()
        rows = client.stock_basic(list_status=arguments.list_status)
        securities = client.securities(rows)
        reference_store = TushareReferenceStore(arguments.root)
        source = reference_store.write(
            endpoint="stock_basic",
            request={
                "list_status": arguments.list_status,
                "market": TUSHARE_MAIN_BOARD_MARKET,
                "exchanges": "SSE,SZSE",
            },
            records=rows,
            created_at=arguments.as_of,
        )
        snapshot = build_universe_snapshot(
            as_of=arguments.as_of,
            source="tushare:stock_basic",
            source_snapshot_ids=(source.snapshot_id,),
            securities=securities,
        )
        stored = UniverseSnapshotStore(arguments.root).write(snapshot, reference_store)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(stored.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--as-of", type=_parse_aware_datetime, required=True)
    parser.add_argument("--list-status", choices=("L", "D", "P", "G"), default="L")
    return parser.parse_args()


def _parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--as-of must include a timezone offset")
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
