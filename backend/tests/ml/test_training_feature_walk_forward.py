import hashlib
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stratgauge.ml.training_datasets import (
    SelectionTrainingLabelRow,
    TrainingDatasetPurpose,
    TrainingLabel,
    TrainingLabelReadinessReport,
    TrainingLabelSplitManifest,
    label_contract_id_for,
)
from stratgauge.ml.training_feature_baseline import TrainingFeatureSplitInput
from stratgauge.ml.training_feature_join import (
    SelectionTrainingFeatureRow,
    TrainingFeatureJoinReport,
    TrainingFeatureSplitManifest,
)
from stratgauge.ml.training_feature_lightgbm import (
    FeatureLightGBMEvaluationEvidence,
    FeatureLightGBMModel,
)
from stratgauge.ml.training_feature_lightgbm_calibration import FeatureLightGBMOutput
from stratgauge.ml.training_feature_registry import (
    FeatureModelRegistration,
    _registration_id,
)
from stratgauge.ml.training_feature_walk_forward import (
    FeatureWalkForwardFoldSpec,
    FeatureWalkForwardReportStore,
    FeatureWalkForwardUnavailableError,
    validate_feature_walk_forward,
)


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
    def __init__(self, multiclass: bool) -> None:
        self.multiclass = multiclass

    def predict(self, matrix: list[list[float]]) -> list[float] | list[list[float]]:
        if self.multiclass:
            return [[0.2, 0.5, 0.3] for _ in matrix]
        return [0.75 for _ in matrix]


class FakeLightGBM:
    Dataset = FakeDataset
    train_calls: list[tuple[dict[str, object], FakeDataset, int]] = []

    @classmethod
    def train(
        cls,
        parameters: dict[str, object],
        dataset: FakeDataset,
        *,
        num_boost_round: int,
    ) -> FakeBooster:
        cls.train_calls.append((parameters, dataset, num_boost_round))
        return FakeBooster(parameters["objective"] == "multiclass")


class FakeNumpy:
    @staticmethod
    def asarray(matrix: list[list[float]], *, dtype: type[float]) -> list[list[float]]:
        return matrix


class TrainingFeatureWalkForwardTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeLightGBM.train_calls = []

    def test_supports_all_purposes_and_keeps_test_and_purged_out(self) -> None:
        for purpose in (
            TrainingDatasetPurpose.CANDIDATE_SUCCESS,
            TrainingDatasetPurpose.ANNOTATION_DECISION,
            TrainingDatasetPurpose.BUY_TIMING,
            TrainingDatasetPurpose.SELL_TIMING,
        ):
            with self.subTest(purpose=purpose):
                split, registration, training = _fixture(purpose)
                with patch(
                    "stratgauge.ml.training_feature_walk_forward.helpers_b.import_module",
                    side_effect=_fake_import,
                ):
                    report = validate_feature_walk_forward(
                        split,
                        registration=registration,
                        training_output=training,
                        calibration_output=None,
                        folds=(_fold(),),
                        evaluated_at=datetime(2026, 2, 1, 17, tzinfo=UTC),
                    )

                expected_labels = _labels_for(purpose)
                self.assertEqual(report.class_labels, expected_labels)
                self.assertEqual(report.purpose, purpose)
                self.assertEqual(report.purged_row_count, 1)
                self.assertEqual(report.final_test_row_count, 1)
                self.assertEqual(report.folds[0].purged_train_row_count, 1)
                self.assertEqual(report.folds[0].validation_row_count, 1)
                self.assertEqual(len(report.folds[0].predictions), 1)
                self.assertEqual(
                    set(report.folds[0].predictions[0].predicted_probabilities),
                    {label.value for label in expected_labels},
                )
                self.assertEqual(len(FakeLightGBM.train_calls), 1)
                parameters, dataset, _ = FakeLightGBM.train_calls[-1]
                self.assertEqual(
                    parameters["objective"], "multiclass" if len(expected_labels) == 3 else "binary"
                )
                self.assertEqual(len(dataset.matrix), len(split.train_rows) - 1)
                FakeLightGBM.train_calls = []

    def test_purges_labels_by_validation_start_and_rejects_unavailable_evaluation(self) -> None:
        split, registration, training = _fixture(TrainingDatasetPurpose.CANDIDATE_SUCCESS)
        with patch(
            "stratgauge.ml.training_feature_walk_forward.helpers_b.import_module",
            side_effect=_fake_import,
        ):
            report = validate_feature_walk_forward(
                split,
                registration=registration,
                training_output=training,
                calibration_output=None,
                folds=(_fold(),),
                evaluated_at=datetime(2026, 2, 1, 17, tzinfo=UTC),
            )
        fold = report.folds[0]
        self.assertEqual(fold.train_candidate_row_count, len(split.train_rows))
        self.assertEqual(fold.train_row_count, len(split.train_rows) - 1)
        self.assertEqual(fold.purged_train_row_count, 1)

        late_label = split.train_rows[0].model_copy(
            update={"label_available_at": datetime(2026, 2, 2, tzinfo=UTC)}
        )
        late_split = _replace_train_row(split, late_label)
        with self.assertRaisesRegex(ValueError, "unavailable at evaluated_at"):
            validate_feature_walk_forward(
                late_split,
                registration=registration,
                training_output=training,
                calibration_output=None,
                folds=(_fold(),),
                evaluated_at=datetime(2026, 2, 1, 17, tzinfo=UTC),
            )

    def test_registered_hash_and_feature_contract_must_match(self) -> None:
        split, registration, training = _fixture(TrainingDatasetPurpose.ANNOTATION_DECISION)
        broken_registration = registration.model_copy(
            update={"feature_names": ("wrong", "columns")}
        )
        with self.assertRaisesRegex(ValueError, "registration feature names"):
            validate_feature_walk_forward(
                split,
                registration=broken_registration,
                training_output=training,
                calibration_output=None,
                folds=(_fold(),),
                evaluated_at=datetime(2026, 2, 1, 17, tzinfo=UTC),
            )

        broken_training = FeatureLightGBMOutput(
            model=training.model,
            metrics=training.metrics,
            predictions=training.predictions,
            manifest=training.manifest,
            model_sha256="e" * 64,
            metrics_sha256=training.metrics_sha256,
            predictions_sha256=training.predictions_sha256,
            manifest_sha256=training.manifest_sha256,
        )
        with self.assertRaisesRegex(ValueError, "training model hash"):
            validate_feature_walk_forward(
                split,
                registration=registration,
                training_output=broken_training,
                calibration_output=None,
                folds=(_fold(),),
                evaluated_at=datetime(2026, 2, 1, 17, tzinfo=UTC),
            )

    def test_report_store_is_immutable_and_detects_tampering(self) -> None:
        split, registration, training = _fixture(TrainingDatasetPurpose.CANDIDATE_SUCCESS)
        with patch(
            "stratgauge.ml.training_feature_walk_forward.helpers_b.import_module",
            side_effect=_fake_import,
        ):
            report = validate_feature_walk_forward(
                split,
                registration=registration,
                training_output=training,
                calibration_output=None,
                folds=(_fold(),),
                evaluated_at=datetime(2026, 2, 1, 17, tzinfo=UTC),
            )
        with TemporaryDirectory() as directory:
            store = FeatureWalkForwardReportStore(directory)
            self.assertEqual(store.write(report), report)
            self.assertEqual(store.write(report), report)
            path = (
                Path(directory)
                / "models"
                / "feature-registry"
                / "walk-forward-reports"
                / f"{report.report_id}.json"
            )
            tampered = report.model_copy(
                update={"evaluated_at": datetime(2026, 2, 2, 17, tzinfo=UTC)}
            )
            path.write_text(tampered.model_dump_json(indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identity"):
                store.load(report.report_id)
            with self.assertRaisesRegex(ValueError, "identity"):
                store.write(tampered)

    def test_missing_lightgbm_is_reported_after_contract_validation(self) -> None:
        split, registration, training = _fixture(TrainingDatasetPurpose.BUY_TIMING)
        with patch(
            "stratgauge.ml.training_feature_walk_forward.helpers_b.import_module",
            side_effect=ModuleNotFoundError("lightgbm"),
        ):
            with self.assertRaises(FeatureWalkForwardUnavailableError):
                validate_feature_walk_forward(
                    split,
                    registration=registration,
                    training_output=training,
                    calibration_output=None,
                    folds=(_fold(),),
                    evaluated_at=datetime(2026, 2, 1, 17, tzinfo=UTC),
                )


def _fake_import(name: str) -> object:
    if name == "lightgbm":
        return FakeLightGBM
    if name == "numpy":
        return FakeNumpy
    raise ModuleNotFoundError(name)


def _fold() -> FeatureWalkForwardFoldSpec:
    return FeatureWalkForwardFoldSpec(
        fold_id="fold-1",
        train_start=date(2026, 1, 1),
        train_end=date(2026, 1, 3),
        validation_start=date(2026, 1, 8),
        validation_end=date(2026, 1, 10),
    )


def _fixture(
    purpose: TrainingDatasetPurpose,
) -> tuple[TrainingFeatureSplitInput, FeatureModelRegistration, FeatureLightGBMOutput]:
    labels = _labels_for(purpose)
    train_targets = labels
    rows: list[SelectionTrainingLabelRow] = []
    for index, target in enumerate(train_targets, start=1):
        rows.append(_label_row(purpose, f"train-{index}", date(2026, 1, index), target, index))
    rows.append(
        _label_row(
            purpose,
            "train-late",
            date(2026, 1, 3),
            labels[0],
            13,
            available_at=datetime(2026, 1, 9, 17, tzinfo=UTC),
        )
    )
    rows.append(_label_row(purpose, "validation-1", date(2026, 1, 8), labels[0], 10))
    rows.append(_label_row(purpose, "test-1", date(2026, 1, 15), labels[-1], 11))
    rows.append(
        _label_row(
            purpose,
            "purged-1",
            date(2026, 1, 3),
            labels[0],
            12,
            available_at=datetime(2026, 1, 8, 17, tzinfo=UTC),
        )
    )
    label_train = tuple(row for row in rows if row.sample_id.startswith("train-"))
    label_validation = (next(row for row in rows if row.sample_id == "validation-1"),)
    label_test = (next(row for row in rows if row.sample_id == "test-1"),)
    purged = (next(row for row in rows if row.sample_id == "purged-1"),)
    feature_train = tuple(_feature_row(row) for row in label_train)
    feature_validation = tuple(_feature_row(row) for row in label_validation)
    feature_test = tuple(_feature_row(row) for row in label_test)
    source_hash = "b" * 64
    split_hash = "c" * 64
    feature_manifest_hash = "f" * 64
    join_report = TrainingFeatureJoinReport(
        purpose=purpose,
        label_contract_id=label_contract_id_for(purpose),
        feature_set_version_id="features-fixture-v1",
        source_label_dataset_sha256=source_hash,
        source_label_row_count=len(rows),
        joined_row_count=len(feature_train) + len(feature_validation) + len(feature_test),
        purged_row_count=len(purged),
        failure_count=0,
        joined_feature_snapshot_ids=tuple(
            row.feature_snapshot_id for row in feature_train + feature_validation + feature_test
        ),
    )
    label_manifest = TrainingLabelSplitManifest(
        purpose=purpose,
        label_contract_id=label_contract_id_for(purpose),
        source_schema_version="fixture-v1",
        source_file_sha256=source_hash,
        prepared_dataset_sha256="d" * 64,
        train_end=date(2026, 1, 3),
        validation_end=date(2026, 1, 10),
        label_available_through=datetime(2026, 1, 31, 17, tzinfo=UTC),
        source_row_count=len(rows),
        skipped_unlabeled_row_count=0,
        filtered_policy_row_count=0,
        row_counts={"train": len(label_train), "validation": 1, "test": 1, "purged": 1},
    )
    readiness = TrainingLabelReadinessReport(
        purpose=purpose,
        label_contract_id=label_contract_id_for(purpose),
        prepared_dataset_sha256="d" * 64,
        minimum_labeled_samples=1,
        source_row_count=len(rows),
        labeled_row_count=len(rows),
        accepted_row_count=len(rows) - 1,
        skipped_unlabeled_row_count=0,
        filtered_policy_row_count=0,
        purged_future_label_count=1,
        row_counts={"train": len(label_train), "validation": 1, "test": 1, "purged": 1},
        target_counts={
            label.value: sum(row.target is label for row in rows[:-1]) for label in labels
        },
        train_target_counts={
            label.value: sum(row.target is label for row in label_train) for label in labels
        },
        duplicate_sample_count=0,
        duplicate_source_row_count=0,
        missing_source_snapshot_count=0,
        eligible=True,
    )
    manifest = TrainingFeatureSplitManifest(
        purpose=purpose,
        label_contract_id=label_contract_id_for(purpose),
        feature_set_version_id="features-fixture-v1",
        label_split_manifest_sha256=split_hash,
        label_readiness_sha256="e" * 64,
        source_label_dataset_sha256=source_hash,
        prepared_label_dataset_sha256="d" * 64,
        row_counts={"train": len(feature_train), "validation": 1, "test": 1, "purged": 1},
        label_row_counts={"train": len(label_train), "validation": 1, "test": 1, "purged": 1},
        partition_sha256={
            name: hashlib.sha256(name.encode()).hexdigest()
            for name in ("train", "validation", "test", "purged")
        },
        label_partition_sha256={
            name: hashlib.sha256(f"label-{name}".encode()).hexdigest()
            for name in ("train", "validation", "test", "purged")
        },
        feature_names=("close", "return_1"),
        join_report_sha256="a" * 64,
    )
    split = TrainingFeatureSplitInput(
        manifest=manifest,
        report=join_report,
        label_split_manifest=label_manifest,
        label_readiness=readiness,
        label_train_rows=label_train,
        label_validation_rows=label_validation,
        label_test_rows=label_test,
        purged_rows=purged,
        train_rows=feature_train,
        validation_rows=feature_validation,
        test_rows=feature_test,
        manifest_sha256=feature_manifest_hash,
    )
    model = FeatureLightGBMModel(
        model_version_id=f"fixture-{purpose.value}",
        purpose=purpose,
        label_contract_id=label_contract_id_for(purpose),
        feature_set_version_id=manifest.feature_set_version_id,
        feature_names=manifest.feature_names,
        class_labels=labels,
        train_row_count=len(feature_train),
        train_target_counts={
            label.value: sum(row.target is label for row in feature_train) for label in labels
        },
        random_seed=0,
        minimum_labeled_samples=1,
        feature_dataset_manifest_sha256=feature_manifest_hash,
        source_label_dataset_sha256=source_hash,
        label_split_manifest_sha256=split_hash,
        artifact_sha256="a" * 64,
        parameters=(
            {
                "objective": "multiclass" if len(labels) == 3 else "binary",
                "num_boost_round": 2,
                **({"num_class": 3} if len(labels) == 3 else {}),
            }
        ),
        row_counts={"train": len(feature_train), "validation": 1, "test": 1},
        purged_row_count=1,
    )
    training = FeatureLightGBMOutput(
        model=model,
        metrics=FeatureLightGBMEvaluationEvidence(
            model_version_id=model.model_version_id,
            purpose=purpose,
            label_contract_id=model.label_contract_id,
            feature_set_version_id=model.feature_set_version_id,
            feature_dataset_manifest_sha256=feature_manifest_hash,
            metrics=(),
        ),
        predictions=(),
        manifest={},
        model_sha256="1" * 64,
        metrics_sha256="2" * 64,
        predictions_sha256="3" * 64,
        manifest_sha256="4" * 64,
    )
    registration = FeatureModelRegistration(
        registration_id=_registration_id(model.model_version_id, None),
        model_version_id=model.model_version_id,
        purpose=purpose,
        label_contract_id=model.label_contract_id,
        feature_set_version_id=model.feature_set_version_id,
        feature_names=model.feature_names,
        class_labels=model.class_labels,
        training_model_sha256=training.model_sha256,
        training_runtime_artifact_sha256=model.artifact_sha256,
        training_metrics_sha256=training.metrics_sha256,
        training_predictions_sha256=training.predictions_sha256,
        training_manifest_sha256=training.manifest_sha256,
        evaluation_sha256=training.metrics_sha256,
        feature_dataset_manifest_sha256=feature_manifest_hash,
        source_label_dataset_sha256=source_hash,
        label_split_manifest_sha256=split_hash,
        registered_at=datetime(2026, 1, 20, 17, tzinfo=UTC),
        registered_by="fixture",
    )
    return split, registration, training


def _label_row(
    purpose: TrainingDatasetPurpose,
    sample_id: str,
    observation_date: date,
    target: TrainingLabel,
    index: int,
    *,
    available_at: datetime | None = None,
) -> SelectionTrainingLabelRow:
    observed_at = datetime.combine(
        observation_date, datetime.min.time().replace(hour=16), tzinfo=UTC
    )
    return SelectionTrainingLabelRow(
        purpose=purpose,
        label_contract_id=label_contract_id_for(purpose),
        sample_id=sample_id,
        source_schema_version="fixture-v1",
        source_row_sha256=("a" * 63) + format(index, "x"),
        source_reference_id=sample_id,
        candidate_id=f"candidate-{sample_id}",
        batch_id="batch-fixture",
        symbol="000001.SZ",
        observation_date=observation_date,
        label_available_at=available_at or observed_at + timedelta(minutes=1),
        target=target,
        observation_at=observed_at,
        tracking_policy_id="tracking-v1"
        if purpose is TrainingDatasetPurpose.CANDIDATE_SUCCESS
        else None,
        outcome_policy_id="outcome-v1"
        if purpose is TrainingDatasetPurpose.CANDIDATE_SUCCESS
        else None,
        window_sessions=3
        if purpose in {TrainingDatasetPurpose.BUY_TIMING, TrainingDatasetPurpose.SELL_TIMING}
        else None,
    )


def _feature_row(row: SelectionTrainingLabelRow) -> SelectionTrainingFeatureRow:
    feature_time = row.observation_at or datetime.combine(
        row.observation_date, datetime.min.time(), tzinfo=UTC
    )
    feature_time = feature_time.replace(hour=15)
    return SelectionTrainingFeatureRow(
        purpose=row.purpose,
        label_contract_id=row.label_contract_id,
        sample_id=row.sample_id,
        source_label_row_sha256=row.source_row_sha256,
        candidate_id=row.candidate_id,
        batch_id=row.batch_id,
        symbol=row.symbol,
        observation_date=row.observation_date,
        observation_at=row.observation_at,
        label_available_at=row.label_available_at,
        target=row.target,
        feature_set_version_id="features-fixture-v1",
        feature_snapshot_id=f"feature-{row.sample_id}",
        feature_as_of=feature_time,
        feature_available_at=feature_time,
        input_session_start=date(2025, 12, 1),
        input_session_end=date(2025, 12, 31),
        source_snapshot_ids=(f"snapshot-{row.sample_id}",),
        features={"close": Decimal("10"), "return_1": Decimal("0.01")},
    )


def _replace_train_row(
    split: TrainingFeatureSplitInput,
    replacement: SelectionTrainingFeatureRow,
) -> TrainingFeatureSplitInput:
    return split.model_copy(
        update={
            "train_rows": tuple(
                replacement if row.sample_id == replacement.sample_id else row
                for row in split.train_rows
            )
        }
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
