"""Contracts for semi-manual stock-selection research."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel, Exchange


class ManualSelectionDecision(StrEnum):
    """Human decision recorded independently from the market outcome."""

    BUY_CANDIDATE = "buy_candidate"
    WATCH = "watch"
    PASS = "pass"
    INVALID = "invalid"


class SelectionOutcomeStatus(StrEnum):
    """Objective status produced after enough future bars are available."""

    WIN = "win"
    LOSS = "loss"
    FLAT = "flat"
    NO_ENTRY = "no_entry"
    UNRESOLVED = "unresolved"


class SelectionBatch(DomainModel):
    """Immutable metadata for one TongdaXin export."""

    batch_id: str
    source: str = "tongdaxin"
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


class ManualSelectionLabel(DomainModel):
    """A time-stamped human label; it is not an objective outcome."""

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


class SelectionOutcome(DomainModel):
    """A fixed-horizon market outcome, separate from any human label."""

    candidate_id: str
    batch_id: str
    selection_date: date
    status: SelectionOutcomeStatus
    evaluated_at: datetime
    horizon_sessions: int = Field(gt=0)
    entry_session: date | None = None
    exit_session: date | None = None
    entry_price: Decimal | None = None
    exit_price: Decimal | None = None
    gross_return: Decimal | None = None
    net_return: Decimal | None = None
    reason: str
    tracking_policy_id: str = "legacy-unknown"
    cost_schedule_id: str = "unspecified"
    position_notional: Decimal | None = Field(default=None, gt=0)
    source_snapshot_ids: tuple[str, ...] = ()

    @field_validator("candidate_id", "batch_id", "reason", "tracking_policy_id", "cost_schedule_id")
    @classmethod
    def validate_outcome_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value.strip()

    @field_validator("evaluated_at")
    @classmethod
    def validate_evaluated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluated_at must include a timezone")
        return value

    @field_validator(
        "entry_price",
        "exit_price",
        "gross_return",
        "net_return",
        "position_notional",
        mode="before",
    )
    @classmethod
    def validate_outcome_decimals(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise ValueError("outcome value must be a decimal number") from exc
        if not result.is_finite():
            raise ValueError("outcome value must be finite")
        return result

    @field_validator("source_snapshot_ids")
    @classmethod
    def validate_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not snapshot_id.strip() for snapshot_id in value):
            raise ValueError("source snapshot identifiers must not be empty")
        return tuple(snapshot_id.strip() for snapshot_id in value)

    @model_validator(mode="after")
    def validate_trade_fields(self) -> SelectionOutcome:
        trade_statuses = {
            SelectionOutcomeStatus.WIN,
            SelectionOutcomeStatus.LOSS,
            SelectionOutcomeStatus.FLAT,
        }
        has_trade = self.status in trade_statuses
        trade_fields = (
            self.entry_session,
            self.exit_session,
            self.entry_price,
            self.exit_price,
            self.gross_return,
            self.net_return,
        )
        if has_trade and any(value is None for value in trade_fields):
            raise ValueError("completed outcomes require entry, exit and return fields")
        if self.entry_session is not None and self.exit_session is not None:
            if self.exit_session <= self.entry_session:
                raise ValueError("exit_session must follow entry_session")
            if self.evaluated_at.date() < self.exit_session:
                raise ValueError("evaluated_at cannot precede the exit session")
        return self


class TdxSelectionImport(DomainModel):
    """Parsed export with immutable batch metadata and candidates."""

    batch: SelectionBatch
    candidates: tuple[SelectionCandidate, ...]

    @model_validator(mode="after")
    def validate_batch_membership(self) -> TdxSelectionImport:
        if self.batch.candidate_count != len(self.candidates):
            raise ValueError("batch candidate_count must match candidates")
        if any(candidate.batch_id != self.batch.batch_id for candidate in self.candidates):
            raise ValueError("all candidates must belong to the batch")
        return self

    def model_dump_for_storage(self) -> dict[str, Any]:
        """Return a JSON-compatible payload for a future immutable import store."""

        return self.model_dump(mode="json")
