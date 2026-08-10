"""Synchronize one explicit full-market Tushare daily session."""

from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from stratgauge.data_pipeline.history_backfill import read_trading_sessions
from stratgauge.data_pipeline.market_daily_sync import (
    MarketDailySyncError,
    sync_market_session,
)
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore, SnapshotStoreError
from stratgauge.data_pipeline.universe import UniverseSnapshotStore, UniverseSnapshotStoreError
from stratgauge.data_sources.base import AdapterError
from stratgauge.data_sources.tushare import TushareAdapter


def main() -> int:
    arguments = _parse_arguments()
    try:
        sessions = read_trading_sessions(arguments.calendar_file)
        if arguments.session_date not in sessions:
            print(
                json.dumps(
                    {
                        "status": "skipped",
                        "reason": "not_trading_session",
                        "session_date": arguments.session_date.isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        universe = UniverseSnapshotStore(arguments.universe_root).load(
            arguments.universe_snapshot_id
        )
        result = sync_market_session(
            adapter=TushareAdapter(),
            session_date=arguments.session_date,
            universe=universe,
            store=SnapshotStore(arguments.market_root),
            minimum_security_coverage=arguments.minimum_coverage,
        )
    except (
        AdapterError,
        MarketDailySyncError,
        OSError,
        SnapshotStoreError,
        UniverseSnapshotStoreError,
        ValueError,
    ) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(result.model_dump_json(indent=2))
    return 0 if result.status is not SnapshotStatus.REJECTED else 1


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-date", type=_parse_date, required=True)
    parser.add_argument("--calendar-file", type=Path, required=True)
    parser.add_argument("--universe-snapshot-id", required=True)
    parser.add_argument("--universe-root", type=Path, default=Path("data"))
    parser.add_argument("--market-root", type=Path, default=Path("data/market"))
    parser.add_argument(
        "--minimum-coverage",
        type=_parse_decimal,
        default=Decimal("0.90"),
    )
    return parser.parse_args()


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _parse_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("coverage must be a decimal") from exc
    if not parsed.is_finite() or not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("coverage must be between 0 and 1")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
