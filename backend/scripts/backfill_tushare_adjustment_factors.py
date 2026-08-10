"""Backfill Tushare adjustment factors for approved market snapshots."""

from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from stratgauge.data_pipeline.adjustment_factor_sync import (
    AdjustmentFactorSyncError,
    sync_adjustment_factor_sessions,
)
from stratgauge.data_pipeline.history_backfill import read_trading_sessions
from stratgauge.data_pipeline.store import SnapshotStore, SnapshotStoreError
from stratgauge.data_pipeline.universe import UniverseSnapshotStore, UniverseSnapshotStoreError
from stratgauge.data_sources.base import AdapterError
from stratgauge.data_sources.tushare import TushareAdapter


def main() -> int:
    arguments = _parse_arguments()
    try:
        sessions = read_trading_sessions(arguments.calendar_file)
        selected_sessions = _select_sessions(arguments, sessions)
        if not selected_sessions:
            print(
                json.dumps(
                    {"status": "skipped", "reason": "no_trading_sessions_in_range"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        universe = UniverseSnapshotStore(arguments.universe_root).load(
            arguments.universe_snapshot_id
        )
        report = sync_adjustment_factor_sessions(
            adapter=TushareAdapter(),
            session_dates=selected_sessions,
            universe=universe,
            store=SnapshotStore(arguments.market_root),
            minimum_security_coverage=arguments.minimum_coverage,
        )
    except (
        AdapterError,
        AdjustmentFactorSyncError,
        OSError,
        SnapshotStoreError,
        UniverseSnapshotStoreError,
        ValueError,
    ) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(report.model_dump_json(indent=2))
    return 0 if report.ready else 1


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument("--session-date", type=_parse_date)
    date_group.add_argument("--start-date", type=_parse_date)
    parser.add_argument("--end-date", type=_parse_date)
    parser.add_argument("--calendar-file", type=Path, required=True)
    parser.add_argument("--universe-snapshot-id", required=True)
    parser.add_argument("--universe-root", type=Path, default=Path("data"))
    parser.add_argument("--market-root", type=Path, default=Path("data/market"))
    parser.add_argument(
        "--minimum-coverage",
        type=_parse_decimal,
        default=Decimal("0.90"),
    )
    arguments = parser.parse_args()
    if arguments.session_date is not None and arguments.end_date is not None:
        parser.error("--end-date requires --start-date")
    if arguments.start_date is not None and arguments.end_date is None:
        parser.error("--start-date requires --end-date")
    if (
        arguments.start_date is not None
        and arguments.end_date is not None
        and arguments.end_date < arguments.start_date
    ):
        parser.error("--end-date must not precede --start-date")
    return arguments


def _select_sessions(
    arguments: argparse.Namespace,
    sessions: tuple[date, ...],
) -> tuple[date, ...]:
    if arguments.session_date is not None:
        return (arguments.session_date,) if arguments.session_date in sessions else ()
    assert arguments.start_date is not None
    assert arguments.end_date is not None
    return tuple(
        session for session in sessions if arguments.start_date <= session <= arguments.end_date
    )


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
