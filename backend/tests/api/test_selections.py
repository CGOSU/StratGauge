import base64
import json
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from stratgauge.api.app import create_app
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.base import DailyBarRequest
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    SelectionOutcome,
    SelectionOutcomeStatus,
)

CONTENT = "代码,名称,市场,评分\n000001,平安银行,SZ,85\n".encode()


class SelectionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.store = SelectionImportStore(self.directory.name)
        self.client = TestClient(
            create_app(selection_store=self.store, model_root=self.directory.name)
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

    def test_candidate_analysis_returns_evidence_insufficient_without_history(self) -> None:
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

        response = self.client.get(
            f"/v1/selection-batches/{batch_id}/candidates/{candidate_id}/analysis"
        )

        self.assertEqual(response.status_code, 200)
        analysis = response.json()
        self.assertIsNone(analysis["tracking_policy_id"])
        self.assertFalse(analysis["conditional_stats"]["evidence_sufficient"])
        self.assertIsNone(analysis["conditional_stats"]["win_rate"])
        self.assertEqual(analysis["entry_price_stats"]["sample_count"], 0)

    def test_candidate_model_scores_are_empty_without_shadow_runs(self) -> None:
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

        response = self.client.get(
            f"/v1/selection-batches/{batch_id}/candidates/{candidate_id}/model-scores"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scores"], [])
        self.assertEqual(response.json()["failures"], [])

    def test_recommendation_workbench_is_read_only_and_blocks_missing_evidence(self) -> None:
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

        response = self.client.get(f"/v1/selection-batches/{batch_id}/recommendation-workbench")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["automatic_action"])
        self.assertEqual(response.json()["candidates"][0]["state"], "blocked")
        self.assertIn(
            "quality:not_assessed",
            response.json()["candidates"][0]["blocking_reasons"],
        )

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

    def test_management_stats_filter_by_batch_label_and_tracking_policy(self) -> None:
        formula = self.client.post(
            "/v1/formulas",
            json={"name": "统计公式", "version": "v1"},
        ).json()["formula"]
        imported = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_id": formula["formula_id"],
            },
        ).json()
        batch = imported["batch"]
        candidate_id = imported["candidates"][0]["candidate"]["candidate_id"]
        labeled = self.client.post(
            f"/v1/selection-batches/{batch['batch_id']}/labels",
            json={
                "candidate_id": candidate_id,
                "decision": ManualSelectionDecision.WATCH,
                "labeled_at": "2026-01-05T15:10:00+08:00",
            },
        )
        self.assertEqual(labeled.status_code, 200)
        self.store.write_outcome(
            SelectionOutcome(
                candidate_id=candidate_id,
                batch_id=batch["batch_id"],
                selection_date=date(2026, 1, 5),
                status=SelectionOutcomeStatus.WIN,
                evaluated_at=datetime(2026, 1, 12, 17, tzinfo=UTC),
                horizon_sessions=5,
                entry_session=date(2026, 1, 6),
                exit_session=date(2026, 1, 12),
                entry_price=Decimal("10"),
                exit_price=Decimal("11"),
                gross_return=Decimal("0.1"),
                net_return=Decimal("0.09"),
                reason="fixed_horizon",
                tracking_policy_id="policy-v1",
                position_notional=Decimal("10000"),
            )
        )

        response = self.client.get(
            "/v1/data-management/stats",
            params={
                "formula_version": "v1",
                "source": "tongdaxin",
                "selection_date_from": "2026-01-01",
                "selection_date_to": "2026-01-31",
                "manual_decision": "watch",
                "tracking_policy_id": "policy-v1",
            },
        )

        self.assertEqual(response.status_code, 200)
        stats = response.json()
        self.assertEqual(stats["batch_count"], 1)
        self.assertEqual(stats["candidate_count"], 1)
        self.assertEqual(stats["pending"], 0)
        self.assertEqual(stats["sample_count"], 1)
        self.assertEqual(stats["tracking_policy_ids"], ["policy-v1"])
        self.assertEqual(stats["summary"]["wins"], 1)
        self.assertEqual(stats["summary"]["average_net_return"], "0.09")

    def test_management_stats_rejects_reversed_date_filter(self) -> None:
        response = self.client.get(
            "/v1/data-management/stats",
            params={
                "selection_date_from": "2026-02-01",
                "selection_date_to": "2026-01-01",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_SELECTION_STATS_FILTER")

    def test_manual_batch_persists_canonical_raw_evidence_and_is_idempotent(self) -> None:
        formula = self.client.post(
            "/v1/formulas",
            json={"name": "手工公式", "version": "v1"},
        ).json()["formula"]
        request = {
            "selection_date": "2026-01-05",
            "recorded_at": "2026-01-05T15:05:00+08:00",
            "formula_id": formula["formula_id"],
            "candidates": [
                {
                    "local_code": "000001",
                    "exchange": "SZ",
                    "name": "平安银行",
                    "note": "收盘后确认",
                }
            ],
        }

        first = self.client.post("/v1/selection-batches/manual", json=request)
        second = self.client.post("/v1/selection-batches/manual", json=request)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json(), second.json())
        batch = first.json()["batch"]
        self.assertEqual(batch["source"], "manual")
        self.assertEqual(batch["source_filename"], "manual-2026-01-05.json")
        self.assertEqual(first.json()["candidates"][0]["candidate"]["symbol"], "000001.SZ")
        self.assertEqual(
            first.json()["candidates"][0]["candidate"]["source_fields"],
            {"note": "收盘后确认"},
        )

        raw = self.client.get(f"/v1/data-management/batches/{batch['batch_id']}/raw")
        expected = (
            '{"candidates":[{"exchange":"SZ","local_code":"000001",'
            '"name":"平安银行","note":"收盘后确认"}],'
            f'"formula_id":"{formula["formula_id"]}",'
            '"recorded_at":"2026-01-05T15:05:00+08:00",'
            '"selection_date":"2026-01-05"}'
        ).encode()
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.content, expected)

    def test_manual_batch_rejects_duplicate_symbol(self) -> None:
        formula = self.client.post(
            "/v1/formulas",
            json={"name": "重复校验公式", "version": "v1"},
        ).json()["formula"]
        response = self.client.post(
            "/v1/selection-batches/manual",
            json={
                "selection_date": "2026-01-05",
                "recorded_at": "2026-01-05T15:05:00+08:00",
                "formula_id": formula["formula_id"],
                "candidates": [
                    {"local_code": "000001", "exchange": "SZ"},
                    {"local_code": "000001", "exchange": "SZ"},
                ],
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_MANUAL_SELECTION")

    def test_manual_batch_uses_each_explicit_exchange(self) -> None:
        formula = self.client.post(
            "/v1/formulas",
            json={"name": "显式交易所公式", "version": "v1"},
        ).json()["formula"]
        response = self.client.post(
            "/v1/selection-batches/manual",
            json={
                "selection_date": "2026-01-05",
                "recorded_at": "2026-01-05T15:05:00+08:00",
                "formula_id": formula["formula_id"],
                "candidates": [
                    {"local_code": "000001", "exchange": "SH"},
                    {"local_code": "000001", "exchange": "SZ"},
                    {"local_code": "920001", "exchange": "BJ"},
                ],
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            [item["candidate"]["symbol"] for item in response.json()["candidates"]],
            ["000001.SH", "000001.SZ", "920001.BJ"],
        )

    def test_manual_batch_reuses_formula_not_found_and_archived_errors(self) -> None:
        request = {
            "selection_date": "2026-01-05",
            "recorded_at": "2026-01-05T15:05:00+08:00",
            "formula_id": "formula-ffffffffffffffffffff",
            "candidates": [{"local_code": "000001", "exchange": "SZ"}],
        }
        unknown = self.client.post("/v1/selection-batches/manual", json=request)
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json()["error"]["code"], "SELECTION_FORMULA_NOT_FOUND")

        formula = self.client.post(
            "/v1/formulas",
            json={"name": "归档公式", "version": "v1"},
        ).json()["formula"]
        archived = self.client.post(
            f"/v1/data-management/formulas/{formula['formula_id']}/archive",
            json={"reason": "手工测试"},
        )
        self.assertEqual(archived.status_code, 200)
        request["formula_id"] = formula["formula_id"]
        response = self.client.post("/v1/selection-batches/manual", json=request)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "SELECTION_FORMULA_ARCHIVED")

    def test_formula_versions_can_coexist_without_changing_historical_manual_batch(self) -> None:
        v1 = self.client.post(
            "/v1/formulas",
            json={"name": "版本化公式", "version": "v1"},
        ).json()["formula"]
        v2 = self.client.post(
            "/v1/formulas",
            json={"name": "版本化公式", "version": "v2"},
        ).json()["formula"]
        request = {
            "selection_date": "2026-01-05",
            "recorded_at": "2026-01-05T15:05:00+08:00",
            "candidates": [{"local_code": "000001", "exchange": "SZ"}],
        }
        request["formula_id"] = v1["formula_id"]
        first = self.client.post("/v1/selection-batches/manual", json=request)
        request["formula_id"] = v2["formula_id"]
        second = self.client.post("/v1/selection-batches/manual", json=request)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        first_batch = first.json()["batch"]
        second_batch = second.json()["batch"]
        self.assertNotEqual(first_batch["batch_id"], second_batch["batch_id"])
        self.assertEqual(first_batch["formula_id"], v1["formula_id"])
        self.assertEqual(first_batch["formula_version"], "v1")
        self.assertEqual(second_batch["formula_id"], v2["formula_id"])
        self.assertEqual(second_batch["formula_version"], "v2")

        historical = self.client.get(f"/v1/selection-batches/{first_batch['batch_id']}")
        self.assertEqual(historical.status_code, 200)
        self.assertEqual(historical.json()["batch"]["formula_id"], v1["formula_id"])
        self.assertEqual(historical.json()["batch"]["formula_version"], "v1")
        self.assertEqual(len(self.client.get("/v1/formulas").json()), 2)

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

    def test_maturity_task_api_is_idempotent_and_run_is_safe_to_repeat(self) -> None:
        with TemporaryDirectory() as market_directory:
            evaluated_at = datetime(2026, 1, 7, 9, tzinfo=UTC)
            calls = 0

            def fetch(_: DailyBarRequest) -> list[dict[str, str]]:
                nonlocal calls
                calls += 1
                return [
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
                ]

            client = TestClient(
                create_app(
                    selection_store=SelectionImportStore(self.directory.name),
                    market_root=market_directory,
                    market_adapter_factory=lambda _: AKShareAdapter(
                        fetcher=fetch,
                        clock=lambda: evaluated_at,
                    ),
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
            batch_id = imported["batch"]["batch_id"]
            request = {
                "provider": "akshare",
                "evaluated_at": "2026-01-07T17:00:00+08:00",
                "horizon_sessions": 1,
                "position_notional": "10000",
                "max_retries": 1,
                "timeout_seconds": 900,
            }

            first = client.post(f"/v1/selection-batches/{batch_id}/maturity-tasks", json=request)
            second = client.post(f"/v1/selection-batches/{batch_id}/maturity-tasks", json=request)
            self.assertEqual(first.status_code, 201)
            self.assertEqual(second.status_code, 201)
            self.assertEqual(first.json(), second.json())
            task_id = first.json()["task_id"]
            listed = client.get(f"/v1/selection-batches/{batch_id}/maturity-tasks")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(len(listed.json()), 1)

            run = client.post(f"/v1/maturity-tasks/{task_id}/run")
            rerun = client.post(f"/v1/maturity-tasks/{task_id}/run")
            self.assertEqual(run.status_code, 200)
            self.assertEqual(run.json()["status"], "completed")
            self.assertEqual(rerun.status_code, 200)
            self.assertEqual(rerun.json()["status"], "completed")
            self.assertEqual(calls, 1)
            self.assertGreaterEqual(len(rerun.json()["events"]), 3)

    def test_maturity_task_cannot_be_created_for_archived_batch(self) -> None:
        imported = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_name": "公式 A",
            },
        ).json()
        batch_id = imported["batch"]["batch_id"]
        archived = self.client.post(
            f"/v1/data-management/batches/{batch_id}/archive",
            json={"reason": "调度测试"},
        )
        self.assertEqual(archived.status_code, 200)
        response = self.client.post(
            f"/v1/selection-batches/{batch_id}/maturity-tasks",
            json={
                "provider": "baostock",
                "evaluated_at": "2026-01-07T17:00:00+08:00",
                "horizon_sessions": 1,
                "position_notional": "10000",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "SELECTION_BATCH_ARCHIVED")

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
