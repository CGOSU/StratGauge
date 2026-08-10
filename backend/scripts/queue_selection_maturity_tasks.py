"""Queue snapshot-backed selection maturity tasks for OS-level scheduling."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from stratgauge.backtest.maturity_queue import MaturityQueueError, queue_snapshot_maturity_tasks
from stratgauge.backtest.selection_outcomes import SelectionTrackingPolicy
from stratgauge.data_pipeline.history_backfill import read_trading_sessions
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotStore


def main() -> int:
    arguments = _parse_arguments()
    try:
        result = queue_snapshot_maturity_tasks(
            SelectionImportStore(arguments.root),
            SnapshotStore(arguments.market_root),
            provider=arguments.provider,
            policy=SelectionTrackingPolicy(
                horizon_sessions=arguments.horizon_sessions,
                position_notional=arguments.position_notional,
            ),
            trading_sessions=read_trading_sessions(arguments.calendar_file),
            now=arguments.now,
            max_retries=arguments.max_retries,
            timeout_seconds=arguments.timeout_seconds,
            universe_snapshot_id=arguments.universe_snapshot_id,
        )
    except (MaturityQueueError, OSError, SelectionStoreError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(result.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/selection"))
    parser.add_argument("--market-root", type=Path, default=Path("data/selection-market"))
    parser.add_argument("--calendar-file", type=Path, required=True)
    parser.add_argument(
        "--provider",
        choices=("tushare", "akshare", "baostock", "waizao"),
        default="tushare",
    )
    parser.add_argument("--horizon-sessions", type=_positive_int, default=10)
    parser.add_argument("--position-notional", type=_positive_decimal, required=True)
    parser.add_argument("--max-retries", type=_retry_count, default=2)
    parser.add_argument("--timeout-seconds", type=_timeout_seconds, default=900.0)
    parser.add_argument("--universe-snapshot-id")
    parser.add_argument("--now", type=_parse_aware_datetime)
    arguments = parser.parse_args()
    arguments.now = arguments.now or datetime.now(UTC)
    return arguments


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _retry_count(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("max retries must be an integer") from exc
    if not 0 <= parsed <= 5:
        raise argparse.ArgumentTypeError("max retries must be between 0 and 5")
    return parsed


def _timeout_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not 0 < parsed <= 86400:
        raise argparse.ArgumentTypeError("timeout must be between 1 and 86400 seconds")
    return parsed


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("notional must be a decimal number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("notional must be positive and finite")
    return parsed


def _parse_aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("datetime must use ISO format") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone offset")
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
