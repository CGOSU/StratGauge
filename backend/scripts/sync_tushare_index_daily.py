"""Synchronize the explicit 000300.SH Tushare index_daily benchmark."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from stratgauge.data_pipeline.history_backfill import read_trading_sessions
from stratgauge.data_pipeline.ingest import IngestResult, ingest
from stratgauge.data_pipeline.store import SnapshotStore, SnapshotStoreError
from stratgauge.data_sources.base import (
    INDEX_DAILY_BENCHMARK_SECURITY_ID,
    INDEX_DAILY_BENCHMARK_SYMBOL,
    AdapterError,
    IndexDailyRequest,
)
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.domain.models import Exchange, Security


def main() -> int:
    arguments = _parse_arguments()
    try:
        sessions = read_trading_sessions(arguments.calendar_file)
        selected_sessions = _select_sessions(arguments, sessions)
        if not selected_sessions:
            print(
                json.dumps(
                    {
                        "status": "skipped",
                        "reason": "no_trading_sessions_in_range",
                        "symbol": INDEX_DAILY_BENCHMARK_SYMBOL,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        request = IndexDailyRequest(
            security=_benchmark_security(),
            start_date=selected_sessions[0],
            end_date=selected_sessions[-1],
        )
        store = SnapshotStore(arguments.market_root)
        with TushareAdapter() as adapter:
            result = ingest(
                adapter,
                request,
                store,
                expected_sessions=selected_sessions,
                expected_security_ids=(INDEX_DAILY_BENCHMARK_SECURITY_ID,),
            )
    except (AdapterError, OSError, SnapshotStoreError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(_result_payload(request, result), ensure_ascii=False, indent=2))
    return 0 if result.quality.approved else 1


def _benchmark_security() -> Security:
    return Security(
        security_id=INDEX_DAILY_BENCHMARK_SECURITY_ID,
        local_code=INDEX_DAILY_BENCHMARK_SYMBOL.split(".", 1)[0],
        symbol=INDEX_DAILY_BENCHMARK_SYMBOL,
        exchange=Exchange.SH,
        name="沪深300指数",
    )


def _result_payload(request: IndexDailyRequest, result: IngestResult) -> dict[str, object]:
    return {
        "status": "completed" if result.quality.approved else "partial",
        "request_kind": request.request_kind,
        "symbol": request.security.symbol,
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "snapshot_id": result.manifest.snapshot_id,
        "manifest": result.manifest.model_dump(mode="json"),
        "quality": result.quality.model_dump(mode="json"),
        "access_plan": result.access_plan.model_dump(mode="json"),
    }


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument("--session-date", type=_parse_date)
    date_group.add_argument("--start-date", type=_parse_date)
    parser.add_argument("--end-date", type=_parse_date)
    parser.add_argument("--calendar-file", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, default=Path("data/selection-market"))
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


if __name__ == "__main__":
    raise SystemExit(main())
