"""Pure helpers for workbench projections."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from stratgauge.application.workbench.views import (
    MarketEvidence,
    MarketEvidenceStatus,
    WorkbenchCohortMetrics,
    WorkbenchHorizonMetric,
    WorkbenchObservationPerformance,
    WorkbenchPositionView,
    WorkbenchReadError,
    WorkbenchSignalView,
    WorkbenchTimelineEvent,
)
from stratgauge.domain.models import DailyBar, Exchange
from stratgauge.domain.portfolio import (
    ManualFill,
    ManualOrder,
    ManualOrderSide,
    ManualPositionTrackingCase,
    PortfolioPosition,
    PortfolioReview,
)
from stratgauge.domain.selections import (
    SelectionAnnotation,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
    SelectionCandidateReviewState,
    SelectionPerformance,
    SelectionPerformancePoint,
    SelectionPerformanceStatus,
)


def _position_view(
    position: PortfolioPosition,
    orders: Iterable[ManualOrder],
    fills: Iterable[ManualFill],
    *,
    market: MarketEvidence,
    as_of: datetime,
) -> WorkbenchPositionView:
    symbol_orders = tuple(
        order for order in orders if order.security.symbol == position.security.symbol
    )
    order_by_id = {order.order_id: order for order in symbol_orders}
    symbol_fills = tuple(
        sorted(
            (fill for fill in fills if fill.order_id in order_by_id),
            key=lambda fill: (fill.filled_at, fill.fill_id),
        )
    )
    source = _sources_for_orders(symbol_orders)
    candidate_ids = tuple(
        sorted(
            {
                order.selection_candidate_id
                for order in symbol_orders
                if order.selection_candidate_id is not None
            }
        )
    )
    open_since = _open_position_start(symbol_fills, order_by_id)
    holding_days = max((as_of.date() - open_since.date()).days, 0) if open_since else 0
    if market.close is None:
        return WorkbenchPositionView(
            position=position,
            as_of=as_of,
            source=source,
            selection_candidate_ids=candidate_ids,
            holding_days=holding_days,
            market=market,
            unrealized_reason=market.reason or "没有可用的最新批准收盘价。",
        )
    unrealized_profit = (market.close - position.average_cost) * position.quantity
    unrealized_return = None
    if position.average_cost != 0:
        unrealized_return = (market.close - position.average_cost) / position.average_cost
    return WorkbenchPositionView(
        position=position,
        as_of=as_of,
        source=source,
        selection_candidate_ids=candidate_ids,
        holding_days=holding_days,
        market=market,
        unrealized_profit=unrealized_profit,
        unrealized_return=unrealized_return,
    )


def _sources_for_orders(
    orders: Iterable[ManualOrder],
) -> Literal["signal", "manual_discovery", "mixed"]:
    sources = {
        "signal" if order.selection_candidate_id is not None else "manual_discovery"
        for order in orders
    }
    if sources == {"signal"}:
        return "signal"
    if sources == {"manual_discovery"} or not sources:
        return "manual_discovery"
    return "mixed"


def _order_source(order: ManualOrder) -> Literal["signal", "manual_discovery", "mixed"]:
    return "signal" if order.selection_candidate_id is not None else "manual_discovery"


def _stock_source(
    signals: Iterable[WorkbenchSignalView],
    orders: Iterable[ManualOrder],
    fills: Iterable[ManualFill],
    reviews: Iterable[PortfolioReview],
    tracking_cases: Iterable[ManualPositionTrackingCase],
) -> Literal["signal", "manual_discovery", "mixed"]:
    has_signal = any(True for _ in signals)
    has_manual_evidence = (
        any(True for _ in orders)
        or any(True for _ in fills)
        or any(True for _ in reviews)
        or any(True for _ in tracking_cases)
    )
    if has_signal and has_manual_evidence:
        return "mixed"
    if has_signal:
        return "signal"
    return "manual_discovery"


def _open_position_start(
    fills: Iterable[ManualFill],
    orders_by_id: dict[str, ManualOrder],
) -> datetime | None:
    quantity = Decimal("0")
    open_since: datetime | None = None
    for fill in fills:
        order = orders_by_id.get(fill.order_id)
        if order is None:
            continue
        if order.side is ManualOrderSide.BUY:
            if quantity == 0:
                open_since = fill.filled_at
            quantity += fill.quantity
        else:
            quantity = max(quantity - fill.quantity, Decimal("0"))
            if quantity == 0:
                open_since = None
    return open_since


def _cohort_views(
    signals: Iterable[WorkbenchSignalView],
    orders: Iterable[ManualOrder],
    fills: Iterable[ManualFill],
) -> tuple[WorkbenchCohortMetrics, ...]:
    signal_list = tuple(signals)
    confirmed_trade_ids = _confirmed_trade_candidate_ids(signal_list, orders, fills)
    cohorts: tuple[
        tuple[
            Literal["all_signals", "human_bullish", "confirmed_trades"],
            tuple[WorkbenchSignalView, ...],
        ],
        ...,
    ] = (
        ("all_signals", tuple(signal_list)),
        (
            "human_bullish",
            tuple(
                signal
                for signal in signal_list
                if signal.annotations
                and signal.annotations[0].outlook is SelectionAnnotationOutlook.BULLISH
            ),
        ),
        (
            "confirmed_trades",
            tuple(
                signal
                for signal in signal_list
                if signal.candidate.candidate_id in confirmed_trade_ids
            ),
        ),
    )
    return tuple(_cohort_metrics(cohort, cohort_signals) for cohort, cohort_signals in cohorts)


def _cohort_metrics(
    cohort: Literal["all_signals", "human_bullish", "confirmed_trades"],
    signals: tuple[WorkbenchSignalView, ...],
) -> WorkbenchCohortMetrics:
    performance_by_candidate: dict[str, SelectionPerformance] = {}
    ambiguous_candidates: set[str] = set()
    for signal in signals:
        policies = {performance.performance_policy_id for performance in signal.performances}
        resolved = tuple(
            performance
            for performance in signal.performances
            if performance.status is SelectionPerformanceStatus.RESOLVED
        )
        if len(policies) > 1:
            ambiguous_candidates.add(signal.candidate.candidate_id)
            continue
        if resolved:
            performance_by_candidate[signal.candidate.candidate_id] = max(
                resolved,
                key=lambda performance: (
                    performance.evaluated_at,
                    performance.performance_policy_id,
                ),
            )
    sample_count = len(signals)
    resolved_count = sum(
        signal.candidate.candidate_id in performance_by_candidate
        and signal.candidate.candidate_id not in ambiguous_candidates
        and not signal.excluded
        for signal in signals
    )
    excluded_count = sample_count - resolved_count
    return WorkbenchCohortMetrics(
        cohort=cohort,
        sample_count=sample_count,
        resolved_count=resolved_count,
        excluded_count=excluded_count,
        horizons=tuple(
            _horizon_metric(horizon, signals, performance_by_candidate, ambiguous_candidates)
            for horizon in (1, 3, 5, 10)
        ),
    )


def _horizon_metric(
    horizon: int,
    signals: tuple[WorkbenchSignalView, ...],
    performance_by_candidate: dict[str, SelectionPerformance],
    ambiguous_candidates: set[str],
) -> WorkbenchHorizonMetric:
    points: list[SelectionPerformancePoint] = []
    for signal in signals:
        if signal.excluded or signal.candidate.candidate_id in ambiguous_candidates:
            continue
        performance = performance_by_candidate.get(signal.candidate.candidate_id)
        if performance is None:
            continue
        points.extend(
            point
            for point in performance.points
            if point.horizon_sessions == horizon and point.return_from_anchor is not None
        )
    sample_count = len(points)
    if not points:
        return WorkbenchHorizonMetric(horizon_sessions=horizon, sample_count=0)
    returns = [point.return_from_anchor for point in points if point.return_from_anchor is not None]
    rises = [point.maximum_rise for point in points if point.maximum_rise is not None]
    drawdowns = [point.maximum_drawdown for point in points if point.maximum_drawdown is not None]
    return WorkbenchHorizonMetric(
        horizon_sessions=horizon,
        sample_count=sample_count,
        average_return=_average(returns),
        average_maximum_rise=_average(rises),
        average_maximum_drawdown=_average(drawdowns),
        target_3_percent_rate=Decimal(sum(value >= Decimal("0.03") for value in returns))
        / Decimal(sample_count),
        target_5_percent_rate=Decimal(sum(value >= Decimal("0.05") for value in returns))
        / Decimal(sample_count),
    )


def _average(values: Iterable[Decimal | None]) -> Decimal | None:
    present = [value for value in values if value is not None]
    return sum(present, Decimal("0")) / Decimal(len(present)) if present else None


def _confirmed_trade_candidate_ids(
    signals: tuple[WorkbenchSignalView, ...],
    orders: Iterable[ManualOrder],
    fills: Iterable[ManualFill],
) -> set[str]:
    orders_by_id = {order.order_id: order for order in orders}
    filled_order_ids = {fill.order_id for fill in fills}
    sell_symbols = {
        order.security.symbol
        for order in orders_by_id.values()
        if order.side is ManualOrderSide.SELL and order.order_id in filled_order_ids
    }
    candidates_by_symbol: dict[str, set[str]] = {}
    for order in orders_by_id.values():
        if (
            order.side is ManualOrderSide.BUY
            and order.selection_candidate_id is not None
            and order.order_id in filled_order_ids
        ):
            candidates_by_symbol.setdefault(order.security.symbol, set()).add(
                order.selection_candidate_id
            )
    return {
        signal.candidate.candidate_id
        for signal in signals
        if signal.candidate.symbol in sell_symbols
        and candidates_by_symbol.get(signal.candidate.symbol, set())
        == {signal.candidate.candidate_id}
    }


def _available_market_evidence(snapshot_id: str, bar: DailyBar) -> MarketEvidence:
    change_percent = None
    if bar.previous_close is not None and bar.previous_close != 0:
        change_percent = (bar.close - bar.previous_close) / bar.previous_close
    return MarketEvidence(
        status=MarketEvidenceStatus.AVAILABLE,
        snapshot_id=snapshot_id,
        provider=bar.provider,
        session_date=bar.session_date,
        available_at=bar.available_at,
        close=bar.close,
        previous_close=bar.previous_close,
        change_percent=change_percent,
        volume=bar.volume,
        amount=bar.amount,
    )


_WATCH_DECISIONS = frozenset(
    {SelectionAnnotationDecision.WATCH, SelectionAnnotationDecision.PREPARE_BUY}
)


def _current_observation_start(
    annotations: Iterable[SelectionAnnotation],
) -> datetime | None:
    """Return the first reviewed time in the latest uninterrupted watch period."""

    ordered = tuple(annotations)
    if not ordered or ordered[-1].decision not in _WATCH_DECISIONS:
        return None
    started_at = ordered[-1].reviewed_at
    for annotation in reversed(ordered[:-1]):
        if annotation.decision not in _WATCH_DECISIONS:
            break
        started_at = annotation.reviewed_at
    return started_at


def _observation_performance(
    started_at: datetime | None,
    *,
    market: MarketEvidence,
    anchor_snapshot_id: str | None = None,
    anchor_bar: DailyBar | None = None,
    anchor_reason: str | None = None,
) -> WorkbenchObservationPerformance:
    """Build a two-sided observation return without manufacturing missing evidence."""

    if started_at is None:
        return WorkbenchObservationPerformance(
            status=MarketEvidenceStatus.UNAVAILABLE,
            reason="没有当前连续观察周期。",
        )
    if anchor_bar is None:
        status = (
            MarketEvidenceStatus.INCOMPLETE
            if market.status is MarketEvidenceStatus.AVAILABLE
            else market.status
        )
        return WorkbenchObservationPerformance(
            status=status,
            started_at=started_at,
            reason=anchor_reason or "没有在观察开始时可见的已批准 market_daily 收盘价。",
        )
    if market.status is not MarketEvidenceStatus.AVAILABLE or market.close is None:
        return WorkbenchObservationPerformance(
            status=MarketEvidenceStatus.INCOMPLETE,
            started_at=started_at,
            start_snapshot_id=anchor_snapshot_id,
            start_session_date=anchor_bar.session_date,
            start_available_at=anchor_bar.available_at,
            start_close=anchor_bar.close,
            reason="最新收盘价不可用于观察期计算：" + (market.reason or "没有可用的已批准收盘价。"),
        )
    if anchor_bar.close == 0:
        return WorkbenchObservationPerformance(
            status=MarketEvidenceStatus.INCOMPLETE,
            started_at=started_at,
            start_snapshot_id=anchor_snapshot_id,
            start_session_date=anchor_bar.session_date,
            start_available_at=anchor_bar.available_at,
            start_close=anchor_bar.close,
            reason="观察开始收盘价为 0，无法计算涨跌幅。",
        )
    return WorkbenchObservationPerformance(
        status=MarketEvidenceStatus.AVAILABLE,
        started_at=started_at,
        start_snapshot_id=anchor_snapshot_id,
        start_session_date=anchor_bar.session_date,
        start_available_at=anchor_bar.available_at,
        start_close=anchor_bar.close,
        change_percent=(market.close - anchor_bar.close) / anchor_bar.close,
    )


def _confirmed_buy_fill_ids(
    orders: Iterable[ManualOrder],
    fills: Iterable[ManualFill],
) -> dict[str, tuple[str, ...]]:
    orders_by_id = {order.order_id: order for order in orders}
    result: dict[str, list[str]] = {}
    for fill in fills:
        order = orders_by_id.get(fill.order_id)
        if (
            order is None
            or order.side is not ManualOrderSide.BUY
            or order.selection_candidate_id is None
        ):
            continue
        result.setdefault(order.selection_candidate_id, []).append(fill.fill_id)
    return {candidate_id: tuple(fill_ids) for candidate_id, fill_ids in result.items()}


def _review_state(
    annotation: SelectionAnnotation | None,
) -> SelectionCandidateReviewState:
    if annotation is None:
        return SelectionCandidateReviewState.UNREVIEWED
    mapping = {
        "discard": SelectionCandidateReviewState.DISCARDED,
        "watch": SelectionCandidateReviewState.WATCHING,
        "prepare_buy": SelectionCandidateReviewState.PREPARING,
    }
    return mapping[annotation.decision.value]


def _matches_query(signal: WorkbenchSignalView, query: str) -> bool:
    values = (
        signal.candidate.symbol,
        signal.candidate.name or "",
        signal.batch.formula_name,
        signal.batch.formula_version or "",
    )
    return any(query in value.casefold() for value in values)


def _normalize_symbol(symbol: str) -> str:
    parts = symbol.strip().upper().split(".")
    if len(parts) != 2 or len(parts[0]) != 6 or not parts[0].isdigit():
        raise WorkbenchReadError("WORKBENCH_SYMBOL_INVALID", "证券必须使用 CODE.EXCHANGE。", 422)
    try:
        exchange = Exchange(parts[1])
    except ValueError as exc:
        raise WorkbenchReadError(
            "WORKBENCH_SYMBOL_INVALID", "证券必须使用 CODE.EXCHANGE。", 422
        ) from exc
    return f"{parts[0]}.{exchange.value}"


def _require_aware(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None or result.utcoffset() is None:
        raise WorkbenchReadError("WORKBENCH_TIME_INVALID", "as_of 必须包含时区。", 422)
    return result.astimezone(UTC)


def _build_timeline(
    signals: Iterable[WorkbenchSignalView],
    orders: Iterable[ManualOrder],
    fills: Iterable[ManualFill],
    reviews: Iterable[PortfolioReview],
    *,
    symbol: str | None = None,
    tracking_case_ids: Iterable[str] = (),
) -> tuple[WorkbenchTimelineEvent, ...]:
    events: list[WorkbenchTimelineEvent] = []
    signal_list = tuple(signals)
    order_list = tuple(orders)
    fill_list = tuple(fills)
    review_list = tuple(reviews)
    order_by_id = {order.order_id: order for order in order_list}
    candidate_ids = {signal.candidate.candidate_id for signal in signal_list}
    target_symbol = symbol or next(
        (
            evidence_symbol
            for evidence_symbol in (
                *(signal.candidate.symbol for signal in signal_list),
                *(order.security.symbol for order in order_list),
                *(review.symbol for review in review_list),
            )
            if evidence_symbol
        ),
        None,
    )
    if target_symbol is None:
        return ()
    tracking_case_id_set = set(tracking_case_ids)
    for signal in signal_list:
        events.append(
            WorkbenchTimelineEvent(
                event_id=f"signal:{signal.batch.batch_id}:{signal.candidate.candidate_id}",
                event_type="signal",
                occurred_at=signal.batch.exported_at,
                source_id=signal.batch.batch_id,
                title=f"策略信号：{signal.batch.formula_name}",
                detail=f"选股日期 {signal.batch.selection_date.isoformat()}",
            )
        )
        for annotation in signal.annotations:
            events.append(
                WorkbenchTimelineEvent(
                    event_id=annotation.annotation_id,
                    event_type="annotation",
                    occurred_at=annotation.reviewed_at,
                    source_id=annotation.annotation_id,
                    title=f"人工评审：{annotation.overall_score} 分",
                    detail=(
                        f"观点 {annotation.outlook.value} · 决策 {annotation.decision.value}"
                        + (f" · {annotation.note}" if annotation.note else "")
                    ),
                )
            )
        for exclusion in signal.exclusions:
            events.append(
                WorkbenchTimelineEvent(
                    event_id=exclusion.exclusion_id,
                    event_type="exclusion",
                    occurred_at=exclusion.excluded_at,
                    source_id=exclusion.exclusion_id,
                    title="候选排除",
                    detail=exclusion.reason,
                )
            )
        for outcome in signal.outcomes:
            source_id = (
                f"{outcome.candidate_id}:{outcome.tracking_policy_id}:"
                f"{outcome.evaluated_at.isoformat()}"
            )
            events.append(
                WorkbenchTimelineEvent(
                    event_id=f"outcome:{source_id}",
                    event_type="outcome",
                    occurred_at=outcome.evaluated_at,
                    source_id=source_id,
                    title=f"客观结果：{outcome.status.value}",
                    detail=outcome.reason,
                )
            )
        for performance in signal.performances:
            source_id = (
                f"{performance.candidate_id}:{performance.performance_policy_id}:"
                f"{performance.evaluated_at.isoformat()}"
            )
            events.append(
                WorkbenchTimelineEvent(
                    event_id=f"performance:{source_id}",
                    event_type="performance",
                    occurred_at=performance.evaluated_at,
                    source_id=source_id,
                    title=f"前向表现：{performance.status.value}",
                    detail=performance.reason,
                )
            )
    related_orders = tuple(
        order
        for order in order_list
        if order.security.symbol == target_symbol
        and (
            not candidate_ids
            or order.selection_candidate_id in candidate_ids
            or order.selection_candidate_id is None
            or order.tracking_case_id in tracking_case_id_set
        )
    )
    related_order_ids = {order.order_id for order in related_orders}
    for order in related_orders:
        events.append(
            WorkbenchTimelineEvent(
                event_id=order.order_id,
                event_type="order",
                occurred_at=order.ordered_at,
                source_id=order.order_id,
                title=f"{order.side.value.upper()} 订单",
                detail=order.note or "用户记录的手工订单意图",
            )
        )
    for fill in fill_list:
        fill_order = order_by_id.get(fill.order_id)
        if fill_order is None or fill.order_id not in related_order_ids:
            continue
        events.append(
            WorkbenchTimelineEvent(
                event_id=fill.fill_id,
                event_type="fill",
                occurred_at=fill.filled_at,
                source_id=fill.fill_id,
                title=f"{fill_order.side.value.upper()} 成交",
                detail=f"数量 {fill.quantity} · 价格 {fill.price}",
            )
        )
    for review in review_list:
        events.append(
            WorkbenchTimelineEvent(
                event_id=review.review_id,
                event_type="review",
                occurred_at=review.reviewed_at,
                source_id=review.review_id,
                title="持仓复盘",
                detail=review.note,
            )
        )
    return tuple(sorted(events, key=lambda event: (event.occurred_at, event.event_id)))
