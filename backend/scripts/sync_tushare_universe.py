"""Fetch Tushare stock_basic and create an immutable point-in-time universe."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.data_pipeline.universe import (
    SecurityMasterStore,
    UniverseSnapshotStore,
    build_security_master_snapshot,
    build_universe_snapshot,
)
from stratgauge.data_sources.tushare_reference import (
    TUSHARE_MAIN_BOARD_MARKET,
    TushareReferenceClient,
    TushareReferenceStore,
)
from stratgauge.domain.models import SecurityMasterRecord


def main() -> int:
    arguments = _parse_arguments()
    try:
        client = TushareReferenceClient()
        statuses = ("L", "D", "P") if arguments.historical else (arguments.list_status,)
        rows_by_status = {status: client.stock_basic(list_status=status) for status in statuses}
        rows = tuple(row for status in statuses for row in rows_by_status[status])
        master_records = _merge_master_records(
            tuple(client.security_master_records(rows_by_status[status]) for status in statuses)
        )
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
        master_snapshot = build_security_master_snapshot(
            observed_at=arguments.as_of,
            source_snapshot_id=source.snapshot_id,
            records=master_records,
        )
        SecurityMasterStore(arguments.root).write(master_snapshot, reference_store)
        effective_securities = tuple(
            record.to_security(effective_date=arguments.as_of.date())
            for record in master_records
            if record.is_effective_on(arguments.as_of.date())
        )
        snapshot = build_universe_snapshot(
            as_of=arguments.as_of,
            membership_date=arguments.as_of.date(),
            source="tushare:stock_basic",
            source_snapshot_ids=(source.snapshot_id,),
            security_master_snapshot_id=master_snapshot.snapshot_id,
            securities=effective_securities,
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


def _merge_master_records(
    groups: tuple[tuple[SecurityMasterRecord, ...], ...],
) -> tuple[SecurityMasterRecord, ...]:
    merged: dict[str, SecurityMasterRecord] = {}
    for group in groups:
        for record in group:
            existing = merged.get(record.symbol)
            if existing is not None and existing != record:
                raise ValueError(f"stock_basic returned conflicting metadata: {record.symbol}")
            merged[record.symbol] = record
    return tuple(sorted(merged.values(), key=lambda record: record.symbol))


def _parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--as-of must include a timezone offset")
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
