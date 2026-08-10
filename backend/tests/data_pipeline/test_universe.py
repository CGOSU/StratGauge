import unittest
from datetime import UTC, date, datetime
from tempfile import TemporaryDirectory

from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.universe import (
    UniverseSnapshotStore,
    UniverseSnapshotStoreError,
    build_universe_snapshot,
)
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.base import DailyBarRequest
from stratgauge.domain.models import Exchange, Security


class UniverseSnapshotTests(unittest.TestCase):
    def test_snapshot_is_resolved_only_when_known_at_requested_time(self) -> None:
        with TemporaryDirectory() as directory:
            market_store = _approved_market_store(directory)
            store = UniverseSnapshotStore(directory)
            source_snapshot_id = market_store.list_manifests()[0].snapshot_id
            security = _security("000001", Exchange.SZ)
            snapshot = build_universe_snapshot(
                as_of=datetime(2026, 1, 5, 16, tzinfo=UTC),
                source="fixture-universe",
                source_snapshot_ids=(source_snapshot_id,),
                securities=(security,),
            )
            store.write(snapshot, market_store)
            self.assertEqual(
                store.latest_at(datetime(2026, 1, 6, tzinfo=UTC)),
                snapshot,
            )
            self.assertIsNone(store.latest_at(datetime(2026, 1, 4, tzinfo=UTC)))

    def test_unapproved_source_snapshot_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            market_store = SnapshotStore(directory)
            adapter = AKShareAdapter(
                fetcher=lambda _: [
                    {
                        "日期": "2026-01-05",
                        "开盘": "10",
                        "最高": "10",
                        "最低": "10",
                        "收盘": "10",
                        "成交量": "1",
                        "成交额": "10",
                    }
                ],
                clock=lambda: datetime(2026, 1, 6, 8, tzinfo=UTC),
            )
            request = DailyBarRequest(
                security=_security("000001", Exchange.SZ),
                start_date=date(2026, 1, 5),
                end_date=date(2026, 1, 5),
            )
            manifest = market_store.write(adapter.fetch_daily_bars(request))
            snapshot = build_universe_snapshot(
                as_of=datetime(2026, 1, 5, 16, tzinfo=UTC),
                source="fixture-universe",
                source_snapshot_ids=(manifest.snapshot_id,),
                securities=(_security("000001", Exchange.SZ),),
            )
            with self.assertRaises(UniverseSnapshotStoreError):
                UniverseSnapshotStore(directory).write(snapshot, market_store)


def _approved_market_store(directory: str) -> SnapshotStore:
    store = SnapshotStore(directory)
    adapter = AKShareAdapter(
        fetcher=lambda _: [
            {
                "日期": "2026-01-05",
                "开盘": "10",
                "最高": "10",
                "最低": "10",
                "收盘": "10",
                "成交量": "1",
                "成交额": "10",
            }
        ],
        clock=lambda: datetime(2026, 1, 6, 8, tzinfo=UTC),
    )
    manifest = store.write(
        adapter.fetch_daily_bars(
            DailyBarRequest(
                security=_security("000001", Exchange.SZ),
                start_date=date(2026, 1, 5),
                end_date=date(2026, 1, 5),
            )
        )
    )
    store.update_status(manifest.snapshot_id, SnapshotStatus.APPROVED)
    return store


def _security(local_code: str, exchange: Exchange) -> Security:
    symbol = f"{local_code}.{exchange.value}"
    return Security(
        security_id=f"security-{symbol}",
        local_code=local_code,
        symbol=symbol,
        exchange=exchange,
    )


if __name__ == "__main__":
    unittest.main()
