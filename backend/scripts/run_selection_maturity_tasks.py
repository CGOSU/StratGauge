"""Run queued selection maturity tasks once for OS-level scheduling."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.backtest.selection_tasks import SelectionMaturityTaskError, run_maturity_task
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.baostock import BaoStockAdapter
from stratgauge.data_sources.base import MarketDataAdapter
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.data_sources.waizao import WaizaoAdapter
from stratgauge.domain.selections import SelectionMaturityTask, SelectionMaturityTaskStatus


def main() -> int:
    arguments = _parse_arguments()
    store = SelectionImportStore(arguments.root)
    tasks = _select_tasks(store, arguments)
    results = []
    failed = False
    for task in tasks:
        try:
            result = run_maturity_task(
                task,
                imported=store.load_import(task.batch_id),
                adapter=_adapter(task.provider),
                selection_store=store,
                market_store=SnapshotStore(arguments.market_root),
                now=datetime.now(UTC),
            )
            results.append(result.model_dump(mode="json"))
            failed = failed or result.status is SelectionMaturityTaskStatus.FAILED
        except (OSError, SelectionStoreError, SelectionMaturityTaskError, ValueError) as exc:
            failed = True
            results.append({"task_id": task.task_id, "error": str(exc)})

    print(json.dumps({"tasks": results}, ensure_ascii=False, indent=2))
    return 2 if failed else 0


def _select_tasks(
    store: SelectionImportStore,
    arguments: argparse.Namespace,
) -> tuple[SelectionMaturityTask, ...]:
    if arguments.task_id:
        return (store.load_maturity_task(arguments.task_id),)
    return store.list_maturity_tasks(
        status=SelectionMaturityTaskStatus.QUEUED,
    )[: arguments.limit]


def _adapter(provider: str) -> MarketDataAdapter:
    adapters: dict[str, Callable[[], MarketDataAdapter]] = {
        "tushare": TushareAdapter,
        "akshare": AKShareAdapter,
        "baostock": BaoStockAdapter,
        "waizao": WaizaoAdapter,
    }
    return adapters[provider]()


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/selection"))
    parser.add_argument("--market-root", type=Path, default=Path("data/selection-market"))
    parser.add_argument("--task-id")
    parser.add_argument("--limit", type=int, default=1)
    arguments = parser.parse_args()
    if arguments.task_id is None and arguments.limit <= 0:
        parser.error("--limit must be positive")
    return arguments


if __name__ == "__main__":
    raise SystemExit(main())
