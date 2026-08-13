import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from stratgauge.backtest.models import BacktestPolicy
from stratgauge.backtest.selection_outcomes import (
    SelectionPerformancePolicy,
    SelectionTrackingPolicy,
    evaluate_selection_batch,
    evaluate_selection_outcome,
    evaluate_selection_performance,
    latest_selection_outcomes,
    selection_outcome_policy_id,
)
from stratgauge.backtest.selection_stats import summarize_selection_outcomes
from stratgauge.domain.models import DailyBar, Exchange
from stratgauge.domain.selections import (
    SelectionBatch,
    SelectionCandidate,
    SelectionOutcome,
    SelectionOutcomeStatus,
    SelectionPerformanceStatus,
    TdxSelectionImport,
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
    def test_forward_performance_records_requested_windows_rise_drawdown_and_target(self) -> None:
        bars = [
            make_bar(
                date(2026, 1, 6), open=Decimal("10"), high=Decimal("10.5"), close=Decimal("10.2")
            ),
            make_bar(date(2026, 1, 7), high=Decimal("10.8"), close=Decimal("10.0")),
            make_bar(date(2026, 1, 8), high=Decimal("11.5"), close=Decimal("11.0")),
            make_bar(date(2026, 1, 9), high=Decimal("11.2"), close=Decimal("10.8")),
            make_bar(date(2026, 1, 10), high=Decimal("12.0"), close=Decimal("11.5")),
        ]

        result = evaluate_selection_performance(
            candidate(),
            selection_date=date(2026, 1, 5),
            bars=bars,
            evaluated_at=datetime(2026, 1, 10, 17, tzinfo=UTC),
            policy=SelectionPerformancePolicy(
                target_return=Decimal("0.1"),
                horizons=(1, 3, 5),
            ),
            source_snapshot_ids=("snapshot-2", "snapshot-1"),
        )

        self.assertEqual(result.status, SelectionPerformanceStatus.RESOLVED)
        self.assertTrue(result.performance_policy_id.startswith("selection-performance-"))
        self.assertEqual(result.anchor_session, date(2026, 1, 6))
        self.assertEqual(result.anchor_price, Decimal("10"))
        self.assertEqual(result.source_snapshot_ids, ("snapshot-1", "snapshot-2"))
        self.assertEqual(result.points[0].return_from_anchor, Decimal("0.02"))
        self.assertEqual(result.points[1].maximum_rise, Decimal("0.15"))
        self.assertEqual(
            result.points[1].maximum_drawdown, Decimal("-0.0196078431372549019607843137")
        )
        self.assertTrue(result.points[1].target_achieved)
        self.assertEqual(result.points[2].session_date, date(2026, 1, 10))

    def test_forward_performance_distinguishes_non_tradable_anchor_from_missing_data(self) -> None:
        result = evaluate_selection_performance(
            candidate(),
            selection_date=date(2026, 1, 5),
            bars=[make_bar(date(2026, 1, 6), suspended=True, tradable=False)],
            evaluated_at=datetime(2026, 1, 6, 17, tzinfo=UTC),
        )

        self.assertEqual(result.status, SelectionPerformanceStatus.NO_ENTRY)
        self.assertEqual(result.reason, "anchor_bar_not_tradable")

    def test_forward_performance_keeps_partial_windows_unresolved(self) -> None:
        result = evaluate_selection_performance(
            candidate(),
            selection_date=date(2026, 1, 5),
            bars=[make_bar(date(2026, 1, 6))],
            evaluated_at=datetime(2026, 1, 6, 17, tzinfo=UTC),
        )

        self.assertEqual(result.status, SelectionPerformanceStatus.UNRESOLVED)
        self.assertEqual(result.points[0].session_date, date(2026, 1, 6))
        self.assertIsNone(result.points[1].session_date)

    def test_default_policy_uses_ten_sessions_and_records_its_policy_id(self) -> None:
        entry_session = date(2026, 1, 6)
        bars = [make_bar(entry_session, open=Decimal("10.00"))]
        bars.extend(
            make_bar(
                entry_session + timedelta(days=index),
                close=Decimal("10.20"),
            )
            for index in range(1, 11)
        )
        policy = SelectionTrackingPolicy(position_notional=Decimal("10000"))

        result = evaluate_selection_outcome(
            candidate(),
            selection_date=date(2026, 1, 5),
            bars=bars,
            evaluated_at=datetime(2026, 1, 20, 17, tzinfo=UTC),
            policy=policy,
        )

        self.assertEqual(policy.horizon_sessions, 10)
        self.assertEqual(
            result.outcome_policy_id,
            "outcome-fixed-10-net-positive-v1",
        )
        self.assertEqual(result.exit_session, date(2026, 1, 16))
        self.assertEqual(result.status, SelectionOutcomeStatus.WIN)

    def test_custom_horizon_gets_a_distinct_outcome_policy_id(self) -> None:
        policy = SelectionTrackingPolicy(horizon_sessions=1)

        self.assertNotEqual(
            selection_outcome_policy_id(policy),
            "outcome-fixed-10-net-positive-v1",
        )

    def test_net_return_thresholds_produce_win_flat_or_loss(self) -> None:
        policy = SelectionTrackingPolicy(
            horizon_sessions=1,
            cost_policy=BacktestPolicy(
                commission_rate=Decimal("0"),
                minimum_commission=Decimal("0"),
                stamp_duty=Decimal("0"),
            ),
        )
        for close, expected in (
            (Decimal("10.10"), SelectionOutcomeStatus.WIN),
            (Decimal("10.00"), SelectionOutcomeStatus.FLAT),
            (Decimal("9.90"), SelectionOutcomeStatus.LOSS),
        ):
            with self.subTest(close=close):
                result = evaluate_selection_outcome(
                    candidate(),
                    selection_date=date(2026, 1, 5),
                    bars=[
                        make_bar(date(2026, 1, 6), open=Decimal("10.00")),
                        make_bar(date(2026, 1, 7), close=close),
                    ],
                    evaluated_at=datetime(2026, 1, 7, 17, tzinfo=UTC),
                    policy=policy,
                )
                self.assertEqual(result.status, expected)

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

    def test_minimum_commission_uses_explicit_position_notional(self) -> None:
        result = evaluate_selection_outcome(
            candidate(),
            selection_date=date(2026, 1, 5),
            bars=[
                make_bar(date(2026, 1, 6), open=Decimal("10.00")),
                make_bar(date(2026, 1, 7), close=Decimal("10.50")),
            ],
            evaluated_at=datetime(2026, 1, 7, 17, tzinfo=UTC),
            policy=SelectionTrackingPolicy(
                horizon_sessions=1,
                position_notional=Decimal("10000"),
                cost_policy=BacktestPolicy(
                    commission_rate=Decimal("0.001"),
                    minimum_commission=Decimal("5"),
                    stamp_duty=Decimal("0"),
                ),
            ),
        )

        self.assertEqual(result.net_return, Decimal("0.04795"))
        self.assertEqual(result.position_notional, Decimal("10000"))

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

    def test_batch_evaluation_uses_batch_selection_date_for_each_candidate(self) -> None:
        imported = TdxSelectionImport(
            batch=SelectionBatch(
                batch_id="tdx-batch",
                selection_date=date(2026, 1, 5),
                exported_at=datetime(2026, 1, 5, 15, 5, tzinfo=UTC),
                imported_at=datetime(2026, 1, 5, 15, 10, tzinfo=UTC),
                formula_name="公式 A",
                source_file_sha256="a" * 64,
                candidate_count=1,
            ),
            candidates=(candidate(),),
        )

        outcomes = evaluate_selection_batch(
            imported,
            bars=[
                make_bar(date(2026, 1, 6)),
                make_bar(date(2026, 1, 7)),
            ],
            evaluated_at=datetime(2026, 1, 7, 17, tzinfo=UTC),
            policy=SelectionTrackingPolicy(
                horizon_sessions=1,
                position_notional=Decimal("10000"),
            ),
        )

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].entry_session, date(2026, 1, 6))

    def test_main_board_costs_round_quantity_down_to_one_hundred_share_lots(self) -> None:
        result = evaluate_selection_outcome(
            candidate(),
            selection_date=date(2026, 1, 5),
            bars=[
                make_bar(date(2026, 1, 6), open=Decimal("10.03")),
                make_bar(date(2026, 1, 7), close=Decimal("10.50")),
            ],
            evaluated_at=datetime(2026, 1, 7, 17, tzinfo=UTC),
            policy=SelectionTrackingPolicy(
                horizon_sessions=1,
                position_notional=Decimal("10000"),
            ),
        )

        self.assertEqual(result.net_return, Decimal("0.04522820427606070676858313947"))

    def test_minimum_commission_without_notional_is_rejected_for_completed_outcome(self) -> None:
        with self.assertRaisesRegex(ValueError, "position_notional"):
            evaluate_selection_outcome(
                candidate(),
                selection_date=date(2026, 1, 5),
                bars=[make_bar(date(2026, 1, 6)), make_bar(date(2026, 1, 7))],
                evaluated_at=datetime(2026, 1, 7, 17, tzinfo=UTC),
                policy=SelectionTrackingPolicy(horizon_sessions=1),
            )

    def test_latest_outcome_keeps_one_row_per_policy(self) -> None:
        older = make_outcome(SelectionOutcomeStatus.WIN, Decimal("0.10")).model_copy(
            update={
                "candidate_id": candidate().candidate_id,
                "batch_id": candidate().batch_id,
                "tracking_policy_id": "policy-1",
                "evaluated_at": datetime(2026, 1, 7, 17, tzinfo=UTC),
            }
        )
        newer = older.model_copy(
            update={
                "evaluated_at": datetime(2026, 1, 8, 17, tzinfo=UTC),
                "net_return": Decimal("0.12"),
            }
        )

        latest = latest_selection_outcomes([older, newer])

        self.assertEqual(latest, (newer,))

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
    def test_summary_reveals_win_rate_at_evidence_threshold(self) -> None:
        outcomes = tuple(
            make_outcome(SelectionOutcomeStatus.WIN, Decimal("0.1")).model_copy(
                update={"candidate_id": f"candidate-{index}"}
            )
            for index in range(100)
        )

        stats = summarize_selection_outcomes(outcomes)

        self.assertTrue(stats.evidence_sufficient)
        self.assertEqual(stats.win_rate, Decimal("1"))

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
        self.assertIsNone(stats.win_rate)
        self.assertFalse(stats.evidence_sufficient)
        self.assertEqual(stats.minimum_evidence_samples, 100)
        self.assertEqual(
            stats.average_net_return,
            (Decimal("0.10") - Decimal("0.05")) / Decimal(3),
        )


if __name__ == "__main__":
    unittest.main()
