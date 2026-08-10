import unittest
from datetime import UTC, date, datetime
from tempfile import TemporaryDirectory

from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.universe import (
    SecurityMasterStore,
    UniverseSnapshotStore,
    UniverseSnapshotStoreError,
    build_security_master_snapshot,
    build_universe_snapshot,
    securities_at,
)
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.base import DailyBarRequest
from stratgauge.domain.models import Exchange, Security, SecurityMasterRecord


class UniverseSnapshotTests(unittest.TestCase):
    def test_snapshot_is_resolved_only_when_known_at_requested_time(self) -> None:
        with TemporaryDirectory() as directory:
            market_store = _approved_market_store(directory)
            store = UniverseSnapshotStore(directory)
            source_snapshot_id = market_store.list_manifests()[0].snapshot_id
            security = _security("000001", Exchange.SZ)
            snapshot = build_universe_snapshot(
                as_of=datetime(2026, 1, 5, 16, tzinfo=UTC),
                membership_date=date(2026, 1, 5),
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

    def test_securities_at_excludes_not_yet_listed_and_delisted_members(self) -> None:
        securities = (
            Security(
                security_id="security-before",
                local_code="000001",
                symbol="000001.SZ",
                exchange=Exchange.SZ,
                listed_at=date(2026, 1, 5),
            ),
            Security(
                security_id="security-active",
                local_code="600000",
                symbol="600000.SH",
                exchange=Exchange.SH,
                listed_at=date(2020, 1, 1),
                delisted_at=date(2026, 1, 10),
            ),
        )

        before = securities_at(securities, as_of=date(2026, 1, 4))
        on_listing = securities_at(securities, as_of=date(2026, 1, 5))
        after_delisting = securities_at(securities, as_of=date(2026, 1, 10))

        self.assertEqual(tuple(item.symbol for item in before), ("600000.SH",))
        self.assertEqual(tuple(item.symbol for item in on_listing), ("000001.SZ", "600000.SH"))
        self.assertEqual(tuple(item.symbol for item in after_delisting), ("000001.SZ",))

    def test_security_master_is_resolved_by_known_at_not_effective_date(self) -> None:
        with TemporaryDirectory() as directory:
            reference_store = _approved_market_store(directory)
            source_snapshot_id = reference_store.list_manifests()[0].snapshot_id
            client_records = (_master_record("000001", Exchange.SZ, listed_at=date(2020, 1, 1)),)
            snapshot = build_security_master_snapshot(
                observed_at=datetime(2026, 1, 6, 8, tzinfo=UTC),
                source_snapshot_id=source_snapshot_id,
                records=client_records,
            )
            store = SecurityMasterStore(directory)
            store.write(snapshot, reference_store)

            self.assertIsNone(store.latest_at(datetime(2026, 1, 5, tzinfo=UTC)))
            known_snapshot = store.latest_at(datetime(2026, 1, 6, 9, tzinfo=UTC))
            self.assertEqual(known_snapshot, snapshot)
            assert known_snapshot is not None
            self.assertEqual(
                tuple(record.symbol for record in known_snapshot.records),
                ("000001.SZ",),
            )

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


def _master_record(
    local_code: str,
    exchange: Exchange,
    *,
    listed_at: date | None = None,
) -> SecurityMasterRecord:
    return SecurityMasterRecord(
        security_id=f"security-{local_code}.{exchange.value}",
        local_code=local_code,
        symbol=f"{local_code}.{exchange.value}",
        exchange=exchange,
        name="测试证券",
        market="主板",
        list_status="L",
        listed_at=listed_at,
    )


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
