"""Formula, batch, candidate and lifecycle models."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel, Exchange
from stratgauge.domain.selections.enums import (
    SelectionLifecycleStatus,
)


class SelectionLifecycleEvent(DomainModel):
    """Append-only lifecycle change for a formula or imported batch."""

    event_id: str
    entity_type: Literal["formula", "batch"]
    entity_id: str
    status: SelectionLifecycleStatus
    changed_at: datetime
    reason: str | None = None

    @field_validator("event_id", "entity_id")
    @classmethod
    def validate_lifecycle_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("lifecycle identifier must not be empty")
        return value.strip()

    @field_validator("changed_at")
    @classmethod
    def validate_lifecycle_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("changed_at must include a timezone")
        return value

    @field_validator("reason")
    @classmethod
    def normalize_lifecycle_reason(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None


class SelectionFormula(DomainModel):
    """A named formula reference; TongdaXin remains the formula executor."""

    formula_id: str
    name: str
    version: str | None = None
    description: str | None = None
    created_at: datetime

    @field_validator("formula_id")
    @classmethod
    def validate_formula_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"formula-[0-9a-f]{20}", normalized):
            raise ValueError("formula_id has an invalid format")
        return normalized

    @field_validator("name")
    @classmethod
    def validate_formula_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("formula name must not be empty")
        return value.strip()

    @field_validator("version", "description")
    @classmethod
    def normalize_optional_formula_text(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @field_validator("created_at")
    @classmethod
    def validate_formula_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value


class SelectionBatch(DomainModel):
    """Immutable metadata for one TongdaXin export."""

    batch_id: str
    source: Literal["tongdaxin", "manual"] = "tongdaxin"
    source_filename: str | None = None
    formula_id: str | None = None
    selection_date: date
    exported_at: datetime
    imported_at: datetime
    formula_name: str
    formula_version: str | None = None
    formula_sha256: str | None = None
    source_file_sha256: str
    candidate_count: int = Field(ge=0)

    @field_validator("batch_id", "source", "formula_name", "source_file_sha256")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value.strip()

    @field_validator("source_filename")
    @classmethod
    def validate_source_filename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if any(character in normalized for character in ("/", "\\", "\0", "\r", "\n")):
            raise ValueError("source_filename must be a file name")
        return normalized

    @field_validator("formula_version")
    @classmethod
    def validate_formula_version(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @field_validator("exported_at", "imported_at")
    @classmethod
    def validate_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @field_validator("formula_sha256", "source_file_sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ValueError("sha256 must be 64 hexadecimal characters")
        return normalized

    @model_validator(mode="after")
    def validate_import_order(self) -> SelectionBatch:
        if self.imported_at < self.exported_at:
            raise ValueError("imported_at cannot precede exported_at")
        return self


class SelectionCandidate(DomainModel):
    """One candidate emitted by a selection batch."""

    candidate_id: str
    batch_id: str
    local_code: str
    symbol: str
    exchange: Exchange
    name: str | None = None
    source_fields: dict[str, str] = Field(default_factory=dict)

    @field_validator("candidate_id", "batch_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifier must not be empty")
        return value.strip()

    @field_validator("local_code")
    @classmethod
    def validate_local_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.fullmatch(r"\d{6}", normalized):
            raise ValueError("local_code must contain exactly six digits")
        return normalized

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @field_validator("source_fields")
    @classmethod
    def normalize_source_fields(cls, value: dict[str, str]) -> dict[str, str]:
        return {key.strip(): item.strip() for key, item in value.items() if key.strip()}

    @model_validator(mode="after")
    def validate_symbol(self) -> SelectionCandidate:
        expected = f"{self.local_code}.{self.exchange.value}"
        if self.symbol != expected:
            raise ValueError("symbol must match local_code and exchange")
        return self
