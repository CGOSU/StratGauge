"""Fixed-horizon outcome evaluation for manually imported selections."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.backtest.models import BacktestPolicy
from stratgauge.domain.models import DailyBar
from stratgauge.domain.selections import (
    SelectionCandidate,
    SelectionOutcome,
    SelectionOutcomeStatus,
)


class SelectionTrackingPolicy(BaseModel):
    """Explicit policy for the first objective outcome calculation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    horizon_sessions: int = Field(gt=0)
    win_threshold: Decimal = Decimal("0")
    cost_policy: BacktestPolicy = Field(default_factory=BacktestPolicy)

    @field_validator("win_threshold", mode="before")
    @classmethod
    def normalize_threshold(cls, value: Decimal | str | int | float) -> Decimal:
        return Decimal(str(value))


def evaluate_selection_outcome(
    candidate: SelectionCandidate,
    *,
    selection_date: date,
    bars: Sequence[DailyBar],
    evaluated_at: datetime,
    policy: SelectionTrackingPolicy,
    source_snapshot_ids: Sequence[str] = (),
) -> SelectionOutcome:
    """Evaluate next-tradable-entry and fixed-horizon close performance.

    This deliberately does not invent stop-loss or target rules. Those belong in a
    later, user-frozen tracking policy and can then reuse the existing backtest engine.
    """

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must include a timezone")
    lineage = tuple(sorted(set(source_snapshot_ids)))
    available_bars = sorted(
        (
            bar
            for bar in bars
            if bar.symbol == candidate.symbol
            and bar.session_date > selection_date
            and bar.available_at <= evaluated_at
        ),
        key=lambda bar: bar.session_date,
    )
    if not available_bars:
        return _unresolved(
            candidate,
            selection_date=selection_date,
            evaluated_at=evaluated_at,
            policy=policy,
            reason="no_market_data",
            source_snapshot_ids=lineage,
        )

    entry_bar = available_bars[0]
    if not entry_bar.tradable or entry_bar.suspended:
        return SelectionOutcome(
            candidate_id=candidate.candidate_id,
            batch_id=candidate.batch_id,
            selection_date=selection_date,
            status=SelectionOutcomeStatus.NO_ENTRY,
            evaluated_at=evaluated_at,
            horizon_sessions=policy.horizon_sessions,
            reason="entry_bar_not_tradable",
            source_snapshot_ids=lineage,
        )
    if entry_bar.open <= 0:
        return _unresolved(
            candidate,
            selection_date=selection_date,
            evaluated_at=evaluated_at,
            policy=policy,
            reason="invalid_entry_price",
            source_snapshot_ids=lineage,
        )

    exit_bars = [
        bar
        for bar in available_bars
        if bar.session_date > entry_bar.session_date and bar.tradable and not bar.suspended
    ]
    if len(exit_bars) < policy.horizon_sessions:
        return SelectionOutcome(
            candidate_id=candidate.candidate_id,
            batch_id=candidate.batch_id,
            selection_date=selection_date,
            status=SelectionOutcomeStatus.UNRESOLVED,
            evaluated_at=evaluated_at,
            horizon_sessions=policy.horizon_sessions,
            entry_session=entry_bar.session_date,
            entry_price=_entry_price(entry_bar, policy.cost_policy),
            reason="insufficient_horizon_data",
            source_snapshot_ids=lineage,
        )

    exit_bar = exit_bars[policy.horizon_sessions - 1]
    if exit_bar.close <= 0:
        return _unresolved(
            candidate,
            selection_date=selection_date,
            evaluated_at=evaluated_at,
            policy=policy,
            reason="invalid_exit_price",
            source_snapshot_ids=lineage,
        )
    entry_price = _entry_price(entry_bar, policy.cost_policy)
    exit_price = _exit_price(exit_bar, policy.cost_policy)
    total_cost = _total_cost(entry_price, exit_price, policy.cost_policy)
    gross_return = exit_price / entry_price - Decimal("1")
    net_return = (exit_price - entry_price - total_cost) / entry_price
    status = _status(net_return, policy.win_threshold)
    return SelectionOutcome(
        candidate_id=candidate.candidate_id,
        batch_id=candidate.batch_id,
        selection_date=selection_date,
        status=status,
        evaluated_at=evaluated_at,
        horizon_sessions=policy.horizon_sessions,
        entry_session=entry_bar.session_date,
        exit_session=exit_bar.session_date,
        entry_price=entry_price,
        exit_price=exit_price,
        gross_return=gross_return,
        net_return=net_return,
        reason="fixed_horizon",
        source_snapshot_ids=lineage,
    )


def _unresolved(
    candidate: SelectionCandidate,
    *,
    selection_date: date,
    evaluated_at: datetime,
    policy: SelectionTrackingPolicy,
    reason: str,
    source_snapshot_ids: tuple[str, ...],
) -> SelectionOutcome:
    return SelectionOutcome(
        candidate_id=candidate.candidate_id,
        batch_id=candidate.batch_id,
        selection_date=selection_date,
        status=SelectionOutcomeStatus.UNRESOLVED,
        evaluated_at=evaluated_at,
        horizon_sessions=policy.horizon_sessions,
        reason=reason,
        source_snapshot_ids=source_snapshot_ids,
    )


def _entry_price(bar: DailyBar, policy: BacktestPolicy) -> Decimal:
    return bar.open * (Decimal("1") + policy.slippage)


def _exit_price(bar: DailyBar, policy: BacktestPolicy) -> Decimal:
    return bar.close * (Decimal("1") - policy.slippage)


def _total_cost(entry_price: Decimal, exit_price: Decimal, policy: BacktestPolicy) -> Decimal:
    entry_cost = max(entry_price * policy.commission_rate, policy.minimum_commission)
    exit_cost = max(exit_price * policy.commission_rate, policy.minimum_commission)
    return entry_cost + exit_cost + exit_price * policy.stamp_duty


def _status(net_return: Decimal, win_threshold: Decimal) -> SelectionOutcomeStatus:
    if net_return > win_threshold:
        return SelectionOutcomeStatus.WIN
    if net_return < win_threshold:
        return SelectionOutcomeStatus.LOSS
    return SelectionOutcomeStatus.FLAT
