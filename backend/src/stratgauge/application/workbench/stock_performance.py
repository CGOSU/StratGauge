"""Historical single-security net-return read projection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from stratgauge.application.workbench.views import (
    MarketEvidenceStatus,
    WorkbenchStockPerformance,
    WorkbenchStockPerformancePoint,
)
from stratgauge.domain.models import DailyBar
from stratgauge.domain.portfolio import ManualFill, ManualOrder, ManualOrderSide

SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
ZERO = Decimal("0")


class StockPerformanceError(RuntimeError):
    """A stock's confirmed fills cannot be safely replayed for performance."""


@dataclass(slots=True)
class _PositionState:
    quantity: Decimal = ZERO
    cost: Decimal = ZERO


def build_stock_performance(
    symbol: str,
    orders: Iterable[ManualOrder],
    fills: Iterable[ManualFill],
    market_bars: Mapping[tuple[str, date], tuple[str, DailyBar]],
    *,
    as_of: datetime,
    market_session_dates: Iterable[date] = (),
    invalid_snapshot_ids: Iterable[str] = (),
) -> WorkbenchStockPerformance:
    """Replay one security's confirmed fills against approved daily closes.

    Return values are Decimal ratios rather than percentages in whole-number units:
    ``Decimal("0.1")`` is rendered as 10%.  The denominator is the cumulative
    buy cash cost, including buy-side fees, so a reopened position remains
    comparable to the capital deployed across its confirmed buy fills.
    """

    orders_by_id = {order.order_id: order for order in orders}
    eligible_fills: list[tuple[ManualOrder, ManualFill]] = []
    for fill in fills:
        if fill.filled_at > as_of:
            continue
        order = orders_by_id.get(fill.order_id)
        if order is None:
            raise StockPerformanceError(f"fill references missing order: {fill.order_id}")
        if order.security.symbol == symbol:
            eligible_fills.append((order, fill))
    eligible_fills.sort(key=lambda item: (item[1].filled_at, item[1].fill_id))
    invalid = tuple(sorted(set(invalid_snapshot_ids)))
    if not eligible_fills:
        return _unavailable_performance(
            symbol,
            as_of,
            "没有截至当前 as_of 的该证券已确认成交，无法生成历史净收益率。",
        )

    state = _PositionState()
    realized_profit = ZERO
    for order, fill in eligible_fills:
        realized_profit = _apply_fill(order, fill, state, realized_profit)
    if state.quantity <= ZERO:
        return _unavailable_performance(
            symbol,
            as_of,
            "截至当前 as_of 该证券没有活动持仓（已平仓），无法生成历史净收益率。",
        )

    first_fill_date = _session_date(eligible_fills[0][1].filled_at)
    known_session_dates = set(market_session_dates)
    known_session_dates.update(
        bar.session_date for _, bar in market_bars.values() if bar.symbol == symbol
    )
    session_dates = tuple(
        sorted(
            session_date
            for session_date in known_session_dates
            if session_date >= first_fill_date and _close_time(session_date) <= as_of
        )
    )
    if not session_dates:
        reasons = ["没有可用于该证券已确认成交的 approved market_daily 估值点。"]
        if invalid:
            reasons.append("以下 approved 行情快照无法读取：" + "、".join(invalid))
        return _unavailable_performance(symbol, as_of, "；".join(reasons))

    replay_state = _PositionState()
    replay_realized = ZERO
    total_buy_cost = ZERO
    fill_index = 0
    previous_return: Decimal | None = None
    previous_point_available = False
    points: list[WorkbenchStockPerformancePoint] = []
    for session_date in session_dates:
        valuation_at = _close_time(session_date)
        while (
            fill_index < len(eligible_fills)
            and eligible_fills[fill_index][1].filled_at <= valuation_at
        ):
            order, fill = eligible_fills[fill_index]
            total_buy_cost += _buy_cash_cost(order, fill)
            replay_realized = _apply_fill(order, fill, replay_state, replay_realized)
            fill_index += 1

        market_entry = market_bars.get((symbol, session_date))
        market_snapshot_ids = (market_entry[0],) if market_entry is not None else ()
        active = replay_state.quantity > ZERO
        if active and not _bar_is_visible(market_entry, as_of, session_date):
            points.append(
                WorkbenchStockPerformancePoint(
                    session_date=session_date,
                    valuation_at=valuation_at,
                    status=MarketEvidenceStatus.INCOMPLETE,
                    market_snapshot_ids=market_snapshot_ids,
                    reason=_missing_bar_reason(
                        symbol,
                        market_entry,
                        as_of=as_of,
                        invalid_snapshot_ids=invalid,
                    ),
                )
            )
            previous_point_available = False
            continue

        if total_buy_cost <= ZERO:
            continue
        unrealized_profit = (
            market_entry[1].close * replay_state.quantity - replay_state.cost
            if active and market_entry is not None
            else ZERO
        )
        cumulative_profit = replay_realized + unrealized_profit
        cumulative_return = cumulative_profit / total_buy_cost
        if previous_point_available and previous_return is not None:
            daily_return = cumulative_return - previous_return
            point_reason = None
        elif not points:
            daily_return = None
            point_reason = None
        else:
            daily_return = None
            point_reason = "前一估值日不可估值，无法安全计算本日收益率。"
        points.append(
            WorkbenchStockPerformancePoint(
                session_date=session_date,
                valuation_at=valuation_at,
                status=MarketEvidenceStatus.AVAILABLE,
                market_snapshot_ids=market_snapshot_ids,
                daily_return=daily_return,
                cumulative_return=cumulative_return,
                reason=point_reason,
            )
        )
        previous_return = cumulative_return
        previous_point_available = True

    if not points:
        reasons = ["没有可用于该证券已确认成交的有效收益率估值点。"]
        if invalid:
            reasons.append("以下 approved 行情快照无法读取：" + "、".join(invalid))
        return _unavailable_performance(symbol, as_of, "；".join(reasons))
    return _summarize_performance(symbol, as_of, tuple(points))


def _apply_fill(
    order: ManualOrder,
    fill: ManualFill,
    state: _PositionState,
    realized_profit: Decimal,
) -> Decimal:
    fees = fill.commission + fill.stamp_duty
    if order.side is ManualOrderSide.BUY:
        state.quantity += fill.quantity
        state.cost += fill.quantity * fill.price + fees
        return realized_profit
    if fill.quantity > state.quantity:
        raise StockPerformanceError("sell fills produce a negative position")
    average_cost = state.cost / state.quantity if state.quantity else ZERO
    realized_profit += fill.quantity * fill.price - fees - average_cost * fill.quantity
    state.quantity -= fill.quantity
    state.cost -= average_cost * fill.quantity
    if state.quantity == ZERO:
        state.cost = ZERO
    return realized_profit


def _buy_cash_cost(order: ManualOrder, fill: ManualFill) -> Decimal:
    if order.side is not ManualOrderSide.BUY:
        return ZERO
    return fill.quantity * fill.price + fill.commission + fill.stamp_duty


def _summarize_performance(
    symbol: str,
    as_of: datetime,
    points: tuple[WorkbenchStockPerformancePoint, ...],
) -> WorkbenchStockPerformance:
    available_points = tuple(
        point for point in points if point.status is MarketEvidenceStatus.AVAILABLE
    )
    highest = _first_extreme(available_points, positive=True)
    maximum_loss = _first_extreme(available_points, positive=False)
    incomplete_reasons = tuple(
        dict.fromkeys(
            point.reason
            for point in points
            if point.status is MarketEvidenceStatus.INCOMPLETE and point.reason
        )
    )
    status = (
        MarketEvidenceStatus.INCOMPLETE if incomplete_reasons else MarketEvidenceStatus.AVAILABLE
    )
    return WorkbenchStockPerformance(
        symbol=symbol,
        as_of=as_of,
        status=status,
        start_session_date=points[0].session_date,
        end_session_date=points[-1].session_date,
        latest_session_date=points[-1].session_date,
        latest_daily_return=points[-1].daily_return,
        latest_cumulative_return=points[-1].cumulative_return,
        highest_return=highest[0] if highest else None,
        highest_return_date=highest[1] if highest else None,
        maximum_loss_return=maximum_loss[0] if maximum_loss else None,
        maximum_loss_date=maximum_loss[1] if maximum_loss else None,
        points=points,
        reason="；".join(incomplete_reasons) if incomplete_reasons else None,
    )


def _first_extreme(
    points: Iterable[WorkbenchStockPerformancePoint],
    *,
    positive: bool,
) -> tuple[Decimal, date] | None:
    selected: tuple[Decimal, date] | None = None
    for point in points:
        value = point.cumulative_return
        if value is None or (value <= ZERO if positive else value >= ZERO):
            continue
        if selected is None or (value > selected[0] if positive else value < selected[0]):
            selected = (value, point.session_date)
    return selected


def _unavailable_performance(
    symbol: str,
    as_of: datetime,
    reason: str,
) -> WorkbenchStockPerformance:
    return WorkbenchStockPerformance(
        symbol=symbol,
        as_of=as_of,
        status=MarketEvidenceStatus.UNAVAILABLE,
        reason=reason,
    )


def _bar_is_visible(
    market_entry: tuple[str, DailyBar] | None,
    as_of: datetime,
    session_date: date,
) -> bool:
    if market_entry is None:
        return False
    bar = market_entry[1]
    return (
        bar.event_time <= as_of and bar.available_at <= as_of and _close_time(session_date) <= as_of
    )


def _missing_bar_reason(
    symbol: str,
    market_entry: tuple[str, DailyBar] | None,
    *,
    as_of: datetime,
    invalid_snapshot_ids: tuple[str, ...],
) -> str:
    if market_entry is None:
        if invalid_snapshot_ids:
            return (
                f"缺少该证券的 approved market_daily 行情：{symbol}；"
                "以下 approved 行情快照无法读取：" + "、".join(invalid_snapshot_ids)
            )
        return f"缺少该证券的 approved market_daily 行情：{symbol}。"
    if market_entry[1].available_at > as_of:
        return "该证券的 approved market_daily 行情在当前 as_of 时点尚不可用。"
    return "该证券的 approved market_daily 估值事件晚于当前 as_of 时点。"


def _close_time(session_date: date) -> datetime:
    return datetime.combine(session_date, time(15, 0), tzinfo=SHANGHAI)


def _session_date(value: datetime) -> date:
    return value.astimezone(SHANGHAI).date()


__all__ = [
    "SHANGHAI",
    "StockPerformanceError",
    "build_stock_performance",
]
