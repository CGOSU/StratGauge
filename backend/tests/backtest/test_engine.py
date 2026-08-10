import json
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from stratgauge.backtest.engine import run_backtest
from stratgauge.backtest.models import (
    AmbiguousBarrierPolicy,
    BacktestPolicy,
    EntryExecutionPolicy,
)
from stratgauge.domain.models import DailyBar, Signal


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


def make_signal(**changes: object) -> Signal:
    values: dict[str, object] = {
        "signal_id": "signal-1",
        "security_id": "security-1",
        "symbol": "000001.SZ",
        "as_of": date(2026, 1, 5),
        "earliest_execution": date(2026, 1, 6),
        "latest_execution": date(2026, 1, 6),
        "strategy_version_id": "strategy-v1",
        "feature_set_version": "features-v1",
        "source_snapshot_id": "snapshot-1",
        "entry_lower": Decimal("9.90"),
        "entry_upper": Decimal("10.10"),
        "stop_price": Decimal("9.50"),
        "target_price": Decimal("10.80"),
    }
    values.update(changes)
    return Signal.model_validate(values)


class BacktestEngineTests(unittest.TestCase):
    def test_fixture_run_is_reproducible_and_carries_lineage(self) -> None:
        fixture_path = Path(__file__).parents[1] / "fixtures" / "phase0_bars.json"
        bars = tuple(DailyBar.model_validate(row) for row in json.loads(fixture_path.read_text()))
        signal = make_signal(target_price=Decimal("10.80"))
        policy = BacktestPolicy(
            commission_rate=Decimal("0"),
            minimum_commission=Decimal("0"),
            stamp_duty=Decimal("0"),
        )

        first = run_backtest(run_id="fixture-run", bars=bars, signals=[signal], policy=policy)
        second = run_backtest(run_id="fixture-run", bars=bars, signals=[signal], policy=policy)

        self.assertEqual(first.model_dump_json(), second.model_dump_json())
        self.assertEqual(first.source_snapshot_ids, ("snapshot-1",))
        self.assertEqual(first.feature_set_version_ids, ("features-v1",))
        self.assertEqual(first.strategy_version_ids, ("strategy-v1",))
        self.assertEqual(first.entry_policy_id, "entry-limit-chase-v1")
        self.assertEqual(first.trades[0].entry_policy_id, "entry-limit-chase-v1")
        self.assertEqual(first.trades[0].exit_reason, "target")

    def test_after_close_signal_cannot_fill_same_day(self) -> None:
        result = run_backtest(
            run_id="run-1",
            bars=[make_bar(date(2026, 1, 5), low=Decimal("9.90"))],
            signals=[make_signal()],
            policy=BacktestPolicy(),
        )
        self.assertEqual(result.trades, ())
        self.assertEqual(result.unfilled[0].reason, "entry_zone_not_reached")

    def test_suspended_security_cannot_fill(self) -> None:
        result = run_backtest(
            run_id="run-1",
            bars=[
                make_bar(
                    date(2026, 1, 6),
                    suspended=True,
                    tradable=False,
                    low=Decimal("9.90"),
                )
            ],
            signals=[make_signal()],
            policy=BacktestPolicy(),
        )
        self.assertEqual(result.unfilled[0].reason, "no_tradable_bar")

    def test_gap_beyond_chase_limit_rejects_entry(self) -> None:
        result = run_backtest(
            run_id="run-1",
            bars=[
                make_bar(
                    date(2026, 1, 6),
                    open=Decimal("11.00"),
                    high=Decimal("11.20"),
                    low=Decimal("10.90"),
                    close=Decimal("11.10"),
                )
            ],
            signals=[make_signal()],
            policy=BacktestPolicy(entry_policy=EntryExecutionPolicy(chase_limit=Decimal("0.01"))),
        )
        self.assertEqual(result.unfilled[0].reason, "gap_beyond_chase_limit")

    def test_gap_down_fills_at_open_below_limit_price(self) -> None:
        result = run_backtest(
            run_id="run-1",
            bars=[
                make_bar(
                    date(2026, 1, 6),
                    open=Decimal("9.50"),
                    high=Decimal("9.70"),
                    low=Decimal("9.40"),
                    close=Decimal("9.60"),
                ),
                make_bar(date(2026, 1, 7), close=Decimal("9.80")),
            ],
            signals=[make_signal(max_holding_sessions=1)],
            policy=BacktestPolicy(),
        )
        self.assertEqual(result.trades[0].entry_price, Decimal("9.50"))

    def test_suspended_exit_bar_cannot_fill(self) -> None:
        result = run_backtest(
            run_id="run-1",
            bars=[
                make_bar(date(2026, 1, 6)),
                make_bar(
                    date(2026, 1, 7),
                    suspended=True,
                    tradable=False,
                    low=Decimal("9.00"),
                    high=Decimal("11.00"),
                ),
            ],
            signals=[make_signal(max_holding_sessions=1)],
            policy=BacktestPolicy(),
        )
        self.assertEqual(result.trades, ())
        self.assertEqual(result.unresolved_signal_ids, ("signal-1",))

    def test_t_plus_one_blocks_same_day_exit(self) -> None:
        result = run_backtest(
            run_id="run-1",
            bars=[
                make_bar(date(2026, 1, 6), low=Decimal("9.40")),
                make_bar(
                    date(2026, 1, 7),
                    open=Decimal("10.00"),
                    high=Decimal("10.10"),
                    low=Decimal("9.90"),
                    close=Decimal("10.00"),
                ),
            ],
            signals=[make_signal(max_holding_sessions=1)],
            policy=BacktestPolicy(),
        )
        self.assertEqual(result.trades[0].entry_session, "2026-01-06")
        self.assertEqual(result.trades[0].exit_session, "2026-01-07")
        self.assertEqual(result.trades[0].exit_reason, "time_stop")

    def test_ambiguous_barrier_uses_conservative_stop(self) -> None:
        result = run_backtest(
            run_id="run-1",
            bars=[
                make_bar(date(2026, 1, 6)),
                make_bar(
                    date(2026, 1, 7),
                    high=Decimal("11.00"),
                    low=Decimal("9.00"),
                ),
            ],
            signals=[make_signal()],
            policy=BacktestPolicy(
                commission_rate=Decimal("0"),
                minimum_commission=Decimal("0"),
                stamp_duty=Decimal("0"),
                ambiguous_barrier=AmbiguousBarrierPolicy.CONSERVATIVE_STOP,
            ),
        )
        self.assertTrue(result.trades[0].ambiguous)
        self.assertEqual(result.trades[0].exit_reason, "ambiguous_conservative_stop")
        self.assertEqual(result.trades[0].exit_price, Decimal("9.50"))

    def test_minimum_commission_is_included_in_net_return(self) -> None:
        result = run_backtest(
            run_id="run-1",
            bars=[
                make_bar(date(2026, 1, 6)),
                make_bar(date(2026, 1, 7), high=Decimal("11.00")),
            ],
            signals=[make_signal()],
            policy=BacktestPolicy(
                commission_rate=Decimal("0.001"),
                minimum_commission=Decimal("5"),
                stamp_duty=Decimal("0"),
            ),
        )
        self.assertEqual(result.trades[0].total_cost, Decimal("10"))


if __name__ == "__main__":
    unittest.main()
