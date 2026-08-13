"""Read-only projections for the desktop research workbench."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stratgauge.data_pipeline.portfolio_store import (
    ManualPortfolioStore,
    PortfolioStoreError,
)
from stratgauge.data_pipeline.selection_store import (
    SelectionImportStore,
    SelectionStoreError,
)
from stratgauge.data_pipeline.store import (
    SnapshotStatus,
    SnapshotStore,
    SnapshotStoreError,
)
from stratgauge.domain.models import DailyBar, Exchange
from stratgauge.domain.portfolio import (
    ManualFill,
    ManualOrder,
    ManualOrderSide,
    PortfolioPosition,
    PortfolioReview,
)
from stratgauge.domain.selections import (
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionAnnotationOutlook,
    SelectionBatch,
    SelectionCandidate,
    SelectionCandidateExclusion,
    SelectionCandidateReviewState,
    SelectionOutcome,
    SelectionPerformance,
    SelectionPerformancePoint,
    SelectionPerformanceStatus,
)


class WorkbenchReadError(RuntimeError):
    """A workbench projection cannot be safely read."""

    def __init__(self, code: str, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class MarketEvidenceStatus(StrEnum):
    """Availability state for values shown in the workbench."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INCOMPLETE = "incomplete"


class MarketEvidence(BaseModel):
    """Latest approved close evidence, never a value from an import file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: MarketEvidenceStatus
    snapshot_id: str | None = None
    provider: str | None = None
    session_date: date | None = None
    available_at: datetime | None = None
    close: Decimal | None = None
    previous_close: Decimal | None = None
    change_percent: Decimal | None = None
    volume: Decimal | None = None
    amount: Decimal | None = None
    reason: str | None = None


class WorkbenchSignalView(BaseModel):
    """One immutable selection candidate projected as a strategy signal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    batch: SelectionBatch
    candidate: SelectionCandidate
    labels: tuple[ManualSelectionLabel, ...] = ()
    annotations: tuple[SelectionAnnotation, ...] = ()
    latest_annotation: SelectionAnnotation | None = None
    exclusions: tuple[SelectionCandidateExclusion, ...] = ()
    latest_exclusion: SelectionCandidateExclusion | None = None
    review_state: SelectionCandidateReviewState = SelectionCandidateReviewState.UNREVIEWED
    excluded: bool = False
    outcomes: tuple[SelectionOutcome, ...] = ()
    performances: tuple[SelectionPerformance, ...] = ()
    market: MarketEvidence
    confirmed_buy: bool = False
    confirmed_buy_fill_ids: tuple[str, ...] = ()


class WorkbenchSummary(BaseModel):
    """Action counts for the current workbench scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    signal_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    unreviewed_count: int = Field(ge=0)
    reviewed_count: int = Field(ge=0)
    watching_count: int = Field(ge=0)
    preparing_count: int = Field(ge=0)
    confirmed_buy_count: int = Field(ge=0)


class WorkbenchTimelineEvent(BaseModel):
    """A source-preserving event in one stock's research lifecycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    event_type: Literal[
        "signal",
        "annotation",
        "exclusion",
        "order",
        "fill",
        "review",
        "outcome",
        "performance",
    ]
    occurred_at: datetime
    source_id: str
    title: str
    detail: str


class WorkbenchStockDetail(BaseModel):
    """Stock-centric read model shared by every stock entry point."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    name: str | None = None
    as_of: datetime
    market: MarketEvidence
    signals: tuple[WorkbenchSignalView, ...]
    timeline: tuple[WorkbenchTimelineEvent, ...]


class WorkbenchPositionView(BaseModel):
    """A derived open position with explicit market-evidence boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position: PortfolioPosition
    as_of: datetime
    source: Literal["signal", "manual_discovery", "mixed"]
    selection_candidate_ids: tuple[str, ...] = ()
    holding_days: int = Field(ge=0)
    market: MarketEvidence
    unrealized_profit: Decimal | None = None
    unrealized_return: Decimal | None = None
    unrealized_reason: str | None = None


class WorkbenchTradeView(BaseModel):
    """Confirmed order/fill evidence; signals and outcomes are not trades."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order: ManualOrder
    fills: tuple[ManualFill, ...]
    source: Literal["signal", "manual_discovery", "mixed"]


class WorkbenchHorizonMetric(BaseModel):
    """Aggregated objective evidence for one forward horizon."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    horizon_sessions: int = Field(gt=0)
    sample_count: int = Field(ge=0)
    average_return: Decimal | None = None
    average_maximum_rise: Decimal | None = None
    average_maximum_drawdown: Decimal | None = None
    target_3_percent_rate: Decimal | None = None
    target_5_percent_rate: Decimal | None = None


class WorkbenchCohortMetrics(BaseModel):
    """A cohort with explicit size, resolved count and exclusions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cohort: Literal["all_signals", "human_bullish", "confirmed_trades"]
    sample_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    horizons: tuple[WorkbenchHorizonMetric, ...] = ()


class WorkbenchStrategyView(BaseModel):
    """Objective strategy-version statistics for the workbench."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    formula_id: str | None
    formula_name: str
    formula_version: str | None
    signal_count: int = Field(ge=0)
    cohorts: tuple[WorkbenchCohortMetrics, ...]


class WorkbenchAnalyticsView(BaseModel):
    """Cross-strategy objective cohort comparison."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    cohorts: tuple[WorkbenchCohortMetrics, ...]


class WorkbenchService:
    """Compose local immutable evidence without provider or write side effects."""

    def __init__(
        self,
        selection_store: SelectionImportStore,
        portfolio_store: ManualPortfolioStore,
        market_store: SnapshotStore,
    ) -> None:
        self._selection_store = selection_store
        self._portfolio_store = portfolio_store
        self._market_store = market_store

    def list_daily_signals(
        self,
        *,
        selection_date: date | None = None,
        query: str = "",
        review_state: SelectionCandidateReviewState | None = None,
        include_excluded: bool = True,
        as_of: datetime | None = None,
    ) -> tuple[WorkbenchSignalView, ...]:
        visible_at = _require_aware(as_of)
        normalized_query = query.strip().casefold()
        try:
            orders = self._portfolio_store.list_orders()
            fills = self._portfolio_store.list_fills()
            fill_ids_by_candidate = _confirmed_buy_fill_ids(orders, fills)
            signals: list[WorkbenchSignalView] = []
            for manifest in self._selection_store.list_manifests():
                imported = self._selection_store.load_import(manifest.batch_id)
                if imported.batch.exported_at > visible_at:
                    continue
                if selection_date is not None and imported.batch.selection_date != selection_date:
                    continue
                for candidate in imported.candidates:
                    signal = self._build_signal(
                        imported.batch,
                        candidate,
                        fill_ids_by_candidate=fill_ids_by_candidate,
                        as_of=visible_at,
                    )
                    if signal.excluded and not include_excluded:
                        continue
                    if review_state is not None and signal.review_state is not review_state:
                        continue
                    if normalized_query and not _matches_query(signal, normalized_query):
                        continue
                    signals.append(signal)
            return tuple(
                sorted(
                    signals,
                    key=lambda item: (
                        item.batch.selection_date,
                        item.batch.exported_at,
                        item.candidate.symbol,
                    ),
                    reverse=True,
                )
            )
        except (SelectionStoreError, PortfolioStoreError) as exc:
            raise WorkbenchReadError("WORKBENCH_READ_FAILED", str(exc)) from exc

    def list_watchlist(
        self,
        *,
        query: str = "",
        as_of: datetime | None = None,
    ) -> tuple[WorkbenchSignalView, ...]:
        signals = self.list_daily_signals(
            query=query,
            include_excluded=False,
            as_of=as_of,
        )
        return tuple(
            signal
            for signal in signals
            if signal.review_state
            in {
                SelectionCandidateReviewState.WATCHING,
                SelectionCandidateReviewState.PREPARING,
            }
            and not signal.confirmed_buy
        )

    def summary(
        self,
        *,
        selection_date: date | None = None,
        as_of: datetime | None = None,
    ) -> WorkbenchSummary:
        visible_at = _require_aware(as_of)
        signals = self.list_daily_signals(
            selection_date=selection_date,
            as_of=visible_at,
            include_excluded=True,
        )
        unreviewed = sum(
            signal.review_state is SelectionCandidateReviewState.UNREVIEWED for signal in signals
        )
        watching = sum(
            signal.review_state is SelectionCandidateReviewState.WATCHING for signal in signals
        )
        preparing = sum(
            signal.review_state is SelectionCandidateReviewState.PREPARING for signal in signals
        )
        return WorkbenchSummary(
            as_of=visible_at,
            signal_count=len(signals),
            excluded_count=sum(signal.excluded for signal in signals),
            unreviewed_count=unreviewed,
            reviewed_count=len(signals) - unreviewed,
            watching_count=watching,
            preparing_count=preparing,
            confirmed_buy_count=sum(signal.confirmed_buy for signal in signals),
        )

    def list_positions(
        self,
        *,
        as_of: datetime | None = None,
    ) -> tuple[WorkbenchPositionView, ...]:
        visible_at = _require_aware(as_of)
        try:
            orders = self._portfolio_store.list_orders()
            fills = self._portfolio_store.list_fills()
            positions = tuple(
                position for position in self._portfolio_store.positions() if position.quantity > 0
            )
            return tuple(
                _position_view(
                    position,
                    orders,
                    fills,
                    market=self._market_evidence(position.security.symbol, visible_at),
                    as_of=visible_at,
                )
                for position in positions
            )
        except (PortfolioStoreError, SnapshotStoreError) as exc:
            raise WorkbenchReadError("WORKBENCH_READ_FAILED", str(exc)) from exc

    def list_trades(self) -> tuple[WorkbenchTradeView, ...]:
        try:
            orders = self._portfolio_store.list_orders()
            fills = self._portfolio_store.list_fills()
            fills_by_order: dict[str, list[ManualFill]] = {}
            for fill in fills:
                fills_by_order.setdefault(fill.order_id, []).append(fill)
            return tuple(
                WorkbenchTradeView(
                    order=order,
                    fills=tuple(fills_by_order[order.order_id]),
                    source=_order_source(order),
                )
                for order in orders
                if order.order_id in fills_by_order
            )
        except PortfolioStoreError as exc:
            raise WorkbenchReadError("WORKBENCH_READ_FAILED", str(exc)) from exc

    def list_strategies(
        self,
        *,
        as_of: datetime | None = None,
    ) -> tuple[WorkbenchStrategyView, ...]:
        visible_at = _require_aware(as_of)
        signals = self.list_daily_signals(as_of=visible_at, include_excluded=True)
        orders, fills = self._portfolio_evidence()
        grouped: dict[tuple[str | None, str, str | None], list[WorkbenchSignalView]] = {}
        for signal in signals:
            key = (
                signal.batch.formula_id,
                signal.batch.formula_name,
                signal.batch.formula_version,
            )
            grouped.setdefault(key, []).append(signal)
        return tuple(
            WorkbenchStrategyView(
                formula_id=formula_id,
                formula_name=formula_name,
                formula_version=formula_version,
                signal_count=len(strategy_signals),
                cohorts=_cohort_views(strategy_signals, orders, fills),
            )
            for (formula_id, formula_name, formula_version), strategy_signals in sorted(
                grouped.items(),
                key=lambda item: (item[0][1], item[0][2] or ""),
            )
        )

    def analytics(self, *, as_of: datetime | None = None) -> WorkbenchAnalyticsView:
        visible_at = _require_aware(as_of)
        signals = self.list_daily_signals(as_of=visible_at, include_excluded=True)
        orders, fills = self._portfolio_evidence()
        return WorkbenchAnalyticsView(
            as_of=visible_at,
            cohorts=_cohort_views(signals, orders, fills),
        )

    def _portfolio_evidence(self) -> tuple[tuple[ManualOrder, ...], tuple[ManualFill, ...]]:
        try:
            return self._portfolio_store.list_orders(), self._portfolio_store.list_fills()
        except PortfolioStoreError as exc:
            raise WorkbenchReadError("WORKBENCH_READ_FAILED", str(exc)) from exc

    def stock_detail(
        self,
        symbol: str,
        *,
        as_of: datetime | None = None,
    ) -> WorkbenchStockDetail:
        visible_at = _require_aware(as_of)
        normalized_symbol = _normalize_symbol(symbol)
        try:
            orders = self._portfolio_store.list_orders()
            fills = self._portfolio_store.list_fills()
            reviews = self._portfolio_store.list_reviews(symbol=normalized_symbol)
            fill_ids_by_candidate = _confirmed_buy_fill_ids(orders, fills)
            signals: list[WorkbenchSignalView] = []
            for manifest in self._selection_store.list_manifests(include_archived=True):
                imported = self._selection_store.load_import(manifest.batch_id)
                if imported.batch.exported_at > visible_at:
                    continue
                for candidate in imported.candidates:
                    if candidate.symbol == normalized_symbol:
                        signals.append(
                            self._build_signal(
                                imported.batch,
                                candidate,
                                fill_ids_by_candidate=fill_ids_by_candidate,
                                as_of=visible_at,
                            )
                        )
            if not signals:
                raise WorkbenchReadError(
                    "WORKBENCH_STOCK_NOT_FOUND",
                    "没有找到该证券的策略信号。",
                    404,
                )
            timeline = _build_timeline(signals, orders, fills, reviews)
            return WorkbenchStockDetail(
                symbol=normalized_symbol,
                name=next(
                    (signal.candidate.name for signal in signals if signal.candidate.name),
                    None,
                ),
                as_of=visible_at,
                market=self._market_evidence(normalized_symbol, visible_at),
                signals=tuple(
                    sorted(
                        signals,
                        key=lambda item: (
                            item.batch.selection_date,
                            item.batch.exported_at,
                            item.batch.batch_id,
                        ),
                        reverse=True,
                    )
                ),
                timeline=timeline,
            )
        except WorkbenchReadError:
            raise
        except (SelectionStoreError, PortfolioStoreError) as exc:
            raise WorkbenchReadError("WORKBENCH_READ_FAILED", str(exc)) from exc

    def _build_signal(
        self,
        batch: SelectionBatch,
        candidate: SelectionCandidate,
        *,
        fill_ids_by_candidate: dict[str, tuple[str, ...]],
        as_of: datetime,
    ) -> WorkbenchSignalView:
        annotations = self._selection_store.list_annotations(candidate.candidate_id)
        exclusions = self._selection_store.list_exclusions(candidate.candidate_id)
        labels = self._selection_store.list_labels(candidate.candidate_id)
        outcomes = self._selection_store.list_outcomes(candidate.candidate_id)
        performances = self._selection_store.list_performances(candidate.candidate_id)
        latest_annotation = annotations[-1] if annotations else None
        latest_exclusion = exclusions[-1] if exclusions else None
        return WorkbenchSignalView(
            batch=batch,
            candidate=candidate,
            labels=labels,
            annotations=annotations,
            latest_annotation=latest_annotation,
            exclusions=exclusions,
            latest_exclusion=latest_exclusion,
            review_state=_review_state(latest_annotation),
            excluded=latest_exclusion is not None,
            outcomes=outcomes,
            performances=performances,
            market=self._market_evidence(candidate.symbol, as_of),
            confirmed_buy=candidate.candidate_id in fill_ids_by_candidate,
            confirmed_buy_fill_ids=fill_ids_by_candidate.get(candidate.candidate_id, ()),
        )

    def _market_evidence(self, symbol: str, as_of: datetime) -> MarketEvidence:
        try:
            manifests = tuple(
                manifest
                for manifest in self._market_store.list_manifests(SnapshotStatus.APPROVED)
                if manifest.request_kind == "market_daily" and manifest.end_date <= as_of.date()
            )
        except (SnapshotStoreError, OSError) as exc:
            raise WorkbenchReadError("WORKBENCH_MARKET_READ_FAILED", str(exc)) from exc
        manifests = tuple(
            sorted(
                manifests,
                key=lambda item: (item.end_date, item.created_at, item.snapshot_id),
                reverse=True,
            )
        )
        had_late_bar = False
        invalid_snapshot_ids: list[str] = []
        for manifest in manifests:
            try:
                bars = self._market_store.load_bars(manifest.snapshot_id)
            except SnapshotStoreError:
                invalid_snapshot_ids.append(manifest.snapshot_id)
                continue
            for bar in bars:
                if bar.symbol != symbol:
                    continue
                if bar.available_at > as_of:
                    had_late_bar = True
                    continue
                return _available_market_evidence(manifest.snapshot_id, bar)
        if had_late_bar:
            return MarketEvidence(
                status=MarketEvidenceStatus.INCOMPLETE,
                reason="已批准行情存在，但在当前 as_of 时点尚不可用。",
            )
        if invalid_snapshot_ids:
            return MarketEvidence(
                status=MarketEvidenceStatus.INCOMPLETE,
                reason=("已批准行情快照无法读取：" + ", ".join(sorted(invalid_snapshot_ids))),
            )
        return MarketEvidence(
            status=MarketEvidenceStatus.UNAVAILABLE,
            reason="没有可用于该证券的已批准 market_daily 快照。",
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
) -> tuple[WorkbenchTimelineEvent, ...]:
    events: list[WorkbenchTimelineEvent] = []
    order_by_id = {order.order_id: order for order in orders}
    candidate_ids = {signal.candidate.candidate_id for signal in signals}
    signal_by_candidate = {signal.candidate.candidate_id: signal for signal in signals}
    for signal in signals:
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
        for order in orders
        if order.security.symbol == next(iter(signal_by_candidate.values())).candidate.symbol
        and (order.selection_candidate_id in candidate_ids or order.selection_candidate_id is None)
    )
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
    for fill in fills:
        fill_order = order_by_id.get(fill.order_id)
        if (
            fill_order is None
            or fill_order.security.symbol
            != next(iter(signal_by_candidate.values())).candidate.symbol
        ):
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
    for review in reviews:
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
