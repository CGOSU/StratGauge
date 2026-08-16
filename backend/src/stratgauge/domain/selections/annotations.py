"""Manual label, annotation and exclusion models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.domain.selections.enums import (
    SELECTION_ANNOTATION_TAXONOMY_VERSION,
    ManualSelectionDecision,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
    SelectionPatternTag,
    SelectionPositionTag,
    SelectionPriceVolumeTag,
    SelectionSectorTag,
)
from stratgauge.domain.selections.reusable_tags import SelectionReusableTagSnapshot


class SelectionAnnotation(DomainModel):
    """An immutable, append-only human review of one selection candidate."""

    annotation_id: str
    candidate_id: str
    reviewed_at: datetime
    recorded_at: datetime
    overall_score: int = Field(ge=1, le=5)
    outlook: SelectionAnnotationOutlook
    decision: SelectionAnnotationDecision
    position_tags: tuple[SelectionPositionTag, ...] = ()
    pattern_tags: tuple[SelectionPatternTag, ...] = ()
    price_volume_tags: tuple[SelectionPriceVolumeTag, ...] = ()
    sector_tags: tuple[SelectionSectorTag, ...] = ()
    reusable_tag_snapshots: tuple[SelectionReusableTagSnapshot, ...] = ()
    taxonomy_version: str = SELECTION_ANNOTATION_TAXONOMY_VERSION
    note: str | None = None

    @field_validator("annotation_id", "candidate_id")
    @classmethod
    def validate_annotation_ids(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("annotation identifier must not be empty")
        if any(character in normalized for character in ("/", "\\", "\0", "\r", "\n")):
            raise ValueError("annotation identifier contains an invalid path character")
        return normalized

    @field_validator("reviewed_at", "recorded_at")
    @classmethod
    def validate_annotation_times(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("annotation timestamps must include a timezone")
        return value

    @field_validator(
        "position_tags",
        "pattern_tags",
        "price_volume_tags",
        "sector_tags",
        "reusable_tag_snapshots",
    )
    @classmethod
    def validate_unique_annotation_tags(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if len(value) != len({item if isinstance(item, str) else item.tag_id for item in value}):
            raise ValueError("annotation tags must not contain duplicates")
        return value

    @field_validator("taxonomy_version")
    @classmethod
    def validate_taxonomy_version(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != SELECTION_ANNOTATION_TAXONOMY_VERSION:
            raise ValueError("unsupported selection annotation taxonomy version")
        return normalized

    @field_validator("note")
    @classmethod
    def normalize_annotation_note(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @model_validator(mode="after")
    def validate_annotation_order(self) -> SelectionAnnotation:
        if self.recorded_at < self.reviewed_at:
            raise ValueError("recorded_at cannot precede reviewed_at")
        return self


class SelectionCandidateExclusion(DomainModel):
    """An immutable event excluding a candidate as invalid research data."""

    exclusion_id: str
    candidate_id: str
    excluded_at: datetime
    recorded_at: datetime
    reason: str

    @field_validator("exclusion_id", "candidate_id")
    @classmethod
    def validate_exclusion_ids(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("exclusion identifier must not be empty")
        if any(character in normalized for character in ("/", "\\", "\0", "\r", "\n")):
            raise ValueError("exclusion identifier contains an invalid path character")
        return normalized

    @field_validator("excluded_at", "recorded_at")
    @classmethod
    def validate_exclusion_times(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("exclusion timestamps must include a timezone")
        return value

    @field_validator("reason")
    @classmethod
    def validate_exclusion_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("exclusion reason must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_exclusion_order(self) -> SelectionCandidateExclusion:
        if self.recorded_at < self.excluded_at:
            raise ValueError("recorded_at cannot precede excluded_at")
        return self


class ManualSelectionLabel(DomainModel):
    """A legacy time-stamped human label kept for backward-compatible reads."""

    candidate_id: str
    decision: ManualSelectionDecision
    labeled_at: datetime
    note: str | None = None

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("candidate_id must not be empty")
        return value.strip()

    @field_validator("labeled_at")
    @classmethod
    def validate_labeled_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("labeled_at must include a timezone")
        return value

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None
