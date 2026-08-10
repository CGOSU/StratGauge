"""Manual order, fill, review, and position contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel, Security


class ManualOrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class ManualOrder(DomainModel):
    """A user's intended manual order; it is not sent to a broker."""

    order_id: str
    security: Security
    side: ManualOrderSide
    ordered_at: datetime
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    strategy_version_id: str | None = None
    recommendation_id: str | None = None
    note: str | None = None

    @field_validator("order_id")
    @classmethod
    def validate_order_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("order_id must not be empty")
        if any(character in normalized for character in ("/", "\\", "\0", "\r", "\n")):
            raise ValueError("order_id contains an invalid path character")
        return normalized

    @field_validator("ordered_at")
    @classmethod
    def validate_ordered_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ordered_at must include a timezone")
        return value

    @field_validator("quantity", "limit_price", mode="before")
    @classmethod
    def normalize_decimal(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("portfolio decimal must be finite")
        return result

    @field_validator("strategy_version_id", "recommendation_id", "note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None


class ManualFill(DomainModel):
    """A confirmed manual execution recorded separately from the order."""

    fill_id: str
    order_id: str
    filled_at: datetime
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    commission: Decimal = Field(default=Decimal("0"), ge=0)
    stamp_duty: Decimal = Field(default=Decimal("0"), ge=0)
    note: str | None = None

    @field_validator("fill_id", "order_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("fill identifier must not be empty")
        if any(character in normalized for character in ("/", "\\", "\0", "\r", "\n")):
            raise ValueError("fill identifier contains an invalid path character")
        return normalized

    @field_validator("filled_at")
    @classmethod
    def validate_filled_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("filled_at must include a timezone")
        return value

    @field_validator("quantity", "price", "commission", "stamp_duty", mode="before")
    @classmethod
    def normalize_fill_decimal(cls, value: Any) -> Decimal:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("fill decimal must be finite")
        return result

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None


class PortfolioReview(DomainModel):
    """An append-only human review attached to a symbol or recommendation."""

    review_id: str
    symbol: str
    reviewed_at: datetime
    note: str
    recommendation_id: str | None = None

    @field_validator("review_id")
    @classmethod
    def validate_review_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("review_id must not be empty")
        if any(character in normalized for character in ("/", "\\", "\0", "\r", "\n")):
            raise ValueError("review_id contains an invalid path character")
        return normalized

    @field_validator("symbol", "note")
    @classmethod
    def validate_review_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("review text must not be empty")
        return normalized

    @field_validator("reviewed_at")
    @classmethod
    def validate_reviewed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must include a timezone")
        return value

    @field_validator("recommendation_id")
    @classmethod
    def normalize_recommendation_id(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None


class PortfolioPosition(DomainModel):
    """A derived position, never an independently editable balance."""

    security: Security
    quantity: Decimal = Field(ge=0)
    average_cost: Decimal = Field(ge=0)
    invested_notional: Decimal = Field(ge=0)
    last_fill_at: datetime | None = None

    @field_validator("quantity", "average_cost", "invested_notional", mode="before")
    @classmethod
    def normalize_position_decimal(cls, value: Any) -> Decimal:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("position decimal must be finite")
        return result

    @field_validator("last_fill_at")
    @classmethod
    def validate_last_fill_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("last_fill_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_zero_position(self) -> PortfolioPosition:
        if self.quantity == 0 and self.average_cost != 0:
            raise ValueError("zero position must have zero average_cost")
        return self
