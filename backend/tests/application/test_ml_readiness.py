import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.application.ml_readiness import MLReadinessService
from stratgauge.data_pipeline.ingest import ingest
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.base import DailyBarRequest, IndexDailyRequest
from stratgauge.domain.models import Exchange, Security
from stratgauge.domain.selections import (
    SelectionOutcome,
    SelectionOutcomeStatus,
    TdxSelectionImport,
)


class _OtherIndexDailyRequest(DailyBarRequest):
    @property
    def request_kind(self) -> str:
        return "index_daily"


class MLReadinessTests(unittest.TestCase):
    def test_empty_evidence_is_blocked_without_creating_read_only_directories(self) -> None:
        with TemporaryDirectory() as selection_directory, TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            market_root = Path(directory) / "market"
            model_root = Path(directory) / "models"
            report = MLReadinessService(
                SelectionImportStore(selection_directory),
                data_root=data_root,
                market_root=market_root,
                model_root=model_root,
            ).get_readiness()

            self.assertEqual(report.stage, "collecting_labels")
            self.assertEqual(report.accepted_label_count, 0)
            self.assertEqual(report.minimum_labeled_samples, 100)
            self.assertEqual(report.label_shortfall, 100)
            self.assertFalse(report.label_ready)
            self.assertFalse(report.training_ready)
            self.assertTrue(any("还需要 100 条" in blocker for blocker in report.blockers))
            self.assertFalse(data_root.exists())
            self.assertFalse(market_root.exists())
            self.assertFalse(model_root.exists())

    def test_only_completed_outcomes_become_labels_and_evidence_remains_a_gate(self) -> None:
        with TemporaryDirectory() as selection_directory, TemporaryDirectory() as directory:
            store = SelectionImportStore(selection_directory)
            imported = _write_batch(store, 100)
            for index, candidate in enumerate(imported.candidates):
                _write_outcome(
                    store,
                    imported.batch.batch_id,
                    candidate.candidate_id,
                    SelectionOutcomeStatus.WIN if index % 2 == 0 else SelectionOutcomeStatus.LOSS,
                    index,
                )
            pending = imported.candidates[-1]
            self.assertIsNotNone(pending)

            report = MLReadinessService(
                store,
                data_root=Path(directory) / "data",
                market_root=Path(directory) / "market",
                model_root=Path(directory) / "models",
            ).get_readiness()

            self.assertEqual(report.accepted_label_count, 100)
            self.assertEqual(report.target_counts, {"not_success": 50, "success": 50})
            self.assertEqual(report.outcome_counts["win"], 50)
            self.assertEqual(report.outcome_counts["loss"], 50)
            self.assertEqual(report.outcome_counts["pending"], 0)
            self.assertTrue(report.label_ready)
            self.assertFalse(report.evidence_ready)
            self.assertFalse(report.training_ready)
            self.assertEqual(report.stage, "awaiting_feature_evidence")
            self.assertTrue(any("index_daily" in blocker for blocker in report.blockers))
            self.assertTrue(any("特征快照不足" in blocker for blocker in report.blockers))

    def test_only_approved_csi300_index_daily_counts_as_benchmark_evidence(self) -> None:
        with TemporaryDirectory() as selection_directory, TemporaryDirectory() as directory:
            market_root = Path(directory) / "market"
            _write_approved_market_snapshot(
                market_root,
                _OtherIndexDailyRequest(
                    security=Security(
                        security_id="index-000905.SH",
                        local_code="000905",
                        symbol="000905.SH",
                        exchange=Exchange.SH,
                    ),
                    start_date=date(2026, 1, 5),
                    end_date=date(2026, 1, 5),
                ),
            )
            _write_approved_market_snapshot(
                market_root,
                DailyBarRequest(
                    security=Security(
                        security_id="security-000001.SZ",
                        local_code="000001",
                        symbol="000001.SZ",
                        exchange=Exchange.SZ,
                    ),
                    start_date=date(2026, 1, 5),
                    end_date=date(2026, 1, 5),
                ),
            )
            (market_root / "manifests" / "invalid.json").write_text("{}", encoding="utf-8")

            service = MLReadinessService(
                SelectionImportStore(selection_directory),
                data_root=Path(directory) / "data",
                market_root=market_root,
                model_root=Path(directory) / "models",
            )
            without_supported_benchmark = service.get_readiness()
            self.assertEqual(without_supported_benchmark.benchmark_snapshot_count, 0)
            self.assertTrue(
                any(
                    "缺少已批准的 000300.SH" in blocker
                    for blocker in without_supported_benchmark.blockers
                )
            )

            _write_approved_market_snapshot(
                market_root,
                IndexDailyRequest(
                    security=Security(
                        security_id="index-000300.SH",
                        local_code="000300",
                        symbol="000300.SH",
                        exchange=Exchange.SH,
                    ),
                    start_date=date(2026, 1, 5),
                    end_date=date(2026, 1, 5),
                ),
            )
            report = service.get_readiness()

            self.assertEqual(report.benchmark_snapshot_count, 1)
            self.assertEqual(report.invalid_market_manifest_count, 1)
            self.assertFalse(
                any("缺少已批准的 000300.SH" in blocker for blocker in report.blockers)
            )

    def test_pending_candidate_is_not_counted_as_training_label(self) -> None:
        with TemporaryDirectory() as selection_directory, TemporaryDirectory() as directory:
            store = SelectionImportStore(selection_directory)
            imported = _write_batch(store, 2)
            _write_outcome(
                store,
                imported.batch.batch_id,
                imported.candidates[0].candidate_id,
                SelectionOutcomeStatus.NO_ENTRY,
                0,
            )
            _write_outcome(
                store,
                imported.batch.batch_id,
                imported.candidates[1].candidate_id,
                SelectionOutcomeStatus.UNRESOLVED,
                1,
            )

            report = MLReadinessService(
                store,
                data_root=Path(directory) / "data",
                market_root=Path(directory) / "market",
                model_root=Path(directory) / "models",
            ).get_readiness()

            self.assertEqual(report.accepted_label_count, 0)
            self.assertEqual(report.target_counts, {"not_success": 0, "success": 0})
            self.assertEqual(report.outcome_counts["no_entry"], 1)
            self.assertEqual(report.outcome_counts["unresolved"], 1)
            self.assertNotIn("no_entry", report.target_counts)
            self.assertNotIn("unresolved", report.target_counts)


def _write_approved_market_snapshot(root: Path, request: DailyBarRequest) -> None:
    result = ingest(
        AKShareAdapter(
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
            clock=lambda: datetime(2026, 1, 6, 8, tzinfo=UTC),
        ),
        request,
        SnapshotStore(root),
        expected_sessions=[date(2026, 1, 5)],
        expected_security_ids=[request.security.security_id],
    )
    if not result.quality.approved:
        raise AssertionError("test market fixture must be approved")


def _write_batch(store: SelectionImportStore, count: int) -> TdxSelectionImport:
    content = "代码,名称,市场\n" + "".join(
        f"{index:06d},测试{index},SZ\n" for index in range(1, count + 1)
    )
    imported = import_tdx_export(
        content.encode("utf-8"),
        selection_date=date(2026, 1, 5),
        exported_at=datetime(2026, 1, 5, 15, 5, tzinfo=UTC),
        imported_at=datetime(2026, 1, 5, 15, 10, tzinfo=UTC),
        formula_name="测试公式",
    )
    store.write(content.encode("utf-8"), imported)
    return imported


def _write_outcome(
    store: SelectionImportStore,
    batch_id: str,
    candidate_id: str,
    status: SelectionOutcomeStatus,
    index: int,
) -> None:
    entry_session = date(2026, 1, 6)
    exit_session = date(2026, 1, 16)
    store.write_outcome(
        SelectionOutcome(
            candidate_id=candidate_id,
            batch_id=batch_id,
            selection_date=date(2026, 1, 5),
            status=status,
            evaluated_at=datetime(2026, 2, 1, 12, index % 60, tzinfo=UTC),
            horizon_sessions=10,
            entry_session=None
            if status in {SelectionOutcomeStatus.NO_ENTRY, SelectionOutcomeStatus.UNRESOLVED}
            else entry_session,
            exit_session=None
            if status in {SelectionOutcomeStatus.NO_ENTRY, SelectionOutcomeStatus.UNRESOLVED}
            else exit_session,
            entry_price=None
            if status in {SelectionOutcomeStatus.NO_ENTRY, SelectionOutcomeStatus.UNRESOLVED}
            else Decimal("10"),
            exit_price=None
            if status in {SelectionOutcomeStatus.NO_ENTRY, SelectionOutcomeStatus.UNRESOLVED}
            else Decimal("11"),
            gross_return=None
            if status in {SelectionOutcomeStatus.NO_ENTRY, SelectionOutcomeStatus.UNRESOLVED}
            else Decimal("0.1"),
            net_return=None
            if status in {SelectionOutcomeStatus.NO_ENTRY, SelectionOutcomeStatus.UNRESOLVED}
            else Decimal("0.1"),
            reason=status.value,
            tracking_policy_id="tracking-v1",
            outcome_policy_id="outcome-v1",
            source_snapshot_ids=(f"snapshot-{index}",),
        )
    )


if __name__ == "__main__":
    unittest.main()
