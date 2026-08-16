"""Domain models for the semi-manual selection pipeline."""

from __future__ import annotations

from stratgauge.domain.selections.annotations import (
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionCandidateExclusion,
)
from stratgauge.domain.selections.core import (
    SelectionBatch,
    SelectionCandidate,
    SelectionFormula,
    SelectionLifecycleEvent,
)
from stratgauge.domain.selections.enums import (
    SELECTION_ANNOTATION_TAXONOMY_VERSION,
    ManualSelectionDecision,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
    SelectionCandidateReviewState,
    SelectionLifecycleStatus,
    SelectionOutcomeStatus,
    SelectionPatternTag,
    SelectionPerformanceStatus,
    SelectionPositionTag,
    SelectionPriceVolumeTag,
    SelectionReusableTagCategory,
    SelectionReusableTagEventType,
    SelectionSectorTag,
)
from stratgauge.domain.selections.imports import (
    TdxSelectionImport,
)
from stratgauge.domain.selections.maturity import (
    SelectionMaturityTask,
    SelectionMaturityTaskEvent,
    SelectionMaturityTaskStatus,
)
from stratgauge.domain.selections.outcomes import (
    DEFAULT_OUTCOME_POLICY_ID,
    DEFAULT_PERFORMANCE_HORIZONS,
    DEFAULT_SELECTION_PERFORMANCE_POLICY_ID,
    SelectionOutcome,
    SelectionPerformance,
    SelectionPerformancePoint,
)
from stratgauge.domain.selections.reusable_tags import (
    SelectionReusableTag,
    SelectionReusableTagEvent,
    SelectionReusableTagSnapshot,
    SelectionReusableTagView,
)

__all__ = [
    "ManualSelectionDecision",
    "SelectionOutcomeStatus",
    "SelectionPerformanceStatus",
    "SelectionLifecycleStatus",
    "SelectionAnnotationOutlook",
    "SelectionAnnotationDecision",
    "SelectionCandidateReviewState",
    "SelectionPositionTag",
    "SelectionPatternTag",
    "SelectionPriceVolumeTag",
    "SelectionSectorTag",
    "SelectionReusableTagCategory",
    "SelectionReusableTagEventType",
    "SELECTION_ANNOTATION_TAXONOMY_VERSION",
    "SelectionReusableTag",
    "SelectionReusableTagEvent",
    "SelectionReusableTagSnapshot",
    "SelectionReusableTagView",
    "SelectionMaturityTaskStatus",
    "SelectionMaturityTaskEvent",
    "SelectionMaturityTask",
    "SelectionLifecycleEvent",
    "SelectionFormula",
    "SelectionBatch",
    "SelectionCandidate",
    "SelectionAnnotation",
    "SelectionCandidateExclusion",
    "ManualSelectionLabel",
    "SelectionOutcome",
    "SelectionPerformancePoint",
    "SelectionPerformance",
    "DEFAULT_OUTCOME_POLICY_ID",
    "DEFAULT_PERFORMANCE_HORIZONS",
    "DEFAULT_SELECTION_PERFORMANCE_POLICY_ID",
    "TdxSelectionImport",
]
