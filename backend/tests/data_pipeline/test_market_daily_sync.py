import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.data_pipeline.market_daily_sync import sync_market_session
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.universe import build_universe_snapshot
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


def market_row(symbol: str) -> dict[str, str]:
    return {
        "trade_date": "20260105",
        "ts_code": symbol,
        "pre_close": "10.00",
        "open": "10.10",
        "high": "10.50",
        "low": "9.90",
        "close": "10.20",
        "vol": "100",
        "amount": "20",
    }


class MarketDailySyncTests(unittest.TestCase):
    def test_session_is_approved_and_second_run_is_idempotent(self) -> None:
        securities = (
            security("security-1", "000001", Exchange.SZ),
            security("security-2", "600000", Exchange.SH),
        )
        universe = build_universe_snapshot(
            as_of=datetime(2026, 1, 5, 17, tzinfo=UTC),
            source="fixture",
            source_snapshot_ids=("reference-1",),
            securities=securities,
        )
        calls = 0

        def fetch(_: object) -> list[dict[str, str]]:
            nonlocal calls
            calls += 1
            return [market_row("000001.SZ"), market_row("600000.SH")]

        adapter = TushareAdapter(market_fetcher=fetch, clock=lambda: FETCHED_AT)
        with TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            first = sync_market_session(
                adapter=adapter,
                session_date=date(2026, 1, 5),
                universe=universe,
                store=store,
            )
            second = sync_market_session(
                adapter=adapter,
                session_date=date(2026, 1, 5),
                universe=universe,
                store=store,
            )

            self.assertEqual(calls, 1)
            self.assertEqual(first.status, SnapshotStatus.APPROVED)
            self.assertEqual(first.coverage, Decimal("1"))
            self.assertFalse(first.skipped)
            self.assertTrue(second.skipped)
            self.assertEqual(first.snapshot_id, second.snapshot_id)
            manifest = store.load_manifest(first.snapshot_id)
            self.assertEqual(manifest.request_kind, "market_daily")
            self.assertEqual(manifest.universe_snapshot_id, universe.universe_snapshot_id)

    def test_missing_security_is_a_warning_when_coverage_passes(self) -> None:
        securities = (
            security("security-1", "000001", Exchange.SZ),
            security("security-2", "600000", Exchange.SH),
        )
        universe = build_universe_snapshot(
            as_of=datetime(2026, 1, 5, 17, tzinfo=UTC),
            source="fixture",
            source_snapshot_ids=("reference-1",),
            securities=securities,
        )
        adapter = TushareAdapter(
            market_fetcher=lambda _: [market_row("000001.SZ")],
            clock=lambda: FETCHED_AT,
        )
        with TemporaryDirectory() as directory:
            result = sync_market_session(
                adapter=adapter,
                session_date=date(2026, 1, 5),
                universe=universe,
                store=SnapshotStore(directory),
                minimum_security_coverage=Decimal("0.5"),
            )

            self.assertEqual(result.status, SnapshotStatus.APPROVED)
            self.assertEqual(result.coverage, Decimal("0.5"))

    def test_low_security_coverage_rejects_the_snapshot(self) -> None:
        securities = (
            security("security-1", "000001", Exchange.SZ),
            security("security-2", "600000", Exchange.SH),
        )
        universe = build_universe_snapshot(
            as_of=datetime(2026, 1, 5, 17, tzinfo=UTC),
            source="fixture",
            source_snapshot_ids=("reference-1",),
            securities=securities,
        )
        adapter = TushareAdapter(
            market_fetcher=lambda _: [market_row("000001.SZ")],
            clock=lambda: FETCHED_AT,
        )
        with TemporaryDirectory() as directory:
            result = sync_market_session(
                adapter=adapter,
                session_date=date(2026, 1, 5),
                universe=universe,
                store=SnapshotStore(directory),
                minimum_security_coverage=Decimal("0.9"),
            )

            self.assertEqual(result.status, SnapshotStatus.REJECTED)
            self.assertEqual(result.coverage, Decimal("0.5"))


if __name__ == "__main__":
    unittest.main()
