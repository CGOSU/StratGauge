import base64
import json
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from stratgauge.api.app import create_app
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.base import DailyBarRequest
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    SelectionOutcome,
    SelectionOutcomeStatus,
    SelectionReusableTagCategory,
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

    def test_batch_reads_use_bulk_candidate_queries(self) -> None:
        formula = self.client.post(
            "/v1/formulas",
            json={"name": "批量读取公式", "version": "v1"},
        ).json()["formula"]
        imported = self.client.post(
            "/v1/selection-batches/manual",
            json={
                "selection_date": "2026-01-05",
                "recorded_at": "2026-01-05T15:05:00+08:00",
                "formula_id": formula["formula_id"],
                "candidates": [
                    {"local_code": "000001", "exchange": "SZ"},
                    {"local_code": "000002", "exchange": "SZ"},
                ],
            },
        )
        self.assertEqual(imported.status_code, 201)
        batch_id = imported.json()["batch"]["batch_id"]

        with (
            patch.object(self.store, "list_labels", side_effect=AssertionError("label N+1 scan")),
            patch.object(
                self.store,
                "list_annotations",
                side_effect=AssertionError("annotation N+1 scan"),
            ),
            patch.object(
                self.store,
                "list_exclusions",
                side_effect=AssertionError("exclusion N+1 scan"),
            ),
            patch.object(
                self.store, "list_outcomes", side_effect=AssertionError("outcome N+1 scan")
            ),
            patch.object(
                self.store,
                "list_performances",
                side_effect=AssertionError("performance N+1 scan"),
            ),
            patch.object(
                self.store,
                "list_lifecycle_events",
                side_effect=AssertionError("lifecycle N+1 scan"),
            ),
        ):
            fetched = self.client.get(f"/v1/selection-batches/{batch_id}")
            managed = self.client.get("/v1/data-management/batches")
            stats = self.client.get("/v1/data-management/stats")

        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(len(fetched.json()["candidates"]), 2)
        self.assertEqual(managed.status_code, 200)
        self.assertEqual(managed.json()[0]["batch"]["batch_id"], batch_id)
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json()["candidate_count"], 2)

    def test_paged_lists_and_streaming_dataset_exports_preserve_legacy_contracts(self) -> None:
        first = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_name": "分页公式",
            },
        ).json()
        second = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-06",
                "exported_at": "2026-01-06T15:05:00+08:00",
                "formula_name": "分页公式",
            },
        ).json()

        page = self.client.get(
            "/v1/selection-batches/page",
            params={"page": 1, "page_size": 1},
        )
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["total"], 2)
        self.assertTrue(page.json()["has_next"])
        self.assertEqual(page.json()["items"][0]["batch_id"], second["batch"]["batch_id"])
        invalid_page = self.client.get(
            "/v1/selection-batches/page",
            params={"page": 1, "page_size": 201},
        )
        self.assertEqual(invalid_page.status_code, 422)

        dataset = self.client.get(
            f"/v1/data-management/batches/{second['batch']['batch_id']}/dataset"
        )
        self.assertEqual(dataset.status_code, 200)
        self.assertIn("application/x-ndjson", dataset.headers["content-type"])
        self.assertEqual(
            json.loads(dataset.content.decode().splitlines()[0])["batch_id"],
            second["batch"]["batch_id"],
        )
        self.assertIn("filename=selection-", dataset.headers["content-disposition"])
        app = cast(Any, self.client.app)
        stream_route = next(
            route
            for route in app.routes
            if getattr(route, "path", None) == "/v1/data-management/batches/{batch_id}/dataset"
        )
        stream_response = stream_route.endpoint(second["batch"]["batch_id"])
        self.assertIsInstance(stream_response, StreamingResponse)
        self.assertEqual(stream_response.media_type, "application/x-ndjson")
        self.assertNotEqual(first["batch"]["batch_id"], second["batch"]["batch_id"])

    def test_maturity_status_page_covers_all_states_and_latest_policies(self) -> None:
        formula = self.client.post(
            "/v1/formulas",
            json={"name": "成熟状态公式", "version": "v1"},
        ).json()["formula"]
        imported = self.client.post(
            "/v1/selection-batches/manual",
            json={
                "selection_date": "2026-01-05",
                "recorded_at": "2026-01-05T15:05:00+08:00",
                "formula_id": formula["formula_id"],
                "candidates": [
                    {"local_code": "000001", "exchange": "SZ", "name": "候选一"},
                    {"local_code": "000002", "exchange": "SH", "name": "候选二"},
                    {"local_code": "000003", "exchange": "SZ", "name": "候选三"},
                    {"local_code": "000004", "exchange": "SH", "name": "候选四"},
                    {"local_code": "000005", "exchange": "SZ", "name": "候选五"},
                ],
            },
        )
        self.assertEqual(imported.status_code, 201)
        batch = imported.json()["batch"]
        candidates = {
            item["candidate"]["local_code"]: item["candidate"]
            for item in imported.json()["candidates"]
        }

        def write_outcome(
            local_code: str,
            status: SelectionOutcomeStatus,
            tracking_policy_id: str,
            evaluated_at: datetime,
            net_return: str | None = None,
        ) -> None:
            is_trade = status in {
                SelectionOutcomeStatus.WIN,
                SelectionOutcomeStatus.LOSS,
                SelectionOutcomeStatus.FLAT,
            }
            exit_price = None
            gross_return = None
            if status is SelectionOutcomeStatus.WIN:
                exit_price = Decimal("11")
                gross_return = Decimal("0.1")
            elif status is SelectionOutcomeStatus.LOSS:
                exit_price = Decimal("9")
                gross_return = Decimal("-0.1")
            elif is_trade:
                exit_price = Decimal("10")
                gross_return = Decimal("0")
            self.store.write_outcome(
                SelectionOutcome(
                    candidate_id=candidates[local_code]["candidate_id"],
                    batch_id=batch["batch_id"],
                    selection_date=date(2026, 1, 5),
                    status=status,
                    evaluated_at=evaluated_at,
                    horizon_sessions=10,
                    entry_session=date(2026, 1, 6) if is_trade else None,
                    exit_session=date(2026, 1, 12) if is_trade else None,
                    entry_price=Decimal("10") if is_trade else None,
                    exit_price=exit_price,
                    gross_return=gross_return,
                    net_return=Decimal(net_return) if net_return is not None else None,
                    reason="fixed_horizon" if is_trade else status.value,
                    tracking_policy_id=tracking_policy_id,
                    outcome_policy_id="outcome-v1",
                )
            )

        write_outcome(
            "000001",
            SelectionOutcomeStatus.LOSS,
            "policy-a",
            datetime(2026, 1, 12, 17, tzinfo=UTC),
            "-0.11",
        )
        write_outcome(
            "000001",
            SelectionOutcomeStatus.WIN,
            "policy-a",
            datetime(2026, 1, 13, 17, tzinfo=UTC),
            "0.09",
        )
        write_outcome(
            "000001",
            SelectionOutcomeStatus.LOSS,
            "policy-b",
            datetime(2026, 1, 12, 17, tzinfo=UTC),
            "-0.11",
        )
        write_outcome(
            "000002",
            SelectionOutcomeStatus.FLAT,
            "policy-a",
            datetime(2026, 1, 12, 17, tzinfo=UTC),
            "0",
        )
        write_outcome(
            "000003",
            SelectionOutcomeStatus.NO_ENTRY,
            "policy-a",
            datetime(2026, 1, 12, 17, tzinfo=UTC),
        )
        write_outcome(
            "000004",
            SelectionOutcomeStatus.UNRESOLVED,
            "policy-a",
            datetime(2026, 1, 12, 17, tzinfo=UTC),
        )

        excluded = self.client.post(
            f"/v1/selection-batches/{batch['batch_id']}/exclusions",
            json={
                "candidate_id": candidates["000005"]["candidate_id"],
                "excluded_at": "2026-01-06T09:00:00+08:00",
                "reason": "测试排除",
            },
        )
        self.assertEqual(excluded.status_code, 200)
        archived = self.client.post(
            f"/v1/data-management/batches/{batch['batch_id']}/archive",
            json={"reason": "测试归档"},
        )
        self.assertEqual(archived.status_code, 200)

        page = self.client.get(
            "/v1/selection-maturity/page",
            params={"page": 1, "page_size": 2},
        )
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["total"], 6)
        self.assertTrue(page.json()["has_next"])
        self.assertEqual(
            page.json()["status_counts"],
            {"flat": 1, "loss": 1, "no_entry": 1, "pending": 1, "unresolved": 1, "win": 1},
        )
        readiness = self.client.get("/v1/ml/readiness")
        self.assertEqual(readiness.status_code, 200)
        self.assertEqual(readiness.json()["outcome_counts"], page.json()["status_counts"])
        all_items = self.client.get(
            "/v1/selection-maturity/page",
            params={"page": 1, "page_size": 200},
        ).json()["items"]
        self.assertEqual(len(all_items), 6)
        self.assertTrue(all(not item["active"] for item in all_items))
        excluded_items = [item for item in all_items if item["excluded"]]
        self.assertEqual(len(excluded_items), 1)
        self.assertEqual(excluded_items[0]["status"], "pending")
        self.assertEqual(excluded_items[0]["latest_exclusion"]["reason"], "测试排除")
        self.assertEqual(
            sorted(
                item["outcome"]["tracking_policy_id"]
                for item in all_items
                if item["outcome"] is not None and item["candidate"]["local_code"] == "000001"
            ),
            ["policy-a", "policy-b"],
        )
        policy_a = next(
            item
            for item in all_items
            if item["candidate"]["local_code"] == "000001"
            and item["outcome"]["tracking_policy_id"] == "policy-a"
        )
        self.assertEqual(policy_a["status"], "win")

        for status in ("pending", "win", "loss", "flat", "no_entry", "unresolved"):
            filtered = self.client.get(
                "/v1/selection-maturity/page",
                params={"status": status, "page_size": 200},
            )
            self.assertEqual(filtered.status_code, 200)
            self.assertEqual(filtered.json()["total"], 1)
            self.assertEqual(filtered.json()["items"][0]["status"], status)

        second_page = self.client.get(
            "/v1/selection-maturity/page",
            params={"page": 2, "page_size": 2},
        )
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(len(second_page.json()["items"]), 2)
        self.assertEqual(
            {item["candidate"]["candidate_id"] for item in page.json()["items"]}.isdisjoint(
                item["candidate"]["candidate_id"] for item in second_page.json()["items"]
            ),
            True,
        )
        self.assertEqual(
            self.client.get(
                "/v1/selection-maturity/page",
                params={"status": "invalid"},
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(
                "/v1/selection-maturity/page",
                params={"page_size": 201},
            ).status_code,
            422,
        )

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

    def test_reusable_annotation_tags_can_be_created_listed_and_lifecycle_managed(self) -> None:
        created = self.client.post(
            "/v1/annotation-tags",
            json={"category": "pattern", "label": "缩量企稳"},
        )
        self.assertEqual(created.status_code, 201)
        tag = created.json()
        self.assertEqual(tag["category"], SelectionReusableTagCategory.PATTERN.value)
        self.assertTrue(tag["active"])

        listed = self.client.get("/v1/annotation-tags")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([item["tag_id"] for item in listed.json()], [tag["tag_id"]])

        managed = self.client.get("/v1/data-management/annotation-tags")
        self.assertEqual(managed.status_code, 200)
        self.assertEqual(managed.json()[0]["label"], "缩量企稳")

        renamed = self.client.post(
            f"/v1/data-management/annotation-tags/{tag['tag_id']}/rename",
            json={"label": "缩量企稳确认"},
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["label"], "缩量企稳确认")

        archived = self.client.post(
            f"/v1/data-management/annotation-tags/{tag['tag_id']}/archive",
            json={"reason": "暂不使用"},
        )
        self.assertEqual(archived.status_code, 200)
        self.assertFalse(archived.json()["active"])
        self.assertEqual(self.client.get("/v1/annotation-tags").json(), [])

        restored = self.client.post(
            f"/v1/data-management/annotation-tags/{tag['tag_id']}/restore",
            json={},
        )
        self.assertEqual(restored.status_code, 200)
        self.assertTrue(restored.json()["active"])

    def test_reusable_annotation_tag_ids_are_resolved_to_server_snapshots(self) -> None:
        tag = self.client.post(
            "/v1/annotation-tags",
            json={"category": "position", "label": "低位支撑"},
        ).json()
        imported = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_name": "趋势选股",
            },
        ).json()
        batch_id = imported["batch"]["batch_id"]
        candidate_id = imported["candidates"][0]["candidate"]["candidate_id"]

        annotated = self.client.post(
            f"/v1/selection-batches/{batch_id}/annotations",
            json={
                "candidate_id": candidate_id,
                "reviewed_at": "2026-01-05T15:10:00+08:00",
                "overall_score": 4,
                "outlook": "bullish",
                "decision": "watch",
                "reusable_tag_ids": [tag["tag_id"]],
            },
        )
        self.assertEqual(annotated.status_code, 200)
        snapshot = annotated.json()["candidates"][0]["annotations"][0]["reusable_tag_snapshots"][0]
        self.assertEqual(
            snapshot,
            {
                "tag_id": tag["tag_id"],
                "category": "position",
                "label": "低位支撑",
            },
        )

        renamed = self.client.post(
            f"/v1/data-management/annotation-tags/{tag['tag_id']}/rename",
            json={"label": "低位支撑确认"},
        )
        self.assertEqual(renamed.status_code, 200)
        fetched = self.client.get(f"/v1/selection-batches/{batch_id}")
        self.assertEqual(
            fetched.json()["candidates"][0]["annotations"][0]["reusable_tag_snapshots"][0]["label"],
            "低位支撑",
        )

    def test_reusable_annotation_tag_errors_are_structured_and_client_cannot_submit_snapshot(
        self,
    ) -> None:
        imported = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_name": "趋势选股",
            },
        ).json()
        batch_id = imported["batch"]["batch_id"]
        candidate_id = imported["candidates"][0]["candidate"]["candidate_id"]
        unknown = self.client.post(
            f"/v1/selection-batches/{batch_id}/annotations",
            json={
                "candidate_id": candidate_id,
                "reviewed_at": "2026-01-05T15:10:00+08:00",
                "overall_score": 3,
                "outlook": "neutral",
                "decision": "watch",
                "reusable_tag_ids": ["annotation-tag-0123456789abcdef0123"],
            },
        )
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json()["error"]["code"], "ANNOTATION_TAG_NOT_FOUND")

        tag = self.client.post(
            "/v1/annotation-tags",
            json={"category": "sector", "label": "行业强"},
        ).json()
        archived = self.client.post(
            f"/v1/data-management/annotation-tags/{tag['tag_id']}/archive",
            json={},
        )
        self.assertEqual(archived.status_code, 200)
        archived_use = self.client.post(
            f"/v1/selection-batches/{batch_id}/annotations",
            json={
                "candidate_id": candidate_id,
                "reviewed_at": "2026-01-05T15:10:00+08:00",
                "overall_score": 3,
                "outlook": "neutral",
                "decision": "watch",
                "reusable_tag_ids": [tag["tag_id"]],
            },
        )
        self.assertEqual(archived_use.status_code, 422)
        self.assertEqual(archived_use.json()["error"]["code"], "ANNOTATION_TAG_ARCHIVED")

        snapshot_payload = self.client.post(
            f"/v1/selection-batches/{batch_id}/annotations",
            json={
                "candidate_id": candidate_id,
                "reviewed_at": "2026-01-05T15:10:00+08:00",
                "overall_score": 3,
                "outlook": "neutral",
                "decision": "watch",
                "reusable_tag_snapshots": [],
            },
        )
        self.assertEqual(snapshot_payload.status_code, 422)

    def test_annotation_api_appends_reviews_and_projects_observation_state(self) -> None:
        imported = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_name": "趋势选股",
            },
        ).json()
        batch_id = imported["batch"]["batch_id"]
        candidate_id = imported["candidates"][0]["candidate"]["candidate_id"]
        first = self.client.post(
            f"/v1/selection-batches/{batch_id}/annotations",
            json={
                "candidate_id": candidate_id,
                "reviewed_at": "2026-01-05T15:10:00+08:00",
                "overall_score": 3,
                "outlook": "neutral",
                "decision": "watch",
                "position_tags": ["support"],
                "pattern_tags": ["pullback"],
                "price_volume_tags": ["price_up_volume_down"],
                "sector_tags": ["sector_strong"],
                "note": "等待次日确认",
            },
        )
        self.assertEqual(first.status_code, 200)
        first_candidate = first.json()["candidates"][0]
        self.assertEqual(first_candidate["review_state"], "watching")
        self.assertEqual(first_candidate["latest_annotation"]["overall_score"], 3)
        self.assertEqual(len(first_candidate["annotations"]), 1)

        second = self.client.post(
            f"/v1/selection-batches/{batch_id}/annotations",
            json={
                "candidate_id": candidate_id,
                "reviewed_at": "2026-01-06T10:00:00+08:00",
                "overall_score": 4,
                "outlook": "bullish",
                "decision": "prepare_buy",
            },
        )
        self.assertEqual(second.status_code, 200)
        second_candidate = second.json()["candidates"][0]
        self.assertEqual(second_candidate["review_state"], "preparing")
        self.assertEqual(len(second_candidate["annotations"]), 2)
        self.assertEqual(second_candidate["latest_annotation"]["decision"], "prepare_buy")

        pool = self.client.get(
            "/v1/selection-observation-pool",
            params={"review_state": "watching"},
        )
        self.assertEqual(pool.status_code, 200)
        self.assertEqual(pool.json(), [])
        preparing_pool = self.client.get(
            "/v1/selection-observation-pool",
            params={"review_state": "preparing"},
        )
        self.assertEqual(preparing_pool.status_code, 200)
        self.assertEqual(len(preparing_pool.json()), 1)
        self.assertEqual(preparing_pool.json()[0]["candidate"]["candidate"]["symbol"], "000001.SZ")

    def test_exclusion_is_separate_from_decision_and_hidden_from_default_pool(self) -> None:
        imported = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_name": "趋势选股",
            },
        ).json()
        batch_id = imported["batch"]["batch_id"]
        candidate_id = imported["candidates"][0]["candidate"]["candidate_id"]
        excluded = self.client.post(
            f"/v1/selection-batches/{batch_id}/exclusions",
            json={
                "candidate_id": candidate_id,
                "excluded_at": "2026-01-06T09:00:00+08:00",
                "reason": "代码无法复核",
            },
        )
        self.assertEqual(excluded.status_code, 200)
        candidate = excluded.json()["candidates"][0]
        self.assertTrue(candidate["excluded"])
        self.assertEqual(candidate["review_state"], "unreviewed")
        self.assertEqual(candidate["exclusions"][0]["reason"], "代码无法复核")
        self.assertEqual(self.client.get("/v1/selection-observation-pool").json(), [])
        included = self.client.get(
            "/v1/selection-observation-pool",
            params={"include_excluded": "true"},
        )
        self.assertEqual(included.status_code, 200)
        self.assertEqual(len(included.json()), 1)

    def test_annotation_before_export_or_on_archived_batch_is_rejected(self) -> None:
        imported = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_name": "趋势选股",
            },
        ).json()
        batch_id = imported["batch"]["batch_id"]
        candidate_id = imported["candidates"][0]["candidate"]["candidate_id"]
        before_export = self.client.post(
            f"/v1/selection-batches/{batch_id}/annotations",
            json={
                "candidate_id": candidate_id,
                "reviewed_at": "2026-01-05T15:00:00+08:00",
                "overall_score": 1,
                "outlook": "bearish",
                "decision": "discard",
            },
        )
        self.assertEqual(before_export.status_code, 422)
        self.assertEqual(before_export.json()["error"]["code"], "SELECTION_ANNOTATION_NOT_ALLOWED")
        archived = self.client.post(
            f"/v1/data-management/batches/{batch_id}/archive",
            json={"reason": "测试归档"},
        )
        self.assertEqual(archived.status_code, 200)
        after_archive = self.client.post(
            f"/v1/selection-batches/{batch_id}/annotations",
            json={
                "candidate_id": candidate_id,
                "reviewed_at": "2026-01-05T15:10:00+08:00",
                "overall_score": 1,
                "outlook": "bearish",
                "decision": "discard",
            },
        )
        self.assertEqual(after_archive.status_code, 422)
        self.assertEqual(after_archive.json()["error"]["code"], "SELECTION_BATCH_ARCHIVED")

    def test_annotation_dataset_endpoint_is_point_in_time_and_versioned(self) -> None:
        imported = self.client.post(
            "/v1/selection-batches",
            json={
                "content_base64": base64.b64encode(CONTENT).decode(),
                "selection_date": "2026-01-05",
                "exported_at": "2026-01-05T15:05:00+08:00",
                "formula_name": "趋势选股",
            },
        ).json()
        batch_id = imported["batch"]["batch_id"]
        candidate_id = imported["candidates"][0]["candidate"]["candidate_id"]
        annotation = self.client.post(
            f"/v1/selection-batches/{batch_id}/annotations",
            json={
                "candidate_id": candidate_id,
                "reviewed_at": "2026-01-05T15:10:00+08:00",
                "overall_score": 4,
                "outlook": "bullish",
                "decision": "prepare_buy",
            },
        )
        self.assertEqual(annotation.status_code, 200)

        response = self.client.get(f"/v1/data-management/batches/{batch_id}/annotation-dataset")

        self.assertEqual(response.status_code, 200)
        row = json.loads(response.content.decode().strip())
        self.assertEqual(row["schema_version"], "selection-annotation-dataset-v1")
        self.assertEqual(row["overall_score"], 4)
        self.assertEqual(row["outcome_status"], None)

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
                    "target_return": "0.02",
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["outcomes_written"], 1)
            self.assertEqual(response.json()["performances_written"], 1)
            fetched = client.get(f"/v1/selection-batches/{imported['batch']['batch_id']}")
            self.assertEqual(len(fetched.json()["candidates"][0]["performances"]), 1)
            self.assertTrue(
                fetched.json()["candidates"][0]["performances"][0]["points"][0]["target_achieved"]
            )
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
