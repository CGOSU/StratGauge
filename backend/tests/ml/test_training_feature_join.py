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
    TrainingLabelDatasetSplit,
    TrainingLabelReadinessReport,
    TrainingLabelSplitManifest,
    audit_training_label_split,
    build_training_label_split_manifest,
    prepare_training_label_dataset,
    split_training_label_dataset,
)
from stratgauge.ml.training_feature_join import (
    join_training_features,
    write_training_feature_split,
)


class TrainingFeatureJoinTests(unittest.TestCase):
    def test_joins_latest_safe_snapshot_and_excludes_purged_label(self) -> None:
        content = _annotation_content(
            _annotation("train-discard", date(2026, 1, 1), "discard"),
            _annotation("train-watch", date(2026, 1, 2), "watch"),
            _annotation("future", date(2026, 1, 4), "prepare_buy", recorded_day=7),
            _annotation("validation-watch", date(2026, 1, 8), "watch"),
        )
        label_split, manifest, readiness = _label_split(content, minimum_labeled_samples=3)
        with TemporaryDirectory() as directory:
            store = FeatureSnapshotStore(directory)
            old = _snapshot("feature-old", date(2026, 1, 1), datetime(2026, 1, 1, 9, tzinfo=UTC))
            latest = _snapshot(
                "feature-latest",
                date(2026, 1, 1),
                datetime(2026, 1, 1, 15, tzinfo=UTC),
                value=Decimal("2"),
            )
            train_second = _snapshot(
                "feature-train-second",
                date(2026, 1, 2),
                datetime(2026, 1, 2, 9, tzinfo=UTC),
            )
            validation = _snapshot(
                "feature-validation",
                date(2026, 1, 8),
                datetime(2026, 1, 8, 9, tzinfo=UTC),
            )
            future = _snapshot(
                "feature-future",
                date(2026, 1, 4),
                datetime(2026, 1, 4, 9, tzinfo=UTC),
            )
            for snapshot in (old, latest, train_second, validation, future):
                store.write(snapshot)

            result = join_training_features(
                label_split,
                feature_store=store,
                feature_snapshot_ids=(
                    old.feature_snapshot_id,
                    latest.feature_snapshot_id,
                    train_second.feature_snapshot_id,
                    validation.feature_snapshot_id,
                    future.feature_snapshot_id,
                ),
                feature_set_version_id="features-fixture-v1",
                label_split_manifest=manifest,
                label_readiness=readiness,
            )

            self.assertEqual(result.report.failure_count, 0)
            self.assertEqual(result.report.purged_row_count, 1)
            self.assertEqual(
                result.report.joined_feature_snapshot_ids,
                ("feature-latest", "feature-train-second", "feature-validation"),
            )
            self.assertEqual(result.train_rows[0].feature_snapshot_id, "feature-latest")
            self.assertEqual(result.train_rows[0].features["close"], Decimal("2"))
            self.assertEqual(result.purged_rows[0].sample_id.startswith("training-"), True)

    def test_reviewed_at_rejects_feature_snapshot_recorded_after_review(self) -> None:
        content = _annotation_content(_annotation("review", date(2026, 1, 1), "watch"))
        label_split, manifest, readiness = _label_split(content, minimum_labeled_samples=1)
        with TemporaryDirectory() as directory:
            store = FeatureSnapshotStore(directory)
            snapshot = _snapshot(
                "feature-after-review",
                date(2026, 1, 1),
                datetime(2026, 1, 1, 17, tzinfo=UTC),
            )
            store.write(snapshot)

            result = join_training_features(
                label_split,
                feature_store=store,
                feature_snapshot_ids=(snapshot.feature_snapshot_id,),
                feature_set_version_id="features-fixture-v1",
                label_split_manifest=manifest,
                label_readiness=readiness,
            )

            self.assertEqual(result.train_rows, ())
            self.assertEqual(result.report.failure_count, 1)
            self.assertIn("no safe feature snapshot", result.report.failures[0][1])

    def test_duplicate_safe_latest_snapshots_are_reported_as_ambiguous(self) -> None:
        content = _annotation_content(_annotation("review", date(2026, 1, 1), "watch"))
        label_split, manifest, readiness = _label_split(content, minimum_labeled_samples=1)
        with TemporaryDirectory() as directory:
            store = FeatureSnapshotStore(directory)
            first = _snapshot(
                "feature-a",
                date(2026, 1, 1),
                datetime(2026, 1, 1, 9, tzinfo=UTC),
            )
            second = _snapshot(
                "feature-b",
                date(2026, 1, 1),
                datetime(2026, 1, 1, 9, tzinfo=UTC),
            )
            store.write(first)
            store.write(second)

            result = join_training_features(
                label_split,
                feature_store=store,
                feature_snapshot_ids=(first.feature_snapshot_id, second.feature_snapshot_id),
                feature_set_version_id="features-fixture-v1",
                label_split_manifest=manifest,
                label_readiness=readiness,
            )

            self.assertEqual(result.report.failure_count, 1)
            self.assertIn("multiple feature snapshots", result.report.failures[0][1])

    def test_mismatched_feature_set_is_rejected_before_join(self) -> None:
        content = _annotation_content(_annotation("review", date(2026, 1, 1), "watch"))
        label_split, manifest, readiness = _label_split(content, minimum_labeled_samples=1)
        with TemporaryDirectory() as directory:
            store = FeatureSnapshotStore(directory)
            snapshot = _snapshot(
                "feature-other-set",
                date(2026, 1, 1),
                datetime(2026, 1, 1, 9, tzinfo=UTC),
                feature_set_version_id="features-other-v1",
            )
            store.write(snapshot)

            with self.assertRaisesRegex(ValueError, "different feature set"):
                join_training_features(
                    label_split,
                    feature_store=store,
                    feature_snapshot_ids=(snapshot.feature_snapshot_id,),
                    feature_set_version_id="features-fixture-v1",
                    label_split_manifest=manifest,
                    label_readiness=readiness,
                )

    def test_join_writer_is_non_overwriting(self) -> None:
        content = _annotation_content(
            _annotation("train-discard", date(2026, 1, 1), "discard"),
            _annotation("train-watch", date(2026, 1, 2), "watch"),
            _annotation("validation-watch", date(2026, 1, 8), "watch"),
        )
        label_split, manifest, readiness = _label_split(content, minimum_labeled_samples=2)
        with TemporaryDirectory() as directory:
            store = FeatureSnapshotStore(directory)
            snapshots = tuple(
                _snapshot(
                    f"feature-{index}",
                    row.observation_date,
                    datetime.combine(row.observation_date, datetime.min.time(), tzinfo=UTC),
                )
                for index, row in enumerate(label_split.train_rows + label_split.validation_rows)
            )
            for snapshot in snapshots:
                store.write(snapshot)
            result = join_training_features(
                label_split,
                feature_store=store,
                feature_snapshot_ids=tuple(snapshot.feature_snapshot_id for snapshot in snapshots),
                feature_set_version_id="features-fixture-v1",
                label_split_manifest=manifest,
                label_readiness=readiness,
            )
            output = Path(directory) / "joined"
            write_training_feature_split(output, result)
            self.assertTrue((output / "train.jsonl").exists())
            with self.assertRaises(FileExistsError):
                write_training_feature_split(output, result)


def _label_split(
    content: bytes,
    *,
    minimum_labeled_samples: int,
) -> tuple[TrainingLabelDatasetSplit, TrainingLabelSplitManifest, TrainingLabelReadinessReport]:
    prepared = prepare_training_label_dataset(
        content,
        purpose=TrainingDatasetPurpose.ANNOTATION_DECISION,
    )
    split = split_training_label_dataset(
        prepared,
        train_end=date(2026, 1, 5),
        validation_end=date(2026, 1, 10),
        label_available_through=datetime(2026, 1, 20, 17, tzinfo=UTC),
    )
    report = audit_training_label_split(split, minimum_labeled_samples=minimum_labeled_samples)
    return split, build_training_label_split_manifest(split), report


def _annotation_content(*rows: SelectionAnnotationDatasetRow) -> bytes:
    return serialize_annotation_dataset(rows).encode()


def _annotation(
    annotation_id: str,
    reviewed_date: date,
    decision: str,
    recorded_day: int | None = None,
) -> SelectionAnnotationDatasetRow:
    recorded_at = datetime(
        reviewed_date.year,
        reviewed_date.month,
        recorded_day if recorded_day is not None else reviewed_date.day,
        16,
        tzinfo=UTC,
    )
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
        annotation_reviewed_at=datetime.combine(
            reviewed_date,
            datetime.min.time().replace(hour=16),
            tzinfo=UTC,
        ).isoformat(),
        annotation_recorded_at=recorded_at.replace(hour=16, minute=1).isoformat(),
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


def _snapshot(
    snapshot_id: str,
    selection_date: date,
    as_of: datetime,
    *,
    value: Decimal = Decimal("1"),
    feature_set_version_id: str = "features-fixture-v1",
) -> FeatureSnapshot:
    return FeatureSnapshot(
        feature_snapshot_id=snapshot_id,
        feature_set_version_id=feature_set_version_id,
        security_id="security-000001.SZ",
        symbol="000001.SZ",
        selection_date=selection_date,
        as_of=as_of,
        available_at=as_of,
        input_session_start=date(2025, 12, 1),
        input_session_end=date(2025, 12, 31),
        source_snapshot_ids=(f"market-{snapshot_id}",),
        values={"close": value},
    )


if __name__ == "__main__":
    unittest.main()
