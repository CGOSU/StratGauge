import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from stratgauge.api.analysis import AnalysisRecord
from stratgauge.api.app import create_app
from stratgauge.data_pipeline.store import SnapshotStatus
from stratgauge.domain.models import ActionState


class AnalysisApiTests(unittest.TestCase):
    def setUp(self) -> None:
        record = AnalysisRecord(
            symbol="000001.SZ",
            as_of=date(2026, 1, 5),
            snapshot_status=SnapshotStatus.APPROVED,
            data_freshness=datetime(2026, 1, 5, 8, tzinfo=UTC),
            action_state=ActionState.WAIT_ENTRY,
            source_snapshot_id="snapshot-1",
            feature_set_version="features-v1",
            strategy_version_id="strategy-v1",
            probability=Decimal("0.62"),
            failed_conditions=("entry_trigger_not_reached",),
            reasons=("条件满足，等待入场区间。",),
        )
        self.client = TestClient(create_app({("000001.SZ", date(2026, 1, 5)): record}))

    def test_health_endpoint_reports_service_status(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "stratgauge-api"})

    def test_returns_analysis_with_lineage(self) -> None:
        response = self.client.get("/v1/stocks/000001.SZ/analysis?as_of=2026-01-05")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source_snapshot_id"], "snapshot-1")
        self.assertEqual(body["strategy_version_id"], "strategy-v1")

    def test_missing_analysis_uses_structured_error(self) -> None:
        response = self.client.get("/v1/stocks/000001.SZ/analysis?as_of=2026-01-06")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "ANALYSIS_NOT_FOUND")

    def test_invalid_symbol_uses_structured_error(self) -> None:
        response = self.client.get("/v1/stocks/not-a-symbol/analysis?as_of=2026-01-05")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INVALID_SYMBOL")

    def test_unapproved_snapshot_blocks_analysis(self) -> None:
        record = AnalysisRecord(
            symbol="000001.SZ",
            as_of=date(2026, 1, 5),
            snapshot_status=SnapshotStatus.REJECTED,
            data_freshness=datetime(2026, 1, 5, 8, tzinfo=UTC),
            action_state=ActionState.OBSERVE,
            source_snapshot_id="snapshot-rejected",
            feature_set_version="features-v1",
            strategy_version_id="strategy-v1",
        )
        client = TestClient(create_app({("000001.SZ", date(2026, 1, 5)): record}))
        response = client.get("/v1/stocks/000001.SZ/analysis?as_of=2026-01-05")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "DATA_SNAPSHOT_NOT_APPROVED")


if __name__ == "__main__":
    unittest.main()
