import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.backtest.selection_dataset import (
    SelectionAnnotationDatasetRow,
    serialize_annotation_dataset,
)
from stratgauge.domain.selections import (
    SELECTION_ANNOTATION_TAXONOMY_VERSION,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
)
from stratgauge.features.contract import FeatureSnapshot
from stratgauge.features.store import FeatureSnapshotStore
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    audit_training_label_split,
    build_training_label_split_manifest,
    prepare_training_label_dataset,
    split_training_label_dataset,
)
from stratgauge.ml.training_feature_baseline import (
    TrainingFeatureSplitInput,
    read_training_feature_split,
    train_constant_feature_baseline,
    write_constant_feature_baseline,
)
from stratgauge.ml.training_feature_join import (
    TrainingFeatureDatasetSplit,
    join_training_features,
    write_training_feature_split,
)


class TrainingFeatureBaselineTests(unittest.TestCase):
    def test_baseline_consumes_feature_rows_and_retains_feature_identity(self) -> None:
        split_input = _feature_split_input(
            _annotation("train-discard", date(2026, 1, 1), "discard"),
            _annotation("train-watch", date(2026, 1, 2), "watch"),
            _annotation("validation-watch", date(2026, 1, 8), "watch"),
        )

        result = train_constant_feature_baseline(split_input, minimum_labeled_samples=2)

        self.assertEqual(result.model.model_type, "constant_label_distribution")
        self.assertEqual(result.model.feature_set_version_id, "features-fixture-v1")
        self.assertEqual(result.model.feature_names, ("close", "return_1"))
        self.assertEqual(result.model.train_target_counts["discard"], 1)
        self.assertEqual(result.model.train_target_counts["watch"], 1)
        self.assertEqual(result.predictions[0].feature_snapshot_id, "feature-2026-01-08")

    def test_manifest_partition_tampering_is_rejected(self) -> None:
        split_input = _feature_split_input(
            _annotation("train-discard", date(2026, 1, 1), "discard"),
            _annotation("train-watch", date(2026, 1, 2), "watch"),
            _annotation("validation-watch", date(2026, 1, 8), "watch"),
        )
        with TemporaryDirectory() as directory:
            output = Path(directory) / "joined"
            write_training_feature_split(output, _split_from_input(split_input))
            train_path = output / "train.jsonl"
            content = train_path.read_text(encoding="utf-8")
            train_path.write_text(content.replace('"close":"1"', '"close":"9"'), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "hash does not match train"):
                read_training_feature_split(output)

    def test_label_partition_tampering_is_rejected(self) -> None:
        split_input = _feature_split_input(
            _annotation("train-discard", date(2026, 1, 1), "discard"),
            _annotation("train-watch", date(2026, 1, 2), "watch"),
            _annotation("validation-watch", date(2026, 1, 8), "watch"),
        )
        with TemporaryDirectory() as directory:
            output = Path(directory) / "joined"
            write_training_feature_split(output, _split_from_input(split_input))
            label_path = output / "label-train.jsonl"
            label_path.write_text(
                label_path.read_text(encoding="utf-8").replace("discard", "watch"), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "label hash does not match train"):
                read_training_feature_split(output)

    def test_feature_columns_must_match_across_partitions(self) -> None:
        split_input = _feature_split_input(
            _annotation("train-discard", date(2026, 1, 1), "discard"),
            _annotation("train-watch", date(2026, 1, 2), "watch"),
            _annotation("validation-watch", date(2026, 1, 8), "watch"),
        )
        with TemporaryDirectory() as directory:
            output = Path(directory) / "joined"
            write_training_feature_split(output, _split_from_input(split_input))
            validation_path = output / "validation.jsonl"
            content = validation_path.read_text(encoding="utf-8")
            validation_path.write_text(
                content.replace('"return_1":"0.1"', '"other":"0.1"'), encoding="utf-8"
            )

            with self.assertRaises(ValueError):
                read_training_feature_split(output)

    def test_join_failure_blocks_feature_baseline(self) -> None:
        split_input = _feature_split_input(
            _annotation("train-discard", date(2026, 1, 1), "discard"),
            _annotation("train-watch", date(2026, 1, 2), "watch"),
            _annotation("validation-watch", date(2026, 1, 8), "watch"),
            missing_snapshot_for=date(2026, 1, 2),
        )

        with self.assertRaisesRegex(ValueError, "contains failures"):
            train_constant_feature_baseline(split_input, minimum_labeled_samples=2)

    def test_feature_baseline_writer_is_non_overwriting(self) -> None:
        split_input = _feature_split_input(
            _annotation("train-discard", date(2026, 1, 1), "discard"),
            _annotation("train-watch", date(2026, 1, 2), "watch"),
            _annotation("validation-watch", date(2026, 1, 8), "watch"),
        )
        result = train_constant_feature_baseline(split_input, minimum_labeled_samples=2)

        with TemporaryDirectory() as directory:
            output = Path(directory) / "model"
            write_constant_feature_baseline(output, result)
            self.assertTrue((output / "model.json").exists())
            self.assertTrue((output / "metrics.json").exists())
            self.assertTrue((output / "predictions.jsonl").exists())
            with self.assertRaises(FileExistsError):
                write_constant_feature_baseline(output, result)


def _feature_split_input(
    *annotations: SelectionAnnotationDatasetRow,
    missing_snapshot_for: date | None = None,
) -> TrainingFeatureSplitInput:
    content = serialize_annotation_dataset(annotations).encode()
    prepared = prepare_training_label_dataset(
        content,
        purpose=TrainingDatasetPurpose.ANNOTATION_DECISION,
    )
    label_split = split_training_label_dataset(
        prepared,
        train_end=date(2026, 1, 5),
        validation_end=date(2026, 1, 10),
        label_available_through=datetime(2026, 1, 20, 17, tzinfo=UTC),
    )
    label_manifest = build_training_label_split_manifest(label_split)
    label_readiness = audit_training_label_split(label_split, minimum_labeled_samples=2)
    with TemporaryDirectory() as directory:
        store = FeatureSnapshotStore(directory)
        snapshots = tuple(
            _snapshot(row.observation_date)
            for row in label_split.train_rows + label_split.validation_rows
            if row.observation_date != missing_snapshot_for
        )
        for snapshot in snapshots:
            store.write(snapshot)
        joined = join_training_features(
            label_split,
            feature_store=store,
            feature_snapshot_ids=tuple(snapshot.feature_snapshot_id for snapshot in snapshots),
            feature_set_version_id="features-fixture-v1",
            label_split_manifest=label_manifest,
            label_readiness=label_readiness,
        )
        output = Path(directory) / "joined"
        write_training_feature_split(output, joined)
        persisted = read_training_feature_split(output)
        return persisted


def _split_from_input(split_input: TrainingFeatureSplitInput) -> TrainingFeatureDatasetSplit:
    return TrainingFeatureDatasetSplit(
        report=split_input.report,
        label_split_manifest=split_input.label_split_manifest,
        label_readiness=split_input.label_readiness,
        train_rows=split_input.train_rows,
        validation_rows=split_input.validation_rows,
        test_rows=split_input.test_rows,
        purged_rows=split_input.purged_rows,
        label_train_rows=split_input.label_train_rows,
        label_validation_rows=split_input.label_validation_rows,
        label_test_rows=split_input.label_test_rows,
    )


def _annotation(
    annotation_id: str,
    reviewed_date: date,
    decision: str,
) -> SelectionAnnotationDatasetRow:
    reviewed_at = datetime.combine(reviewed_date, datetime.min.time().replace(hour=16), tzinfo=UTC)
    return SelectionAnnotationDatasetRow(
        annotation_id=annotation_id,
        candidate_id=f"batch-1:{annotation_id}.SZ",
        batch_id="batch-1",
        symbol="000001.SZ",
        selection_date=reviewed_date.isoformat(),
        source_filename=None,
        formula_id="formula-1",
        formula_name="fixture",
        formula_version="v1",
        annotation_reviewed_at=reviewed_at.isoformat(),
        annotation_recorded_at=reviewed_at.replace(minute=1).isoformat(),
        overall_score=4,
        outlook=SelectionAnnotationOutlook.BULLISH,
        decision=SelectionAnnotationDecision(decision),
        position_tags=(),
        pattern_tags=(),
        price_volume_tags=(),
        sector_tags=(),
        taxonomy_version=SELECTION_ANNOTATION_TAXONOMY_VERSION,
        annotation_note=None,
        tracking_policy_id=None,
        outcome_policy_id=None,
        cost_schedule_id=None,
        horizon_sessions=None,
        position_notional=None,
        outcome_status=None,
        outcome_evaluated_at=None,
        net_return=None,
        source_snapshot_ids=(),
    )


def _snapshot(observation_date: date) -> FeatureSnapshot:
    return FeatureSnapshot(
        feature_snapshot_id=f"feature-{observation_date.isoformat()}",
        feature_set_version_id="features-fixture-v1",
        security_id="security-000001.SZ",
        symbol="000001.SZ",
        selection_date=observation_date,
        as_of=datetime.combine(observation_date, datetime.min.time().replace(hour=15), tzinfo=UTC),
        available_at=datetime.combine(
            observation_date, datetime.min.time().replace(hour=15), tzinfo=UTC
        ),
        input_session_start=date(2025, 12, 1),
        input_session_end=date(2025, 12, 31),
        source_snapshot_ids=(f"market-{observation_date.isoformat()}",),
        values={"close": Decimal("1"), "return_1": Decimal("0.1")},
    )


if __name__ == "__main__":
    unittest.main()
