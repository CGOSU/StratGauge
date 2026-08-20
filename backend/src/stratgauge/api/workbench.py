"""HTTP routes for the read-only desktop research workbench projections."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

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


class WorkbenchSignalPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[WorkbenchSignalView, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    total: int = Field(ge=0)
    has_next: bool


def create_workbench_router(service: WorkbenchService) -> APIRouter:
    """Create the read-only workbench router."""
    router = APIRouter(prefix="/v1/workbench", tags=["workbench"])

    @router.get("/summary", response_model=WorkbenchSummary)
    def get_workbench_summary(
        selection_date: date | None = None,
        as_of: datetime | None = None,
    ) -> WorkbenchSummary:
        return service.summary(selection_date=selection_date, as_of=as_of)

    @router.get("/daily-signals", response_model=tuple[WorkbenchSignalView, ...])
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

    @router.get("/daily-signals/page", response_model=WorkbenchSignalPage)
    def list_daily_signals_page(
        selection_date: date | None = None,
        query: str = "",
        review_state: SelectionCandidateReviewState | None = None,
        include_excluded: bool = True,
        as_of: datetime | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> WorkbenchSignalPage:
        items = service.list_daily_signals(
            selection_date=selection_date,
            query=query,
            review_state=review_state,
            include_excluded=include_excluded,
            as_of=as_of,
        )
        return _page(items, page, page_size)

    @router.get("/watchlist", response_model=tuple[WorkbenchSignalView, ...])
    def list_watchlist(
        query: str = "",
        as_of: datetime | None = None,
    ) -> tuple[WorkbenchSignalView, ...]:
        return service.list_watchlist(query=query, as_of=as_of)

    @router.get("/watchlist/page", response_model=WorkbenchSignalPage)
    def list_watchlist_page(
        query: str = "",
        as_of: datetime | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> WorkbenchSignalPage:
        return _page(service.list_watchlist(query=query, as_of=as_of), page, page_size)

    @router.get("/strategies", response_model=tuple[WorkbenchStrategyView, ...])
    def list_strategies(as_of: datetime | None = None) -> tuple[WorkbenchStrategyView, ...]:
        return service.list_strategies(as_of=as_of)

    @router.get("/analytics", response_model=WorkbenchAnalyticsView)
    def get_analytics(as_of: datetime | None = None) -> WorkbenchAnalyticsView:
        return service.analytics(as_of=as_of)

    @router.get("/positions", response_model=tuple[WorkbenchPositionView, ...])
    def list_positions(as_of: datetime | None = None) -> tuple[WorkbenchPositionView, ...]:
        return service.list_positions(as_of=as_of)

    @router.get("/trades", response_model=tuple[WorkbenchTradeView, ...])
    def list_trades() -> tuple[WorkbenchTradeView, ...]:
        return service.list_trades()

    @router.get("/stocks/{symbol}", response_model=WorkbenchStockDetail)
    def get_stock_detail(
        symbol: str,
        as_of: datetime | None = None,
    ) -> WorkbenchStockDetail:
        return service.stock_detail(symbol, as_of=as_of)

    return router


def _page(
    items: tuple[WorkbenchSignalView, ...],
    page: int,
    page_size: int,
) -> WorkbenchSignalPage:
    start = (page - 1) * page_size
    end = start + page_size
    return WorkbenchSignalPage(
        items=items[start:end],
        page=page,
        page_size=page_size,
        total=len(items),
        has_next=end < len(items),
    )


__all__ = ["WorkbenchSignalPage", "create_workbench_router"]
