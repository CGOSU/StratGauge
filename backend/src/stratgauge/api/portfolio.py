"""API contracts for manual portfolio records."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore, PortfolioStoreError
from stratgauge.domain.models import Exchange, Security, canonical_symbol
from stratgauge.domain.portfolio import (
    ManualFill,
    ManualOrder,
    ManualOrderSide,
    ManualSellReason,
    PortfolioPosition,
    PortfolioReview,
)

if TYPE_CHECKING:
    from stratgauge.api.selections import SelectionService


class PortfolioApiError(Exception):
    """Expected API-facing portfolio failure."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ManualOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1)
    local_code: str
    exchange: Exchange
    name: str | None = None
    side: ManualOrderSide
    ordered_at: datetime
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    strategy_version_id: str | None = None
    recommendation_id: str | None = None
    selection_candidate_id: str | None = None
    sell_reason: ManualSellReason | None = None
    note: str | None = None

    @field_validator("local_code")
    @classmethod
    def normalize_local_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 6 or not normalized.isascii() or not normalized.isdigit():
            raise ValueError("local_code must contain exactly six digits")
        return normalized

    @field_validator("ordered_at")
    @classmethod
    def require_aware_ordered_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ordered_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_sell_reason(self) -> ManualOrderRequest:
        if self.side is ManualOrderSide.SELL and self.sell_reason is None:
            raise ValueError("sell_reason is required for sell orders")
        if self.side is ManualOrderSide.BUY and self.sell_reason is not None:
            raise ValueError("sell_reason is only valid for sell orders")
        return self


class CandidateTradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1)
    fill_id: str = Field(min_length=1)
    selection_candidate_id: str = Field(min_length=1)
    local_code: str
    exchange: Exchange
    name: str | None = None
    ordered_at: datetime
    filled_at: datetime
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    commission: Decimal = Field(default=Decimal("0"), ge=0)
    stamp_duty: Decimal = Field(default=Decimal("0"), ge=0)
    note: str | None = None

    @field_validator("local_code")
    @classmethod
    def normalize_local_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 6 or not normalized.isascii() or not normalized.isdigit():
            raise ValueError("local_code must contain exactly six digits")
        return normalized

    @field_validator("ordered_at", "filled_at")
    @classmethod
    def require_aware_trade_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trade timestamps must include a timezone")
        return value


class ConfirmedManualTradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1)
    fill_id: str = Field(min_length=1)
    local_code: str
    exchange: Exchange
    name: str | None = None
    side: ManualOrderSide
    ordered_at: datetime
    filled_at: datetime
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    commission: Decimal = Field(default=Decimal("0"), ge=0)
    stamp_duty: Decimal = Field(default=Decimal("0"), ge=0)
    strategy_version_id: str | None = None
    recommendation_id: str | None = None
    selection_candidate_id: str | None = None
    sell_reason: ManualSellReason | None = None
    note: str | None = None

    @field_validator("local_code")
    @classmethod
    def normalize_local_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 6 or not normalized.isascii() or not normalized.isdigit():
            raise ValueError("local_code must contain exactly six digits")
        return normalized

    @field_validator("ordered_at", "filled_at")
    @classmethod
    def require_aware_trade_times(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trade timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_trade_reason(self) -> ConfirmedManualTradeRequest:
        if self.filled_at < self.ordered_at:
            raise ValueError("filled_at cannot precede ordered_at")
        if self.side is ManualOrderSide.SELL and self.sell_reason is None:
            raise ValueError("sell_reason is required for sell trades")
        if self.side is ManualOrderSide.BUY and self.sell_reason is not None:
            raise ValueError("sell_reason is only valid for buy trades")
        return self


class ManualFillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fill_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    filled_at: datetime
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    commission: Decimal = Field(default=Decimal("0"), ge=0)
    stamp_duty: Decimal = Field(default=Decimal("0"), ge=0)
    note: str | None = None

    @field_validator("filled_at")
    @classmethod
    def require_aware_filled_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("filled_at must include a timezone")
        return value


class PortfolioReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    reviewed_at: datetime
    note: str = Field(min_length=1)
    recommendation_id: str | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        parts = value.strip().upper().split(".")
        if len(parts) != 2:
            raise ValueError("symbol must use CODE.EXCHANGE")
        try:
            return canonical_symbol(parts[0], Exchange(parts[1]))
        except ValueError as exc:
            raise ValueError("symbol must use a valid CODE.EXCHANGE") from exc

    @field_validator("reviewed_at")
    @classmethod
    def require_aware_reviewed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must include a timezone")
        return value


class PortfolioService:
    """Translate API requests into append-only manual portfolio records."""

    def __init__(self, store: ManualPortfolioStore) -> None:
        self._store = store

    def create_order(self, request: ManualOrderRequest) -> ManualOrder:
        symbol = canonical_symbol(request.local_code, request.exchange)
        order = ManualOrder(
            order_id=request.order_id,
            security=Security(
                security_id=f"portfolio-{symbol}",
                local_code=request.local_code,
                symbol=symbol,
                exchange=request.exchange,
                name=request.name,
            ),
            side=request.side,
            ordered_at=request.ordered_at,
            quantity=request.quantity,
            limit_price=request.limit_price,
            strategy_version_id=request.strategy_version_id,
            recommendation_id=request.recommendation_id,
            selection_candidate_id=request.selection_candidate_id,
            sell_reason=request.sell_reason,
            note=request.note,
        )
        try:
            return self._store.create_order(order)
        except PortfolioStoreError as exc:
            raise PortfolioApiError("PORTFOLIO_ORDER_REJECTED", str(exc), 409) from exc

    def confirm_trade(
        self,
        request: ConfirmedManualTradeRequest,
    ) -> tuple[ManualOrder, ManualFill]:
        symbol = canonical_symbol(request.local_code, request.exchange)
        order = ManualOrder(
            order_id=request.order_id,
            security=Security(
                security_id=f"portfolio-{symbol}",
                local_code=request.local_code,
                symbol=symbol,
                exchange=request.exchange,
                name=request.name,
            ),
            side=request.side,
            ordered_at=request.ordered_at,
            quantity=request.quantity,
            strategy_version_id=request.strategy_version_id,
            recommendation_id=request.recommendation_id,
            selection_candidate_id=request.selection_candidate_id,
            sell_reason=request.sell_reason,
            note=request.note,
        )
        fill = ManualFill(
            fill_id=request.fill_id,
            order_id=request.order_id,
            filled_at=request.filled_at,
            quantity=request.quantity,
            price=request.price,
            commission=request.commission,
            stamp_duty=request.stamp_duty,
            note=request.note,
        )
        try:
            return self._store.record_order_and_fill(order, fill)
        except PortfolioStoreError as exc:
            raise PortfolioApiError("PORTFOLIO_TRADE_REJECTED", str(exc), 409) from exc

    def record_fill(self, request: ManualFillRequest) -> ManualFill:
        fill = ManualFill(**request.model_dump())
        try:
            return self._store.record_fill(fill)
        except PortfolioStoreError as exc:
            raise PortfolioApiError("PORTFOLIO_FILL_REJECTED", str(exc), 409) from exc

    def record_candidate_trade(
        self, request: CandidateTradeRequest
    ) -> tuple[ManualOrder, ManualFill]:
        if request.filled_at < request.ordered_at:
            raise PortfolioApiError("PORTFOLIO_TRADE_REJECTED", "成交时间不能早于下单时间。", 422)
        symbol = canonical_symbol(request.local_code, request.exchange)
        order = ManualOrder(
            order_id=request.order_id,
            security=Security(
                security_id=f"portfolio-{symbol}",
                local_code=request.local_code,
                symbol=symbol,
                exchange=request.exchange,
                name=request.name,
            ),
            side=ManualOrderSide.BUY,
            ordered_at=request.ordered_at,
            quantity=request.quantity,
            selection_candidate_id=request.selection_candidate_id,
            note=request.note,
        )
        fill = ManualFill(
            fill_id=request.fill_id,
            order_id=request.order_id,
            filled_at=request.filled_at,
            quantity=request.quantity,
            price=request.price,
            commission=request.commission,
            stamp_duty=request.stamp_duty,
            note=request.note,
        )
        try:
            return self._store.record_order_and_fill(order, fill)
        except PortfolioStoreError as exc:
            raise PortfolioApiError("PORTFOLIO_TRADE_REJECTED", str(exc), 409) from exc

    def write_review(self, request: PortfolioReviewRequest) -> PortfolioReview:
        review = PortfolioReview(**request.model_dump())
        try:
            return self._store.write_review(review)
        except PortfolioStoreError as exc:
            raise PortfolioApiError("PORTFOLIO_REVIEW_REJECTED", str(exc), 409) from exc

    def positions(self) -> tuple[PortfolioPosition, ...]:
        try:
            return self._store.positions()
        except PortfolioStoreError as exc:
            raise PortfolioApiError("PORTFOLIO_READ_FAILED", str(exc), 409) from exc

    def orders(self) -> tuple[ManualOrder, ...]:
        try:
            return self._store.list_orders()
        except PortfolioStoreError as exc:
            raise PortfolioApiError("PORTFOLIO_READ_FAILED", str(exc), 409) from exc

    def fills(self) -> tuple[ManualFill, ...]:
        try:
            return self._store.list_fills()
        except PortfolioStoreError as exc:
            raise PortfolioApiError("PORTFOLIO_READ_FAILED", str(exc), 409) from exc

    def reviews(self) -> tuple[PortfolioReview, ...]:
        try:
            return self._store.list_reviews()
        except PortfolioStoreError as exc:
            raise PortfolioApiError("PORTFOLIO_READ_FAILED", str(exc), 409) from exc


def create_portfolio_router(
    portfolio: PortfolioService,
    selections: SelectionService,
) -> APIRouter:
    """FastAPI router for manual portfolio endpoints."""
    router = APIRouter(tags=["portfolio"])

    @router.get("/v1/positions", response_model=list[PortfolioPosition])
    def list_positions() -> list[PortfolioPosition]:
        return list(portfolio.positions())

    @router.get("/v1/manual-orders", response_model=list[ManualOrder])
    def list_manual_orders() -> list[ManualOrder]:
        return list(portfolio.orders())

    @router.get("/v1/manual-fills", response_model=list[ManualFill])
    def list_manual_fills() -> list[ManualFill]:
        return list(portfolio.fills())

    @router.get("/v1/portfolio-reviews", response_model=list[PortfolioReview])
    def list_portfolio_reviews() -> list[PortfolioReview]:
        return list(portfolio.reviews())

    @router.post("/v1/manual-orders", response_model=ManualOrder, status_code=201)
    def create_manual_order(request: ManualOrderRequest) -> ManualOrder:
        return portfolio.create_order(request)

    @router.post(
        "/v1/manual-trades/confirm",
        response_model=tuple[ManualOrder, ManualFill],
        status_code=201,
    )
    def confirm_manual_trade(
        request: ConfirmedManualTradeRequest,
    ) -> tuple[ManualOrder, ManualFill]:
        return portfolio.confirm_trade(request)

    @router.post("/v1/manual-fills", response_model=ManualFill, status_code=201)
    def record_manual_fill(request: ManualFillRequest) -> ManualFill:
        return portfolio.record_fill(request)

    @router.post(
        "/v1/selection-candidate-trades",
        response_model=tuple[ManualOrder, ManualFill],
        status_code=201,
    )
    def record_selection_candidate_trade(
        request: CandidateTradeRequest,
    ) -> tuple[ManualOrder, ManualFill]:
        if request.filled_at < request.ordered_at:
            raise PortfolioApiError(
                "PORTFOLIO_TRADE_REJECTED",
                "成交时间不能早于下单时间。",
                422,
            )
        candidate = selections.get_candidate(request.selection_candidate_id)
        if candidate.local_code != request.local_code or candidate.exchange != request.exchange:
            raise PortfolioApiError(
                "PORTFOLIO_TRADE_REJECTED",
                "实际买入的证券必须与候选证券一致。",
                422,
            )
        return portfolio.record_candidate_trade(request)

    @router.post("/v1/portfolio-reviews", response_model=PortfolioReview, status_code=201)
    def write_portfolio_review(request: PortfolioReviewRequest) -> PortfolioReview:
        return portfolio.write_review(request)

    return router
