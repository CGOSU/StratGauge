"""Shared base and view-factory alias for management mixins."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.domain.selections import (
    TdxSelectionImport,
)

if TYPE_CHECKING:
    from stratgauge.api.selections import (
        SelectionBatchView,
    )


BatchViewFactory = Callable[[TdxSelectionImport], "SelectionBatchView"]


class SelectionManagementBase:
    """Attributes shared by all management service mixins."""

    _store: SelectionImportStore
    _view_factory: BatchViewFactory
