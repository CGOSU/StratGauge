"""Reusable tag models."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import field_validator, model_validator

from stratgauge.domain.models import DomainModel
from stratgauge.domain.selections.enums import (
    SelectionReusableTagCategory,
    SelectionReusableTagEventType,
)


class SelectionReusableTag(DomainModel):
    """Immutable initial definition for one reusable annotation tag."""

    tag_id: str
    category: SelectionReusableTagCategory
    label: str
    created_at: datetime

    @field_validator("tag_id")
    @classmethod
    def validate_tag_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"annotation-tag-[0-9a-f]{20}", normalized):
            raise ValueError("tag_id must use annotation-tag- followed by 20 hex characters")
        return normalized

    @field_validator("label")
    @classmethod
    def validate_tag_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("tag label must not be empty")
        if any(character in normalized for character in ("\0", "\r", "\n")):
            raise ValueError("tag label contains an invalid control character")
        return normalized

    @field_validator("created_at")
    @classmethod
    def validate_tag_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("tag created_at must include a timezone")
        return value


class SelectionReusableTagEvent(DomainModel):
    """One immutable append-only change to a reusable annotation tag."""

    event_id: str
    tag_id: str
    category: SelectionReusableTagCategory
    event_type: SelectionReusableTagEventType
    occurred_at: datetime
    new_label: str | None = None
    reason: str | None = None

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"annotation-tag-event-[0-9a-f]{20}", normalized):
            raise ValueError(
                "event_id must use annotation-tag-event- followed by 20 hex characters"
            )
        return normalized

    @field_validator("tag_id")
    @classmethod
    def validate_event_tag_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"annotation-tag-[0-9a-f]{20}", normalized):
            raise ValueError("event tag_id must use annotation-tag- followed by 20 hex characters")
        return normalized

    @field_validator("occurred_at")
    @classmethod
    def validate_event_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("tag event occurred_at must include a timezone")
        return value

    @field_validator("new_label")
    @classmethod
    def validate_new_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("new tag label must not be empty")
        if any(character in normalized for character in ("\0", "\r", "\n")):
            raise ValueError("new tag label contains an invalid control character")
        return normalized

    @field_validator("reason")
    @classmethod
    def normalize_event_reason(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @model_validator(mode="after")
    def validate_event_payload(self) -> SelectionReusableTagEvent:
        if self.event_type is SelectionReusableTagEventType.RENAME:
            if self.new_label is None:
                raise ValueError("rename event requires new_label")
        elif self.new_label is not None:
            raise ValueError("only rename event may carry new_label")
        return self


class SelectionReusableTagSnapshot(DomainModel):
    """The label meaning embedded in an annotation at review time."""

    tag_id: str
    category: SelectionReusableTagCategory
    label: str

    @field_validator("tag_id")
    @classmethod
    def validate_snapshot_tag_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"annotation-tag-[0-9a-f]{20}", normalized):
            raise ValueError(
                "snapshot tag_id must use annotation-tag- followed by 20 hex characters"
            )
        return normalized

    @field_validator("label")
    @classmethod
    def validate_snapshot_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("snapshot label must not be empty")
        if any(character in normalized for character in ("\0", "\r", "\n")):
            raise ValueError("snapshot label contains an invalid control character")
        return normalized


class SelectionReusableTagView(DomainModel):
    """Current tag state projected from an immutable definition and events."""

    tag_id: str
    category: SelectionReusableTagCategory
    label: str
    created_at: datetime
    active: bool = True
    events: tuple[SelectionReusableTagEvent, ...] = ()

    @classmethod
    def project(
        cls,
        definition: SelectionReusableTag,
        events: tuple[SelectionReusableTagEvent, ...] = (),
    ) -> SelectionReusableTagView:
        current_label = definition.label
        active = True
        previous_time = definition.created_at
        for event in events:
            if event.tag_id != definition.tag_id:
                raise ValueError("tag event belongs to a different tag")
            if event.category is not definition.category:
                raise ValueError("tag event cannot change tag category")
            if event.occurred_at <= previous_time:
                raise ValueError("tag event time must be after the previous tag event")
            if event.event_type is SelectionReusableTagEventType.RENAME:
                if event.new_label is None:
                    raise ValueError("rename event requires new_label")
                current_label = event.new_label
            elif event.event_type is SelectionReusableTagEventType.ARCHIVE:
                if not active:
                    raise ValueError("tag cannot be archived while already archived")
                active = False
            elif event.event_type is SelectionReusableTagEventType.RESTORE:
                if active:
                    raise ValueError("tag cannot be restored while active")
                active = True
            previous_time = event.occurred_at
        return cls(
            tag_id=definition.tag_id,
            category=definition.category,
            label=current_label,
            created_at=definition.created_at,
            active=active,
            events=events,
        )
