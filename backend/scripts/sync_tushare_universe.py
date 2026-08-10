"""Fetch Tushare stock_basic and create an immutable point-in-time universe."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.data_pipeline.universe import (
    UniverseSnapshotStore,
    build_universe_snapshot,
    securities_at,
)
from stratgauge.data_sources.tushare_reference import (
    TUSHARE_MAIN_BOARD_MARKET,
    TushareReferenceClient,
    TushareReferenceStore,
)
from stratgauge.domain.models import Security


def main() -> int:
    arguments = _parse_arguments()
    try:
        client = TushareReferenceClient()
        statuses = ("L", "D", "P") if arguments.historical else (arguments.list_status,)
        rows_by_status = {status: client.stock_basic(list_status=status) for status in statuses}
        rows = tuple(row for status in statuses for row in rows_by_status[status])
        securities = _merge_securities(
            tuple(client.securities(rows_by_status[status]) for status in statuses)
        )
        if arguments.historical:
            securities = securities_at(securities, as_of=arguments.as_of.date())
        reference_store = TushareReferenceStore(arguments.root)
        source = reference_store.write(
            endpoint="stock_basic",
            request={
                "list_status": ",".join(statuses),
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
    parser.add_argument(
        "--historical",
        action="store_true",
        help="fetch L/D/P records and keep securities effective at --as-of",
    )
    return parser.parse_args()


def _merge_securities(groups: tuple[tuple[Security, ...], ...]) -> tuple[Security, ...]:
    merged: dict[str, Security] = {}
    for group in groups:
        for security in group:
            existing = merged.get(security.symbol)
            if existing is None:
                merged[security.symbol] = security
                continue
            if existing.model_copy(update={"listed": True}) != security.model_copy(
                update={"listed": True}
            ):
                raise ValueError(f"stock_basic returned conflicting metadata: {security.symbol}")
            merged[security.symbol] = existing.model_copy(
                update={"listed": existing.listed or security.listed}
            )
    return tuple(sorted(merged.values(), key=lambda security: security.symbol))


def _parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--as-of must include a timezone offset")
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
