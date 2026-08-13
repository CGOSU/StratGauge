import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.ml.training_datasets import TrainingDatasetPurpose, TrainingLabel
from stratgauge.ml.training_feature_lightgbm import (
    FeatureLightGBMTrainingResult,
    write_feature_lightgbm_result,
)
from stratgauge.ml.training_feature_lightgbm_calibration import (
    calibrate_feature_lightgbm,
    read_feature_lightgbm_output,
    write_feature_calibration_result,
)
from stratgauge.ml.training_feature_registry import (
    FeatureModelRegistryError,
    FeatureModelRegistryStore,
)
from tests.ml.test_training_feature_lightgbm_calibration import (
    _model,
    _predictions,
    _training_result,
)


class TrainingFeatureRegistryTests(unittest.TestCase):
    def test_registers_binary_training_output_as_immutable_challenger(self) -> None:
        training_result = _binary_training_result()

        with TemporaryDirectory() as directory:
            training_dir, _ = _write_outputs(directory, training_result)
            store = FeatureModelRegistryStore(directory)
            registration = store.register(
                training_dir,
                registered_at=datetime(2026, 3, 3, tzinfo=UTC),
                registered_by="researcher",
            )

            self.assertEqual(registration.lifecycle, "challenger")
            self.assertFalse(registration.calibrated)
            loaded, training, calibration = store.load_outputs(registration.registration_id)
            self.assertEqual(loaded, registration)
            self.assertEqual(
                training.model.model_version_id,
                training_result.model.model_version_id,
            )
            self.assertIsNone(calibration)
            self.assertEqual(
                store.register(
                    training_dir,
                    registered_at=datetime(2026, 3, 3, tzinfo=UTC),
                    registered_by="researcher",
                ),
                registration,
            )

    def test_registers_multiclass_calibration_and_copies_all_evidence(self) -> None:
        training_result = _multiclass_training_result()

        with TemporaryDirectory() as directory:
            training_dir, calibration_dir = _write_outputs(directory, training_result)
            assert calibration_dir is not None
            store = FeatureModelRegistryStore(directory)
            registration = store.register(
                training_dir,
                calibration_output_dir=calibration_dir,
                registered_at=datetime(2026, 3, 3, tzinfo=UTC),
                registered_by="researcher",
            )
            loaded, _, calibration = store.load_outputs(registration.registration_id)

            self.assertTrue(loaded.calibrated)
            self.assertEqual(loaded.purpose, TrainingDatasetPurpose.ANNOTATION_DECISION)
            self.assertEqual(loaded.class_labels, ("discard", "watch", "prepare_buy"))
            self.assertIsNotNone(calibration)
            for name in (
                "calibration.json",
                "metrics.json",
                "calibrated_predictions.jsonl",
                "manifest.json",
            ):
                self.assertTrue(
                    (
                        store.evidence_dir / registration.registration_id / "calibration" / name
                    ).exists()
                )

    def test_rejects_calibration_from_different_training_output(self) -> None:
        training_result = _binary_training_result()
        model = training_result.model
        calibration = calibrate_feature_lightgbm(
            model,
            training_result.predictions,
            source_prediction_sha256="a" * 64,
            source_model_sha256="b" * 64,
            source_manifest_sha256="c" * 64,
            calibrated_at=datetime(2026, 3, 2, 18, tzinfo=UTC),
            minimum_validation_samples=4,
        )

        with TemporaryDirectory() as directory:
            training_dir, _ = _write_outputs(directory, training_result)
            calibration_dir = Path(directory) / "calibration"
            write_feature_calibration_result(calibration_dir, calibration)
            with self.assertRaisesRegex(FeatureModelRegistryError, "does not match training"):
                FeatureModelRegistryStore(directory).register(
                    training_dir,
                    calibration_output_dir=calibration_dir,
                    registered_at=datetime(2026, 3, 3, tzinfo=UTC),
                    registered_by="researcher",
                )

    def test_rejects_registration_before_calibration_time(self) -> None:
        training_result = _binary_training_result()

        with TemporaryDirectory() as directory:
            training_dir, calibration_dir = _write_outputs(
                directory,
                training_result,
                include_calibration=True,
            )
            assert calibration_dir is not None
            with self.assertRaisesRegex(
                FeatureModelRegistryError,
                "registration cannot precede calibration time",
            ):
                FeatureModelRegistryStore(directory).register(
                    training_dir,
                    calibration_output_dir=calibration_dir,
                    registered_at=datetime(2026, 3, 2, 17, tzinfo=UTC),
                    registered_by="researcher",
                )

    def test_rejects_tampered_copied_evidence(self) -> None:
        training_result = _binary_training_result()

        with TemporaryDirectory() as directory:
            training_dir, _ = _write_outputs(directory, training_result)
            store = FeatureModelRegistryStore(directory)
            registration = store.register(
                training_dir,
                registered_at=datetime(2026, 3, 3, tzinfo=UTC),
                registered_by="researcher",
            )
            copied_model = (
                store.evidence_dir / registration.registration_id / "training" / "model.json"
            )
            copied_model.write_text("tampered", encoding="utf-8")
            with self.assertRaises(FeatureModelRegistryError):
                store.load(registration.registration_id)


def _binary_training_result() -> FeatureLightGBMTrainingResult:
    model = _model(TrainingDatasetPurpose.CANDIDATE_SUCCESS, 4, 1)
    predictions = _predictions(
        model.purpose,
        (
            ("validation-1", date(2026, 1, 1), TrainingLabel.NOT_SUCCESS, "0.2"),
            ("validation-2", date(2026, 1, 2), TrainingLabel.SUCCESS, "0.6"),
            ("validation-3", date(2026, 1, 3), TrainingLabel.NOT_SUCCESS, "0.3"),
            ("validation-4", date(2026, 1, 4), TrainingLabel.SUCCESS, "0.8"),
            ("test-1", date(2026, 2, 1), TrainingLabel.SUCCESS, "0.7"),
        ),
    )
    return _training_result(model, predictions)


def _multiclass_training_result() -> FeatureLightGBMTrainingResult:
    model = _model(TrainingDatasetPurpose.ANNOTATION_DECISION, 6, 1)
    predictions = _predictions(
        model.purpose,
        (
            ("validation-1", date(2026, 1, 1), TrainingLabel.DISCARD, ("0.7", "0.2", "0.1")),
            ("validation-2", date(2026, 1, 2), TrainingLabel.WATCH, ("0.2", "0.7", "0.1")),
            (
                "validation-3",
                date(2026, 1, 3),
                TrainingLabel.PREPARE_BUY,
                ("0.1", "0.2", "0.7"),
            ),
            ("validation-4", date(2026, 1, 4), TrainingLabel.DISCARD, ("0.6", "0.3", "0.1")),
            ("validation-5", date(2026, 1, 5), TrainingLabel.WATCH, ("0.2", "0.6", "0.2")),
            (
                "validation-6",
                date(2026, 1, 6),
                TrainingLabel.PREPARE_BUY,
                ("0.1", "0.3", "0.6"),
            ),
            ("test-1", date(2026, 2, 1), TrainingLabel.WATCH, ("0.2", "0.5", "0.3")),
        ),
    )
    return _training_result(model, predictions)


def _write_outputs(
    directory: str,
    training_result: FeatureLightGBMTrainingResult,
    *,
    include_calibration: bool = False,
) -> tuple[Path, Path | None]:
    training_dir = Path(directory) / "training"
    write_feature_lightgbm_result(training_dir, training_result)
    if (
        not include_calibration
        and training_result.model.purpose is not TrainingDatasetPurpose.ANNOTATION_DECISION
    ):
        return training_dir, None
    training = read_feature_lightgbm_output(training_dir)
    calibration = calibrate_feature_lightgbm(
        training_result.model,
        training_result.predictions,
        source_prediction_sha256=training.predictions_sha256,
        source_model_sha256=training.model_sha256,
        source_manifest_sha256=training.manifest_sha256,
        calibrated_at=datetime(2026, 3, 2, 18, tzinfo=UTC),
        minimum_validation_samples=(training.model.row_counts["validation"]),
    )
    calibration_dir = Path(directory) / "calibration"
    write_feature_calibration_result(calibration_dir, calibration)
    return training_dir, calibration_dir


if __name__ == "__main__":
    unittest.main()
