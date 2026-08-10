import base64
import json
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

    def test_formula_catalog_creates_and_drives_import_metadata(self) -> None:
        created = self.client.post(
            "/v1/formulas",
            json={
                "name": "趋势选股",
                "version": "v3",
                "description": "收盘后趋势筛选",
            },
        )
        self.assertEqual(created.status_code, 201)
        formula = created.json()["formula"]

        listed = self.client.get("/v1/formulas")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["formula_id"], formula["formula_id"])

        imported = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_id": formula["formula_id"],
            },
        )
        self.assertEqual(imported.status_code, 201)
        self.assertEqual(imported.json()["batch"]["formula_id"], formula["formula_id"])
        self.assertEqual(imported.json()["batch"]["formula_name"], "趋势选股")

    def test_data_management_archives_and_restores_formulas_and_batches(self) -> None:
        formula = self.client.post(
            "/v1/formulas",
            json={"name": "管理公式", "version": "v1"},
        ).json()["formula"]
        imported = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "source_filename": "selection.txt",
                "formula_id": formula["formula_id"],
            },
        ).json()
        batch_id = imported["batch"]["batch_id"]

        formulas = self.client.get("/v1/data-management/formulas")
        batches = self.client.get("/v1/data-management/batches")
        self.assertTrue(formulas.json()[0]["active"])
        self.assertEqual(batches.json()[0]["batch"]["source_filename"], "selection.txt")
        self.assertEqual(batches.json()[0]["label_count"], 0)

        archived_formula = self.client.post(
            f"/v1/data-management/formulas/{formula['formula_id']}/archive",
            json={"reason": "停用旧版本"},
        )
        self.assertEqual(archived_formula.status_code, 200)
        self.assertFalse(archived_formula.json()["active"])
        self.assertEqual(self.client.get("/v1/formulas").json(), [])

        rejected_import = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-06",
                "exported_at": "2026-01-06T15:05:00+08:00",
                "formula_id": formula["formula_id"],
            },
        )
        self.assertEqual(rejected_import.status_code, 422)
        self.assertEqual(rejected_import.json()["error"]["code"], "SELECTION_FORMULA_ARCHIVED")

        archived_batch = self.client.post(
            f"/v1/data-management/batches/{batch_id}/archive",
            json={"reason": "归档测试"},
        )
        self.assertEqual(archived_batch.status_code, 200)
        self.assertFalse(archived_batch.json()["active"])
        self.assertEqual(self.client.get("/v1/selection-batches").json(), [])

        raw = self.client.get(f"/v1/data-management/batches/{batch_id}/raw")
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.content, CONTENT)
        self.assertIn("selection.txt", raw.headers["content-disposition"])

        dataset = self.client.get(f"/v1/data-management/batches/{batch_id}/dataset")
        self.assertEqual(dataset.status_code, 200)
        self.assertIn("application/x-ndjson", dataset.headers["content-type"])
        self.assertIn(f"selection-{batch_id}.jsonl", dataset.headers["content-disposition"])
        self.assertEqual(
            json.loads(dataset.text.splitlines()[0])["source_filename"], "selection.txt"
        )

        restored_batch = self.client.post(
            f"/v1/data-management/batches/{batch_id}/restore",
            json={},
        )
        self.assertTrue(restored_batch.json()["active"])
        restored_formula = self.client.post(
            f"/v1/data-management/formulas/{formula['formula_id']}/restore",
            json={},
        )
        self.assertTrue(restored_formula.json()["active"])

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
