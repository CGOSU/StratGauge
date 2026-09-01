"""Manual order, fill, review, and position contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel, Security

MANUAL_POSITION_TRACKING_POLICY_ID = "manual-position-tracking-v1"


class ManualOrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class ManualPositionTrackingOrigin(StrEnum):
    CONFIRMED_TRADE = "confirmed_trade"
    HISTORICAL_ENROLLMENT = "historical_enrollment"


class ManualPositionTrackingPolicy(DomainModel):
    """The built-in policy for recording manual holding evidence."""

    policy_id: str = MANUAL_POSITION_TRACKING_POLICY_ID
    version: int = 1
    name: str = "手工持仓跟踪 v1"
    description: str = "只记录手工持仓事实，不生成选股信号或自动交易。"

    @field_validator("policy_id")
    @classmethod
    def validate_policy_id(cls, value: str) -> str:
        if value != MANUAL_POSITION_TRACKING_POLICY_ID:
            raise ValueError("only the built-in manual position tracking policy is supported")
        return value

    @field_validator("version")
    @classmethod
    def validate_policy_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("only manual position tracking policy version 1 is supported")
        return value


DEFAULT_MANUAL_POSITION_TRACKING_POLICY = ManualPositionTrackingPolicy()


class ManualSellReason(StrEnum):
    """Structured reason recorded when a user confirms a sell."""

    TARGET_REACHED = "target_reached"
    STOP_LOSS_BROKEN = "stop_loss_broken"
    TREND_WEAKENED = "trend_weakened"
    SECTOR_WEAKENED = "sector_weakened"
    MARKET_RISK = "market_risk"
    STRATEGY_INVALIDATED = "strategy_invalidated"
    OTHER = "other"


class ManualPositionTrackingCase(DomainModel):
    """An append-only, source-specific manual holding tracking case."""

    tracking_case_id: str
    tracking_policy_id: str = MANUAL_POSITION_TRACKING_POLICY_ID
    security: Security
    tracking_started_at: datetime
    created_at: datetime
    origin: ManualPositionTrackingOrigin
    source_order_ids: tuple[str, ...] = ()
    source_fill_ids: tuple[str, ...] = ()

    @field_validator("tracking_case_id")
    @classmethod
    def validate_tracking_case_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("tracking_case_id must not be empty")
        if any(character in normalized for character in ("/", "\\", "\0", "\r", "\n")):
            raise ValueError("tracking_case_id contains an invalid path character")
        return normalized

    @field_validator("tracking_policy_id")
    @classmethod
    def validate_tracking_policy_id(cls, value: str) -> str:
        if value != MANUAL_POSITION_TRACKING_POLICY_ID:
            raise ValueError("only the built-in manual position tracking policy is supported")
        return value

    @field_validator("tracking_started_at", "created_at")
    @classmethod
    def validate_tracking_times(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("tracking timestamps must include a timezone")
        return value

    @field_validator("source_order_ids", "source_fill_ids")
    @classmethod
    def validate_source_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for item in value:
            item = item.strip()
            if not item:
                raise ValueError("tracking source identifiers must not be empty")
            if any(character in item for character in ("/", "\\", "\0", "\r", "\n")):
                raise ValueError("tracking source identifier contains an invalid path character")
            if item not in normalized:
                normalized.append(item)
        return tuple(normalized)


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
    selection_candidate_id: str | None = None
    tracking_case_id: str | None = None
    sell_reason: ManualSellReason | None = None
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

    @field_validator(
        "strategy_version_id",
        "recommendation_id",
        "selection_candidate_id",
        "tracking_case_id",
        "note",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @model_validator(mode="after")
    def validate_tracking_source(self) -> ManualOrder:
        if self.selection_candidate_id is not None and self.tracking_case_id is not None:
            raise ValueError("selection candidate orders cannot reference manual tracking cases")
        return self


class ManualFill(DomainModel):
    """A confirmed manual execution recorded separately from the order."""

    fill_id: str
    order_id: str
    tracking_case_id: str | None = None
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

    @field_validator("tracking_case_id", "note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
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
