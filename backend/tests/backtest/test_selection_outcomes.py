import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from stratgauge.backtest.models import BacktestPolicy
from stratgauge.backtest.selection_outcomes import (
    SelectionTrackingPolicy,
    evaluate_selection_outcome,
)
from stratgauge.backtest.selection_stats import summarize_selection_outcomes
from stratgauge.domain.models import DailyBar, Exchange
from stratgauge.domain.selections import (
    SelectionCandidate,
    SelectionOutcome,
    SelectionOutcomeStatus,
)


def make_bar(session: date, **changes: object) -> DailyBar:
    values: dict[str, object] = {
        "security_id": "security-1",
        "symbol": "000001.SZ",
        "session_date": session,
        "provider": "fixture",
        "provider_symbol": "000001",
        "open": Decimal("10.00"),
        "high": Decimal("10.50"),
        "low": Decimal("9.80"),
        "close": Decimal("10.20"),
        "volume": Decimal("1000"),
        "amount": Decimal("10200"),
        "event_time": datetime.combine(session, datetime.min.time(), tzinfo=UTC),
        "available_at": datetime.combine(session, datetime.min.time(), tzinfo=UTC),
        "ingested_at": datetime.combine(session, datetime.min.time(), tzinfo=UTC),
    }
    values.update(changes)
    return DailyBar.model_validate(values)


def candidate() -> SelectionCandidate:
    return SelectionCandidate(
        candidate_id="tdx-batch:000001.SZ",
        batch_id="tdx-batch",
        local_code="000001",
        symbol="000001.SZ",
        exchange=Exchange.SZ,
    )


def make_outcome(status: SelectionOutcomeStatus, net_return: Decimal | None) -> SelectionOutcome:
    completed = status in {
        SelectionOutcomeStatus.WIN,
        SelectionOutcomeStatus.LOSS,
        SelectionOutcomeStatus.FLAT,
    }
    return SelectionOutcome(
        candidate_id=f"candidate-{status.value}",
        batch_id="batch-1",
        selection_date=date(2026, 1, 5),
        status=status,
        evaluated_at=datetime(2026, 1, 10, 17, tzinfo=UTC),
        horizon_sessions=1,
        entry_session=date(2026, 1, 6) if completed else None,
        exit_session=date(2026, 1, 7) if completed else None,
        entry_price=Decimal("10") if completed else None,
        exit_price=Decimal("10") if completed else None,
        gross_return=net_return if completed else None,
        net_return=net_return,
        reason="fixture",
    )


class SelectionOutcomeTests(unittest.TestCase):
    def test_fixed_horizon_outcome_uses_next_session_entry_and_net_return(self) -> None:
        result = evaluate_selection_outcome(
            candidate(),
            selection_date=date(2026, 1, 5),
            bars=[
                make_bar(date(2026, 1, 6), open=Decimal("10.00")),
                make_bar(date(2026, 1, 7), close=Decimal("10.50")),
                make_bar(date(2026, 1, 8), high=Decimal("11.00"), close=Decimal("11.00")),
            ],
            evaluated_at=datetime(2026, 1, 8, 17, tzinfo=UTC),
            policy=SelectionTrackingPolicy(
                horizon_sessions=2,
                cost_policy=BacktestPolicy(
                    commission_rate=Decimal("0"),
                    minimum_commission=Decimal("0"),
                    stamp_duty=Decimal("0"),
                ),
            ),
            source_snapshot_ids=["snapshot-2", "snapshot-1"],
        )

        self.assertEqual(result.status, SelectionOutcomeStatus.WIN)
        self.assertEqual(result.entry_session, date(2026, 1, 6))
        self.assertEqual(result.exit_session, date(2026, 1, 8))
        self.assertEqual(result.net_return, Decimal("0.1"))
        self.assertEqual(result.source_snapshot_ids, ("snapshot-1", "snapshot-2"))

    def test_non_tradable_first_session_is_not_counted_as_a_loss(self) -> None:
        result = evaluate_selection_outcome(
            candidate(),
            selection_date=date(2026, 1, 5),
            bars=[
                make_bar(date(2026, 1, 6), suspended=True, tradable=False),
                make_bar(date(2026, 1, 7), high=Decimal("12.00"), close=Decimal("12.00")),
            ],
            evaluated_at=datetime(2026, 1, 7, 17, tzinfo=UTC),
            policy=SelectionTrackingPolicy(horizon_sessions=1),
        )

        self.assertEqual(result.status, SelectionOutcomeStatus.NO_ENTRY)
        self.assertIsNone(result.net_return)

    def test_future_bars_beyond_evaluation_time_do_not_complete_outcome(self) -> None:
        result = evaluate_selection_outcome(
            candidate(),
            selection_date=date(2026, 1, 5),
            bars=[
                make_bar(date(2026, 1, 6)),
                make_bar(date(2026, 1, 7)),
                make_bar(date(2026, 1, 8)),
            ],
            evaluated_at=datetime(2026, 1, 7, 17, tzinfo=UTC),
            policy=SelectionTrackingPolicy(horizon_sessions=2),
        )

        self.assertEqual(result.status, SelectionOutcomeStatus.UNRESOLVED)
        self.assertEqual(result.reason, "insufficient_horizon_data")


class SelectionStatsTests(unittest.TestCase):
    def test_summary_keeps_unresolved_rows_out_of_win_rate(self) -> None:
        stats = summarize_selection_outcomes(
            [
                make_outcome(SelectionOutcomeStatus.WIN, Decimal("0.10")),
                make_outcome(SelectionOutcomeStatus.LOSS, Decimal("-0.05")),
                make_outcome(SelectionOutcomeStatus.FLAT, Decimal("0")),
                make_outcome(SelectionOutcomeStatus.NO_ENTRY, None),
                make_outcome(SelectionOutcomeStatus.UNRESOLVED, None),
            ]
        )

        self.assertEqual(stats.total, 5)
        self.assertEqual(stats.completed, 3)
        self.assertEqual(stats.wins, 1)
        self.assertEqual(stats.no_entry, 1)
        self.assertEqual(stats.unresolved, 1)
        self.assertEqual(stats.win_rate, Decimal(1) / Decimal(3))
        self.assertEqual(
            stats.average_net_return,
            (Decimal("0.10") - Decimal("0.05")) / Decimal(3),
        )


if __name__ == "__main__":
    unittest.main()
