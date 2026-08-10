import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from stratgauge.ml.selection_training import BaselineLabel, SelectionFeatureRow
from stratgauge.ml.walk_forward import WalkForwardFoldSpec, validate_walk_forward


class WalkForwardTests(unittest.TestCase):
    def test_fold_purges_labels_not_known_at_validation_start(self) -> None:
        rows = (
            _row("candidate-1", date(2026, 1, 1), "win", date(2026, 1, 3)),
            _row("candidate-2", date(2026, 1, 2), "loss", date(2026, 1, 3)),
            _row("candidate-3", date(2026, 1, 3), "win", date(2026, 1, 4)),
            _row("candidate-4", date(2026, 1, 4), "loss", date(2026, 1, 10)),
            _row("candidate-5", date(2026, 1, 5), "win", date(2026, 1, 10)),
        )
        report = validate_walk_forward(
            rows,
            folds=(
                WalkForwardFoldSpec(
                    fold_id="fold-1",
                    train_end=date(2026, 1, 3),
                    validation_start=date(2026, 1, 4),
                    validation_end=date(2026, 1, 5),
                    market_state="bull",
                ),
            ),
            source_file_sha256="source-hash",
        )

        fold = report.folds[0]
        self.assertFalse(report.final_test_used)
        self.assertEqual(fold.train_row_count, 2)
        self.assertEqual(fold.purged_train_row_count, 1)
        self.assertEqual(fold.validation_row_count, 2)
        self.assertEqual(fold.ranking_auc, Decimal("0.5"))
        self.assertIsNone(fold.average_net_return)
        self.assertEqual(fold.market_state, "bull")

    def test_final_test_rows_are_excluded_from_fold_validation(self) -> None:
        rows = (
            _row("candidate-1", date(2026, 1, 1), "win", date(2026, 1, 3)),
            _row("candidate-2", date(2026, 1, 2), "loss", date(2026, 1, 3)),
            _row("candidate-3", date(2026, 1, 4), "win", date(2026, 1, 10)),
            _row("candidate-test", date(2026, 2, 1), "loss", date(2026, 2, 10)),
        )
        report = validate_walk_forward(
            rows,
            folds=(
                WalkForwardFoldSpec(
                    fold_id="fold-1",
                    train_end=date(2026, 1, 2),
                    validation_start=date(2026, 1, 4),
                    validation_end=date(2026, 1, 4),
                ),
            ),
            source_file_sha256="source-hash",
            final_test_start=date(2026, 2, 1),
        )

        self.assertEqual(report.final_test_start, date(2026, 2, 1))
        self.assertEqual(report.final_test_row_count, 1)
        self.assertEqual(report.folds[0].validation_row_count, 1)
        self.assertFalse(report.final_test_used)

    def test_multiple_tracking_policies_are_rejected(self) -> None:
        rows = (
            _row("candidate-1", date(2026, 1, 1), "win", date(2026, 1, 3)),
            _row(
                "candidate-2",
                date(2026, 1, 2),
                "loss",
                date(2026, 1, 3),
                tracking_policy_id="policy-v2",
            ),
        )
        with self.assertRaisesRegex(ValueError, "multiple tracking policies"):
            validate_walk_forward(
                rows,
                folds=(
                    WalkForwardFoldSpec(
                        fold_id="fold-1",
                        train_end=date(2026, 1, 2),
                        validation_start=date(2026, 1, 3),
                        validation_end=date(2026, 1, 3),
                    ),
                ),
                source_file_sha256="source-hash",
            )

    def test_duplicate_candidate_dates_are_rejected(self) -> None:
        row = _row("candidate-1", date(2026, 1, 1), "win", date(2026, 1, 3))
        with self.assertRaisesRegex(ValueError, "duplicate candidate/date"):
            validate_walk_forward(
                (row, row),
                folds=(
                    WalkForwardFoldSpec(
                        fold_id="fold-1",
                        train_end=date(2026, 1, 1),
                        validation_start=date(2026, 1, 2),
                        validation_end=date(2026, 1, 2),
                    ),
                ),
                source_file_sha256="source-hash",
            )

    def test_single_class_training_is_rejected(self) -> None:
        rows = (
            _row("candidate-1", date(2026, 1, 1), "win", date(2026, 1, 3)),
            _row("candidate-2", date(2026, 1, 2), "win", date(2026, 1, 3)),
            _row("candidate-3", date(2026, 1, 4), "loss", date(2026, 1, 10)),
        )
        with self.assertRaisesRegex(ValueError, "one class"):
            validate_walk_forward(
                rows,
                folds=(
                    WalkForwardFoldSpec(
                        fold_id="fold-1",
                        train_end=date(2026, 1, 2),
                        validation_start=date(2026, 1, 4),
                        validation_end=date(2026, 1, 4),
                    ),
                ),
                source_file_sha256="source-hash",
            )

    def test_overlapping_validation_windows_are_rejected(self) -> None:
        rows = (
            _row("candidate-1", date(2026, 1, 1), "win", date(2026, 2, 1)),
            _row("candidate-2", date(2026, 1, 2), "loss", date(2026, 2, 1)),
            _row("candidate-3", date(2026, 1, 4), "win", date(2026, 2, 1)),
        )
        folds = (
            WalkForwardFoldSpec(
                fold_id="fold-1",
                train_end=date(2026, 1, 2),
                validation_start=date(2026, 1, 3),
                validation_end=date(2026, 1, 4),
            ),
            WalkForwardFoldSpec(
                fold_id="fold-2",
                train_end=date(2026, 1, 3),
                validation_start=date(2026, 1, 4),
                validation_end=date(2026, 1, 5),
            ),
        )
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            validate_walk_forward(rows, folds=folds, source_file_sha256="source-hash")


def _row(
    candidate_id: str,
    selection_date: date,
    label: BaselineLabel,
    label_date: date,
    tracking_policy_id: str = "policy-v1",
) -> SelectionFeatureRow:
    return SelectionFeatureRow(
        candidate_id=candidate_id,
        symbol="000001.SZ",
        selection_date=selection_date,
        feature_set_version_id="features-v1",
        feature_snapshot_id=f"feature-{candidate_id}",
        as_of=datetime.combine(selection_date, datetime.min.time(), tzinfo=UTC),
        label_evaluated_at=datetime.combine(label_date, datetime.min.time(), tzinfo=UTC),
        tracking_policy_id=tracking_policy_id,
        label=label,
        source_snapshot_ids=(f"snapshot-{candidate_id}",),
        features={"return_1": Decimal("0.01")},
    )


if __name__ == "__main__":
    unittest.main()
