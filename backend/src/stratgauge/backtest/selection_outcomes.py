"""Fixed-horizon outcome evaluation for manually imported selections."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.backtest.models import BacktestPolicy
from stratgauge.domain.models import DailyBar
from stratgauge.domain.selections import (
    DEFAULT_OUTCOME_POLICY_ID,
    DEFAULT_PERFORMANCE_HORIZONS,
    DEFAULT_SELECTION_PERFORMANCE_POLICY_ID,
    SelectionCandidate,
    SelectionOutcome,
    SelectionOutcomeStatus,
    SelectionPerformance,
    SelectionPerformancePoint,
    SelectionPerformanceStatus,
    TdxSelectionImport,
)


class SelectionTrackingPolicy(BaseModel):
    """Explicit policy for the first objective outcome calculation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    horizon_sessions: int = Field(default=10, gt=0)
    win_threshold: Decimal = Decimal("0")
    position_notional: Decimal | None = Field(default=None, gt=0)
    cost_policy: BacktestPolicy = Field(default_factory=BacktestPolicy)

    @field_validator("win_threshold", "position_notional", mode="before")
    @classmethod
    def normalize_threshold(cls, value: Decimal | str | int | float | None) -> Decimal | None:
        if value is None:
            return None
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("tracking policy decimal must be finite")
        return result


class SelectionPerformancePolicy(BaseModel):
    """Explicit forward-window measurement policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    horizons: tuple[int, ...] = DEFAULT_PERFORMANCE_HORIZONS
    target_return: Decimal | None = None

    @field_validator("horizons")
    @classmethod
    def validate_horizons(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        normalized = tuple(sorted(set(value)))
        if not normalized or any(item <= 0 for item in normalized):
            raise ValueError("performance horizons must contain positive sessions")
        return normalized

    @field_validator("target_return", mode="before")
    @classmethod
    def normalize_target_return(
        cls,
        value: Decimal | str | int | float | None,
    ) -> Decimal | None:
        if value is None:
            return None
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError("target_return must be finite and non-negative")
        return result


def validate_selection_tracking_policy(policy: SelectionTrackingPolicy) -> None:
    if policy.cost_policy.minimum_commission > 0 and policy.position_notional is None:
        raise ValueError("position_notional is required when minimum_commission is non-zero")


def selection_tracking_policy_id(policy: SelectionTrackingPolicy) -> str:
    digest = hashlib.sha256(policy.model_dump_json().encode("utf-8")).hexdigest()
    return f"selection-policy-{digest[:20]}"


def selection_outcome_policy_id(policy: SelectionTrackingPolicy) -> str:
    """Return the immutable ID for the fixed-close outcome semantics."""

    if policy.horizon_sessions == 10 and policy.win_threshold == Decimal("0"):
        return DEFAULT_OUTCOME_POLICY_ID
    digest = hashlib.sha256(
        f"fixed-close|{policy.horizon_sessions}|{policy.win_threshold}".encode()
    ).hexdigest()
    return f"outcome-fixed-{digest[:20]}"


def selection_performance_policy_id(policy: SelectionPerformancePolicy) -> str:
    """Return the immutable ID for the fixed forward-window semantics."""

    if policy == SelectionPerformancePolicy():
        return DEFAULT_SELECTION_PERFORMANCE_POLICY_ID
    digest = hashlib.sha256(policy.model_dump_json().encode("utf-8")).hexdigest()
    return f"selection-performance-{digest[:20]}"


def evaluate_selection_performance(
    candidate: SelectionCandidate,
    *,
    selection_date: date,
    bars: Sequence[DailyBar],
    evaluated_at: datetime,
    policy: SelectionPerformancePolicy | None = None,
    source_snapshot_ids: Sequence[str] = (),
    maturity_task_id: str | None = None,
) -> SelectionPerformance:
    """Measure fixed forward windows from the next tradable session's open.

    Maximum rise uses daily highs from the anchor. Maximum drawdown uses the
    close-to-close peak-to-trough path because daily OHLC does not reveal the
    intraday order of highs and lows.
    """

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must include a timezone")
    measurement_policy = policy or SelectionPerformancePolicy()
    performance_policy_id = selection_performance_policy_id(measurement_policy)
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
    empty_points = tuple(
        SelectionPerformancePoint(horizon_sessions=horizon)
        for horizon in measurement_policy.horizons
    )
    if not available_bars:
        return SelectionPerformance(
            candidate_id=candidate.candidate_id,
            batch_id=candidate.batch_id,
            selection_date=selection_date,
            status=SelectionPerformanceStatus.UNRESOLVED,
            evaluated_at=evaluated_at,
            performance_policy_id=performance_policy_id,
            target_return=measurement_policy.target_return,
            points=empty_points,
            reason="no_market_data",
            source_snapshot_ids=lineage,
            maturity_task_id=maturity_task_id,
        )
    if not available_bars[0].tradable or available_bars[0].suspended:
        return SelectionPerformance(
            candidate_id=candidate.candidate_id,
            batch_id=candidate.batch_id,
            selection_date=selection_date,
            status=SelectionPerformanceStatus.NO_ENTRY,
            evaluated_at=evaluated_at,
            performance_policy_id=performance_policy_id,
            target_return=measurement_policy.target_return,
            points=empty_points,
            reason="anchor_bar_not_tradable",
            source_snapshot_ids=lineage,
            maturity_task_id=maturity_task_id,
        )
    candidate_bars = [bar for bar in available_bars if bar.tradable and not bar.suspended]
    anchor = candidate_bars[0]
    if anchor.open <= 0:
        return SelectionPerformance(
            candidate_id=candidate.candidate_id,
            batch_id=candidate.batch_id,
            selection_date=selection_date,
            status=SelectionPerformanceStatus.UNRESOLVED,
            evaluated_at=evaluated_at,
            performance_policy_id=performance_policy_id,
            target_return=measurement_policy.target_return,
            points=empty_points,
            reason="invalid_anchor_price",
            source_snapshot_ids=lineage,
            maturity_task_id=maturity_task_id,
        )
    anchor_price = anchor.open
    points: list[SelectionPerformancePoint] = []
    for horizon in measurement_policy.horizons:
        window = candidate_bars[:horizon]
        if len(window) < horizon:
            points.append(SelectionPerformancePoint(horizon_sessions=horizon))
            continue
        terminal = window[-1]
        running_peak: Decimal | None = None
        maximum_drawdown = Decimal("0")
        for bar in window:
            running_peak = bar.close if running_peak is None else max(running_peak, bar.close)
            maximum_drawdown = min(maximum_drawdown, bar.close / running_peak - Decimal("1"))
        maximum_rise = max(bar.high / anchor_price - Decimal("1") for bar in window)
        return_from_anchor = terminal.close / anchor_price - Decimal("1")
        points.append(
            SelectionPerformancePoint(
                horizon_sessions=horizon,
                session_date=terminal.session_date,
                close_price=terminal.close,
                return_from_anchor=return_from_anchor,
                maximum_rise=maximum_rise,
                maximum_drawdown=maximum_drawdown,
                target_achieved=(
                    maximum_rise >= measurement_policy.target_return
                    if measurement_policy.target_return is not None
                    else None
                ),
            )
        )
    status = (
        SelectionPerformanceStatus.RESOLVED
        if all(point.session_date is not None for point in points)
        else SelectionPerformanceStatus.UNRESOLVED
    )
    return SelectionPerformance(
        candidate_id=candidate.candidate_id,
        batch_id=candidate.batch_id,
        selection_date=selection_date,
        status=status,
        evaluated_at=evaluated_at,
        performance_policy_id=performance_policy_id,
        anchor_session=anchor.session_date,
        anchor_price=anchor_price,
        target_return=measurement_policy.target_return,
        points=tuple(points),
        reason="forward_windows"
        if status is SelectionPerformanceStatus.RESOLVED
        else "insufficient_horizon_data",
        source_snapshot_ids=lineage,
        maturity_task_id=maturity_task_id,
    )


def latest_selection_outcomes(
    outcomes: Iterable[SelectionOutcome],
) -> tuple[SelectionOutcome, ...]:
    """Keep one latest observation per candidate and tracking policy."""

    latest: dict[tuple[str, str], SelectionOutcome] = {}
    for outcome in outcomes:
        key = (outcome.candidate_id, outcome.tracking_policy_id)
        previous = latest.get(key)
        if previous is None or (
            outcome.evaluated_at,
            outcome.model_dump_json(),
        ) > (previous.evaluated_at, previous.model_dump_json()):
            latest[key] = outcome
    return tuple(
        sorted(
            latest.values(),
            key=lambda outcome: (
                outcome.candidate_id,
                outcome.tracking_policy_id,
                outcome.evaluated_at,
            ),
        )
    )


def evaluate_selection_batch(
    imported: TdxSelectionImport,
    *,
    bars: Sequence[DailyBar],
    evaluated_at: datetime,
    policy: SelectionTrackingPolicy,
    source_snapshot_ids: Sequence[str] = (),
) -> tuple[SelectionOutcome, ...]:
    """Evaluate every candidate in one immutable selection batch."""

    return tuple(
        evaluate_selection_outcome(
            candidate,
            selection_date=imported.batch.selection_date,
            bars=bars,
            evaluated_at=evaluated_at,
            policy=policy,
            source_snapshot_ids=source_snapshot_ids,
        )
        for candidate in imported.candidates
    )


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
            tracking_policy_id=selection_tracking_policy_id(policy),
            outcome_policy_id=selection_outcome_policy_id(policy),
            cost_schedule_id=policy.cost_policy.cost_schedule_id,
            position_notional=policy.position_notional,
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
            tracking_policy_id=selection_tracking_policy_id(policy),
            outcome_policy_id=selection_outcome_policy_id(policy),
            cost_schedule_id=policy.cost_policy.cost_schedule_id,
            position_notional=policy.position_notional,
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
    total_cost = _total_cost(entry_price, exit_price, policy)
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
        tracking_policy_id=selection_tracking_policy_id(policy),
        outcome_policy_id=selection_outcome_policy_id(policy),
        cost_schedule_id=policy.cost_policy.cost_schedule_id,
        position_notional=policy.position_notional,
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
        tracking_policy_id=selection_tracking_policy_id(policy),
        outcome_policy_id=selection_outcome_policy_id(policy),
        cost_schedule_id=policy.cost_policy.cost_schedule_id,
        position_notional=policy.position_notional,
        source_snapshot_ids=source_snapshot_ids,
    )


def _entry_price(bar: DailyBar, policy: BacktestPolicy) -> Decimal:
    return bar.open * (Decimal("1") + policy.slippage)


def _exit_price(bar: DailyBar, policy: BacktestPolicy) -> Decimal:
    return bar.close * (Decimal("1") - policy.slippage)


def _total_cost(
    entry_price: Decimal,
    exit_price: Decimal,
    policy: SelectionTrackingPolicy,
) -> Decimal:
    cost_policy = policy.cost_policy
    if cost_policy.minimum_commission > 0 and policy.position_notional is None:
        raise ValueError("position_notional is required when minimum_commission is non-zero")
    if policy.position_notional is None:
        entry_cost = entry_price * cost_policy.commission_rate
        exit_cost = exit_price * cost_policy.commission_rate
        return entry_cost + exit_cost + exit_price * cost_policy.stamp_duty

    lot_size = Decimal(cost_policy.board_lot_size)
    quantity = (policy.position_notional / entry_price / lot_size).to_integral_value(
        rounding=ROUND_FLOOR
    ) * lot_size
    if quantity <= 0:
        raise ValueError("position_notional is insufficient for one board lot")
    entry_value = quantity * entry_price
    exit_value = quantity * exit_price
    entry_cost = max(
        entry_value * cost_policy.commission_rate,
        cost_policy.minimum_commission,
    )
    exit_cost = max(exit_value * cost_policy.commission_rate, cost_policy.minimum_commission)
    return (entry_cost + exit_cost + exit_value * cost_policy.stamp_duty) / quantity


def _status(net_return: Decimal, win_threshold: Decimal) -> SelectionOutcomeStatus:
    if net_return > win_threshold:
        return SelectionOutcomeStatus.WIN
    if net_return < win_threshold:
        return SelectionOutcomeStatus.LOSS
    return SelectionOutcomeStatus.FLAT
