"""Request models for the selection API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.application.selection_management import SelectionApiError as SelectionApiError
from stratgauge.domain.models import Exchange
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
    SelectionPatternTag,
    SelectionPositionTag,
    SelectionPriceVolumeTag,
    SelectionReusableTagCategory,
    SelectionSectorTag,
)


class SelectionFormulaCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str | None = None
    description: str | None = None


class SelectionReusableTagCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: SelectionReusableTagCategory
    label: str = Field(min_length=1)


class SelectionReusableTagRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)


class SelectionImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_base64: str = Field(min_length=1)
    selection_date: date
    exported_at: datetime
    source_filename: str | None = None
    formula_id: str | None = None
    formula_name: str | None = None
    formula_version: str | None = None
    formula_sha256: str | None = None

    @field_validator("exported_at")
    @classmethod
    def require_aware_export_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("exported_at must include a timezone")
        return value


class ManualSelectionCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_code: str
    exchange: Exchange
    name: str | None = None
    note: str | None = None

    @field_validator("local_code")
    @classmethod
    def normalize_local_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 6 or not normalized.isascii() or not normalized.isdigit():
            raise ValueError("local_code must contain exactly six digits")
        return normalized

    @field_validator("name", "note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None


class ManualSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_date: date
    recorded_at: datetime
    formula_id: str = Field(min_length=1)
    candidates: list[ManualSelectionCandidateRequest] = Field(min_length=1)

    @field_validator("recorded_at")
    @classmethod
    def require_aware_recorded_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value


class SelectionLifecycleChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class SelectionLabelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    decision: ManualSelectionDecision
    labeled_at: datetime
    note: str | None = None

    @field_validator("labeled_at")
    @classmethod
    def require_aware_label_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("labeled_at must include a timezone")
        return value


class SelectionAnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    reviewed_at: datetime
    overall_score: int = Field(ge=1, le=5)
    outlook: SelectionAnnotationOutlook
    decision: SelectionAnnotationDecision
    position_tags: tuple[SelectionPositionTag, ...] = ()
    pattern_tags: tuple[SelectionPatternTag, ...] = ()
    price_volume_tags: tuple[SelectionPriceVolumeTag, ...] = ()
    sector_tags: tuple[SelectionSectorTag, ...] = ()
    reusable_tag_ids: tuple[str, ...] = ()
    note: str | None = None

    @field_validator("reusable_tag_ids")
    @classmethod
    def validate_reusable_tag_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value)
        if any(not item for item in normalized):
            raise ValueError("reusable tag IDs must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("reusable tag IDs must not contain duplicates")
        return normalized

    @field_validator("reviewed_at")
    @classmethod
    def require_aware_review_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must include a timezone")
        return value

    @field_validator("note")
    @classmethod
    def normalize_annotation_note(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None


class SelectionExclusionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    excluded_at: datetime
    reason: str = Field(min_length=1)

    @field_validator("excluded_at")
    @classmethod
    def require_aware_exclusion_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("excluded_at must include a timezone")
        return value

    @field_validator("reason")
    @classmethod
    def normalize_exclusion_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("exclusion reason must not be empty")
        return normalized


class SelectionMatureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["tushare", "akshare", "baostock", "waizao"]
    evaluated_at: datetime
    horizon_sessions: int = Field(default=10, gt=0)
    position_notional: Decimal = Field(gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    timeout_seconds: int = Field(default=900, gt=0, le=86400)
    target_return: Decimal | None = Field(default=None, ge=0)

    @field_validator("evaluated_at")
    @classmethod
    def require_aware_evaluation_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluated_at must include a timezone")
        return value

    @field_validator("position_notional", mode="before")
    @classmethod
    def require_finite_notional(cls, value: Decimal | str | int | float) -> Decimal:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("position_notional must be finite")
        return result


__all__ = [
    "SelectionFormulaCreateRequest",
    "SelectionReusableTagCreateRequest",
    "SelectionReusableTagRenameRequest",
    "SelectionImportRequest",
    "ManualSelectionCandidateRequest",
    "ManualSelectionRequest",
    "SelectionLifecycleChangeRequest",
    "SelectionLabelRequest",
    "SelectionAnnotationRequest",
    "SelectionExclusionRequest",
    "SelectionMatureRequest",
]
