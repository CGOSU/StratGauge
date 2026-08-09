import unittest
from datetime import UTC, date, datetime
from tempfile import TemporaryDirectory

from stratgauge.data_pipeline.ingest import ingest
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
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


def adapter_for(session: str = "2026-01-05") -> AKShareAdapter:
    return AKShareAdapter(
        fetcher=lambda _: [
            {
                "日期": session,
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


class IngestTests(unittest.TestCase):
    def test_quality_gate_approves_complete_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            result = ingest(
                adapter_for(),
                request(),
                SnapshotStore(directory),
                expected_sessions=[date(2026, 1, 5)],
                expected_security_ids=["security-1"],
            )
            self.assertTrue(result.quality.approved)
            self.assertEqual(result.manifest.status, SnapshotStatus.APPROVED)
            self.assertIsNotNone(result.manifest.quality_run_id)

    def test_quality_gate_rejects_missing_session(self) -> None:
        with TemporaryDirectory() as directory:
            result = ingest(
                adapter_for(),
                request(),
                SnapshotStore(directory),
                expected_sessions=[date(2026, 1, 5), date(2026, 1, 6)],
            )
            self.assertFalse(result.quality.approved)
            self.assertEqual(result.manifest.status, SnapshotStatus.REJECTED)


if __name__ == "__main__":
    unittest.main()
