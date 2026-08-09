import unittest
from datetime import UTC, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.backtest.models import BacktestPolicy
from stratgauge.backtest.selection_job import run_selection_outcome_job
from stratgauge.backtest.selection_outcomes import SelectionTrackingPolicy
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.domain.selections import SelectionOutcomeStatus, TdxSelectionImport

CONTENT = "代码,名称,市场\n000001,平安银行,SZ\n".encode()
EXPORTED_AT = datetime(2026, 1, 5, 15, 5, tzinfo=UTC)
IMPORTED_AT = datetime(2026, 1, 5, 15, 10, tzinfo=UTC)
EVALUATED_AT = datetime(2026, 1, 7, 17, tzinfo=UTC)


def imported_selection() -> TdxSelectionImport:
    return import_tdx_export(
        CONTENT,
        selection_date=EXPORTED_AT.date(),
        exported_at=EXPORTED_AT,
        imported_at=IMPORTED_AT,
        formula_name="公式 A",
    )


class SelectionOutcomeJobTests(unittest.TestCase):
    def test_job_persists_quality_safe_outcome_and_market_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            root = TemporaryDirectory()
            self.addCleanup(root.cleanup)
            selection_store = SelectionImportStore(directory)
            selection = imported_selection()
            selection_store.write(CONTENT, selection)
            market_store = SnapshotStore(root.name)
            adapter = AKShareAdapter(
                fetcher=lambda _: [
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
                ],
                clock=lambda: EVALUATED_AT,
            )

            result = run_selection_outcome_job(
                selection,
                adapter=adapter,
                selection_store=selection_store,
                market_store=market_store,
                evaluated_at=EVALUATED_AT,
                policy=SelectionTrackingPolicy(
                    horizon_sessions=1,
                    cost_policy=BacktestPolicy(
                        commission_rate=Decimal("0"),
                        minimum_commission=Decimal("0"),
                        stamp_duty=Decimal("0"),
                    ),
                ),
            )

            self.assertEqual(result.rejected_candidates, ())
            self.assertEqual(result.outcomes[0].status, SelectionOutcomeStatus.WIN)
            self.assertEqual(len(result.market_snapshot_ids), 1)
            self.assertEqual(
                market_store.list_manifests(SnapshotStatus.APPROVED)[0].snapshot_id,
                result.market_snapshot_ids[0],
            )
            self.assertEqual(
                selection_store.list_outcomes(selection.candidates[0].candidate_id),
                result.outcomes,
            )

    def test_provider_failure_rejects_candidate_without_creating_outcome(self) -> None:
        with TemporaryDirectory() as directory, TemporaryDirectory() as market_directory:
            selection_store = SelectionImportStore(directory)
            selection = imported_selection()
            selection_store.write(CONTENT, selection)
            market_store = SnapshotStore(market_directory)
            adapter = AKShareAdapter(
                fetcher=lambda _: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
                clock=lambda: EVALUATED_AT,
            )

            result = run_selection_outcome_job(
                selection,
                adapter=adapter,
                selection_store=selection_store,
                market_store=market_store,
                evaluated_at=EVALUATED_AT,
                policy=SelectionTrackingPolicy(
                    horizon_sessions=1,
                    position_notional=Decimal("10000"),
                ),
            )

            self.assertEqual(len(result.outcomes), 0)
            self.assertEqual(result.rejected_candidates[0][0], selection.candidates[0].candidate_id)
            self.assertEqual(
                selection_store.list_outcomes(selection.candidates[0].candidate_id),
                (),
            )


if __name__ == "__main__":
    unittest.main()
