"""Queue and run selection maturity from approved local market snapshots only."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, cast

from stratgauge.application.offline_pipeline import (
    OfflinePipelineError,
    run_offline_maturity_pipeline,
)
from stratgauge.backtest.selection_outcomes import SelectionTrackingPolicy
from stratgauge.data_pipeline.history_backfill import read_trading_sessions
from stratgauge.data_pipeline.selection_store import SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotStoreError


def main() -> int:
    arguments = _parse_arguments()
    try:
        sessions = read_trading_sessions(arguments.calendar_file)
        selected_sessions = _select_sessions(arguments, sessions)
        result = run_offline_maturity_pipeline(
            arguments.root,
            arguments.market_root,
            provider=cast(
                Literal["tushare", "akshare", "baostock", "waizao"],
                arguments.provider,
            ),
            policy=SelectionTrackingPolicy(
                horizon_sessions=arguments.horizon_sessions,
                position_notional=arguments.position_notional,
            ),
            trading_sessions=selected_sessions,
            now=arguments.now,
            limit=arguments.limit,
        )
    except (
        OfflinePipelineError,
        OSError,
        SelectionStoreError,
        SnapshotStoreError,
        ValueError,
    ) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    payload = {
        "queue": result.queue.model_dump(mode="json"),
        "jobs": [job.model_dump(mode="json") for job in result.jobs],
        "tasks": [task.model_dump(mode="json") for task in result.tasks],
        "verification": result.verification.model_dump(mode="json"),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.verification.healthy else 1


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument("--session-date", type=_parse_date)
    date_group.add_argument("--start-date", type=_parse_date)
    parser.add_argument("--end-date", type=_parse_date)
    parser.add_argument("--calendar-file", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("data/selection"))
    parser.add_argument("--market-root", type=Path, default=Path("data/selection-market"))
    parser.add_argument(
        "--provider",
        choices=("tushare", "akshare", "baostock", "waizao"),
        default="tushare",
    )
    parser.add_argument("--horizon-sessions", type=_positive_int, default=10)
    parser.add_argument("--position-notional", type=_parse_decimal, default=Decimal("10000"))
    parser.add_argument("--now", type=_parse_datetime, required=True)
    parser.add_argument("--limit", type=_positive_int)
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
        if arguments.session_date not in sessions:
            raise ValueError("--session-date is not present in --calendar-file")
        return (arguments.session_date,)
    assert arguments.start_date is not None
    assert arguments.end_date is not None
    selected = tuple(
        session for session in sessions if arguments.start_date <= session <= arguments.end_date
    )
    if not selected:
        raise ValueError("the requested date range contains no trading sessions")
    return selected


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("datetime must use ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone")
    return parsed.astimezone(UTC)


def _parse_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("position-notional must be a decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("position-notional must be positive")
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
