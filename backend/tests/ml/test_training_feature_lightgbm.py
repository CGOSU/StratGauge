import json
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stratgauge.backtest.selection_dataset import (
    SelectionAnnotationDatasetRow,
    SelectionDatasetRow,
    serialize_annotation_dataset,
    serialize_selection_dataset,
)
from stratgauge.domain.selections import (
    SELECTION_ANNOTATION_TAXONOMY_VERSION,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
    SelectionOutcomeStatus,
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
)
from stratgauge.ml.training_feature_join import join_training_features, write_training_feature_split
from stratgauge.ml.training_feature_lightgbm import (
    FeatureLightGBMUnavailableError,
    train_feature_lightgbm,
    write_feature_lightgbm_result,
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
    def __init__(self, *, multiclass: bool) -> None:
        self.multiclass = multiclass

    def predict(self, matrix: list[list[float]]) -> list[float] | list[list[float]]:
        if self.multiclass:
            return [[0.2, 0.5, 0.3] for _ in matrix]
        return [0.75 for _ in matrix]

    def model_to_string(self) -> str:
        return "fake-purpose-lightgbm-model"


class FakeLightGBM:
    Dataset = FakeDataset
    last_parameters: dict[str, object] = {}

    @staticmethod
    def train(
        parameters: dict[str, object],
        dataset: FakeDataset,
        *,
        num_boost_round: int,
    ) -> FakeBooster:
        FakeLightGBM.last_parameters = parameters
        if num_boost_round != 50:
            raise AssertionError("fixture expects fixed boost rounds")
        if dataset.feature_name != ["close", "return_1"]:
            raise AssertionError("fixture expects stable feature columns")
        return FakeBooster(multiclass=parameters["objective"] == "multiclass")


class FakeNumpy:
    @staticmethod
    def asarray(matrix: list[list[float]], *, dtype: type[float]) -> list[list[float]]:
        return matrix


class TrainingFeatureLightGBMTests(unittest.TestCase):
    def test_annotation_decision_uses_three_class_lightgbm_and_feature_lineage(self) -> None:
        split_input = _annotation_split_input(
            _annotation("train-discard", date(2026, 1, 1), "discard"),
            _annotation("train-watch", date(2026, 1, 2), "watch"),
            _annotation("train-prepare", date(2026, 1, 3), "prepare_buy"),
            _annotation("validation-watch", date(2026, 1, 8), "watch"),
            _annotation("test-prepare", date(2026, 1, 15), "prepare_buy"),
        )

        with patch(
            "stratgauge.ml.training_feature_lightgbm.import_module",
            side_effect=_fake_import,
        ):
            result = train_feature_lightgbm(split_input, minimum_labeled_samples=5, random_seed=7)

        self.assertEqual(result.model.purpose, TrainingDatasetPurpose.ANNOTATION_DECISION)
        self.assertEqual(result.model.class_labels, ("discard", "watch", "prepare_buy"))
        self.assertEqual(FakeLightGBM.last_parameters["objective"], "multiclass")
        self.assertEqual(FakeLightGBM.last_parameters["num_class"], 3)
        self.assertEqual(result.model.train_target_counts["prepare_buy"], 1)
        self.assertEqual(result.predictions[0].predicted_label.value, "watch")
        self.assertEqual(result.predictions[0].feature_snapshot_id, "feature-2026-01-08")
        self.assertEqual(
            result.predictions[0].predicted_probabilities,
            {"discard": Decimal("0.2"), "watch": Decimal("0.5"), "prepare_buy": Decimal("0.3")},
        )

        with TemporaryDirectory() as directory:
            output = Path(directory) / "model"
            write_feature_lightgbm_result(output, result)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["calibrated"])
            self.assertEqual(manifest["purpose"], "annotation_decision")
            self.assertTrue((output / "model.txt").exists())
            with self.assertRaises(FileExistsError):
                write_feature_lightgbm_result(output, result)

    def test_candidate_success_uses_binary_lightgbm_and_keeps_purged_out(self) -> None:
        split_input = _candidate_split_input(
            _candidate("train-win", date(2026, 1, 1), SelectionOutcomeStatus.WIN, 4),
            _candidate("train-loss", date(2026, 1, 2), SelectionOutcomeStatus.LOSS, 5),
            _candidate("validation-loss", date(2026, 1, 8), SelectionOutcomeStatus.LOSS, 9),
            _candidate("test-win", date(2026, 1, 15), SelectionOutcomeStatus.WIN, 16),
        )

        with patch(
            "stratgauge.ml.training_feature_lightgbm.import_module",
            side_effect=_fake_import,
        ):
            result = train_feature_lightgbm(split_input, minimum_labeled_samples=4)

        self.assertEqual(result.model.purpose, TrainingDatasetPurpose.CANDIDATE_SUCCESS)
        self.assertEqual(FakeLightGBM.last_parameters["objective"], "binary")
        self.assertNotIn("num_class", FakeLightGBM.last_parameters)
        self.assertEqual(result.model.purged_row_count, 0)
        self.assertEqual(result.model.row_counts, {"train": 2, "validation": 1, "test": 1})
        self.assertEqual(result.metrics[1].row_count, 1)
        self.assertEqual(result.predictions[0].predicted_probabilities["success"], Decimal("0.75"))

    def test_join_failure_blocks_training_before_optional_dependency_load(self) -> None:
        split_input = _annotation_split_input(
            _annotation("train-discard", date(2026, 1, 1), "discard"),
            _annotation("train-watch", date(2026, 1, 2), "watch"),
            _annotation("train-prepare", date(2026, 1, 3), "prepare_buy"),
            _annotation("validation-watch", date(2026, 1, 8), "watch"),
            missing_snapshot_for=date(2026, 1, 2),
        )

        with patch(
            "stratgauge.ml.training_feature_lightgbm.import_module",
            side_effect=ModuleNotFoundError("lightgbm"),
        ):
            with self.assertRaisesRegex(ValueError, "contains failures"):
                train_feature_lightgbm(split_input, minimum_labeled_samples=4)

    def test_missing_optional_dependency_is_explicit_after_input_validation(self) -> None:
        split_input = _candidate_split_input(
            _candidate("train-win", date(2026, 1, 1), SelectionOutcomeStatus.WIN, 4),
            _candidate("train-loss", date(2026, 1, 2), SelectionOutcomeStatus.LOSS, 5),
            _candidate("validation-loss", date(2026, 1, 8), SelectionOutcomeStatus.LOSS, 9),
        )

        with patch(
            "stratgauge.ml.training_feature_lightgbm.import_module",
            side_effect=ModuleNotFoundError("lightgbm"),
        ):
            with self.assertRaises(FeatureLightGBMUnavailableError):
                train_feature_lightgbm(split_input, minimum_labeled_samples=3)


def _fake_import(name: str) -> object:
    if name == "lightgbm":
        return FakeLightGBM
    if name == "numpy":
        return FakeNumpy
    raise ModuleNotFoundError(name)


def _annotation_split_input(
    *rows: SelectionAnnotationDatasetRow,
    missing_snapshot_for: date | None = None,
) -> TrainingFeatureSplitInput:
    return _joined_split(
        serialize_annotation_dataset(rows).encode("utf-8"),
        TrainingDatasetPurpose.ANNOTATION_DECISION,
        missing_snapshot_for=missing_snapshot_for,
    )


def _candidate_split_input(
    *rows: SelectionDatasetRow,
    missing_snapshot_for: date | None = None,
) -> TrainingFeatureSplitInput:
    return _joined_split(
        serialize_selection_dataset(rows).encode("utf-8"),
        TrainingDatasetPurpose.CANDIDATE_SUCCESS,
        missing_snapshot_for=missing_snapshot_for,
    )


def _joined_split(
    content: bytes,
    purpose: TrainingDatasetPurpose,
    *,
    missing_snapshot_for: date | None,
) -> TrainingFeatureSplitInput:
    prepared = prepare_training_label_dataset(content, purpose=purpose)
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
        label_rows = label_split.train_rows + label_split.validation_rows + label_split.test_rows
        snapshots = tuple(
            _snapshot(row.observation_date)
            for row in label_rows
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
        return read_training_feature_split(output)


def _annotation(
    annotation_id: str,
    reviewed_date: date,
    decision: str,
) -> SelectionAnnotationDatasetRow:
    reviewed_at = datetime.combine(
        reviewed_date,
        datetime.min.time().replace(hour=16),
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


def _candidate(
    candidate_id: str,
    selection_date: date,
    status: SelectionOutcomeStatus,
    evaluated_day: int,
) -> SelectionDatasetRow:
    return SelectionDatasetRow(
        candidate_id=candidate_id,
        batch_id="batch-1",
        symbol="000001.SZ",
        selection_date=selection_date.isoformat(),
        source_filename=None,
        formula_id="formula-1",
        formula_name="fixture",
        formula_version="v1",
        manual_decision=None,
        manual_labeled_at=None,
        tracking_policy_id="tracking-v1",
        outcome_policy_id="outcome-v1",
        cost_schedule_id="cost-v1",
        horizon_sessions=10,
        position_notional="10000",
        outcome_status=status,
        outcome_evaluated_at=datetime(2026, 1, evaluated_day, 17, tzinfo=UTC).isoformat(),
        net_return="0.01" if status is SelectionOutcomeStatus.WIN else "-0.01",
        source_snapshot_ids=(f"market-{candidate_id}",),
    )


def _snapshot(observation_date: date) -> FeatureSnapshot:
    feature_time = datetime.combine(
        observation_date,
        datetime.min.time().replace(hour=15),
        tzinfo=UTC,
    )
    return FeatureSnapshot(
        feature_snapshot_id=f"feature-{observation_date.isoformat()}",
        feature_set_version_id="features-fixture-v1",
        security_id="security-000001.SZ",
        symbol="000001.SZ",
        selection_date=observation_date,
        as_of=feature_time,
        available_at=feature_time,
        input_session_start=date(2025, 12, 1),
        input_session_end=date(2025, 12, 31),
        source_snapshot_ids=(f"market-{observation_date.isoformat()}",),
        values={"close": Decimal("1"), "return_1": Decimal("0.1")},
    )


if __name__ == "__main__":
    unittest.main()
