import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from stratgauge.application.ml import (
    JoinTrainingFeaturesRequest,
    PrepareTrainingDatasetRequest,
    TrainFeatureBaselineRequest,
    TrainFeatureLightGBMRequest,
    TrainingDatasetPurpose,
    TrainLabelBaselineRequest,
    run_join_training_features,
    run_prepare_training_dataset,
    run_train_feature_baseline,
    run_train_feature_lightgbm,
    run_train_label_baseline,
)


class MLTrainingApplicationTests(unittest.TestCase):
    def test_prepare_workflow_keeps_all_label_stages_explicit(self) -> None:
        with TemporaryDirectory() as directory:
            input_path = Path(directory) / "source.csv"
            input_path.write_bytes(b"source")
            prepared = object()
            split = object()
            report = object()
            manifest = object()
            with (
                patch(
                    "stratgauge.application.ml.training.prepare_training_label_dataset",
                    return_value=prepared,
                ) as prepare,
                patch(
                    "stratgauge.application.ml.training.split_training_label_dataset",
                    return_value=split,
                ) as split_dataset,
                patch(
                    "stratgauge.application.ml.training.audit_training_label_split",
                    return_value=report,
                ) as audit,
                patch(
                    "stratgauge.application.ml.training.write_training_label_split",
                    return_value=manifest,
                ) as write,
            ):
                result = run_prepare_training_dataset(
                    PrepareTrainingDatasetRequest(
                        input_path=input_path,
                        purpose=TrainingDatasetPurpose.CANDIDATE_SUCCESS,
                        output_dir=Path(directory) / "labels",
                        train_end=date(2025, 1, 1),
                        validation_end=date(2025, 2, 1),
                        label_available_through=datetime.fromisoformat("2025-03-01T00:00:00+00:00"),
                        minimum_labeled_samples=10,
                        tracking_policy_id="tracking-v1",
                    )
                )

            prepare.assert_called_once_with(
                b"source",
                purpose=TrainingDatasetPurpose.CANDIDATE_SUCCESS,
                tracking_policy_id="tracking-v1",
            )
            split_dataset.assert_called_once_with(
                prepared,
                train_end=date(2025, 1, 1),
                validation_end=date(2025, 2, 1),
                label_available_through=datetime.fromisoformat("2025-03-01T00:00:00+00:00"),
            )
            audit.assert_called_once_with(split, minimum_labeled_samples=10)
            write.assert_called_once_with(Path(directory) / "labels", split, report)
            self.assertIs(result.prepared, prepared)
            self.assertIs(result.split, split)
            self.assertIs(result.report, report)
            self.assertIs(result.manifest, manifest)

    def test_join_workflow_strips_snapshot_ids_and_writes_one_stage(self) -> None:
        label_input = SimpleNamespace(split=object(), manifest=object(), readiness=object())
        joined = object()
        store = object()
        with (
            patch(
                "stratgauge.application.ml.training.read_training_label_split",
                return_value=label_input,
            ) as read,
            patch(
                "stratgauge.application.ml.training.FeatureSnapshotStore",
                return_value=store,
            ),
            patch(
                "stratgauge.application.ml.training.join_training_features",
                return_value=joined,
            ) as join,
            patch("stratgauge.application.ml.training.write_training_feature_split") as write,
        ):
            result = run_join_training_features(
                JoinTrainingFeaturesRequest(
                    labels_dir=Path("labels"),
                    feature_root=Path("features"),
                    feature_set_version_id="features-v1",
                    feature_snapshot_ids=(" snapshot-1 ", "", "snapshot-2"),
                    output_dir=Path("joined"),
                )
            )

        read.assert_called_once_with(Path("labels"))
        join.assert_called_once_with(
            label_input.split,
            feature_store=store,
            feature_snapshot_ids=("snapshot-1", "snapshot-2"),
            feature_set_version_id="features-v1",
            label_split_manifest=label_input.manifest,
            label_readiness=label_input.readiness,
        )
        write.assert_called_once_with(Path("joined"), joined)
        self.assertIs(result.split, joined)

    def test_label_baseline_workflow_reads_trains_and_writes_once(self) -> None:
        split_input = object()
        trained = object()
        with (
            patch(
                "stratgauge.application.ml.training.read_training_label_split",
                return_value=split_input,
            ) as read,
            patch(
                "stratgauge.application.ml.training.train_constant_label_baseline",
                return_value=trained,
            ) as train,
            patch("stratgauge.application.ml.training.write_constant_label_baseline") as write,
        ):
            result = run_train_label_baseline(
                TrainLabelBaselineRequest(
                    input_dir=Path("labels"),
                    output_dir=Path("model"),
                    random_seed=7,
                    minimum_labeled_samples=20,
                )
            )

        read.assert_called_once_with(Path("labels"))
        train.assert_called_once_with(
            split_input,
            random_seed=7,
            minimum_labeled_samples=20,
        )
        write.assert_called_once_with(Path("model"), trained)
        self.assertIs(result.result, trained)

    def test_feature_baseline_workflow_reads_trains_and_writes_once(self) -> None:
        split_input = object()
        trained = object()
        with (
            patch(
                "stratgauge.application.ml.training.read_training_feature_split",
                return_value=split_input,
            ) as read,
            patch(
                "stratgauge.application.ml.training.train_constant_feature_baseline",
                return_value=trained,
            ) as train,
            patch("stratgauge.application.ml.training.write_constant_feature_baseline") as write,
        ):
            result = run_train_feature_baseline(
                TrainFeatureBaselineRequest(
                    input_dir=Path("features"),
                    output_dir=Path("model"),
                    random_seed=8,
                    minimum_labeled_samples=30,
                )
            )

        read.assert_called_once_with(Path("features"))
        train.assert_called_once_with(
            split_input,
            random_seed=8,
            minimum_labeled_samples=30,
        )
        write.assert_called_once_with(Path("model"), trained)
        self.assertIs(result.result, trained)

    def test_lightgbm_workflow_reads_trains_and_writes_once(self) -> None:
        split_input = object()
        trained = object()
        with (
            patch(
                "stratgauge.application.ml.training.read_training_feature_split",
                return_value=split_input,
            ) as read,
            patch(
                "stratgauge.application.ml.training.train_feature_lightgbm",
                return_value=trained,
            ) as train,
            patch("stratgauge.application.ml.training.write_feature_lightgbm_result") as write,
        ):
            result = run_train_feature_lightgbm(
                TrainFeatureLightGBMRequest(
                    input_dir=Path("features"),
                    output_dir=Path("model"),
                    random_seed=9,
                    minimum_labeled_samples=40,
                )
            )

        read.assert_called_once_with(Path("features"))
        train.assert_called_once_with(
            split_input,
            random_seed=9,
            minimum_labeled_samples=40,
        )
        write.assert_called_once_with(Path("model"), trained)
        self.assertIs(result.result, trained)


if __name__ == "__main__":
    unittest.main()
