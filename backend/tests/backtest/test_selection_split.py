import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.backtest.selection_dataset import SelectionDatasetRow
from stratgauge.backtest.selection_split import (
    DATASET_SCHEMA_VERSION,
    split_selection_dataset,
    write_selection_dataset_split,
)


def row(candidate_id: str, selection_date: str) -> SelectionDatasetRow:
    return SelectionDatasetRow(
        candidate_id=candidate_id,
        batch_id="batch-1",
        symbol=f"00000{candidate_id[-1]}.SZ",
        selection_date=selection_date,
        source_filename="selection.txt",
        formula_id=None,
        formula_name="公式 A",
        formula_version="v1",
        manual_decision=None,
        manual_labeled_at=None,
        tracking_policy_id=None,
        cost_schedule_id=None,
        horizon_sessions=None,
        position_notional=None,
        outcome_status=None,
        outcome_evaluated_at=None,
        net_return=None,
        source_snapshot_ids=(),
    )


class SelectionSplitTests(unittest.TestCase):
    def test_split_is_date_ordered_and_never_moves_future_rows_back(self) -> None:
        rows = (
            row("candidate-3", "2026-03-01"),
            row("candidate-1", "2026-01-01"),
            row("candidate-2", "2026-02-01"),
        )
        splits = split_selection_dataset(
            rows,
            train_end=date(2026, 1, 31),
            validation_end=date(2026, 2, 28),
        )
        self.assertEqual([item.candidate_id for item in splits["train"]], ["candidate-1"])
        self.assertEqual([item.candidate_id for item in splits["validation"]], ["candidate-2"])
        self.assertEqual([item.candidate_id for item in splits["test"]], ["candidate-3"])

    def test_writer_records_source_hash_and_rejects_overwrite(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "dataset.jsonl"
            output = Path(directory) / "split"
            source.write_text(
                f"{row('candidate-1', '2026-01-01').model_dump_json()}\n", encoding="utf-8"
            )
            manifest = write_selection_dataset_split(
                source,
                output,
                train_end=date(2026, 1, 31),
                validation_end=date(2026, 2, 28),
            )
            self.assertEqual(manifest.dataset_schema_version, DATASET_SCHEMA_VERSION)
            self.assertEqual(manifest.row_counts, {"train": 1, "validation": 0, "test": 0})
            with self.assertRaises(FileExistsError):
                write_selection_dataset_split(
                    source,
                    output,
                    train_end=__import__("datetime").date(2026, 1, 31),
                    validation_end=__import__("datetime").date(2026, 2, 28),
                )

    def test_split_rejects_reversed_boundaries(self) -> None:
        with self.assertRaises(ValueError):
            split_selection_dataset(
                (row("candidate-1", "2026-01-01"),),
                train_end=date(2026, 2, 28),
                validation_end=date(2026, 1, 31),
            )


if __name__ == "__main__":
    unittest.main()
