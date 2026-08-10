"""Fetch an explicit Tushare trading calendar file for feature backfill."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from stratgauge.data_sources.tushare_reference import TushareReferenceClient


def main() -> int:
    arguments = _parse_arguments()
    sessions = TushareReferenceClient().trade_calendar(
        start_date=arguments.start_date,
        end_date=arguments.end_date,
        exchange=arguments.exchange,
    )
    if arguments.output.exists():
        raise SystemExit(f"refusing to overwrite existing calendar: {arguments.output}")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        "".join(f"{session.isoformat()}\n" for session in sessions),
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {len(sessions)} sessions to {arguments.output}")
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=_parse_date, required=True)
    parser.add_argument("--end-date", type=_parse_date, required=True)
    parser.add_argument("--exchange", default="SSE")
    parser.add_argument("--output", type=Path, required=True)
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
