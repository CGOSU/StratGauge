import hashlib
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from stratgauge.ml.isotonic import fit_isotonic_bins
from stratgauge.ml.training_datasets import TrainingDatasetPurpose, TrainingLabel
from stratgauge.ml.training_feature_lightgbm import (
    FeatureLightGBMMetrics,
    FeatureLightGBMModel,
    FeatureLightGBMPrediction,
    FeatureLightGBMTrainingResult,
    write_feature_lightgbm_result,
)
from stratgauge.ml.training_feature_lightgbm_calibration import (
    FeatureCalibrationError,
    calibrate_feature_lightgbm,
    read_feature_lightgbm_output,
    write_feature_calibration_result,
)


class TrainingFeatureLightGBMCalibrationTests(unittest.TestCase):
    def test_binary_calibration_uses_positive_class_complement_and_test_isolation(self) -> None:
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

        result = calibrate_feature_lightgbm(
            model,
            predictions,
            source_prediction_sha256="a" * 64,
            source_model_sha256="b" * 64,
            source_manifest_sha256="c" * 64,
            calibrated_at=datetime(2026, 2, 2, 17, tzinfo=UTC),
            minimum_validation_samples=4,
        )

        self.assertEqual(result.model.calibration_row_count, 4)
        self.assertEqual(result.model.test_row_count, 1)
        self.assertEqual({item.label for item in result.model.bins}, {TrainingLabel.SUCCESS})
        for prediction in result.predictions:
            self.assertEqual(
                sum(prediction.calibrated_probabilities.values(), Decimal("0")), Decimal("1")
            )
            self.assertEqual(
                prediction.calibrated_probabilities[TrainingLabel.NOT_SUCCESS.value]
                + prediction.calibrated_probabilities[TrainingLabel.SUCCESS.value],
                Decimal("1"),
            )
        self.assertEqual(result.metrics[0].split, "validation")
        self.assertEqual(result.metrics[1].split, "test")

    def test_multiclass_calibration_normalizes_one_vs_rest_outputs(self) -> None:
        model = _model(TrainingDatasetPurpose.ANNOTATION_DECISION, 6, 1)
        rows = (
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
        )
        predictions = _predictions(model.purpose, rows)

        result = calibrate_feature_lightgbm(
            model,
            predictions,
            source_prediction_sha256="a" * 64,
            source_model_sha256="b" * 64,
            source_manifest_sha256="c" * 64,
            calibrated_at=datetime(2026, 2, 2, 17, tzinfo=UTC),
            minimum_validation_samples=6,
        )

        self.assertEqual(
            {item.label for item in result.model.bins},
            {
                TrainingLabel.DISCARD,
                TrainingLabel.WATCH,
                TrainingLabel.PREPARE_BUY,
            },
        )
        self.assertTrue(
            all(
                abs(sum(item.calibrated_probabilities.values(), Decimal("0")) - Decimal("1"))
                <= Decimal("1e-6")
                for item in result.predictions
            )
        )

    def test_calibration_rejects_missing_class_future_time_and_tampered_output(self) -> None:
        model = _model(TrainingDatasetPurpose.CANDIDATE_SUCCESS, 4, 1)
        predictions = _predictions(
            model.purpose,
            (
                ("validation-1", date(2026, 1, 1), TrainingLabel.NOT_SUCCESS, "0.2"),
                ("validation-2", date(2026, 1, 2), TrainingLabel.NOT_SUCCESS, "0.3"),
                ("validation-3", date(2026, 1, 3), TrainingLabel.NOT_SUCCESS, "0.4"),
                ("validation-4", date(2026, 1, 4), TrainingLabel.NOT_SUCCESS, "0.5"),
                ("test-1", date(2026, 2, 1), TrainingLabel.SUCCESS, "0.7"),
            ),
        )
        with self.assertRaisesRegex(FeatureCalibrationError, "every purpose class"):
            calibrate_feature_lightgbm(
                model,
                predictions,
                source_prediction_sha256="a" * 64,
                source_model_sha256="b" * 64,
                source_manifest_sha256="c" * 64,
                calibrated_at=datetime(2026, 2, 2, 17, tzinfo=UTC),
                minimum_validation_samples=4,
            )

        valid_predictions = _predictions(
            model.purpose,
            (
                ("validation-1", date(2026, 1, 1), TrainingLabel.NOT_SUCCESS, "0.2"),
                ("validation-2", date(2026, 1, 2), TrainingLabel.SUCCESS, "0.6"),
                ("validation-3", date(2026, 1, 3), TrainingLabel.NOT_SUCCESS, "0.3"),
                ("validation-4", date(2026, 1, 4), TrainingLabel.SUCCESS, "0.8"),
                ("test-1", date(2026, 2, 1), TrainingLabel.SUCCESS, "0.7"),
            ),
        )
        with self.assertRaisesRegex(FeatureCalibrationError, "label availability"):
            calibrate_feature_lightgbm(
                model,
                tuple(
                    item.model_copy(
                        update={"label_available_at": datetime(2026, 3, 1, 17, tzinfo=UTC)}
                    )
                    if item.split == "validation"
                    else item
                    for item in valid_predictions
                ),
                source_prediction_sha256="a" * 64,
                source_model_sha256="b" * 64,
                source_manifest_sha256="c" * 64,
                calibrated_at=datetime(2026, 2, 2, 17, tzinfo=UTC),
                minimum_validation_samples=4,
            )

        training_result = _training_result(model, valid_predictions)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "training"
            write_feature_lightgbm_result(output, training_result)
            prediction_path = output / "predictions.jsonl"
            prediction_path.write_text(
                prediction_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(FeatureCalibrationError, "predictions_sha256"):
                read_feature_lightgbm_output(output)

    def test_isotonic_groups_equal_probabilities_and_calibration_output_is_immutable(self) -> None:
        bins = fit_isotonic_bins(
            (Decimal("0.1"), Decimal("0.1"), Decimal("0.2")),
            (True, False, True),
        )
        self.assertEqual(len(bins), 2)
        self.assertEqual(bins[0].sample_count, 2)
        self.assertEqual(bins[0].calibrated_probability, Decimal("0.5"))

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
        result = calibrate_feature_lightgbm(
            model,
            predictions,
            source_prediction_sha256="a" * 64,
            source_model_sha256="b" * 64,
            source_manifest_sha256="c" * 64,
            calibrated_at=datetime(2026, 2, 2, 17, tzinfo=UTC),
            minimum_validation_samples=4,
        )
        with TemporaryDirectory() as directory:
            output = Path(directory) / "calibration"
            write_feature_calibration_result(output, result)
            self.assertTrue((output / "calibration.json").exists())
            self.assertTrue((output / "calibrated_predictions.jsonl").exists())
            with self.assertRaises(FileExistsError):
                write_feature_calibration_result(output, result)


def _model(
    purpose: TrainingDatasetPurpose, validation_count: int, test_count: int
) -> FeatureLightGBMModel:
    labels = _labels_for(purpose)
    artifact = b"feature-lightgbm-fixture"
    return FeatureLightGBMModel(
        model_version_id=f"feature-lightgbm-{purpose.value}-fixture",
        purpose=purpose,
        label_contract_id={
            TrainingDatasetPurpose.CANDIDATE_SUCCESS: "candidate-success-label-v1",
            TrainingDatasetPurpose.ANNOTATION_DECISION: "annotation-decision-label-v1",
            TrainingDatasetPurpose.BUY_TIMING: "buy-timing-label-v1",
            TrainingDatasetPurpose.SELL_TIMING: "sell-timing-label-v1",
        }[purpose],
        feature_set_version_id="features-fixture-v1",
        feature_names=("close", "return_1"),
        class_labels=labels,
        train_row_count=6,
        train_target_counts={
            label.value: 6 // len(labels) + int(index < 6 % len(labels))
            for index, label in enumerate(labels)
        },
        random_seed=0,
        minimum_labeled_samples=4,
        feature_dataset_manifest_sha256="a" * 64,
        source_label_dataset_sha256="b" * 64,
        label_split_manifest_sha256="c" * 64,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
        parameters={
            "objective": "multiclass" if len(labels) > 2 else "binary",
            **({"num_class": len(labels)} if len(labels) > 2 else {}),
        },
        row_counts={"train": 6, "validation": validation_count, "test": test_count},
        purged_row_count=0,
    )


def _predictions(
    purpose: TrainingDatasetPurpose, rows: tuple[tuple[object, ...], ...]
) -> tuple[FeatureLightGBMPrediction, ...]:
    labels = _labels_for(purpose)
    result = []
    for values in rows:
        sample_id, observation_date, target, probabilities = values
        typed_sample_id = cast(str, sample_id)
        typed_observation_date = cast(date, observation_date)
        typed_target = cast(TrainingLabel, target)
        probability_values = (
            (str(1 - float(probabilities)), probabilities)
            if isinstance(probabilities, str)
            else cast(tuple[str, ...], probabilities)
        )
        probability_map = {
            label.value: Decimal(value)
            for label, value in zip(labels, probability_values, strict=True)
        }
        predicted_label = max(
            labels,
            key=lambda label: (probability_map[label.value], -labels.index(label)),
        )
        result.append(
            FeatureLightGBMPrediction(
                split="validation" if typed_sample_id.startswith("validation") else "test",
                sample_id=typed_sample_id,
                candidate_id=f"candidate-{typed_sample_id}",
                symbol="000001.SZ",
                observation_date=typed_observation_date,
                label_available_at=datetime.combine(
                    typed_observation_date, datetime.min.time().replace(hour=17), tzinfo=UTC
                )
                + timedelta(days=1),
                target=typed_target,
                predicted_label=predicted_label,
                predicted_probabilities=probability_map,
                feature_snapshot_id=f"feature-{sample_id}",
                source_label_row_sha256="d" * 64,
            )
        )
    return tuple(result)


def _training_result(
    model: FeatureLightGBMModel,
    predictions: tuple[FeatureLightGBMPrediction, ...],
) -> FeatureLightGBMTrainingResult:
    labels = _labels_for(model.purpose)
    metrics = tuple(
        FeatureLightGBMMetrics(
            split=split,
            row_count=model.row_counts[split],
            coverage=Decimal("1") if model.row_counts[split] else Decimal("0"),
            target_counts={
                label.value: model.row_counts[split] // len(labels)
                + int(index < model.row_counts[split] % len(labels))
                for index, label in enumerate(labels)
            },
            mean_predicted_probabilities={
                label.value: Decimal("0.5") if len(labels) == 2 else Decimal("0.3333333333333333")
                for label in labels
            },
            log_loss=0.5 if model.row_counts[split] else None,
            normalized_brier_score=0.25 if model.row_counts[split] else None,
        )
        for split in ("train", "validation", "test")
    )
    return FeatureLightGBMTrainingResult(
        model=model,
        metrics=metrics,
        predictions=predictions,
        artifact=b"feature-lightgbm-fixture",
    )


def _labels_for(purpose: TrainingDatasetPurpose) -> tuple[TrainingLabel, ...]:
    return {
        TrainingDatasetPurpose.CANDIDATE_SUCCESS: (
            TrainingLabel.NOT_SUCCESS,
            TrainingLabel.SUCCESS,
        ),
        TrainingDatasetPurpose.ANNOTATION_DECISION: (
            TrainingLabel.DISCARD,
            TrainingLabel.WATCH,
            TrainingLabel.PREPARE_BUY,
        ),
        TrainingDatasetPurpose.BUY_TIMING: (
            TrainingLabel.NOT_PURCHASED,
            TrainingLabel.PURCHASED,
        ),
        TrainingDatasetPurpose.SELL_TIMING: (TrainingLabel.NOT_SOLD, TrainingLabel.SOLD),
    }[purpose]


if __name__ == "__main__":
    unittest.main()
