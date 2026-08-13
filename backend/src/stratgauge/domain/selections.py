"""Contracts for semi-manual stock-selection research."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel, Exchange


class ManualSelectionDecision(StrEnum):
    """Human decision recorded independently from the market outcome."""

    BUY_CANDIDATE = "buy_candidate"
    WATCH = "watch"
    PASS = "pass"
    INVALID = "invalid"


DEFAULT_OUTCOME_POLICY_ID = "outcome-fixed-10-net-positive-v1"
DEFAULT_PERFORMANCE_HORIZONS = (1, 3, 5, 10)
DEFAULT_SELECTION_PERFORMANCE_POLICY_ID = "selection-performance-t1-t3-t5-t10-v1"


class SelectionOutcomeStatus(StrEnum):
    """Objective status produced after enough future bars are available."""

    WIN = "win"
    LOSS = "loss"
    FLAT = "flat"
    NO_ENTRY = "no_entry"
    UNRESOLVED = "unresolved"


class SelectionPerformanceStatus(StrEnum):
    """Status of fixed forward-window performance measurements."""

    RESOLVED = "resolved"
    NO_ENTRY = "no_entry"
    UNRESOLVED = "unresolved"


class SelectionLifecycleStatus(StrEnum):
    """Lifecycle state used by the web data-management archive controls."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class SelectionAnnotationOutlook(StrEnum):
    """Human expectation recorded at one review point."""

    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"


class SelectionAnnotationDecision(StrEnum):
    """Current manual action chosen during one review."""

    DISCARD = "discard"
    WATCH = "watch"
    PREPARE_BUY = "prepare_buy"


class SelectionCandidateReviewState(StrEnum):
    """Materialized current review state derived from the latest annotation."""

    UNREVIEWED = "unreviewed"
    DISCARDED = "discarded"
    WATCHING = "watching"
    PREPARING = "preparing"


class SelectionPositionTag(StrEnum):
    """Position context tags from the versioned annotation vocabulary."""

    LOW = "low"
    MID = "mid"
    HIGH = "high"
    SUPPORT = "support"
    RESISTANCE = "resistance"
    NEW_HIGH = "new_high"


class SelectionPatternTag(StrEnum):
    """Price-pattern tags from the versioned annotation vocabulary."""

    TREND_CONTINUATION = "trend_continuation"
    BREAKOUT = "breakout"
    PULLBACK = "pullback"
    CONSOLIDATION = "consolidation"
    REVERSAL = "reversal"
    PATTERN_BREAK = "pattern_break"


class SelectionPriceVolumeTag(StrEnum):
    """Price/volume relationship tags from the versioned vocabulary."""

    PRICE_UP_VOLUME_UP = "price_up_volume_up"
    PRICE_UP_VOLUME_DOWN = "price_up_volume_down"
    PRICE_DOWN_VOLUME_UP = "price_down_volume_up"
    PRICE_DOWN_VOLUME_DOWN = "price_down_volume_down"
    VOLUME_BREAKOUT = "volume_breakout"
    VOLUME_PULLBACK = "volume_pullback"


class SelectionSectorTag(StrEnum):
    """Sector-context tags from the versioned annotation vocabulary."""

    SECTOR_STRONG = "sector_strong"
    SECTOR_ROTATION = "sector_rotation"
    SECTOR_LEADER = "sector_leader"
    SECTOR_FOLLOWING = "sector_following"
    SECTOR_WEAK = "sector_weak"
    STOCK_INDEPENDENT = "stock_independent"


SELECTION_ANNOTATION_TAXONOMY_VERSION = "selection-annotation-taxonomy-v1"


class SelectionReusableTagCategory(StrEnum):
    """Fixed categories for reusable human-review tags."""

    POSITION = "position"
    PATTERN = "pattern"
    PRICE_VOLUME = "price_volume"
    SECTOR = "sector"


class SelectionReusableTagEventType(StrEnum):
    """Append-only changes that can be applied to a reusable tag."""

    RENAME = "rename"
    ARCHIVE = "archive"
    RESTORE = "restore"


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


class SelectionMaturityTaskStatus(StrEnum):
    """Persisted state of one explicit selection maturity task."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class SelectionMaturityTaskEvent(DomainModel):
    """Append-only audit record for a maturity-task state transition."""

    event_id: str
    task_id: str
    event_type: Literal["queued", "running", "attempt_result", "requeue"]
    occurred_at: datetime
    attempt_count: int = Field(ge=0)
    status: SelectionMaturityTaskStatus
    outcomes_written: int = Field(ge=0)
    rejected_candidates: tuple[tuple[str, str], ...] = ()
    market_snapshot_ids: tuple[str, ...] = ()
    failure_reason: str | None = None
    retryable: bool = False
    reason: str | None = None

    @field_validator("event_id", "task_id")
    @classmethod
    def validate_task_event_ids(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("maturity task event identifier must not be empty")
        return normalized

    @field_validator("occurred_at")
    @classmethod
    def validate_task_event_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("maturity task event time must include a timezone")
        return value

    @field_validator("failure_reason", "reason")
    @classmethod
    def normalize_task_event_text(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @field_validator("rejected_candidates")
    @classmethod
    def normalize_rejected_candidates(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        return tuple((candidate_id.strip(), reason.strip()) for candidate_id, reason in value)

    @field_validator("market_snapshot_ids")
    @classmethod
    def normalize_task_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not snapshot_id.strip() for snapshot_id in value):
            raise ValueError("maturity task snapshot identifiers must not be empty")
        return tuple(snapshot_id.strip() for snapshot_id in value)


class SelectionMaturityTask(DomainModel):
    """Immutable task configuration plus its current materialized ledger state."""

    task_id: str
    batch_id: str
    provider: Literal["tushare", "akshare", "baostock", "waizao"]
    market_data_mode: Literal["provider", "approved_snapshots"] = "provider"
    universe_snapshot_id: str | None = None
    input_market_snapshot_ids: tuple[str, ...] = ()
    evaluated_at: datetime
    target_evaluation_date: date
    tracking_policy_id: str
    performance_policy_id: str = DEFAULT_SELECTION_PERFORMANCE_POLICY_ID
    performance_target_return: Decimal | None = None
    horizon_sessions: int = Field(gt=0)
    position_notional: Decimal = Field(gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    timeout_seconds: float = Field(default=900, gt=0, le=86400)
    status: SelectionMaturityTaskStatus = SelectionMaturityTaskStatus.QUEUED
    attempt_count: int = Field(default=0, ge=0)
    outcomes_written: int = Field(default=0, ge=0)
    rejected_candidates: tuple[tuple[str, str], ...] = ()
    market_snapshot_ids: tuple[str, ...] = ()
    failure_reason: str | None = None
    retryable: bool = False
    created_at: datetime
    updated_at: datetime
    events: tuple[SelectionMaturityTaskEvent, ...] = ()

    @field_validator(
        "task_id",
        "batch_id",
        "provider",
        "market_data_mode",
        "tracking_policy_id",
        "performance_policy_id",
    )
    @classmethod
    def validate_task_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("maturity task value must not be empty")
        return normalized

    @field_validator("evaluated_at", "created_at", "updated_at")
    @classmethod
    def validate_task_times(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("maturity task timestamps must include a timezone")
        return value

    @field_validator("performance_target_return", mode="before")
    @classmethod
    def validate_performance_target_return(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise ValueError("performance_target_return must be a decimal number") from exc
        if not result.is_finite() or result < 0:
            raise ValueError("performance_target_return must be finite and non-negative")
        return result

    @field_validator("position_notional", mode="before")
    @classmethod
    def validate_task_notional(cls, value: Any) -> Decimal:
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise ValueError("position_notional must be a decimal number") from exc
        if not result.is_finite() or result <= 0:
            raise ValueError("position_notional must be positive and finite")
        return result

    @field_validator("timeout_seconds")
    @classmethod
    def validate_task_timeout(cls, value: float) -> float:
        if value <= 0 or value > 86400:
            raise ValueError("timeout_seconds must be positive and no greater than 86400")
        return value

    @field_validator("rejected_candidates")
    @classmethod
    def normalize_task_rejections(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        return tuple((candidate_id.strip(), reason.strip()) for candidate_id, reason in value)

    @field_validator("input_market_snapshot_ids", "market_snapshot_ids")
    @classmethod
    def normalize_task_snapshots(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not snapshot_id.strip() for snapshot_id in value):
            raise ValueError("maturity task snapshot identifiers must not be empty")
        return tuple(snapshot_id.strip() for snapshot_id in value)

    @model_validator(mode="after")
    def validate_task_dates(self) -> SelectionMaturityTask:
        if self.target_evaluation_date != self.evaluated_at.date():
            raise ValueError("target_evaluation_date must match evaluated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.market_data_mode == "approved_snapshots":
            if not self.universe_snapshot_id:
                raise ValueError("snapshot-backed maturity task requires a universe snapshot")
            if not self.input_market_snapshot_ids:
                raise ValueError("snapshot-backed maturity task requires market snapshots")
        elif self.universe_snapshot_id is not None or self.input_market_snapshot_ids:
            raise ValueError("provider maturity task cannot carry market snapshot inputs")
        return self


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
    outcome_policy_id: str = "legacy-unknown"
    cost_schedule_id: str = "unspecified"
    position_notional: Decimal | None = Field(default=None, gt=0)
    source_snapshot_ids: tuple[str, ...] = ()
    maturity_task_id: str | None = None

    @field_validator(
        "candidate_id",
        "batch_id",
        "reason",
        "tracking_policy_id",
        "outcome_policy_id",
        "cost_schedule_id",
    )
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

    @field_validator("maturity_task_id")
    @classmethod
    def validate_maturity_task_id(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

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


class SelectionPerformancePoint(DomainModel):
    """One forward performance observation at a requested trading horizon."""

    horizon_sessions: int = Field(gt=0)
    session_date: date | None = None
    close_price: Decimal | None = None
    return_from_anchor: Decimal | None = None
    maximum_rise: Decimal | None = None
    maximum_drawdown: Decimal | None = None
    target_achieved: bool | None = None

    @field_validator(
        "close_price",
        "return_from_anchor",
        "maximum_rise",
        "maximum_drawdown",
        mode="before",
    )
    @classmethod
    def validate_performance_decimals(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise ValueError("performance value must be a decimal number") from exc
        if not result.is_finite():
            raise ValueError("performance value must be finite")
        return result

    @model_validator(mode="after")
    def validate_point_completeness(self) -> SelectionPerformancePoint:
        values = (
            self.session_date,
            self.close_price,
            self.return_from_anchor,
            self.maximum_rise,
            self.maximum_drawdown,
        )
        if self.target_achieved is not None and any(value is None for value in values):
            raise ValueError("target_achieved requires a complete performance point")
        if self.session_date is None and any(value is not None for value in values):
            raise ValueError("performance values require a session date")
        return self


class SelectionPerformance(DomainModel):
    """Immutable forward performance evidence separate from fixed-horizon outcomes."""

    candidate_id: str
    batch_id: str
    selection_date: date
    status: SelectionPerformanceStatus
    evaluated_at: datetime
    performance_policy_id: str
    anchor_session: date | None = None
    anchor_price: Decimal | None = None
    target_return: Decimal | None = None
    points: tuple[SelectionPerformancePoint, ...] = ()
    reason: str
    source_snapshot_ids: tuple[str, ...] = ()
    maturity_task_id: str | None = None

    @field_validator(
        "candidate_id",
        "batch_id",
        "performance_policy_id",
        "reason",
    )
    @classmethod
    def validate_performance_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("performance value must not be empty")
        return normalized

    @field_validator("evaluated_at")
    @classmethod
    def validate_performance_evaluated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("performance evaluated_at must include a timezone")
        return value

    @field_validator("anchor_price", "target_return", mode="before")
    @classmethod
    def validate_performance_decimal(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise ValueError("performance value must be a decimal number") from exc
        if not result.is_finite():
            raise ValueError("performance value must be finite")
        return result

    @field_validator("maturity_task_id")
    @classmethod
    def normalize_performance_task_id(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @field_validator("source_snapshot_ids")
    @classmethod
    def validate_performance_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not snapshot_id.strip() for snapshot_id in value):
            raise ValueError("performance source snapshot identifiers must not be empty")
        return tuple(snapshot_id.strip() for snapshot_id in value)

    @model_validator(mode="after")
    def validate_performance_structure(self) -> SelectionPerformance:
        if self.status is SelectionPerformanceStatus.RESOLVED:
            if self.anchor_session is None or self.anchor_price is None:
                raise ValueError("resolved performance requires an anchor")
            if any(point.session_date is None for point in self.points):
                raise ValueError("resolved performance requires complete points")
        if self.anchor_price is not None and self.anchor_price <= 0:
            raise ValueError("anchor_price must be positive")
        if self.target_return is not None and self.target_return < 0:
            raise ValueError("target_return must not be negative")
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
