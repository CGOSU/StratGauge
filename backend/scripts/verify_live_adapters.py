"""Explicit network verification for both market-data adapters."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from stratgauge.data_pipeline.ingest import IngestResult, ingest
from stratgauge.data_pipeline.quality import compare_bars
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.baostock import BaoStockAdapter
from stratgauge.data_sources.base import AdapterError, DailyBarRequest
from stratgauge.data_sources.waizao import WaizaoAdapter
from stratgauge.domain.models import Exchange, Security, canonical_symbol


def main() -> int:
    arguments = _parse_arguments()
    symbol = arguments.symbol.upper()
    local_code, exchange_name = symbol.split(".", 1)
    exchange = Exchange(exchange_name)
    security = Security(
        security_id=f"live-{symbol}",
        local_code=local_code,
        symbol=canonical_symbol(local_code, exchange),
        exchange=exchange,
    )
    request = DailyBarRequest(
        security=security,
        start_date=arguments.start_date,
        end_date=arguments.end_date,
    )
    store = SnapshotStore(arguments.root)
    try:
        primary_adapter = AKShareAdapter() if arguments.primary == "akshare" else WaizaoAdapter()
        primary = ingest(
            primary_adapter,
            request,
            store,
            expected_security_ids=[security.security_id],
        )
        fallback = ingest(
            BaoStockAdapter(),
            request,
            store,
            expected_security_ids=[security.security_id],
        )
    except AdapterError as exc:
        print(json.dumps({"message": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    if not primary.quality.approved or not fallback.quality.approved:
        print(json.dumps(_quality_failure(primary, fallback), ensure_ascii=False, indent=2))
        return 1

    comparison = compare_bars(
        primary.bars,
        fallback.bars,
        snapshot_id=f"cross-source-{primary.manifest.snapshot_id}-{fallback.manifest.snapshot_id}",
    )
    summary = {
        "primary": primary.manifest.model_dump(mode="json"),
        "fallback": fallback.manifest.model_dump(mode="json"),
        "cross_source": comparison.model_dump(mode="json"),
        "cross_source_approved": comparison.approved,
        "rows": {"primary": len(primary.bars), "fallback": len(fallback.bars)},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if comparison.approved else 1


def _quality_failure(primary: IngestResult, fallback: IngestResult) -> dict[str, object]:
    return {
        "message": "provider quality gate rejected the live snapshot",
        "primary": primary.quality.model_dump(mode="json"),
        "fallback": fallback.quality.model_dump(mode="json"),
    }


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="000001.SZ")
    parser.add_argument("--primary", choices=("akshare", "waizao"), default="akshare")
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2024, 1, 2))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2024, 1, 5))
    parser.add_argument("--root", type=Path, default=Path("data/live-verification"))
    arguments = parser.parse_args()
    if "." not in arguments.symbol:
        parser.error("--symbol must use the CODE.EXCHANGE form, for example 000001.SZ")
    if arguments.end_date < arguments.start_date:
        parser.error("--end-date must not precede --start-date")
    return arguments


if __name__ == "__main__":
    raise SystemExit(main())
