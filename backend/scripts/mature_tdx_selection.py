"""Mature one imported TongdaXin batch with an explicit daily-bar provider."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from stratgauge.backtest.selection_job import run_selection_outcome_job
from stratgauge.backtest.selection_outcomes import SelectionTrackingPolicy
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.baostock import BaoStockAdapter
from stratgauge.data_sources.base import MarketDataAdapter
from stratgauge.data_sources.waizao import WaizaoAdapter


def main() -> int:
    arguments = _parse_arguments()
    try:
        selection_store = SelectionImportStore(arguments.root)
        imported = selection_store.load_import(arguments.batch_id)
        result = run_selection_outcome_job(
            imported,
            adapter=_adapter(arguments.provider),
            selection_store=selection_store,
            market_store=SnapshotStore(arguments.market_root),
            evaluated_at=arguments.evaluated_at,
            policy=SelectionTrackingPolicy(
                horizon_sessions=arguments.horizon_sessions,
                position_notional=arguments.position_notional,
            ),
        )
    except (OSError, SelectionStoreError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(
        json.dumps(
            {
                "batch_id": arguments.batch_id,
                "provider": arguments.provider,
                "outcomes": [outcome.model_dump(mode="json") for outcome in result.outcomes],
                "rejected_candidates": [
                    {"candidate_id": candidate_id, "reason": reason}
                    for candidate_id, reason in result.rejected_candidates
                ],
                "market_snapshot_ids": result.market_snapshot_ids,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _adapter(provider: str) -> MarketDataAdapter:
    adapters = {
        "akshare": AKShareAdapter,
        "baostock": BaoStockAdapter,
        "waizao": WaizaoAdapter,
    }
    return adapters[provider]()


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/selection"))
    parser.add_argument("--market-root", type=Path, default=Path("data/selection-market"))
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--provider", choices=("akshare", "baostock", "waizao"), required=True)
    parser.add_argument("--evaluated-at", type=_parse_aware_datetime, required=True)
    parser.add_argument("--horizon-sessions", type=int, required=True)
    parser.add_argument("--position-notional", type=_parse_positive_decimal, required=True)
    arguments = parser.parse_args()
    if arguments.horizon_sessions <= 0:
        parser.error("--horizon-sessions must be positive")
    return arguments


def _parse_positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("notional must be a decimal number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("notional must be positive and finite")
    return parsed


def _parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone offset")
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
