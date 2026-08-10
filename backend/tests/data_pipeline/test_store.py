import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore, SnapshotStoreError
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.base import DailyBarRequest
from stratgauge.domain.models import Exchange, Security

FETCHED_AT = datetime(2026, 1, 6, 8, tzinfo=UTC)


def request() -> DailyBarRequest:
    return DailyBarRequest(
        security=Security(
            security_id="security-1",
            local_code="000001",
            symbol="000001.SZ",
            exchange=Exchange.SZ,
        ),
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 5),
    )


class SnapshotStoreTests(unittest.TestCase):
    def test_write_is_content_addressed_and_persists_parquet(self) -> None:
        with TemporaryDirectory() as directory:
            adapter = AKShareAdapter(
                fetcher=lambda _: [
                    {
                        "日期": "2026-01-05",
                        "开盘": "10.00",
                        "最高": "10.50",
                        "最低": "9.80",
                        "收盘": "10.20",
                        "成交量": "1000",
                        "成交额": "10200",
                    }
                ],
                clock=lambda: FETCHED_AT,
            )
            result = adapter.fetch_daily_bars(request())
            store = SnapshotStore(directory)
            first = store.write(result)
            second = store.write(result)

            self.assertEqual(first.snapshot_id, second.snapshot_id)
            self.assertEqual(first.status, SnapshotStatus.PENDING)
            normalized = pl.read_parquet(Path(directory) / first.normalized_path)
            self.assertEqual(normalized.height, 1)
            self.assertEqual(normalized.item(0, "close"), Decimal("10.20"))
            self.assertTrue((Path(directory) / first.raw_path).exists())
            self.assertTrue((Path(directory) / "catalog.duckdb").exists())
            self.assertEqual(store.list_manifests()[0].snapshot_id, first.snapshot_id)

    def test_load_bars_requires_approved_snapshot_by_default(self) -> None:
        with TemporaryDirectory() as directory:
            adapter = AKShareAdapter(
                fetcher=lambda _: [
                    {
                        "日期": "2026-01-05",
                        "开盘": "10.00",
                        "最高": "10.50",
                        "最低": "9.80",
                        "收盘": "10.20",
                        "成交量": "1000",
                        "成交额": "10200",
                    }
                ],
                clock=lambda: FETCHED_AT,
            )
            store = SnapshotStore(directory)
            manifest = store.write(adapter.fetch_daily_bars(request()))

            with self.assertRaises(SnapshotStoreError):
                store.load_bars(manifest.snapshot_id)

            store.update_status(manifest.snapshot_id, SnapshotStatus.APPROVED)
            self.assertEqual(len(store.load_bars(manifest.snapshot_id)), 1)

    def test_status_update_preserves_manifest_identity(self) -> None:
        with TemporaryDirectory() as directory:
            adapter = AKShareAdapter(
                fetcher=lambda _: [
                    {
                        "日期": "2026-01-05",
                        "开盘": "10.00",
                        "最高": "10.50",
                        "最低": "9.80",
                        "收盘": "10.20",
                        "成交量": "1000",
                        "成交额": "10200",
                    }
                ],
                clock=lambda: FETCHED_AT,
            )
            store = SnapshotStore(directory)
            manifest = store.write(adapter.fetch_daily_bars(request()))
            updated = store.update_status(
                manifest.snapshot_id, SnapshotStatus.APPROVED, "quality-1"
            )
            self.assertEqual(updated.snapshot_id, manifest.snapshot_id)
            self.assertEqual(updated.content_sha256, manifest.content_sha256)
            self.assertEqual(
                store.load_manifest(manifest.snapshot_id).status, SnapshotStatus.APPROVED
            )
            self.assertEqual(
                store.list_manifests(SnapshotStatus.APPROVED)[0].snapshot_id,
                manifest.snapshot_id,
            )


if __name__ == "__main__":
    unittest.main()
