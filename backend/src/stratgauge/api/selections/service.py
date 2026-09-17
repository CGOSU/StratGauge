"""Composed selection application service."""

from __future__ import annotations

from stratgauge.api.selections._analysis import AnalysisMixin
from stratgauge.api.selections._annotations import AnnotationsMixin
from stratgauge.api.selections._batches import BatchesMixin
from stratgauge.api.selections._catalog import CatalogMixin
from stratgauge.api.selections._dismissals import DismissalsMixin
from stratgauge.api.selections._maturity import MaturityMixin
from stratgauge.application.selection_management import SelectionApiError as SelectionApiError
from stratgauge.application.selection_management import (
    SelectionManagementService,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore


class SelectionService(
    CatalogMixin,
    BatchesMixin,
    MaturityMixin,
    AnalysisMixin,
    AnnotationsMixin,
    DismissalsMixin,
):
    """Application service backed by immutable local selection storage."""

    def __init__(self, store: SelectionImportStore) -> None:
        self._store = store
        self._management = SelectionManagementService(store, view_factory=self._view)
