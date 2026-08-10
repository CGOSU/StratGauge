import unittest
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.backtest.selection_outcomes import SelectionTrackingPolicy
from stratgauge.backtest.selection_tasks import (
    SelectionMaturityTaskError,
    build_maturity_task,
    create_maturity_task,
    maturity_task_id,
    run_maturity_task,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.base import DailyBarRequest, RetryableAdapterError
from stratgauge.domain.selections import (
    SelectionLifecycleStatus,
    SelectionMaturityTaskStatus,
    TdxSelectionImport,
)

CONTENT = "代码,名称,市场\n000001,平安银行,SZ\n".encode()
SELECTION_DATE = datetime(2026, 1, 5, 15, 5, tzinfo=UTC)
EVALUATED_AT = datetime(2026, 1, 7, 17, tzinfo=UTC)


def imported_selection() -> TdxSelectionImport:
    return import_tdx_export(
        CONTENT,
        selection_date=SELECTION_DATE.date(),
        exported_at=SELECTION_DATE,
        imported_at=datetime(2026, 1, 5, 15, 10, tzinfo=UTC),
        formula_name="公式 A",
    )


def bars() -> list[dict[str, str]]:
    return [
        {
            "日期": "2026-01-06",
            "开盘": "10.00",
            "最高": "10.20",
            "最低": "9.80",
            "收盘": "10.10",
            "成交量": "1000",
            "成交额": "10000",
        },
        {
            "日期": "2026-01-07",
            "开盘": "10.20",
            "最高": "11.00",
            "最低": "10.00",
            "收盘": "11.00",
            "成交量": "1000",
            "成交额": "11000",
        },
    ]


class SelectionMaturityTaskTests(unittest.TestCase):
    def test_task_id_normalizes_equivalent_aware_times(self) -> None:
        first = maturity_task_id(
            "batch-1",
            provider="BaoStock",
            evaluated_at=datetime(2026, 1, 7, 17, tzinfo=UTC),
            tracking_policy_id="policy-1",
        )
        second = maturity_task_id(
            "batch-1",
            provider="baostock",
            evaluated_at=datetime(
                2026,
                1,
                8,
                1,
                tzinfo=timezone(timedelta(hours=8)),
            ),
            tracking_policy_id="policy-1",
        )
        self.assertEqual(first, second)

    def test_create_is_idempotent_and_archived_batch_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            imported = imported_selection()
            store.write(CONTENT, imported)
            policy = SelectionTrackingPolicy(horizon_sessions=1, position_notional=Decimal("10000"))
            task = build_maturity_task(
                imported,
                provider="baostock",
                evaluated_at=EVALUATED_AT,
                policy=policy,
                created_at=SELECTION_DATE,
            )
            first = create_maturity_task(store, task, now=SELECTION_DATE)
            second = create_maturity_task(store, task, now=EVALUATED_AT)
            self.assertEqual(first.task_id, second.task_id)
            self.assertEqual(len(second.events), 1)

            store.set_batch_status(
                imported.batch.batch_id,
                status=SelectionLifecycleStatus.ARCHIVED,
                changed_at=EVALUATED_AT,
            )
            with self.assertRaises(SelectionMaturityTaskError):
                create_maturity_task(
                    store,
                    task.model_copy(update={"task_id": "maturity-task-other"}),
                    now=EVALUATED_AT,
                )

    def test_quality_rejection_fails_without_retrying(self) -> None:
        with TemporaryDirectory() as directory, TemporaryDirectory() as market_directory:
            store = SelectionImportStore(directory)
            imported = imported_selection()
            store.write(CONTENT, imported)
            policy = SelectionTrackingPolicy(horizon_sessions=1, position_notional=Decimal("10000"))
            task = build_maturity_task(
                imported,
                provider="akshare",
                evaluated_at=EVALUATED_AT,
                policy=policy,
                max_retries=2,
                created_at=SELECTION_DATE,
            )
            create_maturity_task(store, task, now=SELECTION_DATE)
            duplicate_bars = bars()
            duplicate_bars.append(duplicate_bars[0])
            adapter = AKShareAdapter(
                fetcher=lambda _: duplicate_bars,
                clock=lambda: EVALUATED_AT,
            )
            result = run_maturity_task(
                task,
                imported=imported,
                adapter=adapter,
                selection_store=store,
                market_store=SnapshotStore(market_directory),
                now=EVALUATED_AT,
            )
            self.assertEqual(result.status, SelectionMaturityTaskStatus.FAILED)
            self.assertFalse(result.retryable)
            self.assertEqual(result.attempt_count, 1)
            self.assertEqual(result.outcomes_written, 0)

    def test_retryable_failure_requeues_then_success_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory, TemporaryDirectory() as market_directory:
            store = SelectionImportStore(directory)
            imported = imported_selection()
            store.write(CONTENT, imported)
            policy = SelectionTrackingPolicy(horizon_sessions=1, position_notional=Decimal("10000"))
            task = build_maturity_task(
                imported,
                provider="akshare",
                evaluated_at=EVALUATED_AT,
                policy=policy,
                max_retries=1,
                created_at=SELECTION_DATE,
            )
            create_maturity_task(store, task, now=SELECTION_DATE)
            calls = 0

            def fetch(_: DailyBarRequest) -> list[dict[str, str]]:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RetryableAdapterError("provider timeout")
                return bars()

            adapter = AKShareAdapter(fetcher=fetch, clock=lambda: EVALUATED_AT)
            result = run_maturity_task(
                task,
                imported=imported,
                adapter=adapter,
                selection_store=store,
                market_store=SnapshotStore(market_directory),
                now=EVALUATED_AT,
            )
            self.assertEqual(result.status, SelectionMaturityTaskStatus.QUEUED)
            self.assertTrue(result.retryable)
            self.assertEqual(result.attempt_count, 1)

            result = run_maturity_task(
                result,
                imported=imported,
                adapter=adapter,
                selection_store=store,
                market_store=SnapshotStore(market_directory),
                now=datetime(2026, 1, 8, 17, tzinfo=UTC),
            )
            self.assertEqual(result.status, SelectionMaturityTaskStatus.COMPLETED)
            self.assertEqual(result.outcomes_written, 1)
            self.assertEqual(calls, 2)
            self.assertEqual(len(store.list_maturity_task_outcomes(task.task_id)), 1)
            self.assertGreaterEqual(len(result.events), 5)

            rerun = run_maturity_task(
                result,
                imported=imported,
                adapter=adapter,
                selection_store=store,
                market_store=SnapshotStore(market_directory),
                now=datetime(2026, 1, 9, 17, tzinfo=UTC),
            )
            self.assertEqual(rerun.status, SelectionMaturityTaskStatus.COMPLETED)
            self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
