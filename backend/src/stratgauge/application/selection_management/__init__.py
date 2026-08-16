"""Management projections over the immutable selection store."""

from __future__ import annotations

from stratgauge.application.selection_management._base import BatchViewFactory
from stratgauge.application.selection_management.errors import SelectionApiError
from stratgauge.application.selection_management.export_rows import (
    annotation_dataset_row,
    latest_outcome_after_annotation,
    selection_dataset_row,
)
from stratgauge.application.selection_management.pagination import (
    is_entity_active,
    page_slice,
    validate_page,
)
from stratgauge.application.selection_management.service import SelectionManagementService

__all__ = [
    "BatchViewFactory",
    "SelectionApiError",
    "SelectionManagementService",
    "annotation_dataset_row",
    "is_entity_active",
    "latest_outcome_after_annotation",
    "page_slice",
    "selection_dataset_row",
    "validate_page",
]
