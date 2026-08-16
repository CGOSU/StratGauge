"""Read-model workbench service."""

from __future__ import annotations

from datetime import date, datetime

from stratgauge.application.workbench.helpers import (
    _available_market_evidence,
    _build_timeline,
    _cohort_views,
    _confirmed_buy_fill_ids,
    _matches_query,
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
    SelectionBatch,
    SelectionCandidate,
    SelectionCandidateReviewState,
)


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
