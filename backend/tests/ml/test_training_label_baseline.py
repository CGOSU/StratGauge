import json
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.backtest.selection_dataset import (
    SelectionAnnotationDatasetRow,
    serialize_annotation_dataset,
)
from stratgauge.backtest.selection_ml_datasets import (
    SelectionBuyTimingRow,
    serialize_buy_timing_dataset,
)
from stratgauge.domain.selections import (
    SELECTION_ANNOTATION_TAXONOMY_VERSION,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
)
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    audit_training_label_split,
    build_training_label_split_manifest,
    prepare_training_label_dataset,
    split_training_label_dataset,
    write_training_label_split,
)
from stratgauge.ml.training_label_baseline import (
    TrainingLabelSplitInput,
    read_training_label_split,
    train_constant_label_baseline,
    write_constant_label_baseline,
)


class TrainingLabelBaselineTests(unittest.TestCase):
    def test_three_class_annotation_baseline_uses_train_distribution_only(self) -> None:
        content = serialize_annotation_dataset(
            (
                _annotation("train-discard", date(2026, 1, 1), "discard"),
                _annotation("train-watch", date(2026, 1, 2), "watch"),
                _annotation("train-watch-2", date(2026, 1, 3), "watch"),
                _annotation("validation-prepare", date(2026, 1, 8), "prepare_buy"),
                _annotation("test-discard", date(2026, 1, 15), "discard"),
                _annotation("future", date(2026, 1, 4), "prepare_buy", recorded_day=7),
            )
        ).encode()
        split_input = _split_input(content, minimum_labeled_samples=4)

        result = train_constant_label_baseline(split_input, minimum_labeled_samples=4)

        self.assertEqual(result.model.model_type, "constant_label_distribution")
        self.assertEqual(result.model.purpose, TrainingDatasetPurpose.ANNOTATION_DECISION)
        self.assertEqual(result.model.train_target_counts["discard"], 1)
        self.assertEqual(result.model.train_target_counts["watch"], 2)
        self.assertEqual(result.model.train_target_counts["prepare_buy"], 0)
        self.assertEqual(len(result.predictions), 2)
        self.assertEqual(result.predictions[0].predicted_label.value, "watch")
        validation = result.metrics[1]
        self.assertEqual(validation.row_count, 1)
        self.assertEqual(validation.target_counts["prepare_buy"], 1)

    def test_purged_rows_are_never_predicted_or_counted(self) -> None:
        content = serialize_annotation_dataset(
            (
                _annotation("train-discard", date(2026, 1, 1), "discard"),
                _annotation("train-watch", date(2026, 1, 2), "watch"),
                _annotation("future", date(2026, 1, 4), "prepare_buy", recorded_day=7),
                _annotation("validation-watch", date(2026, 1, 8), "watch"),
            )
        ).encode()
        split_input = _split_input(content, minimum_labeled_samples=3)

        result = train_constant_label_baseline(split_input, minimum_labeled_samples=3)

        self.assertEqual(result.row_counts["purged"], 1)
        self.assertNotIn(
            "future",
            {prediction.sample_id for prediction in result.predictions},
        )
        self.assertEqual(sum(metric.row_count for metric in result.metrics), 3)

    def test_binary_buy_timing_baseline_uses_its_own_label_contract(self) -> None:
        content = serialize_buy_timing_dataset(
            (
                _buy_timing("buy-yes", date(2026, 1, 1), True, 2),
                _buy_timing("buy-no", date(2026, 1, 2), False, 5),
                _buy_timing("buy-validation", date(2026, 1, 8), True, 9),
            )
        ).encode()
        split_input = _split_input(
            content,
            purpose=TrainingDatasetPurpose.BUY_TIMING,
            minimum_labeled_samples=2,
        )

        result = train_constant_label_baseline(split_input, minimum_labeled_samples=2)

        self.assertEqual(result.model.label_contract_id, "buy-timing-label-v1")
        self.assertEqual(
            tuple(label.value for label in result.model.class_labels),
            ("not_purchased", "purchased"),
        )
        self.assertEqual(result.model.train_target_counts["purchased"], 1)
        self.assertEqual(result.model.train_target_counts["not_purchased"], 1)

    def test_single_class_training_is_rejected(self) -> None:
        content = serialize_annotation_dataset(
            (
                _annotation("train-watch", date(2026, 1, 1), "watch"),
                _annotation("train-watch-2", date(2026, 1, 2), "watch"),
                _annotation("validation-watch", date(2026, 1, 8), "watch"),
            )
        ).encode()
        split_input = _split_input(content, minimum_labeled_samples=2)

        with self.assertRaisesRegex(ValueError, "at least two classes"):
            train_constant_label_baseline(split_input, minimum_labeled_samples=2)

    def test_manifest_tampering_is_rejected_before_training(self) -> None:
        content = serialize_annotation_dataset(
            (
                _annotation("train-discard", date(2026, 1, 1), "discard"),
                _annotation("train-watch", date(2026, 1, 2), "watch"),
                _annotation("validation-watch", date(2026, 1, 8), "watch"),
            )
        ).encode()
        split_input = _split_input(content, minimum_labeled_samples=2)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "split"
            write_training_label_split(path, split_input.split, split_input.readiness)
            manifest_path = path / "manifest.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["row_counts"]["train"] = 99
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "manifest"):
                read_training_label_split(path)

    def test_baseline_writer_is_non_overwriting(self) -> None:
        content = serialize_annotation_dataset(
            (
                _annotation("train-discard", date(2026, 1, 1), "discard"),
                _annotation("train-watch", date(2026, 1, 2), "watch"),
                _annotation("validation-watch", date(2026, 1, 8), "watch"),
            )
        ).encode()
        split_input = _split_input(content, minimum_labeled_samples=2)
        result = train_constant_label_baseline(split_input, minimum_labeled_samples=2)

        with TemporaryDirectory() as directory:
            output = Path(directory) / "model"
            write_constant_label_baseline(output, result)
            self.assertTrue((output / "model.json").exists())
            self.assertTrue((output / "metrics.json").exists())
            self.assertTrue((output / "predictions.jsonl").exists())
            self.assertTrue((output / "manifest.json").exists())
            with self.assertRaises(FileExistsError):
                write_constant_label_baseline(output, result)


def _split_input(
    content: bytes,
    *,
    purpose: TrainingDatasetPurpose = TrainingDatasetPurpose.ANNOTATION_DECISION,
    minimum_labeled_samples: int,
) -> TrainingLabelSplitInput:
    prepared = prepare_training_label_dataset(content, purpose=purpose)
    split = split_training_label_dataset(
        prepared,
        train_end=date(2026, 1, 5),
        validation_end=date(2026, 1, 10),
        label_available_through=datetime(2026, 1, 20, 17, tzinfo=UTC),
    )
    report = audit_training_label_split(split, minimum_labeled_samples=minimum_labeled_samples)

    return TrainingLabelSplitInput(
        split=split,
        manifest=build_training_label_split_manifest(split),
        readiness=report,
        split_manifest_sha256="0" * 64,
    )


def _buy_timing(
    annotation_id: str,
    reviewed_date: date,
    purchased: bool,
    evaluated_day: int,
) -> SelectionBuyTimingRow:
    return SelectionBuyTimingRow(
        annotation_id=annotation_id,
        candidate_id=f"candidate-{annotation_id}",
        batch_id="batch-1",
        symbol="000001.SZ",
        selection_date=reviewed_date,
        annotation_reviewed_at=datetime.combine(reviewed_date, datetime.min.time(), tzinfo=UTC),
        annotation_recorded_at=datetime.combine(reviewed_date, datetime.min.time(), tzinfo=UTC),
        evaluated_at=datetime(2026, 1, evaluated_day, 17, tzinfo=UTC),
        overall_score=3,
        outlook=SelectionAnnotationOutlook.NEUTRAL,
        decision=SelectionAnnotationDecision.WATCH,
        observation_end=reviewed_date + timedelta(days=3),
        window_sessions=3,
        purchased_within_window=purchased,
        order_id="order-1" if purchased else None,
        fill_id="fill-1" if purchased else None,
        filled_at=(
            datetime.combine(
                reviewed_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC
            ).replace(hour=10)
            if purchased
            else None
        ),
        fill_price=Decimal("10") if purchased else None,
        fill_quantity=Decimal("100") if purchased else None,
        source_order_ids=("order-1",) if purchased else (),
    )


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
        candidate_id=f"candidate-{annotation_id}",
        batch_id="batch-1",
        symbol="000001.SZ",
        selection_date=reviewed_date.isoformat(),
        source_filename=None,
        formula_id="formula-1",
        formula_name="fixture",
        formula_version="v1",
        annotation_reviewed_at=datetime.combine(
            reviewed_date,
            datetime.min.time(),
            tzinfo=UTC,
        ).isoformat(),
        annotation_recorded_at=recorded_at.isoformat(),
        overall_score=5,
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


if __name__ == "__main__":
    unittest.main()
