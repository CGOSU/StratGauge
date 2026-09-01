"""Explicitly prepare calendar and market evidence for selection ML research."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from stratgauge.application.ml_market_evidence import (
    SelectionMLMarketEvidenceRequest,
    prepare_selection_ml_market_evidence,
    resolve_selection_batch_ids,
    write_selection_ml_market_evidence_report,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotStore, SnapshotStoreError
from stratgauge.data_pipeline.universe import UniverseSnapshotStore, UniverseSnapshotStoreError


def main() -> int:
    arguments = _parse_arguments()
    try:
        selection_store = SelectionImportStore(arguments.selection_root)
        batch_ids = resolve_selection_batch_ids(
            selection_store,
            batch_ids=arguments.batch_ids,
            all_batches=arguments.all_batches,
            selection_date_from=arguments.start_date,
            selection_date_to=arguments.end_date,
        )
        request = SelectionMLMarketEvidenceRequest(
            batch_ids=batch_ids,
            universe_snapshot_id=arguments.universe_snapshot_id,
            lookback_sessions=arguments.lookback_sessions,
            maturity_horizon_sessions=arguments.maturity_horizon_sessions,
            calendar_exchange=arguments.calendar_exchange,
            minimum_security_coverage=arguments.minimum_coverage,
            now=arguments.now or datetime.now(UTC),
        )
        report = prepare_selection_ml_market_evidence(
            selection_store,
            UniverseSnapshotStore(arguments.universe_root),
            SnapshotStore(arguments.market_root),
            request=request,
            calendar_path=arguments.calendar_cache,
            calendar_reference_root=arguments.reference_root,
        )
        if arguments.output is not None:
            write_selection_ml_market_evidence_report(arguments.output, report)
    except (
        FileExistsError,
        OSError,
        SelectionStoreError,
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
    scope_group = parser.add_mutually_exclusive_group(required=True)
    scope_group.add_argument(
        "--batch-id",
        action="append",
        dest="batch_ids",
        help="one batch ID; repeat for an explicit multi-batch scope",
    )
    scope_group.add_argument(
        "--all-batches",
        action="store_true",
        help="include all historical batches, including archived batches",
    )
    scope_group.add_argument(
        "--start-date",
        "--selection-date-from",
        dest="start_date",
        type=_parse_date,
        help="inclusive selection-date range start",
    )
    parser.add_argument(
        "--end-date",
        "--selection-date-to",
        dest="end_date",
        type=_parse_date,
        help="inclusive selection-date range end",
    )
    parser.add_argument("--selection-root", type=Path, default=Path("data/selection"))
    parser.add_argument("--universe-root", type=Path, default=Path("data"))
    parser.add_argument("--universe-snapshot-id", required=True)
    parser.add_argument("--market-root", type=Path, default=Path("data/selection-market"))
    parser.add_argument(
        "--calendar-cache",
        "--calendar-file",
        dest="calendar_cache",
        type=Path,
        default=Path("data/calendars/trade-cal-sse.txt"),
    )
    parser.add_argument("--reference-root", type=Path, default=Path("data"))
    parser.add_argument("--calendar-exchange", default="SSE")
    parser.add_argument("--lookback-sessions", type=_positive_int, default=20)
    parser.add_argument("--maturity-horizon-sessions", type=_positive_int, default=10)
    parser.add_argument(
        "--minimum-coverage",
        "--minimum-security-coverage",
        dest="minimum_coverage",
        type=_parse_decimal,
        default=Decimal("0.90"),
    )
    parser.add_argument("--now", type=_parse_aware_datetime)
    parser.add_argument("--output", "--report-output", dest="output", type=Path)
    arguments = parser.parse_args()
    if arguments.end_date is not None and arguments.start_date is None:
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


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _parse_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("coverage must be a decimal") from exc
    if not parsed.is_finite() or not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("coverage must be between 0 and 1")
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
