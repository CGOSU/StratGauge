"""Composed selection management service."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stratgauge.data_pipeline.selection_store import SelectionImportStore

if TYPE_CHECKING:
    pass


from stratgauge.application.selection_management._base import (
    BatchViewFactory,
)
from stratgauge.application.selection_management._batches import BatchesMixin
from stratgauge.application.selection_management._catalog import CatalogMixin
from stratgauge.application.selection_management._queries import QueriesMixin


class SelectionManagementService(
    CatalogMixin,
    BatchesMixin,
    QueriesMixin,
):
    """Handle formula, tag, batch lifecycle, and export projections."""

    def __init__(self, store: SelectionImportStore, *, view_factory: BatchViewFactory) -> None:
        self._store = store
        self._view_factory = view_factory
