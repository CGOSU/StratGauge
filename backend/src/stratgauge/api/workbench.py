"""HTTP routes for the read-only desktop research workbench projections."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, FastAPI

from stratgauge.application.workbench import (
    WorkbenchAnalyticsView,
    WorkbenchPositionView,
    WorkbenchService,
    WorkbenchSignalView,
    WorkbenchStockDetail,
    WorkbenchStrategyView,
    WorkbenchSummary,
    WorkbenchTradeView,
)
from stratgauge.domain.selections import SelectionCandidateReviewState

router = APIRouter(prefix="/v1/workbench", tags=["workbench"])


def register_workbench_routes(
    router_target: APIRouter | FastAPI,
    service: WorkbenchService,
) -> None:
    """Register workbench routes on the application-owned router."""

    @router_target.get("/summary", response_model=WorkbenchSummary)
    def get_workbench_summary(
        selection_date: date | None = None,
        as_of: datetime | None = None,
    ) -> WorkbenchSummary:
        return service.summary(selection_date=selection_date, as_of=as_of)

    @router_target.get("/daily-signals", response_model=tuple[WorkbenchSignalView, ...])
    def list_daily_signals(
        selection_date: date | None = None,
        query: str = "",
        review_state: SelectionCandidateReviewState | None = None,
        include_excluded: bool = True,
        as_of: datetime | None = None,
    ) -> tuple[WorkbenchSignalView, ...]:
        return service.list_daily_signals(
            selection_date=selection_date,
            query=query,
            review_state=review_state,
            include_excluded=include_excluded,
            as_of=as_of,
        )

    @router_target.get("/watchlist", response_model=tuple[WorkbenchSignalView, ...])
    def list_watchlist(
        query: str = "",
        as_of: datetime | None = None,
    ) -> tuple[WorkbenchSignalView, ...]:
        return service.list_watchlist(query=query, as_of=as_of)

    @router_target.get("/strategies", response_model=tuple[WorkbenchStrategyView, ...])
    def list_strategies(as_of: datetime | None = None) -> tuple[WorkbenchStrategyView, ...]:
        return service.list_strategies(as_of=as_of)

    @router_target.get("/analytics", response_model=WorkbenchAnalyticsView)
    def get_analytics(as_of: datetime | None = None) -> WorkbenchAnalyticsView:
        return service.analytics(as_of=as_of)

    @router_target.get("/positions", response_model=tuple[WorkbenchPositionView, ...])
    def list_positions(as_of: datetime | None = None) -> tuple[WorkbenchPositionView, ...]:
        return service.list_positions(as_of=as_of)

    @router_target.get("/trades", response_model=tuple[WorkbenchTradeView, ...])
    def list_trades() -> tuple[WorkbenchTradeView, ...]:
        return service.list_trades()

    @router_target.get("/stocks/{symbol}", response_model=WorkbenchStockDetail)
    def get_stock_detail(
        symbol: str,
        as_of: datetime | None = None,
    ) -> WorkbenchStockDetail:
        return service.stock_detail(symbol, as_of=as_of)


__all__ = ["register_workbench_routes", "router"]
