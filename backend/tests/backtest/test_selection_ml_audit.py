import hashlib
import unittest

from stratgauge.backtest.selection_dataset import SelectionDatasetRow
from stratgauge.backtest.selection_ml_audit import (
    audit_selection_dataset,
    audit_selection_rows,
)
from stratgauge.domain.selections import SelectionOutcomeStatus


class SelectionMLAuditTests(unittest.TestCase):
    def test_readiness_requires_explicit_frozen_strategy(self) -> None:
        report = audit_selection_rows(
            (_row("candidate-1", SelectionOutcomeStatus.WIN),),
            source_file_sha256="source-hash",
            strategy_parameters_frozen=False,
            minimum_completed_samples=1,
        )

        self.assertFalse(report.eligible)
        self.assertIn("strategy parameters are not declared frozen", report.blockers)

    def test_ready_dataset_has_two_completed_target_statuses(self) -> None:
        content = b"\n".join(
            (
                _row("candidate-1", SelectionOutcomeStatus.WIN).model_dump_json().encode(),
                _row("candidate-2", SelectionOutcomeStatus.LOSS).model_dump_json().encode(),
            )
        )
        report = audit_selection_dataset(
            content,
            strategy_parameters_frozen=True,
            minimum_completed_samples=2,
            tracking_policy_id="policy-v1",
        )

        self.assertTrue(report.eligible)
        self.assertEqual(report.completed_outcome_count, 2)
        self.assertEqual(report.target_status_counts, {"loss": 1, "win": 1})
        self.assertEqual(report.source_file_sha256, hashlib.sha256(content).hexdigest())

    def test_pending_and_unresolved_rows_are_reported_not_promoted_to_targets(self) -> None:
        rows = (
            _row("candidate-1", SelectionOutcomeStatus.WIN),
            _row("candidate-2", SelectionOutcomeStatus.LOSS),
            _row("candidate-3", None),
            _row("candidate-4", SelectionOutcomeStatus.UNRESOLVED),
        )
        report = audit_selection_rows(
            rows,
            source_file_sha256="source-hash",
            strategy_parameters_frozen=True,
            minimum_completed_samples=2,
            tracking_policy_id="policy-v1",
        )

        self.assertTrue(report.eligible)
        self.assertEqual(report.pending_count, 1)
        self.assertEqual(report.non_trainable_outcome_count, 1)
        self.assertIn("1 candidates have no mature outcome", report.warnings)
        self.assertIn("1 outcomes are no-entry or unresolved", report.warnings)

    def test_outcome_on_selection_date_is_blocked_as_future_data_error(self) -> None:
        report = audit_selection_rows(
            (
                _row(
                    "candidate-1",
                    SelectionOutcomeStatus.WIN,
                    evaluated_at="2026-01-05T15:30:00+08:00",
                ),
                _row("candidate-2", SelectionOutcomeStatus.LOSS),
            ),
            source_file_sha256="source-hash",
            strategy_parameters_frozen=True,
            minimum_completed_samples=2,
            tracking_policy_id="policy-v1",
        )

        self.assertFalse(report.eligible)
        self.assertIn("outcome is not strictly after selection date: candidate-1", report.blockers)

    def test_duplicate_candidate_policy_is_blocked(self) -> None:
        report = audit_selection_rows(
            (
                _row("candidate-1", SelectionOutcomeStatus.WIN),
                _row("candidate-1", SelectionOutcomeStatus.WIN),
                _row("candidate-2", SelectionOutcomeStatus.LOSS),
            ),
            source_file_sha256="source-hash",
            strategy_parameters_frozen=True,
            minimum_completed_samples=2,
            tracking_policy_id="policy-v1",
        )

        self.assertFalse(report.eligible)
        self.assertEqual(report.duplicate_row_count, 1)

    def test_multiple_policies_require_explicit_scope(self) -> None:
        report = audit_selection_rows(
            (
                _row("candidate-1", SelectionOutcomeStatus.WIN, policy="policy-v1"),
                _row("candidate-2", SelectionOutcomeStatus.LOSS, policy="policy-v2"),
            ),
            source_file_sha256="source-hash",
            strategy_parameters_frozen=True,
            minimum_completed_samples=1,
        )

        self.assertFalse(report.eligible)
        self.assertIn(
            "dataset contains multiple tracking policies; select one explicitly",
            report.blockers,
        )


def _row(
    candidate_id: str,
    status: SelectionOutcomeStatus | None,
    *,
    policy: str = "policy-v1",
    evaluated_at: str = "2026-01-12T09:00:00+08:00",
) -> SelectionDatasetRow:
    completed = status in {
        SelectionOutcomeStatus.WIN,
        SelectionOutcomeStatus.LOSS,
        SelectionOutcomeStatus.FLAT,
    }
    return SelectionDatasetRow(
        candidate_id=candidate_id,
        batch_id="batch-1",
        symbol=f"00000{candidate_id[-1]}.SZ",
        selection_date="2026-01-05",
        source_filename="selection.txt",
        formula_id="formula-1",
        formula_name="公式 A",
        formula_version="v1",
        manual_decision=None,
        manual_labeled_at=None,
        tracking_policy_id=policy if status is not None else None,
        cost_schedule_id="cost-v1" if status is not None else None,
        horizon_sessions=5 if status is not None else None,
        position_notional="10000" if status is not None else None,
        outcome_status=status,
        outcome_evaluated_at=evaluated_at if completed else None,
        net_return="0.05" if completed else None,
        source_snapshot_ids=("snapshot-1",) if completed else (),
    )


if __name__ == "__main__":
    unittest.main()
