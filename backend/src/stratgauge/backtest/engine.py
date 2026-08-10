"""Minimal next-session daily-bar backtester."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import NamedTuple

from stratgauge.backtest.models import (
    AmbiguousBarrierPolicy,
    BacktestPolicy,
    BacktestResult,
    EntryFillEvidence,
    Trade,
    UnfilledIntent,
)
from stratgauge.domain.models import DailyBar, Signal


class _EntryFill(NamedTuple):
    bar: DailyBar
    price: Decimal


class _Exit(NamedTuple):
    bar: DailyBar
    price: Decimal
    reason: str
    ambiguous: bool


def run_backtest(
    *,
    run_id: str,
    bars: Sequence[DailyBar],
    signals: Sequence[Signal],
    policy: BacktestPolicy,
) -> BacktestResult:
    bars_by_security: dict[str, list[DailyBar]] = defaultdict(list)
    for bar in bars:
        bars_by_security[bar.security_id].append(bar)
    for security_bars in bars_by_security.values():
        security_bars.sort(key=lambda item: item.session_date)

    trades: list[Trade] = []
    unfilled: list[UnfilledIntent] = []
    unresolved: list[str] = []
    for signal in signals:
        if signal.rejection_reason is not None:
            unfilled.append(
                UnfilledIntent(
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    reason=f"signal_rejected:{signal.rejection_reason}",
                    source_snapshot_id=signal.source_snapshot_id,
                )
            )
            continue
        entry = _find_entry(signal, bars_by_security.get(signal.security_id, []), policy)
        if entry is None:
            unfilled.append(
                UnfilledIntent(
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    reason=_entry_failure_reason(
                        signal, bars_by_security.get(signal.security_id, []), policy
                    ),
                    source_snapshot_id=signal.source_snapshot_id,
                )
            )
            continue
        exit_event = _find_exit(signal, entry, bars_by_security[signal.security_id], policy)
        if exit_event is None:
            unresolved.append(signal.signal_id)
            continue
        trades.append(_make_trade(signal, entry, exit_event, policy))

    return BacktestResult(
        run_id=run_id,
        source_snapshot_ids=tuple(sorted({signal.source_snapshot_id for signal in signals})),
        feature_set_version_ids=tuple(sorted({signal.feature_set_version for signal in signals})),
        strategy_version_ids=tuple(sorted({signal.strategy_version_id for signal in signals})),
        cost_schedule_id=policy.cost_schedule_id,
        entry_policy_id=policy.entry_policy.policy_id,
        trades=tuple(trades),
        unfilled=tuple(unfilled),
        unresolved_signal_ids=tuple(unresolved),
    )


def _find_entry(
    signal: Signal,
    bars: Sequence[DailyBar],
    policy: BacktestPolicy,
) -> _EntryFill | None:
    if signal.entry_lower is None or signal.entry_upper is None:
        return None
    for bar in bars:
        if not signal.earliest_execution <= bar.session_date <= signal.latest_execution:
            continue
        if bar.session_date <= signal.as_of:
            continue
        if not bar.tradable or bar.suspended:
            continue
        if (
            policy.entry_policy.fill_evidence is EntryFillEvidence.DAILY_RANGE_TOUCH
            and bar.open > signal.entry_upper
            and bar.low > signal.entry_upper
        ):
            continue
        if policy.entry_policy.fill_evidence is EntryFillEvidence.OPEN_ONLY:
            if bar.open > signal.entry_upper:
                continue
            raw_price = bar.open
        elif bar.open > signal.entry_upper:
            if bar.open > signal.entry_upper * (Decimal("1") + policy.entry_policy.chase_limit):
                continue
            raw_price = bar.open
        else:
            raw_price = bar.open
        return _EntryFill(bar=bar, price=raw_price * (Decimal("1") + policy.slippage))
    return None


def _entry_failure_reason(
    signal: Signal,
    bars: Sequence[DailyBar],
    policy: BacktestPolicy,
) -> str:
    if not bars:
        return "no_market_data"
    if all(not bar.tradable or bar.suspended for bar in bars):
        return "no_tradable_bar"
    if signal.entry_lower is not None and signal.entry_upper is not None:
        for bar in bars:
            if (
                signal.earliest_execution <= bar.session_date <= signal.latest_execution
                and bar.open > signal.entry_upper * (Decimal("1") + policy.entry_policy.chase_limit)
            ):
                return "gap_beyond_chase_limit"
    return "entry_zone_not_reached"


def _find_exit(
    signal: Signal,
    entry: _EntryFill,
    bars: Sequence[DailyBar],
    policy: BacktestPolicy,
) -> _Exit | None:
    holding_sessions = 0
    stop_price = signal.stop_price
    target_price = signal.target_price
    for bar in bars:
        if bar.session_date <= entry.bar.session_date:
            continue
        if not bar.tradable or bar.suspended:
            continue
        holding_sessions += 1
        stop_hit = stop_price is not None and bar.low <= stop_price
        target_hit = target_price is not None and bar.high >= target_price
        if stop_hit and target_hit:
            assert stop_price is not None
            if policy.ambiguous_barrier == AmbiguousBarrierPolicy.UNRESOLVED:
                return None
            return _Exit(
                bar=bar,
                price=stop_price * (Decimal("1") - policy.slippage),
                reason="ambiguous_conservative_stop",
                ambiguous=True,
            )
        if stop_hit:
            assert stop_price is not None
            return _Exit(
                bar=bar,
                price=stop_price * (Decimal("1") - policy.slippage),
                reason="stop",
                ambiguous=False,
            )
        if target_hit:
            assert target_price is not None
            return _Exit(
                bar=bar,
                price=target_price * (Decimal("1") - policy.slippage),
                reason="target",
                ambiguous=False,
            )
        if (
            signal.max_holding_sessions is not None
            and holding_sessions >= signal.max_holding_sessions
        ):
            return _Exit(
                bar=bar,
                price=bar.close * (Decimal("1") - policy.slippage),
                reason="time_stop",
                ambiguous=False,
            )
    return None


def _make_trade(
    signal: Signal,
    entry: _EntryFill,
    exit_event: _Exit,
    policy: BacktestPolicy,
) -> Trade:
    entry_cost = max(entry.price * policy.commission_rate, policy.minimum_commission)
    exit_cost = max(exit_event.price * policy.commission_rate, policy.minimum_commission)
    stamp_duty = exit_event.price * policy.stamp_duty
    total_cost = entry_cost + exit_cost + stamp_duty
    gross_return = exit_event.price / entry.price - Decimal("1")
    net_return = (exit_event.price - entry.price - total_cost) / entry.price
    return Trade(
        signal_id=signal.signal_id,
        security_id=signal.security_id,
        symbol=signal.symbol,
        entry_session=entry.bar.session_date.isoformat(),
        entry_price=entry.price,
        exit_session=exit_event.bar.session_date.isoformat(),
        exit_price=exit_event.price,
        exit_reason=exit_event.reason,
        gross_return=gross_return,
        net_return=net_return,
        total_cost=total_cost,
        ambiguous=exit_event.ambiguous,
        source_snapshot_id=signal.source_snapshot_id,
        strategy_version_id=signal.strategy_version_id,
        cost_schedule_id=policy.cost_schedule_id,
        entry_policy_id=policy.entry_policy.policy_id,
    )
