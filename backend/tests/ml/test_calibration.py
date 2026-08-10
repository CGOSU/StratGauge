import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from stratgauge.ml.calibration import (
    CalibrationError,
    CalibrationPrediction,
    calibrate_predictions,
    write_calibration_result,
)


class CalibrationTests(unittest.TestCase):
    def test_isotonic_uses_validation_only_and_writes_scoped_artifacts(self) -> None:
        predictions = (
            _prediction("validation", "validation-1", date(2026, 1, 1), "0.1", "win"),
            _prediction("validation", "validation-2", date(2026, 1, 2), "0.2", "loss"),
            _prediction("validation", "validation-3", date(2026, 1, 3), "0.9", "win"),
            _prediction("test", "test-1", date(2026, 2, 1), "0.15", "loss"),
        )
        result = calibrate_predictions(
            predictions,
            model_manifest=_manifest(),
            source_prediction_sha256="prediction-hash",
            source_model_manifest_sha256="manifest-hash",
            calibrated_at=datetime(2026, 2, 2, 17, tzinfo=UTC),
            minimum_validation_samples=2,
        )

        self.assertEqual(result.model.base_model_version_id, "lightgbm-fixture")
        self.assertEqual(result.model.calibration_row_count, 3)
        self.assertEqual(result.model.test_row_count, 1)
        self.assertEqual(len(result.model.bins), 2)
        self.assertEqual(result.model.bins[0].calibrated_probability, Decimal("0.5"))
        self.assertTrue(all(item.calibrated for item in [result.model]))
        test_prediction = next(item for item in result.predictions if item.split == "test")
        self.assertEqual(test_prediction.calibrated_probability, Decimal("0.5"))
        with TemporaryDirectory() as directory:
            output = Path(directory) / "calibration"
            write_calibration_result(output, result)
            self.assertTrue((output / "calibration.json").exists())
            self.assertIn('"calibrated": true', (output / "manifest.json").read_text())
            with self.assertRaises(FileExistsError):
                write_calibration_result(output, result)

    def test_validation_requires_minimum_two_classes_and_samples(self) -> None:
        with self.assertRaisesRegex(CalibrationError, "insufficient"):
            calibrate_predictions(
                (_prediction("validation", "one", date(2026, 1, 1), "0.5", "win"),),
                model_manifest=_manifest(),
                source_prediction_sha256="prediction-hash",
                source_model_manifest_sha256="manifest-hash",
                calibrated_at=datetime(2026, 2, 2, 17, tzinfo=UTC),
                minimum_validation_samples=2,
            )

    def test_lightgbm_manifest_is_required(self) -> None:
        with self.assertRaisesRegex(CalibrationError, "LightGBM training manifest"):
            calibrate_predictions(
                (
                    _prediction("validation", "one", date(2026, 1, 1), "0.1", "win"),
                    _prediction("validation", "two", date(2026, 1, 2), "0.9", "loss"),
                ),
                model_manifest={"report_version": "selection-baseline-v1"},
                source_prediction_sha256="prediction-hash",
                source_model_manifest_sha256="manifest-hash",
                calibrated_at=datetime(2026, 2, 2, 17, tzinfo=UTC),
                minimum_validation_samples=2,
            )


def _manifest() -> dict[str, object]:
    return {
        "report_version": "selection-lightgbm-v1",
        "model_version_id": "lightgbm-fixture",
        "feature_set_version_id": "features-fixture-v1",
        "tracking_policy_id": "policy-v1",
    }


def _prediction(
    split: Literal["validation", "test"],
    candidate_id: str,
    selection_date: date,
    probability: str,
    label: Literal["win", "loss"],
) -> CalibrationPrediction:
    return CalibrationPrediction(
        split=split,
        candidate_id=candidate_id,
        symbol="000001.SZ",
        selection_date=selection_date,
        label=label,
        predicted_probability=Decimal(probability),
        rank=1,
        feature_snapshot_id=f"feature-{candidate_id}",
    )


if __name__ == "__main__":
    unittest.main()
