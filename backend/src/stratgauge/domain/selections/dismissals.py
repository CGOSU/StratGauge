"""Candidate dismissal events for the strategy-signal workbench."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel

DEFAULT_SELECTION_CANDIDATE_DISMISSAL_REASON = "用户从策略信号中移除"


class SelectionCandidateDismissal(DomainModel):
    """An immutable event hiding an unreviewed candidate from active signals."""

    dismissal_id: str
    candidate_id: str
    dismissed_at: datetime
    recorded_at: datetime
    reason: str = Field(default=DEFAULT_SELECTION_CANDIDATE_DISMISSAL_REASON)

    @field_validator("dismissal_id", "candidate_id")
    @classmethod
    def validate_dismissal_ids(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("dismissal identifier must not be empty")
        if any(character in normalized for character in ("/", "\\", "\0", "\r", "\n")):
            raise ValueError("dismissal identifier contains an invalid path character")
        return normalized

    @field_validator("dismissed_at", "recorded_at")
    @classmethod
    def validate_dismissal_times(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("dismissal timestamps must include a timezone")
        return value

    @field_validator("reason")
    @classmethod
    def validate_dismissal_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("dismissal reason must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_dismissal_order(self) -> SelectionCandidateDismissal:
        if self.recorded_at < self.dismissed_at:
            raise ValueError("recorded_at cannot precede dismissed_at")
        return self


__all__ = [
    "DEFAULT_SELECTION_CANDIDATE_DISMISSAL_REASON",
    "SelectionCandidateDismissal",
]
