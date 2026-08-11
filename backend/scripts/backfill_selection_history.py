"""Backfill approved daily history for one selection batch."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from stratgauge.data_pipeline.calendar_cache import CalendarCacheError, TradeCalendarCache
from stratgauge.data_pipeline.history_backfill import (
    HistoryBackfillRequest,
    backfill_selection_history,
    build_history_backfill_targets,
    read_trading_sessions,
    write_history_backfill_report,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotStore, SnapshotStoreError
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.baostock import BaoStockAdapter
from stratgauge.data_sources.base import AdapterError, MarketDataAdapter
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.data_sources.tushare_reference import (
    TushareReferenceClient,
    TushareReferenceStore,
)
from stratgauge.data_sources.waizao import WaizaoAdapter
from stratgauge.domain.models import Exchange, Security, canonical_symbol


def main() -> int:
    arguments = _parse_arguments()
    try:
        selection_store = SelectionImportStore(arguments.root)
        imported = selection_store.load_import(arguments.batch_id)
        sessions = _load_sessions(arguments, imported.batch.selection_date)
        targets = build_history_backfill_targets(
            imported,
            benchmark_securities=tuple(
                _explicit_security(symbol) for symbol in arguments.benchmark
            ),
            industry_securities=tuple(_explicit_security(symbol) for symbol in arguments.industry),
        )
        report = backfill_selection_history(
            HistoryBackfillRequest(
                selection_date=imported.batch.selection_date,
                lookback_sessions=arguments.lookback_sessions,
                expected_sessions=sessions,
            ),
            targets,
            adapter=_adapter(arguments.provider),
            market_store=SnapshotStore(arguments.market_root),
            remote_allowed=not arguments.offline,
        )
        if arguments.output:
            write_history_backfill_report(arguments.output, report)
    except (
        AdapterError,
        OSError,
        SelectionStoreError,
        SnapshotStoreError,
        CalendarCacheError,
        ValueError,
    ) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(report.model_dump_json(indent=2))
    return 0 if report.ready else 1


def _adapter(provider: str) -> MarketDataAdapter:
    adapters: dict[str, Callable[[], MarketDataAdapter]] = {
        "tushare": TushareAdapter,
        "akshare": AKShareAdapter,
        "baostock": BaoStockAdapter,
        "waizao": WaizaoAdapter,
    }
    return adapters[provider]()


def _explicit_security(symbol: str) -> Security:
    normalized = symbol.strip().upper()
    if normalized.count(".") != 1:
        raise ValueError("benchmark and industry symbols must use CODE.EXCHANGE")
    local_code, exchange_name = normalized.split(".")
    exchange = Exchange(exchange_name)
    canonical = canonical_symbol(local_code, exchange)
    return Security(
        security_id=f"history-{canonical}",
        local_code=local_code,
        symbol=canonical,
        exchange=exchange,
    )


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/selection"))
    parser.add_argument("--market-root", type=Path, default=Path("data/selection-market"))
    parser.add_argument("--batch-id", required=True)
    parser.add_argument(
        "--provider",
        choices=("tushare", "akshare", "baostock", "waizao"),
        default="tushare",
    )
    parser.add_argument("--lookback-sessions", type=_positive_int, required=True)
    parser.add_argument(
        "--sessions-file",
        type=Path,
        help="local ISO trading-session file; when omitted, use the persistent Tushare cache",
    )
    parser.add_argument("--calendar-exchange", default="SSE")
    parser.add_argument("--calendar-cache", type=Path)
    parser.add_argument("--reference-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--benchmark",
        action="append",
        default=[],
        help="explicit CODE.EXCHANGE benchmark symbol; may be repeated",
    )
    parser.add_argument(
        "--industry",
        action="append",
        default=[],
        help="explicit CODE.EXCHANGE industry symbol; may be repeated",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="只读取已批准单票快照；必须同时提供 --sessions-file，不访问日历或行情 provider",
    )
    arguments = parser.parse_args()
    if arguments.offline and arguments.sessions_file is None:
        parser.error("--offline requires --sessions-file")
    return arguments


def _load_sessions(arguments: argparse.Namespace, selection_date: date) -> tuple[date, ...]:
    if arguments.sessions_file is not None:
        return read_trading_sessions(arguments.sessions_file)
    end_date = selection_date - timedelta(days=1)
    start_date = selection_date - timedelta(days=max(arguments.lookback_sessions * 5, 30))
    cache_path = arguments.calendar_cache or (
        Path("data") / "calendars" / f"trade-cal-{arguments.calendar_exchange.lower()}.txt"
    )
    result = TradeCalendarCache(cache_path).ensure(
        start_date=start_date,
        end_date=end_date,
        exchange=arguments.calendar_exchange,
        client=TushareReferenceClient(),
        reference_store=TushareReferenceStore(arguments.reference_root),
        now=datetime.now(UTC),
    )
    return result.sessions


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
