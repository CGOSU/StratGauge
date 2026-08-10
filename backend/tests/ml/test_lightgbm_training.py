import hashlib
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stratgauge.ml.lightgbm_training import (
    LightGBMUnavailableError,
    train_lightgbm,
    write_lightgbm_result,
)
from stratgauge.ml.selection_training import BaselineLabel, SelectionFeatureRow


class FakeDataset:
    def __init__(
        self,
        matrix: list[list[float]],
        *,
        label: list[int],
        feature_name: list[str],
        free_raw_data: bool,
    ) -> None:
        self.matrix = matrix
        self.label = label
        self.feature_name = feature_name
        self.free_raw_data = free_raw_data


class FakeBooster:
    def predict(self, matrix: list[list[float]]) -> list[float]:
        return [0.8 if row[0] > 0 else 0.2 for row in matrix]

    def model_to_string(self) -> str:
        return "fake-lightgbm-model"


class FakeLightGBM:
    Dataset = FakeDataset

    @staticmethod
    def asarray(matrix: list[list[float]], *, dtype: type[float]) -> list[list[float]]:
        return matrix

    @staticmethod
    def train(
        parameters: dict[str, object],
        dataset: FakeDataset,
        *,
        num_boost_round: int,
    ) -> FakeBooster:
        if parameters["objective"] != "binary":
            raise AssertionError("fixture expects binary objective")
        if num_boost_round != 50:
            raise AssertionError("fixture expects fixed boost rounds")
        if not dataset.feature_name:
            raise AssertionError("fixture expects feature names")
        return FakeBooster()


class LightGBMTrainingTests(unittest.TestCase):
    def test_training_is_chronological_and_writes_uncalibrated_artifacts(self) -> None:
        rows = (
            _row("candidate-1", date(2026, 1, 1), "win", Decimal("0.01")),
            _row("candidate-2", date(2026, 1, 2), "loss", Decimal("-0.01")),
            _row("candidate-3", date(2026, 1, 3), "win", Decimal("0.02")),
            _row("candidate-4", date(2026, 2, 1), "loss", Decimal("-0.02")),
            _row("candidate-5", date(2026, 2, 2), "win", Decimal("0.03")),
            _row("candidate-6", date(2026, 3, 1), "loss", Decimal("-0.03")),
        )
        source_hash = hashlib.sha256(b"fixture").hexdigest()
        with patch("stratgauge.ml.lightgbm_training.import_module", return_value=FakeLightGBM):
            result = train_lightgbm(
                rows,
                train_end=date(2026, 1, 31),
                validation_end=date(2026, 2, 28),
                source_file_sha256=source_hash,
                random_seed=7,
            )

        self.assertEqual(result.row_counts, {"train": 3, "validation": 2, "test": 1})
        self.assertEqual(result.model.model_type, "lightgbm")
        self.assertEqual(result.model.parameters["deterministic"], True)
        self.assertFalse(result.model.model_version_id.startswith("baseline-"))
        self.assertEqual(len(result.predictions), 3)
        self.assertFalse(result.artifact == b"")
        with TemporaryDirectory() as directory:
            output = Path(directory) / "lightgbm"
            write_lightgbm_result(output, result)
            self.assertTrue((output / "model.txt").exists())
            self.assertIn('"calibrated": false', (output / "manifest.json").read_text())
            with self.assertRaises(FileExistsError):
                write_lightgbm_result(output, result)

    def test_missing_optional_dependency_is_explicit(self) -> None:
        with patch(
            "stratgauge.ml.lightgbm_training.import_module",
            side_effect=ModuleNotFoundError("lightgbm"),
        ):
            with self.assertRaises(LightGBMUnavailableError):
                train_lightgbm(
                    (_row("candidate-1", date(2026, 1, 1), "win", Decimal("0.01")),),
                    train_end=date(2026, 1, 31),
                    validation_end=date(2026, 2, 28),
                    source_file_sha256="source-hash",
                )


def _row(
    candidate_id: str,
    selection_date: date,
    label: BaselineLabel,
    feature_value: Decimal,
) -> SelectionFeatureRow:
    return SelectionFeatureRow(
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
        features={"return_1": feature_value},
    )


if __name__ == "__main__":
    unittest.main()
