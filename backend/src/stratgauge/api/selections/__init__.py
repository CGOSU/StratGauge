"""API contracts and service for the semi-manual selection queue."""

from __future__ import annotations

from stratgauge.api.selections.requests import (
    ManualSelectionCandidateRequest,
    ManualSelectionRequest,
    SelectionAnnotationRequest,
    SelectionDismissalRequest,
    SelectionExclusionRequest,
    SelectionFormulaCreateRequest,
    SelectionImportRequest,
    SelectionLabelRequest,
    SelectionLifecycleChangeRequest,
    SelectionMatureRequest,
    SelectionReusableTagCreateRequest,
    SelectionReusableTagRenameRequest,
)
from stratgauge.api.selections.routes import create_selections_router
from stratgauge.api.selections.service import SelectionService
from stratgauge.api.selections.views import (
    SelectionBatchPage,
    SelectionBatchView,
    SelectionCandidateView,
    SelectionDismissalView,
    SelectionFormulaView,
    SelectionManagedBatchDetailView,
    SelectionManagedBatchPage,
    SelectionManagedBatchView,
    SelectionManagedFormulaView,
    SelectionManagementStatsView,
    SelectionMatureView,
    SelectionObservationPage,
    SelectionObservationView,
    SelectionStatsView,
)
from stratgauge.application.selection_management import (
    SelectionApiError,
    SelectionManagementService,
    page_slice,
    validate_page,
)

__all__ = [
    "SelectionApiError",
    "SelectionManagementService",
    "SelectionService",
    "page_slice",
    "validate_page",
    "SelectionFormulaView",
    "SelectionManagedFormulaView",
    "SelectionManagedBatchView",
    "SelectionMatureView",
    "SelectionCandidateView",
    "SelectionObservationView",
    "SelectionBatchView",
    "SelectionManagedBatchDetailView",
    "SelectionStatsView",
    "SelectionManagementStatsView",
    "SelectionBatchPage",
    "SelectionObservationPage",
    "SelectionManagedBatchPage",
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
    "SelectionDismissalRequest",
    "SelectionDismissalView",
    "SelectionMatureRequest",
    "create_selections_router",
]
