"""Enums and taxonomy constants for selection evidence."""

from __future__ import annotations

from enum import StrEnum


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
