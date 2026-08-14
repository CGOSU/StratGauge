import unittest
from datetime import UTC, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.application.job_runner import JobStatus
from stratgauge.application.offline_pipeline import run_offline_maturity_pipeline
from stratgauge.backtest.selection_outcomes import SelectionTrackingPolicy
from stratgauge.data_pipeline.market_daily_sync import sync_market_session
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.data_pipeline.universe import build_universe_snapshot
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.domain.models import Exchange, Security

CONTENT = "代码,名称,市场\n000001,平安银行,SZ\n".encode()
SELECTION_DATE = datetime(2026, 1, 5, 15, 5, tzinfo=UTC)


class OfflinePipelineTests(unittest.TestCase):
    def test_queue_run_and_verify_never_leave_local_snapshots(self) -> None:
        with TemporaryDirectory() as directory, TemporaryDirectory() as market_directory:
            selection_store = SelectionImportStore(directory)
            imported = import_tdx_export(
                CONTENT,
                selection_date=SELECTION_DATE.date(),
                exported_at=SELECTION_DATE,
                imported_at=datetime(2026, 1, 5, 15, 10, tzinfo=UTC),
                formula_name="公式 A",
            )
            selection_store.write(CONTENT, imported)
            security = Security(
                security_id="security-1",
                local_code="000001",
                symbol="000001.SZ",
                exchange=Exchange.SZ,
            )
            universe = build_universe_snapshot(
                as_of=datetime(2026, 1, 4, 17, tzinfo=UTC),
                source="fixture",
                source_snapshot_ids=("reference-1",),
                securities=(security,),
            )
            market_store = SnapshotStore(market_directory)
            adapter = TushareAdapter(
                market_fetcher=lambda request: [
                    {
                        "trade_date": request.session_date.strftime("%Y%m%d"),
                        "ts_code": "000001.SZ",
                        "pre_close": "10.00",
                        "open": "10.00",
                        "high": "11.00",
                        "low": "9.90",
                        "close": "11.00",
                        "vol": "100",
                        "amount": "10",
                    }
                ],
                clock=lambda: datetime(2026, 1, 8, 17, tzinfo=UTC),
            )
            for session in (datetime(2026, 1, 6).date(), datetime(2026, 1, 7).date()):
                sync_result = sync_market_session(
                    adapter=adapter,
                    session_date=session,
                    universe=universe,
                    store=market_store,
                )
                self.assertEqual(sync_result.status, "approved")

            result = run_offline_maturity_pipeline(
                directory,
                market_directory,
                provider="tushare",
                policy=SelectionTrackingPolicy(
                    horizon_sessions=1,
                    position_notional=Decimal("10000"),
                ),
                trading_sessions=(
                    datetime(2026, 1, 6).date(),
                    datetime(2026, 1, 7).date(),
                ),
                now=datetime(2026, 1, 8, 18, tzinfo=UTC),
            )

            self.assertEqual(len(result.queue.created_tasks), 1)
            self.assertEqual(len(result.tasks), 1)
            self.assertEqual(result.tasks[0].status.value, "completed")
            self.assertEqual(result.jobs[0].status, JobStatus.COMPLETED)
            self.assertEqual(result.tasks[0].outcomes_written, 1)
            self.assertEqual(result.verification.missing_files, ())
            self.assertEqual(result.verification.catalog_orphans, ())


if __name__ == "__main__":
    unittest.main()
