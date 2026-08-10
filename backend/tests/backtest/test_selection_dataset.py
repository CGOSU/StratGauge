import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.backtest.selection_dataset import build_selection_dataset, write_selection_dataset
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    ManualSelectionLabel,
    SelectionOutcome,
    SelectionOutcomeStatus,
)

CONTENT = "代码,名称,市场\n000001,平安银行,SZ\n".encode()
EXPORTED_AT = datetime(2026, 1, 5, 15, 5, tzinfo=UTC)
IMPORTED_AT = datetime(2026, 1, 5, 15, 10, tzinfo=UTC)


class SelectionDatasetTests(unittest.TestCase):
    def test_pending_candidate_is_retained_and_latest_label_is_joined(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            imported = import_tdx_export(
                CONTENT,
                selection_date=EXPORTED_AT.date(),
                exported_at=EXPORTED_AT,
                imported_at=IMPORTED_AT,
                formula_name="公式 A",
                formula_version="v1",
            )
            store.write(CONTENT, imported)
            store.write_label(
                imported.batch.batch_id,
                ManualSelectionLabel(
                    candidate_id=imported.candidates[0].candidate_id,
                    decision=ManualSelectionDecision.WATCH,
                    labeled_at=IMPORTED_AT,
                ),
            )

            rows = build_selection_dataset(store, imported.batch.batch_id)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].schema_version, "selection-dataset-v2")
            self.assertEqual(rows[0].manual_decision, ManualSelectionDecision.WATCH)
            self.assertIsNone(rows[0].outcome_status)
            self.assertEqual(rows[0].source_snapshot_ids, ())

    def test_matured_outcome_is_joined_without_dropping_lineage(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            imported = import_tdx_export(
                CONTENT,
                selection_date=EXPORTED_AT.date(),
                exported_at=EXPORTED_AT,
                imported_at=IMPORTED_AT,
                formula_name="公式 A",
            )
            store.write(CONTENT, imported)
            outcome = SelectionOutcome(
                candidate_id=imported.candidates[0].candidate_id,
                batch_id=imported.batch.batch_id,
                selection_date=EXPORTED_AT.date(),
                status=SelectionOutcomeStatus.WIN,
                evaluated_at=datetime(2026, 1, 10, 17, tzinfo=UTC),
                horizon_sessions=5,
                entry_session=datetime(2026, 1, 6, tzinfo=UTC).date(),
                exit_session=datetime(2026, 1, 10, tzinfo=UTC).date(),
                entry_price=Decimal("10"),
                exit_price=Decimal("11"),
                gross_return=Decimal("0.1"),
                net_return=Decimal("0.09"),
                reason="fixed_horizon",
                outcome_policy_id="outcome-fixed-10-net-positive-v1",
                source_snapshot_ids=("snapshot-1",),
            )
            store.write_outcome(outcome)

            rows = build_selection_dataset(store, imported.batch.batch_id)

            self.assertEqual(rows[0].outcome_status, SelectionOutcomeStatus.WIN)
            self.assertEqual(rows[0].net_return, "0.09")
            self.assertEqual(rows[0].outcome_policy_id, "outcome-fixed-10-net-positive-v1")
            self.assertEqual(rows[0].source_snapshot_ids, ("snapshot-1",))

    def test_dataset_writer_does_not_overwrite(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.jsonl"
            rows = ()
            write_selection_dataset(path, rows)

            with self.assertRaises(FileExistsError):
                write_selection_dataset(path, rows)


if __name__ == "__main__":
    unittest.main()
