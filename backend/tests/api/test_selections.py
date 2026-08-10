import base64
import unittest
from datetime import UTC, datetime
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from stratgauge.api.app import create_app
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_sources.akshare import AKShareAdapter

CONTENT = "代码,名称,市场,评分\n000001,平安银行,SZ,85\n".encode()


class SelectionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.client = TestClient(
            create_app(selection_store=SelectionImportStore(self.directory.name))
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_import_list_view_and_label_candidate(self) -> None:
        imported = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_name": "趋势选股",
            },
        )

        self.assertEqual(imported.status_code, 201)
        batch = imported.json()
        batch_id = batch["batch"]["batch_id"]
        candidate_id = batch["candidates"][0]["candidate"]["candidate_id"]
        self.assertEqual(batch["candidates"][0]["labels"], [])

        listed = self.client.get("/v1/selection-batches")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["batch_id"], batch_id)

        labeled = self.client.post(
            f"/v1/selection-batches/{batch_id}/labels",
            json={
                "candidate_id": candidate_id,
                "decision": "watch",
                "labeled_at": "2026-01-05T15:10:00+08:00",
                "note": "等待确认",
            },
        )
        self.assertEqual(labeled.status_code, 200)
        self.assertEqual(labeled.json()["candidates"][0]["labels"][0]["decision"], "watch")

        fetched = self.client.get(f"/v1/selection-batches/{batch_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["batch"]["candidate_count"], 1)

        stats = self.client.get(f"/v1/selection-batches/{batch_id}/stats")
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json()["pending"], 1)
        self.assertEqual(stats.json()["summary"]["total"], 0)

    def test_mature_endpoint_runs_injected_provider_and_refreshes_results(self) -> None:
        with TemporaryDirectory() as market_directory:
            evaluated_at = datetime(2026, 1, 7, 9, tzinfo=UTC)
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
                clock=lambda: evaluated_at,
            )
            client = TestClient(
                create_app(
                    selection_store=SelectionImportStore(self.directory.name),
                    market_root=market_directory,
                    market_adapter_factory=lambda _: adapter,
                )
            )
            imported = client.post(
                "/v1/selection-batches",
                json={
                    "content_base64": base64.b64encode(CONTENT).decode(),
                    "selection_date": "2026-01-05",
                    "exported_at": "2026-01-05T15:05:00+08:00",
                    "formula_name": "公式 A",
                },
            ).json()

            response = client.post(
                f"/v1/selection-batches/{imported['batch']['batch_id']}/mature",
                json={
                    "provider": "akshare",
                    "evaluated_at": "2026-01-07T17:00:00+08:00",
                    "horizon_sessions": 1,
                    "position_notional": "10000",
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["outcomes_written"], 1)
            stats = client.get(f"/v1/selection-batches/{imported['batch']['batch_id']}/stats")
            self.assertEqual(stats.json()["summary"]["completed"], 1)

    def test_invalid_file_returns_structured_error(self) -> None:
        response = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": "not-base64",
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_name": "公式 A",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_SELECTION_FILE")

    def test_unknown_batch_returns_not_found(self) -> None:
        response = self.client.get("/v1/selection-batches/missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "SELECTION_BATCH_NOT_FOUND")

    def test_label_after_freeze_deadline_returns_structured_error(self) -> None:
        imported = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_name": "公式 A",
            },
        ).json()
        response = self.client.post(
            f"/v1/selection-batches/{imported['batch']['batch_id']}/labels",
            json={
                "candidate_id": imported["candidates"][0]["candidate"]["candidate_id"],
                "decision": "watch",
                "labeled_at": "2026-01-06T09:00:00+08:00",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "SELECTION_LABEL_NOT_ALLOWED")

    def test_mature_requires_aware_evaluation_time(self) -> None:
        response = self.client.post(
            "/v1/selection-batches/batch-1/mature",
            json={
                "provider": "baostock",
                "evaluated_at": "2026-01-10T17:00:00",
                "horizon_sessions": 5,
                "position_notional": "10000",
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_naive_label_timestamp_is_rejected_by_request_model(self) -> None:
        response = self.client.post(
            "/v1/selection-batches/batch-1/labels",
            json={
                "candidate_id": "candidate-1",
                "decision": "pass",
                "labeled_at": "2026-01-05T15:10:00",
            },
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
