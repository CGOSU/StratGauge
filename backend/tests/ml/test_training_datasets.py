import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.backtest.selection_dataset import (
    SelectionAnnotationDatasetRow,
    SelectionDatasetRow,
    serialize_annotation_dataset,
    serialize_selection_dataset,
)
from stratgauge.backtest.selection_ml_datasets import (
    SelectionBuyTimingRow,
    SelectionSellTimingRow,
    serialize_buy_timing_dataset,
    serialize_sell_timing_dataset,
)
from stratgauge.domain.selections import (
    SELECTION_ANNOTATION_TAXONOMY_VERSION,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
    SelectionOutcomeStatus,
)
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    TrainingLabel,
    audit_training_label_split,
    prepare_training_label_dataset,
    read_training_label_dataset,
    split_training_label_dataset,
    write_training_label_split,
)


class TrainingDatasetTests(unittest.TestCase):
    def test_candidate_success_purges_labels_unavailable_at_train_cutoff(self) -> None:
        content = serialize_selection_dataset(
            (
                _candidate_row("train-win", date(2026, 1, 1), SelectionOutcomeStatus.WIN, 2),
                _candidate_row("train-loss", date(2026, 1, 2), SelectionOutcomeStatus.LOSS, 3),
                _candidate_row("train-future", date(2026, 1, 4), SelectionOutcomeStatus.FLAT, 7),
                _candidate_row("validation", date(2026, 1, 8), SelectionOutcomeStatus.LOSS, 9),
                _candidate_row("test", date(2026, 1, 15), SelectionOutcomeStatus.WIN, 16),
                _candidate_row("pending", date(2026, 1, 17), None, None),
            )
        ).encode()

        prepared = prepare_training_label_dataset(
            content,
            purpose=TrainingDatasetPurpose.CANDIDATE_SUCCESS,
        )
        split = split_training_label_dataset(
            prepared,
            train_end=date(2026, 1, 5),
            validation_end=date(2026, 1, 10),
            label_available_through=datetime(2026, 1, 20, 18, tzinfo=UTC),
        )
        report = audit_training_label_split(split, minimum_labeled_samples=4)

        self.assertEqual(
            [row.target for row in split.train_rows],
            [TrainingLabel.SUCCESS, TrainingLabel.NOT_SUCCESS],
        )
        self.assertEqual([row.candidate_id for row in split.purged_rows], ["train-future"])
        self.assertEqual([row.candidate_id for row in split.validation_rows], ["validation"])
        self.assertEqual([row.candidate_id for row in split.test_rows], ["test"])
        self.assertEqual(prepared.skipped_unlabeled_row_count, 1)
        self.assertEqual(report.purged_future_label_count, 1)
        self.assertTrue(report.eligible)

    def test_annotation_decision_contract_omits_same_review_inputs(self) -> None:
        content = serialize_annotation_dataset(
            (
                _annotation_row("annotation-discard", date(2026, 1, 1), "discard"),
                _annotation_row("annotation-watch", date(2026, 1, 2), "watch"),
                _annotation_row("annotation-prepare", date(2026, 1, 8), "prepare_buy"),
            )
        ).encode()

        prepared = prepare_training_label_dataset(
            content,
            purpose=TrainingDatasetPurpose.ANNOTATION_DECISION,
        )

        self.assertEqual(
            [row.target for row in prepared.rows],
            [TrainingLabel.DISCARD, TrainingLabel.WATCH, TrainingLabel.PREPARE_BUY],
        )
        payload = prepared.rows[0].model_dump()
        self.assertNotIn("overall_score", payload)
        self.assertNotIn("outlook", payload)
        self.assertNotIn("position_tags", payload)
        self.assertEqual(prepared.rows[0].label_available_at, datetime(2026, 1, 1, 16, tzinfo=UTC))

    def test_buy_timing_skips_unresolved_labels_and_uses_evaluation_cutoff_for_negative(
        self,
    ) -> None:
        content = serialize_buy_timing_dataset(
            (
                _buy_timing_row("buy-yes", date(2026, 1, 1), True, 2),
                _buy_timing_row("buy-no", date(2026, 1, 2), False, 5),
                _buy_timing_row("buy-pending", date(2026, 1, 3), None, 3),
            )
        ).encode()

        prepared = prepare_training_label_dataset(
            content,
            purpose=TrainingDatasetPurpose.BUY_TIMING,
        )

        self.assertEqual(prepared.window_sessions, 3)
        self.assertEqual(prepared.skipped_unlabeled_row_count, 1)
        self.assertEqual(
            [row.target for row in prepared.rows],
            [TrainingLabel.PURCHASED, TrainingLabel.NOT_PURCHASED],
        )
        self.assertEqual(
            prepared.rows[1].label_available_at,
            datetime(2026, 1, 5, 17, tzinfo=UTC),
        )

    def test_sell_timing_requires_sell_evidence_and_keeps_window_contract(self) -> None:
        content = serialize_sell_timing_dataset(
            (
                _sell_timing_row("sell-yes", date(2026, 1, 1), True, 2),
                _sell_timing_row("sell-no", date(2026, 1, 2), False, 4),
            )
        ).encode()

        prepared = prepare_training_label_dataset(
            content,
            purpose=TrainingDatasetPurpose.SELL_TIMING,
        )

        self.assertEqual(prepared.window_sessions, 1)
        self.assertEqual(
            [row.target for row in prepared.rows],
            [TrainingLabel.SOLD, TrainingLabel.NOT_SOLD],
        )
        invalid = _sell_timing_row("sell-missing-fill", date(2026, 1, 3), True, 4).model_copy(
            update={"sell_fill_ids": ()}
        )
        with self.assertRaisesRegex(ValueError, "lacks fill evidence"):
            prepare_training_label_dataset(
                serialize_sell_timing_dataset((invalid,)).encode(),
                purpose=TrainingDatasetPurpose.SELL_TIMING,
            )

    def test_wrong_source_schema_and_duplicate_samples_are_rejected(self) -> None:
        annotation_content = serialize_annotation_dataset(
            (_annotation_row("annotation-1", date(2026, 1, 1), "watch"),)
        ).encode()
        with self.assertRaisesRegex(ValueError, "unsupported dataset schema"):
            prepare_training_label_dataset(
                annotation_content,
                purpose=TrainingDatasetPurpose.BUY_TIMING,
            )

        row = _annotation_row("annotation-duplicate", date(2026, 1, 1), "watch")
        with self.assertRaisesRegex(ValueError, "duplicate sample IDs"):
            prepare_training_label_dataset(
                serialize_annotation_dataset((row, row)).encode(),
                purpose=TrainingDatasetPurpose.ANNOTATION_DECISION,
            )

    def test_split_writer_is_non_overwriting_and_preserves_purged_rows(self) -> None:
        content = serialize_annotation_dataset(
            (
                _annotation_row("annotation-train-discard", date(2026, 1, 1), "discard"),
                _annotation_row("annotation-train-watch", date(2026, 1, 2), "watch"),
                _annotation_row("annotation-future", date(2026, 1, 4), "prepare_buy", 7),
                _annotation_row("annotation-validation", date(2026, 1, 8), "watch"),
            )
        ).encode()
        prepared = prepare_training_label_dataset(
            content,
            purpose=TrainingDatasetPurpose.ANNOTATION_DECISION,
        )
        split = split_training_label_dataset(
            prepared,
            train_end=date(2026, 1, 5),
            validation_end=date(2026, 1, 10),
            label_available_through=datetime(2026, 1, 12, 17, tzinfo=UTC),
        )
        report = audit_training_label_split(split, minimum_labeled_samples=3)

        with TemporaryDirectory() as directory:
            output_dir = Path(directory) / "prepared"
            manifest = write_training_label_split(output_dir, split, report)

            self.assertEqual(manifest.row_counts["purged"], 1)
            self.assertEqual(
                len(read_training_label_dataset((output_dir / "train.jsonl").read_bytes())), 2
            )
            self.assertEqual(
                len(read_training_label_dataset((output_dir / "purged.jsonl").read_bytes())),
                1,
            )
            with self.assertRaises(FileExistsError):
                write_training_label_split(output_dir, split, report)


def _candidate_row(
    candidate_id: str,
    selection_date: date,
    outcome_status: SelectionOutcomeStatus | None,
    evaluated_day: int | None,
) -> SelectionDatasetRow:
    completed = outcome_status is not None
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
        cost_schedule_id="cost-v1" if completed else None,
        horizon_sessions=10 if completed else None,
        position_notional="10000" if completed else None,
        outcome_status=outcome_status,
        outcome_evaluated_at=(
            datetime(2026, 1, evaluated_day, 17, tzinfo=UTC).isoformat()
            if evaluated_day is not None
            else None
        ),
        net_return="0.01" if completed else None,
        source_snapshot_ids=("snapshot-1",) if completed else (),
    )


def _annotation_row(
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
        annotation_note="should not become a feature",
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


def _buy_timing_row(
    annotation_id: str,
    reviewed_date: date,
    purchased: bool | None,
    evaluated_day: int,
) -> SelectionBuyTimingRow:
    evaluated_at = datetime(2026, 1, evaluated_day, 17, tzinfo=UTC)
    filled_at = datetime(2026, 1, 2, 10, tzinfo=UTC) if purchased else None
    return SelectionBuyTimingRow(
        annotation_id=annotation_id,
        candidate_id=f"candidate-{annotation_id}",
        batch_id="batch-1",
        symbol="000001.SZ",
        selection_date=reviewed_date,
        annotation_reviewed_at=datetime.combine(reviewed_date, datetime.min.time(), tzinfo=UTC),
        annotation_recorded_at=datetime.combine(reviewed_date, datetime.min.time(), tzinfo=UTC),
        evaluated_at=evaluated_at,
        overall_score=3,
        outlook=SelectionAnnotationOutlook.NEUTRAL,
        decision=SelectionAnnotationDecision.WATCH,
        observation_end=date(2026, 1, 5),
        window_sessions=3,
        purchased_within_window=purchased,
        order_id="order-1" if purchased else None,
        fill_id="fill-1" if purchased else None,
        filled_at=filled_at,
        fill_price=Decimal("10") if purchased else None,
        fill_quantity=Decimal("100") if purchased else None,
        source_order_ids=("order-1",) if purchased else (),
    )


def _sell_timing_row(
    candidate_id: str,
    exposure_session: date,
    sold: bool,
    evaluated_day: int,
) -> SelectionSellTimingRow:
    return SelectionSellTimingRow(
        candidate_id=candidate_id,
        batch_id="batch-1",
        symbol="000001.SZ",
        selection_date=date(2026, 1, 1),
        exposure_session=exposure_session,
        evaluated_at=datetime(2026, 1, evaluated_day, 17, tzinfo=UTC),
        sell_window_end=date(2026, 1, 3),
        window_sessions=1,
        sold_within_window=sold,
        exposure_quantity=Decimal("100"),
        average_cost=Decimal("10"),
        buy_order_ids=("buy-order-1",),
        buy_fill_ids=("buy-fill-1",),
        sell_order_ids=("sell-order-1",) if sold else (),
        sell_fill_ids=("sell-fill-1",) if sold else (),
    )


if __name__ == "__main__":
    unittest.main()
