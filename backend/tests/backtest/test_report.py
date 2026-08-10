import unittest
from datetime import date
from decimal import Decimal

from stratgauge.backtest.models import BacktestResult, Trade, UnfilledIntent
from stratgauge.backtest.report import build_backtest_report


def _trade(
    signal_id: str,
    entry_session: str,
    exit_session: str,
    net_return: str,
    *,
    strategy_version_id: str = "strategy-v1",
    cost_schedule_id: str = "cost-v1",
) -> Trade:
    entry = Decimal("10")
    exit_price = entry * (Decimal("1") + Decimal(net_return))
    return Trade(
        signal_id=signal_id,
        security_id="security-1",
        symbol="000001.SZ",
        entry_session=entry_session,
        entry_price=entry,
        exit_session=exit_session,
        exit_price=exit_price,
        exit_reason="target",
        gross_return=Decimal(net_return),
        net_return=Decimal(net_return),
        total_cost=Decimal("0"),
        source_snapshot_id="snapshot-1",
        strategy_version_id=strategy_version_id,
        cost_schedule_id=cost_schedule_id,
    )


class BacktestReportTests(unittest.TestCase):
    def test_report_is_deterministic_and_groups_explicit_market_state(self) -> None:
        result = BacktestResult(
            run_id="run-1",
            source_snapshot_ids=("snapshot-1",),
            feature_set_version_ids=("features-v1",),
            strategy_version_ids=("strategy-v1",),
            cost_schedule_id="cost-v1",
            trades=(
                _trade("signal-2", "2026-01-07", "2026-01-08", "-0.05"),
                _trade("signal-1", "2026-01-06", "2026-01-07", "0.10"),
                _trade("signal-3", "2026-01-09", "2026-01-10", "0"),
            ),
            unfilled=(
                UnfilledIntent(
                    signal_id="signal-4",
                    symbol="000001.SZ",
                    reason="no_tradable_bar",
                    source_snapshot_id="snapshot-1",
                ),
            ),
            unresolved_signal_ids=("signal-5",),
        )

        first = build_backtest_report(
            result,
            market_states={date(2026, 1, 6): "bull", date(2026, 1, 7): "bear"},
        )
        second = build_backtest_report(
            result,
            market_states={date(2026, 1, 7): "bear", date(2026, 1, 6): "bull"},
        )

        self.assertEqual(first.model_dump_json(), second.model_dump_json())
        self.assertEqual(first.evidence_kind, "theoretical_backtest")
        self.assertEqual(first.unfilled_count, 1)
        self.assertEqual(first.unresolved_signal_count, 1)
        self.assertEqual(first.statistics.profitable_trade_count, 1)
        self.assertEqual(first.statistics.losing_trade_count, 1)
        self.assertEqual(first.statistics.flat_trade_count, 1)
        self.assertEqual(first.statistics.win_rate, Decimal("0.3333333333333333333333333333"))
        self.assertEqual(len(first.groups), 3)
        self.assertEqual(
            {group.entry_session: group.market_state for group in first.groups},
            {
                date(2026, 1, 6): "bull",
                date(2026, 1, 7): "bear",
                date(2026, 1, 9): "unspecified",
            },
        )

    def test_empty_market_state_is_rejected_instead_of_guessed(self) -> None:
        result = BacktestResult(
            run_id="run-1",
            source_snapshot_ids=(),
            feature_set_version_ids=(),
            strategy_version_ids=(),
            cost_schedule_id="cost-v1",
        )
        with self.assertRaisesRegex(ValueError, "market state must not be empty"):
            build_backtest_report(result, market_states={date(2026, 1, 6): "  "})


if __name__ == "__main__":
    unittest.main()
