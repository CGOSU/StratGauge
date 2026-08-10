import hashlib
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.ml.evaluation import read_model_evaluation
from stratgauge.ml.selection_training import (
    FEATURE_DATASET_SCHEMA_VERSION,
    BaselineLabel,
    SelectionFeatureRow,
    read_feature_dataset,
    serialize_feature_dataset,
    train_constant_baseline,
    write_baseline_result,
)


class SelectionTrainingTests(unittest.TestCase):
    def test_baseline_uses_time_split_and_is_reproducible(self) -> None:
        rows = (
            _row("candidate-1", date(2026, 1, 1), "win"),
            _row("candidate-2", date(2026, 1, 2), "loss"),
            _row("candidate-3", date(2026, 1, 3), "win"),
            _row("candidate-4", date(2026, 2, 1), "loss"),
            _row("candidate-5", date(2026, 2, 2), "win"),
            _row("candidate-6", date(2026, 3, 1), "loss"),
        )
        content = serialize_feature_dataset(rows).encode("utf-8")
        parsed = read_feature_dataset(content)
        first = train_constant_baseline(
            parsed,
            train_end=date(2026, 1, 31),
            validation_end=date(2026, 2, 28),
            source_file_sha256=hashlib.sha256(content).hexdigest(),
            random_seed=7,
        )
        second = train_constant_baseline(
            parsed,
            train_end=date(2026, 1, 31),
            validation_end=date(2026, 2, 28),
            source_file_sha256=hashlib.sha256(content).hexdigest(),
            random_seed=7,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.row_counts, {"train": 3, "validation": 2, "test": 1})
        self.assertEqual(
            first.model.positive_probability, Decimal("0.6666666666666666666666666667")
        )
        self.assertEqual(len(first.predictions), 3)
        self.assertTrue(all(metric.coverage == Decimal("1") for metric in first.metrics[:2]))

    def test_single_class_train_is_rejected_before_model_output(self) -> None:
        rows = (
            _row("candidate-1", date(2026, 1, 1), "win"),
            _row("candidate-2", date(2026, 1, 2), "win"),
            _row("candidate-3", date(2026, 2, 1), "loss"),
        )
        with self.assertRaisesRegex(ValueError, "at least two classes"):
            train_constant_baseline(
                rows,
                train_end=date(2026, 1, 31),
                validation_end=date(2026, 2, 28),
                source_file_sha256="source-hash",
            )

    def test_writer_is_non_overwriting(self) -> None:
        rows = (
            _row("candidate-1", date(2026, 1, 1), "win"),
            _row("candidate-2", date(2026, 1, 2), "loss"),
            _row("candidate-3", date(2026, 2, 1), "loss"),
        )
        result = train_constant_baseline(
            rows,
            train_end=date(2026, 1, 31),
            validation_end=date(2026, 2, 28),
            source_file_sha256="source-hash",
        )
        with TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            write_baseline_result(output, result)
            self.assertTrue((output / "model.json").exists())
            evaluation = read_model_evaluation((output / "metrics.json").read_bytes())
            self.assertEqual(evaluation.model_version_id, result.model.model_version_id)
            self.assertEqual(
                evaluation.metrics["validation_log_loss"], Decimal("0.6931471805599453")
            )
            with self.assertRaises(FileExistsError):
                write_baseline_result(output, result)

    def test_unknown_schema_is_rejected(self) -> None:
        row = _row("candidate-1", date(2026, 1, 1), "win").model_dump(mode="json")
        row["schema_version"] = "unknown"
        with self.assertRaisesRegex(ValueError, "unsupported feature dataset schema"):
            read_feature_dataset(
                (SelectionFeatureRow.model_validate(row)).model_dump_json().encode()
            )


def _row(candidate_id: str, selection_date: date, label: BaselineLabel) -> SelectionFeatureRow:
    return SelectionFeatureRow(
        schema_version=FEATURE_DATASET_SCHEMA_VERSION,
        candidate_id=candidate_id,
        symbol="000001.SZ",
        selection_date=selection_date,
        feature_set_version_id="features-fixture-v1",
        feature_snapshot_id=f"feature-{candidate_id}",
        as_of=datetime.combine(selection_date, datetime.min.time(), tzinfo=UTC),
        label_evaluated_at=datetime(2026, 4, 1, 12, tzinfo=UTC),
        tracking_policy_id="policy-v1",
        label=label,
        source_snapshot_ids=(f"snapshot-{candidate_id}",),
        features={"return_1": Decimal("0.01")},
    )


if __name__ == "__main__":
    unittest.main()
