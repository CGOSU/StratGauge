"""Create a cross-validated trading calendar from BaoStock and AKShare."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

from stratgauge.data_pipeline.calendar_cache import CalendarCacheError, TradeCalendarCache
from stratgauge.data_sources.base import AdapterError
from stratgauge.data_sources.calendar import (
    AKShareCalendarSource,
    BaoStockCalendarSource,
    CalendarReferenceStore,
    CalendarReferenceStoreError,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        result = TradeCalendarCache(arguments.output).ensure_cross_validated(
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            exchange=arguments.exchange,
            primary=BaoStockCalendarSource(),
            secondary=AKShareCalendarSource(),
            reference_store=CalendarReferenceStore(arguments.root),
            now=datetime.now(UTC),
        )
    except (
        AdapterError,
        CalendarCacheError,
        CalendarReferenceStoreError,
        OSError,
        ValueError,
    ) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(result.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=_parse_date, required=True)
    parser.add_argument("--end-date", type=_parse_date, required=True)
    parser.add_argument("--exchange", default="SSE")
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/calendars/trade-cal-sse.txt"),
    )
    arguments = parser.parse_args()
    if arguments.end_date < arguments.start_date:
        parser.error("--end-date must not precede --start-date")
    return arguments


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


if __name__ == "__main__":
    raise SystemExit(main())
