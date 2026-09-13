"""Read-model workbench service."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from stratgauge.application.workbench.helpers import (
    _available_market_evidence,
    _build_timeline,
    _cohort_views,
    _confirmed_buy_fill_ids,
    _current_observation_start,
    _normalize_symbol,
    _observation_performance,
    _order_source,
    _position_view,
    _require_aware,
    _review_state,
    _stock_source,
)
from stratgauge.application.workbench.observation_performance import (
    build_observation_performance_history,
)
from stratgauge.application.workbench.stock_performance import (
    SHANGHAI,
    StockPerformanceError,
    build_stock_performance,
)
from stratgauge.application.workbench.views import (
    MarketEvidence,
    MarketEvidenceStatus,
    WorkbenchAnalyticsView,
    WorkbenchObservationPerformance,
    WorkbenchObservationPerformanceHistory,
    WorkbenchPositionView,
    WorkbenchReadError,
    WorkbenchSignalView,
    WorkbenchStockDetail,
    WorkbenchStockPerformance,
    WorkbenchStrategyView,
    WorkbenchSummary,
    WorkbenchTradeView,
)
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
from stratgauge.domain.models import DailyBar
from stratgauge.domain.portfolio import (
    ManualFill,
    ManualOrder,
)
from stratgauge.domain.selections import (
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionBatch,
    SelectionCandidate,
    SelectionCandidateExclusion,
    SelectionCandidateReviewState,
    SelectionOutcome,
    SelectionPerformance,
)


@dataclass(frozen=True, slots=True)
class _SignalProjection:
    """Candidate metadata needed before expensive signal hydration."""

    batch: SelectionBatch
    candidate: SelectionCandidate
    annotations: tuple[SelectionAnnotation, ...] = ()
    review_annotations: tuple[SelectionAnnotation, ...] = ()
    observation_started_at: datetime | None = None
    latest_annotation: SelectionAnnotation | None = None
    latest_review_annotation: SelectionAnnotation | None = None
    exclusions: tuple[SelectionCandidateExclusion, ...] = ()
    latest_exclusion: SelectionCandidateExclusion | None = None
    review_state: SelectionCandidateReviewState = SelectionCandidateReviewState.UNREVIEWED
    excluded: bool = False


@dataclass(frozen=True, slots=True)
class _MarketEvidenceBatch:
    latest_by_symbol: dict[str, MarketEvidence]
    observation_by_key: dict[tuple[str, datetime], WorkbenchObservationPerformance]


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
        projections = self._list_signal_projections(
            selection_date=selection_date,
            query=query,
            review_state=review_state,
            include_excluded=include_excluded,
            as_of=visible_at,
        )
        return self._hydrate_signals(projections, as_of=visible_at)

    def list_daily_signals_page(
        self,
        *,
        selection_date: date | None = None,
        query: str = "",
        review_state: SelectionCandidateReviewState | None = None,
        include_excluded: bool = True,
        as_of: datetime | None = None,
        page: int,
        page_size: int,
    ) -> tuple[tuple[WorkbenchSignalView, ...], int]:
        visible_at = _require_aware(as_of)
        projections = self._list_signal_projections(
            selection_date=selection_date,
            query=query,
            review_state=review_state,
            include_excluded=include_excluded,
            as_of=visible_at,
        )
        start = (page - 1) * page_size
        selected = projections[start : start + page_size]
        return self._hydrate_signals(selected, as_of=visible_at), len(projections)

    def list_watchlist(
        self,
        *,
        query: str = "",
        as_of: datetime | None = None,
    ) -> tuple[WorkbenchSignalView, ...]:
        visible_at = _require_aware(as_of)
        projections, orders, fills = self._watchlist_scope(
            query=query,
            as_of=visible_at,
        )
        return self._hydrate_signals(
            projections,
            as_of=visible_at,
            orders=orders,
            fills=fills,
            include_observation_performance=True,
        )

    def list_watchlist_page(
        self,
        *,
        query: str = "",
        as_of: datetime | None = None,
        page: int,
        page_size: int,
    ) -> tuple[tuple[WorkbenchSignalView, ...], int]:
        visible_at = _require_aware(as_of)
        projections, orders, fills = self._watchlist_scope(
            query=query,
            as_of=visible_at,
        )
        start = (page - 1) * page_size
        selected = projections[start : start + page_size]
        return (
            self._hydrate_signals(
                selected,
                as_of=visible_at,
                orders=orders,
                fills=fills,
                include_observation_performance=True,
            ),
            len(projections),
        )

    def observation_performance(
        self,
        candidate_id: str,
        *,
        as_of: datetime | None = None,
    ) -> WorkbenchObservationPerformanceHistory:
        visible_at = _require_aware(as_of)
        projections = self._list_signal_projections(as_of=visible_at)
        projection = next(
            (item for item in projections if item.candidate.candidate_id == candidate_id.strip()),
            None,
        )
        if projection is None:
            raise WorkbenchReadError(
                "WORKBENCH_CANDIDATE_NOT_FOUND",
                "没有找到该候选的当前可见策略证据。",
                404,
            )
        market_bars, session_dates, invalid_snapshot_ids = self._observation_market_history(
            projection.candidate.symbol,
            as_of=visible_at,
        )
        return build_observation_performance_history(
            candidate_id=projection.candidate.candidate_id,
            symbol=projection.candidate.symbol,
            started_at=projection.observation_started_at,
            as_of=visible_at,
            market_bars=market_bars,
            market_session_dates=session_dates,
            invalid_snapshot_ids=invalid_snapshot_ids,
        )

    def summary(
        self,
        *,
        selection_date: date | None = None,
        as_of: datetime | None = None,
    ) -> WorkbenchSummary:
        visible_at = _require_aware(as_of)
        projections = self._list_signal_projections(
            selection_date=selection_date,
            as_of=visible_at,
            include_excluded=True,
        )
        orders, fills = self._portfolio_evidence()
        visible_fills = tuple(fill for fill in fills if fill.filled_at <= visible_at)
        fill_ids_by_candidate = _confirmed_buy_fill_ids(orders, visible_fills)
        unreviewed = sum(
            projection.review_state is SelectionCandidateReviewState.UNREVIEWED
            for projection in projections
        )
        watching = sum(
            projection.review_state is SelectionCandidateReviewState.WATCHING
            for projection in projections
        )
        preparing = sum(
            projection.review_state is SelectionCandidateReviewState.PREPARING
            for projection in projections
        )
        return WorkbenchSummary(
            as_of=visible_at,
            signal_count=len(projections),
            excluded_count=sum(projection.excluded for projection in projections),
            unreviewed_count=unreviewed,
            reviewed_count=len(projections) - unreviewed,
            watching_count=watching,
            preparing_count=preparing,
            confirmed_buy_count=sum(
                projection.candidate.candidate_id in fill_ids_by_candidate
                for projection in projections
            ),
        )

    def _list_signal_projections(
        self,
        *,
        selection_date: date | None = None,
        query: str = "",
        review_state: SelectionCandidateReviewState | None = None,
        include_excluded: bool = True,
        as_of: datetime,
        include_archived: bool = False,
        candidate_symbol: str | None = None,
    ) -> tuple[_SignalProjection, ...]:
        normalized_query = query.strip().casefold()
        try:
            candidates: list[tuple[SelectionBatch, SelectionCandidate]] = []
            for manifest in self._selection_store.list_manifests(include_archived=include_archived):
                imported = self._selection_store.load_import(manifest.batch_id)
                if imported.batch.exported_at > as_of:
                    continue
                if selection_date is not None and imported.batch.selection_date != selection_date:
                    continue
                candidates.extend(
                    (imported.batch, candidate)
                    for candidate in imported.candidates
                    if candidate_symbol is None or candidate.symbol == candidate_symbol
                )
            candidate_ids = tuple(
                dict.fromkeys(candidate.candidate_id for _, candidate in candidates)
            )
            annotations_by_candidate = self._selection_store.list_annotations_for_candidates(
                candidate_ids
            )
            visible_annotations_by_candidate = {
                candidate_id: tuple(
                    annotation
                    for annotation in annotations
                    if annotation.recorded_at <= as_of and annotation.reviewed_at <= as_of
                )
                for candidate_id, annotations in annotations_by_candidate.items()
            }
            annotations_by_review_key: dict[tuple[str, date], list[SelectionAnnotation]] = {}
            for batch, candidate in candidates:
                review_key = (candidate.symbol, batch.selection_date)
                annotations_by_review_key.setdefault(review_key, []).extend(
                    visible_annotations_by_candidate.get(candidate.candidate_id, ())
                )
            visible_annotations_by_review_key = {
                review_key: tuple(
                    sorted(annotations, key=lambda item: (item.recorded_at, item.annotation_id))
                )
                for review_key, annotations in annotations_by_review_key.items()
            }
            exclusions_by_candidate = self._selection_store.list_exclusions_for_candidates(
                candidate_ids
            )
            projections: list[_SignalProjection] = []
            for batch, candidate in candidates:
                annotations = visible_annotations_by_candidate.get(candidate.candidate_id, ())
                review_annotations = visible_annotations_by_review_key.get(
                    (candidate.symbol, batch.selection_date),
                    (),
                )
                exclusions = tuple(
                    exclusion
                    for exclusion in exclusions_by_candidate.get(candidate.candidate_id, ())
                    if exclusion.recorded_at <= as_of and exclusion.excluded_at <= as_of
                )
                latest_annotation = annotations[-1] if annotations else None
                latest_review_annotation = review_annotations[-1] if review_annotations else None
                latest_exclusion = exclusions[-1] if exclusions else None
                projection = _SignalProjection(
                    batch=batch,
                    candidate=candidate,
                    annotations=annotations,
                    review_annotations=review_annotations,
                    observation_started_at=_current_observation_start(review_annotations),
                    latest_annotation=latest_annotation,
                    latest_review_annotation=latest_review_annotation,
                    exclusions=exclusions,
                    latest_exclusion=latest_exclusion,
                    review_state=_review_state(latest_review_annotation),
                    excluded=latest_exclusion is not None,
                )
                if projection.excluded and not include_excluded:
                    continue
                if review_state is not None and projection.review_state is not review_state:
                    continue
                if normalized_query and not _matches_projection(projection, normalized_query):
                    continue
                projections.append(projection)
            return tuple(
                sorted(
                    projections,
                    key=lambda item: (
                        item.batch.selection_date,
                        item.batch.exported_at,
                        item.candidate.symbol,
                    ),
                    reverse=True,
                )
            )
        except SelectionStoreError as exc:
            raise WorkbenchReadError("WORKBENCH_READ_FAILED", str(exc)) from exc

    def _watchlist_scope(
        self,
        *,
        query: str,
        as_of: datetime,
    ) -> tuple[tuple[_SignalProjection, ...], tuple[ManualOrder, ...], tuple[ManualFill, ...]]:
        projections = self._list_signal_projections(
            query=query,
            include_excluded=False,
            as_of=as_of,
        )
        orders, fills = self._portfolio_evidence()
        visible_fills = tuple(fill for fill in fills if fill.filled_at <= as_of)
        fill_ids_by_candidate = _confirmed_buy_fill_ids(orders, visible_fills)
        return (
            tuple(
                projection
                for projection in projections
                if projection.review_state
                in {
                    SelectionCandidateReviewState.WATCHING,
                    SelectionCandidateReviewState.PREPARING,
                }
                and projection.candidate.candidate_id not in fill_ids_by_candidate
            ),
            orders,
            fills,
        )

    def _hydrate_signals(
        self,
        projections: tuple[_SignalProjection, ...],
        *,
        as_of: datetime,
        orders: tuple[ManualOrder, ...] | None = None,
        fills: tuple[ManualFill, ...] | None = None,
        include_observation_performance: bool = False,
    ) -> tuple[WorkbenchSignalView, ...]:
        if not projections:
            return ()
        if orders is None or fills is None:
            orders, fills = self._portfolio_evidence()
        fills = tuple(fill for fill in fills if fill.filled_at <= as_of)
        candidate_ids = tuple(projection.candidate.candidate_id for projection in projections)
        try:
            labels_by_candidate = self._selection_store.list_labels_for_candidates(candidate_ids)
            outcomes_by_candidate = self._selection_store.list_outcomes_for_candidates(
                candidate_ids
            )
            performances_by_candidate = self._selection_store.list_performances_for_candidates(
                candidate_ids
            )
        except SelectionStoreError as exc:
            raise WorkbenchReadError("WORKBENCH_READ_FAILED", str(exc)) from exc
        symbols = tuple(projection.candidate.symbol for projection in projections)
        observation_by_key: dict[tuple[str, datetime], WorkbenchObservationPerformance] = {}
        if include_observation_performance:
            starts_by_symbol: dict[str, set[datetime]] = {}
            for projection in projections:
                if projection.observation_started_at is not None:
                    starts_by_symbol.setdefault(projection.candidate.symbol, set()).add(
                        projection.observation_started_at
                    )
            market_by_symbol, observation_by_key = (
                self._market_evidence_for_symbols_with_observations(
                    symbols,
                    observation_starts={
                        symbol: tuple(sorted(starts)) for symbol, starts in starts_by_symbol.items()
                    },
                    as_of=as_of,
                )
            )
        else:
            market_by_symbol = self._market_evidence_for_symbols(symbols, as_of)
        fill_ids_by_candidate = _confirmed_buy_fill_ids(orders, fills)
        return tuple(
            self._build_signal(
                projection,
                labels_by_candidate=labels_by_candidate,
                outcomes_by_candidate=outcomes_by_candidate,
                performances_by_candidate=performances_by_candidate,
                market_by_symbol=market_by_symbol,
                observation_by_key=observation_by_key,
                include_observation_performance=include_observation_performance,
                fill_ids_by_candidate=fill_ids_by_candidate,
            )
            for projection in projections
        )

    def _build_signal(
        self,
        projection: _SignalProjection,
        *,
        labels_by_candidate: dict[str, tuple[ManualSelectionLabel, ...]],
        outcomes_by_candidate: dict[str, tuple[SelectionOutcome, ...]],
        performances_by_candidate: dict[str, tuple[SelectionPerformance, ...]],
        market_by_symbol: dict[str, MarketEvidence],
        observation_by_key: dict[tuple[str, datetime], WorkbenchObservationPerformance],
        include_observation_performance: bool,
        fill_ids_by_candidate: dict[str, tuple[str, ...]],
    ) -> WorkbenchSignalView:
        candidate_id = projection.candidate.candidate_id
        market = market_by_symbol[projection.candidate.symbol]
        observation_performance = None
        if include_observation_performance:
            if projection.observation_started_at is None:
                observation_performance = _observation_performance(None, market=market)
            else:
                observation_performance = observation_by_key.get(
                    (projection.candidate.symbol, projection.observation_started_at)
                )
                if observation_performance is None:
                    observation_performance = _observation_performance(
                        projection.observation_started_at,
                        market=market,
                        anchor_reason="没有找到观察开始时的本地行情证据。",
                    )
        return WorkbenchSignalView(
            batch=projection.batch,
            candidate=projection.candidate,
            labels=labels_by_candidate.get(candidate_id, ()),
            annotations=projection.annotations,
            review_annotations=projection.review_annotations,
            review_group_key=(
                f"{projection.batch.selection_date.isoformat()}:{projection.candidate.symbol}"
            ),
            review_source_candidate_id=(
                projection.latest_review_annotation.candidate_id
                if projection.latest_review_annotation is not None
                else None
            ),
            latest_annotation=projection.latest_annotation,
            latest_review_annotation=projection.latest_review_annotation,
            exclusions=projection.exclusions,
            latest_exclusion=projection.latest_exclusion,
            review_state=projection.review_state,
            excluded=projection.excluded,
            outcomes=outcomes_by_candidate.get(candidate_id, ()),
            performances=performances_by_candidate.get(candidate_id, ()),
            market=market,
            observation_performance=observation_performance,
            confirmed_buy=candidate_id in fill_ids_by_candidate,
            confirmed_buy_fill_ids=fill_ids_by_candidate.get(candidate_id, ()),
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
            market_by_symbol = self._market_evidence_for_symbols(
                (position.security.symbol for position in positions),
                visible_at,
            )
            return tuple(
                _position_view(
                    position,
                    orders,
                    fills,
                    market=market_by_symbol[position.security.symbol],
                    as_of=visible_at,
                )
                for position in positions
            )
        except (PortfolioStoreError, SnapshotStoreError) as exc:
            raise WorkbenchReadError("WORKBENCH_READ_FAILED", str(exc)) from exc

    def stock_performance(
        self,
        symbol: str,
        *,
        as_of: datetime | None = None,
    ) -> WorkbenchStockPerformance:
        visible_at = _require_aware(as_of)
        normalized_symbol = _normalize_symbol(symbol)
        try:
            orders = self._portfolio_store.list_orders()
            fills = self._portfolio_store.list_fills()
            orders_by_id = {order.order_id: order for order in orders}
            symbol_fills = tuple(
                fill
                for fill in fills
                if fill.filled_at <= visible_at
                and orders_by_id.get(fill.order_id) is not None
                and orders_by_id[fill.order_id].security.symbol == normalized_symbol
            )
            first_fill_date = (
                min(fill.filled_at.astimezone(SHANGHAI).date() for fill in symbol_fills)
                if symbol_fills
                else None
            )
            market_bars: dict[tuple[str, date], tuple[str, DailyBar]] = {}
            market_session_dates: set[date] = set()
            invalid_snapshot_ids: list[str] = []
            if first_fill_date is not None:
                visible_date = visible_at.astimezone(SHANGHAI).date()
                manifests = tuple(
                    sorted(
                        (
                            manifest
                            for manifest in self._market_store.list_manifests(
                                SnapshotStatus.APPROVED
                            )
                            if manifest.request_kind == "market_daily"
                            and manifest.end_date >= first_fill_date
                            and manifest.start_date <= visible_date
                        ),
                        key=lambda item: (
                            item.end_date,
                            item.created_at,
                            item.snapshot_id,
                        ),
                        reverse=True,
                    )
                )
                for manifest in manifests:
                    if manifest.start_date == manifest.end_date:
                        market_session_dates.add(manifest.start_date)
                    try:
                        bars = self._market_store.load_bars(
                            manifest.snapshot_id,
                            symbols=(normalized_symbol,),
                        )
                    except SnapshotStoreError:
                        invalid_snapshot_ids.append(manifest.snapshot_id)
                        continue
                    for bar in bars:
                        market_bars.setdefault(
                            (bar.symbol, bar.session_date),
                            (manifest.snapshot_id, bar),
                        )
                        market_session_dates.add(bar.session_date)
            return build_stock_performance(
                normalized_symbol,
                orders,
                fills,
                market_bars,
                as_of=visible_at,
                market_session_dates=market_session_dates,
                invalid_snapshot_ids=invalid_snapshot_ids,
            )
        except (PortfolioStoreError, SnapshotStoreError, StockPerformanceError) as exc:
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
            tracking_cases = tuple(
                case
                for case in self._portfolio_store.list_tracking_cases(symbol=normalized_symbol)
                if case.tracking_started_at <= visible_at
            )
            symbol_orders = tuple(
                order for order in orders if order.security.symbol == normalized_symbol
            )
            symbol_order_ids = {order.order_id for order in symbol_orders}
            symbol_fills = tuple(fill for fill in fills if fill.order_id in symbol_order_ids)
            projections = self._list_signal_projections(
                as_of=visible_at,
                include_archived=True,
                candidate_symbol=normalized_symbol,
            )
            if not projections and not (symbol_orders or symbol_fills or reviews or tracking_cases):
                raise WorkbenchReadError(
                    "WORKBENCH_STOCK_NOT_FOUND",
                    "没有找到该证券的策略信号或手工台账证据。",
                    404,
                )
            signals = (
                self._hydrate_signals(
                    projections,
                    as_of=visible_at,
                    orders=orders,
                    fills=fills,
                )
                if projections
                else ()
            )
            current_positions = tuple(
                position
                for position in self._portfolio_store.positions()
                if position.security.symbol == normalized_symbol and position.quantity > 0
            )
            market = (
                signals[0].market
                if signals
                else self._market_evidence(
                    normalized_symbol,
                    visible_at,
                )
            )
            timeline = _build_timeline(
                signals,
                orders,
                fills,
                reviews,
                symbol=normalized_symbol,
                tracking_case_ids=(case.tracking_case_id for case in tracking_cases),
            )
            return WorkbenchStockDetail(
                symbol=normalized_symbol,
                name=next(
                    (
                        name
                        for name in (
                            *(signal.candidate.name for signal in signals),
                            *(order.security.name for order in symbol_orders),
                            *(case.security.name for case in tracking_cases),
                        )
                        if name
                    ),
                    None,
                ),
                as_of=visible_at,
                source=_stock_source(
                    signals,
                    symbol_orders,
                    symbol_fills,
                    reviews,
                    tracking_cases,
                ),
                market=market,
                position=current_positions[0] if current_positions else None,
                tracking_cases=tracking_cases,
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

    def _observation_market_history(
        self,
        symbol: str,
        *,
        as_of: datetime,
    ) -> tuple[dict[date, tuple[str, DailyBar]], set[date], tuple[str, ...]]:
        visible_date = as_of.astimezone(SHANGHAI).date()
        try:
            manifests = tuple(
                sorted(
                    (
                        manifest
                        for manifest in self._market_store.list_manifests(SnapshotStatus.APPROVED)
                        if manifest.request_kind == "market_daily"
                        and manifest.start_date <= visible_date
                    ),
                    key=lambda item: (item.end_date, item.created_at, item.snapshot_id),
                    reverse=True,
                )
            )
        except (SnapshotStoreError, OSError) as exc:
            raise WorkbenchReadError("WORKBENCH_MARKET_READ_FAILED", str(exc)) from exc
        market_bars: dict[date, tuple[str, DailyBar]] = {}
        session_dates: set[date] = set()
        invalid_snapshot_ids: list[str] = []
        for manifest in manifests:
            if manifest.start_date == manifest.end_date:
                session_dates.add(manifest.start_date)
            try:
                bars = self._market_store.load_bars(manifest.snapshot_id, symbols=(symbol,))
            except SnapshotStoreError:
                invalid_snapshot_ids.append(manifest.snapshot_id)
                continue
            for bar in bars:
                if bar.symbol != symbol or bar.session_date > visible_date:
                    continue
                session_dates.add(bar.session_date)
                if bar.available_at <= as_of:
                    market_bars.setdefault(bar.session_date, (manifest.snapshot_id, bar))
        return market_bars, session_dates, tuple(sorted(set(invalid_snapshot_ids)))

    def _market_evidence(self, symbol: str, as_of: datetime) -> MarketEvidence:
        return self._market_evidence_for_symbols((symbol,), as_of)[symbol]

    def _market_evidence_for_symbols(
        self,
        symbols: Iterable[str],
        as_of: datetime,
    ) -> dict[str, MarketEvidence]:
        return self._market_evidence_batch(symbols, as_of).latest_by_symbol

    def _market_evidence_for_symbols_with_observations(
        self,
        symbols: Iterable[str],
        *,
        observation_starts: dict[str, tuple[datetime, ...]],
        as_of: datetime,
    ) -> tuple[
        dict[str, MarketEvidence],
        dict[tuple[str, datetime], WorkbenchObservationPerformance],
    ]:
        batch = self._market_evidence_batch(
            symbols,
            as_of,
            observation_starts=observation_starts,
        )
        return batch.latest_by_symbol, batch.observation_by_key

    def _market_evidence_batch(
        self,
        symbols: Iterable[str],
        as_of: datetime,
        *,
        observation_starts: dict[str, tuple[datetime, ...]] | None = None,
    ) -> _MarketEvidenceBatch:
        requested = tuple(dict.fromkeys(symbols))
        if not requested:
            return _MarketEvidenceBatch(latest_by_symbol={}, observation_by_key={})
        starts = observation_starts or {}
        observation_keys = tuple(
            (symbol, started_at)
            for symbol in requested
            for started_at in dict.fromkeys(starts.get(symbol, ()))
        )
        keys_by_symbol: dict[str, list[tuple[str, datetime]]] = {symbol: [] for symbol in requested}
        for key in observation_keys:
            keys_by_symbol[key[0]].append(key)
        future_observation_keys = {key for key in observation_keys if key[1] > as_of}
        eligible_observation_keys = set(observation_keys) - future_observation_keys
        try:
            manifests = tuple(
                manifest
                for manifest in self._market_store.list_manifests(SnapshotStatus.APPROVED)
                if manifest.request_kind == "market_daily"
                and manifest.start_date <= as_of.astimezone(SHANGHAI).date()
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
        market_by_symbol: dict[str, MarketEvidence] = {}
        late_symbols: set[str] = set()
        anchor_bars: dict[tuple[str, datetime], tuple[str, DailyBar]] = {}
        late_observation_keys: set[tuple[str, datetime]] = set()
        invalid_snapshot_ids: list[str] = []
        requested_set = set(requested)
        for manifest in manifests:
            if requested_set.issubset(market_by_symbol) and eligible_observation_keys.issubset(
                anchor_bars
            ):
                break
            try:
                bars = self._market_store.load_bars(manifest.snapshot_id, symbols=requested)
            except SnapshotStoreError:
                invalid_snapshot_ids.append(manifest.snapshot_id)
                continue
            for bar in bars:
                if bar.symbol not in requested_set:
                    continue
                if bar.symbol not in market_by_symbol:
                    if (
                        bar.session_date <= as_of.astimezone(SHANGHAI).date()
                        and bar.available_at <= as_of
                    ):
                        market_by_symbol[bar.symbol] = _available_market_evidence(
                            manifest.snapshot_id,
                            bar,
                        )
                    else:
                        late_symbols.add(bar.symbol)
                for key in keys_by_symbol[bar.symbol]:
                    if key in anchor_bars or key in future_observation_keys:
                        continue
                    started_at = key[1]
                    if bar.session_date > started_at.date():
                        continue
                    if bar.available_at <= started_at and bar.available_at <= as_of:
                        anchor_bars[key] = (manifest.snapshot_id, bar)
                    else:
                        late_observation_keys.add(key)
        result: dict[str, MarketEvidence] = {}
        for symbol in requested:
            if symbol in market_by_symbol:
                result[symbol] = market_by_symbol[symbol]
            elif symbol in late_symbols:
                result[symbol] = MarketEvidence(
                    status=MarketEvidenceStatus.INCOMPLETE,
                    reason="已批准行情存在，但在当前 as_of 时点尚不可用。",
                )
            elif invalid_snapshot_ids:
                result[symbol] = MarketEvidence(
                    status=MarketEvidenceStatus.INCOMPLETE,
                    reason=("已批准行情快照无法读取：" + ", ".join(sorted(invalid_snapshot_ids))),
                )
            else:
                result[symbol] = MarketEvidence(
                    status=MarketEvidenceStatus.UNAVAILABLE,
                    reason="没有可用于该证券的已批准 market_daily 快照。",
                )
        observation_by_key: dict[tuple[str, datetime], WorkbenchObservationPerformance] = {}
        for key in observation_keys:
            symbol, started_at = key
            anchor = anchor_bars.get(key)
            anchor_snapshot_id = anchor[0] if anchor is not None else None
            anchor_bar = anchor[1] if anchor is not None else None
            if key in future_observation_keys:
                anchor_reason = "观察开始时间晚于当前 as_of，无法计算观察期涨跌幅。"
            elif key in late_observation_keys:
                anchor_reason = "观察开始时已有批准行情，但在该时点尚不可用。"
            elif anchor is None and invalid_snapshot_ids:
                anchor_reason = "已批准行情快照无法读取：" + ", ".join(sorted(invalid_snapshot_ids))
            else:
                anchor_reason = None
            observation_by_key[key] = _observation_performance(
                started_at,
                market=result[symbol],
                anchor_snapshot_id=anchor_snapshot_id,
                anchor_bar=anchor_bar,
                anchor_reason=anchor_reason,
            )
        return _MarketEvidenceBatch(
            latest_by_symbol=result,
            observation_by_key=observation_by_key,
        )


def _matches_projection(projection: _SignalProjection, query: str) -> bool:
    values = (
        projection.candidate.symbol,
        projection.candidate.name or "",
        projection.batch.formula_name,
        projection.batch.formula_version or "",
    )
    return any(query in value.casefold() for value in values)
