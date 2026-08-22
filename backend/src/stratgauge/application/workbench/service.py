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
    _normalize_symbol,
    _order_source,
    _position_view,
    _require_aware,
    _review_state,
)
from stratgauge.application.workbench.views import (
    MarketEvidence,
    MarketEvidenceStatus,
    WorkbenchAnalyticsView,
    WorkbenchPositionView,
    WorkbenchReadError,
    WorkbenchSignalView,
    WorkbenchStockDetail,
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
    latest_annotation: SelectionAnnotation | None = None
    exclusions: tuple[SelectionCandidateExclusion, ...] = ()
    latest_exclusion: SelectionCandidateExclusion | None = None
    review_state: SelectionCandidateReviewState = SelectionCandidateReviewState.UNREVIEWED
    excluded: bool = False


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
            ),
            len(projections),
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
        fill_ids_by_candidate = _confirmed_buy_fill_ids(orders, fills)
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
            exclusions_by_candidate = self._selection_store.list_exclusions_for_candidates(
                candidate_ids
            )
            projections: list[_SignalProjection] = []
            for batch, candidate in candidates:
                annotations = annotations_by_candidate.get(candidate.candidate_id, ())
                exclusions = exclusions_by_candidate.get(candidate.candidate_id, ())
                latest_annotation = annotations[-1] if annotations else None
                latest_exclusion = exclusions[-1] if exclusions else None
                projection = _SignalProjection(
                    batch=batch,
                    candidate=candidate,
                    annotations=annotations,
                    latest_annotation=latest_annotation,
                    exclusions=exclusions,
                    latest_exclusion=latest_exclusion,
                    review_state=_review_state(latest_annotation),
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
        fill_ids_by_candidate = _confirmed_buy_fill_ids(orders, fills)
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
    ) -> tuple[WorkbenchSignalView, ...]:
        if not projections:
            return ()
        if orders is None or fills is None:
            orders, fills = self._portfolio_evidence()
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
        market_by_symbol = self._market_evidence_for_symbols(
            (projection.candidate.symbol for projection in projections),
            as_of,
        )
        fill_ids_by_candidate = _confirmed_buy_fill_ids(orders, fills)
        return tuple(
            self._build_signal(
                projection,
                labels_by_candidate=labels_by_candidate,
                outcomes_by_candidate=outcomes_by_candidate,
                performances_by_candidate=performances_by_candidate,
                market_by_symbol=market_by_symbol,
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
        fill_ids_by_candidate: dict[str, tuple[str, ...]],
    ) -> WorkbenchSignalView:
        candidate_id = projection.candidate.candidate_id
        return WorkbenchSignalView(
            batch=projection.batch,
            candidate=projection.candidate,
            labels=labels_by_candidate.get(candidate_id, ()),
            annotations=projection.annotations,
            latest_annotation=projection.latest_annotation,
            exclusions=projection.exclusions,
            latest_exclusion=projection.latest_exclusion,
            review_state=projection.review_state,
            excluded=projection.excluded,
            outcomes=outcomes_by_candidate.get(candidate_id, ()),
            performances=performances_by_candidate.get(candidate_id, ()),
            market=market_by_symbol[projection.candidate.symbol],
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
            projections = self._list_signal_projections(
                as_of=visible_at,
                include_archived=True,
                candidate_symbol=normalized_symbol,
            )
            if not projections:
                raise WorkbenchReadError(
                    "WORKBENCH_STOCK_NOT_FOUND",
                    "没有找到该证券的策略信号。",
                    404,
                )
            signals = self._hydrate_signals(
                projections,
                as_of=visible_at,
                orders=orders,
                fills=fills,
            )
            timeline = _build_timeline(signals, orders, fills, reviews)
            return WorkbenchStockDetail(
                symbol=normalized_symbol,
                name=next(
                    (signal.candidate.name for signal in signals if signal.candidate.name),
                    None,
                ),
                as_of=visible_at,
                market=signals[0].market,
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

    def _market_evidence(self, symbol: str, as_of: datetime) -> MarketEvidence:
        return self._market_evidence_for_symbols((symbol,), as_of)[symbol]

    def _market_evidence_for_symbols(
        self,
        symbols: Iterable[str],
        as_of: datetime,
    ) -> dict[str, MarketEvidence]:
        requested = tuple(dict.fromkeys(symbols))
        if not requested:
            return {}
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
        market_by_symbol: dict[str, MarketEvidence] = {}
        late_symbols: set[str] = set()
        invalid_snapshot_ids: list[str] = []
        requested_set = set(requested)
        for manifest in manifests:
            try:
                bars = self._market_store.load_bars(manifest.snapshot_id)
            except SnapshotStoreError:
                invalid_snapshot_ids.append(manifest.snapshot_id)
                continue
            for bar in bars:
                if bar.symbol not in requested_set or bar.symbol in market_by_symbol:
                    continue
                if bar.available_at > as_of:
                    late_symbols.add(bar.symbol)
                    continue
                market_by_symbol[bar.symbol] = _available_market_evidence(
                    manifest.snapshot_id,
                    bar,
                )
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
        return result


def _matches_projection(projection: _SignalProjection, query: str) -> bool:
    values = (
        projection.candidate.symbol,
        projection.candidate.name or "",
        projection.batch.formula_name,
        projection.batch.formula_version or "",
    )
    return any(query in value.casefold() for value in values)
