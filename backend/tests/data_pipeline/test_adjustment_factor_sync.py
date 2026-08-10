import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.data_pipeline.adjustment_factor_sync import (
    AdjustmentFactorBackfillStatus,
    AdjustmentFactorSyncError,
    sync_adjustment_factor_session,
    sync_adjustment_factor_sessions,
)
from stratgauge.data_pipeline.market_daily_sync import sync_market_session
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.universe import build_universe_snapshot
from stratgauge.data_sources.base import MarketAdapterResult, MarketDailyRequest
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.domain.models import Exchange, Security

FETCHED_AT = datetime(2026, 1, 6, 8, tzinfo=UTC)


def security(security_id: str, local_code: str, exchange: Exchange) -> Security:
    return Security(
        security_id=security_id,
        local_code=local_code,
        symbol=f"{local_code}.{exchange.value}",
        exchange=exchange,
    )


class AdjustmentFactorSyncTests(unittest.TestCase):
    def test_batch_backfill_skips_sessions_without_approved_daily_snapshots(self) -> None:
        security_row = security("security-1", "000001", Exchange.SZ)
        universe = build_universe_snapshot(
            as_of=datetime(2026, 1, 4, 17, tzinfo=UTC),
            source="fixture",
            source_snapshot_ids=("reference-1",),
            securities=(security_row,),
        )
        factor_calls = 0

        def fetch_factors(_: object) -> list[dict[str, str]]:
            nonlocal factor_calls
            factor_calls += 1
            return [{"trade_date": "20260105", "ts_code": "000001.SZ", "adj_factor": "2"}]

        adapter = TushareAdapter(
            market_fetcher=lambda request: [_daily_row("000001.SZ", request.session_date)],
            adjustment_factor_fetcher=fetch_factors,
            clock=lambda: FETCHED_AT,
        )
        with TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            sync_market_session(
                adapter=adapter,
                session_date=date(2026, 1, 5),
                universe=universe,
                store=store,
            )

            report = sync_adjustment_factor_sessions(
                adapter=adapter,
                session_dates=(date(2026, 1, 5), date(2026, 1, 6)),
                universe=universe,
                store=store,
            )

            self.assertFalse(report.ready)
            self.assertEqual(factor_calls, 1)
            self.assertEqual(report.missing_daily_sessions, (date(2026, 1, 6),))
            self.assertEqual(
                tuple(item.status for item in report.results),
                (
                    AdjustmentFactorBackfillStatus.APPROVED,
                    AdjustmentFactorBackfillStatus.MISSING_DAILY_SNAPSHOT,
                ),
            )

    def test_batch_backfill_reports_rejected_factor_snapshot(self) -> None:
        security_row = security("security-1", "000001", Exchange.SZ)
        universe = build_universe_snapshot(
            as_of=datetime(2026, 1, 4, 17, tzinfo=UTC),
            source="fixture",
            source_snapshot_ids=("reference-1",),
            securities=(security_row,),
        )
        adapter = TushareAdapter(
            market_fetcher=lambda request: [_daily_row("000001.SZ", request.session_date)],
            adjustment_factor_fetcher=lambda _: [],
            clock=lambda: FETCHED_AT,
        )
        with TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            sync_market_session(
                adapter=adapter,
                session_date=date(2026, 1, 5),
                universe=universe,
                store=store,
            )

            report = sync_adjustment_factor_sessions(
                adapter=adapter,
                session_dates=(date(2026, 1, 5),),
                universe=universe,
                store=store,
            )

            self.assertFalse(report.ready)
            self.assertEqual(
                report.results[0].status,
                AdjustmentFactorBackfillStatus.REJECTED,
            )
            self.assertEqual(len(report.rejected_snapshot_ids), 1)

    def test_factors_require_same_day_approved_market_snapshot_and_are_idempotent(self) -> None:
        securities = (
            security("security-1", "000001", Exchange.SZ),
            security("security-2", "600000", Exchange.SH),
        )
        universe = build_universe_snapshot(
            as_of=datetime(2026, 1, 4, 17, tzinfo=UTC),
            source="fixture",
            source_snapshot_ids=("reference-1",),
            securities=securities,
        )
        adapter = TushareAdapter(
            market_fetcher=lambda _: [
                _daily_row("000001.SZ"),
                _daily_row("600000.SH"),
            ],
            adjustment_factor_fetcher=lambda _: [
                {"trade_date": "20260105", "ts_code": "000001.SZ", "adj_factor": "2"},
                {"trade_date": "20260105", "ts_code": "600000.SH", "adj_factor": "1.5"},
            ],
            clock=lambda: FETCHED_AT,
        )
        with TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            daily = sync_market_session(
                adapter=adapter,
                session_date=date(2026, 1, 5),
                universe=universe,
                store=store,
            )
            first = sync_adjustment_factor_session(
                adapter=adapter,
                session_date=date(2026, 1, 5),
                universe=universe,
                source_daily_snapshot_id=daily.snapshot_id,
                store=store,
            )
            second = sync_adjustment_factor_session(
                adapter=adapter,
                session_date=date(2026, 1, 5),
                universe=universe,
                source_daily_snapshot_id=daily.snapshot_id,
                store=store,
            )

            self.assertEqual(daily.status, SnapshotStatus.APPROVED)
            self.assertEqual(first.status, SnapshotStatus.APPROVED)
            self.assertFalse(first.skipped)
            self.assertTrue(second.skipped)
            self.assertEqual(first.source_daily_snapshot_id, daily.snapshot_id)
            self.assertEqual(
                tuple(factor.factor for factor in store.load_adjustment_factors(first.snapshot_id)),
                (Decimal("2"), Decimal("1.5")),
            )
            manifest = store.load_manifest(first.snapshot_id)
            self.assertEqual(manifest.source_daily_snapshot_id, daily.snapshot_id)

    def test_empty_factor_response_is_rejected_and_stored(self) -> None:
        security_row = security("security-1", "000001", Exchange.SZ)
        universe = build_universe_snapshot(
            as_of=datetime(2026, 1, 4, 17, tzinfo=UTC),
            source="fixture",
            source_snapshot_ids=("reference-1",),
            securities=(security_row,),
        )
        adapter = TushareAdapter(
            market_fetcher=lambda _: [_daily_row("000001.SZ")],
            adjustment_factor_fetcher=lambda _: [],
            clock=lambda: FETCHED_AT,
        )
        with TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            daily = sync_market_session(
                adapter=adapter,
                session_date=date(2026, 1, 5),
                universe=universe,
                store=store,
            )
            result = sync_adjustment_factor_session(
                adapter=adapter,
                session_date=date(2026, 1, 5),
                universe=universe,
                source_daily_snapshot_id=daily.snapshot_id,
                store=store,
            )

            self.assertEqual(result.status, SnapshotStatus.REJECTED)
            self.assertTrue(
                store.load_manifest(result.snapshot_id).status is SnapshotStatus.REJECTED
            )

    def test_pending_daily_snapshot_blocks_factor_sync(self) -> None:
        security_row = security("security-1", "000001", Exchange.SZ)
        universe = build_universe_snapshot(
            as_of=datetime(2026, 1, 4, 17, tzinfo=UTC),
            source="fixture",
            source_snapshot_ids=("reference-1",),
            securities=(security_row,),
        )
        adapter = TushareAdapter(clock=lambda: FETCHED_AT)
        with TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            request = MarketDailyRequest(
                session_date=date(2026, 1, 5),
                universe_snapshot_id=universe.universe_snapshot_id,
                expected_security_ids=(security_row.security_id,),
            )
            pending = store.write(
                MarketAdapterResult(
                    provider="tushare",
                    adapter_version="0.2",
                    request=request,
                    fetched_at=FETCHED_AT,
                    raw_records=(),
                    bars=(),
                )
            )
            with self.assertRaisesRegex(AdjustmentFactorSyncError, "approved same-day"):
                sync_adjustment_factor_session(
                    adapter=adapter,
                    session_date=date(2026, 1, 5),
                    universe=universe,
                    source_daily_snapshot_id=pending.snapshot_id,
                    store=store,
                )


def _daily_row(symbol: str, session_date: date = date(2026, 1, 5)) -> dict[str, str]:
    return {
        "trade_date": session_date.strftime("%Y%m%d"),
        "ts_code": symbol,
        "pre_close": "10.00",
        "open": "10.10",
        "high": "10.50",
        "low": "9.90",
        "close": "10.20",
        "vol": "100",
        "amount": "20",
    }


if __name__ == "__main__":
    unittest.main()
