"""Composed selection import store."""

from __future__ import annotations

from pathlib import Path

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.data_pipeline.selection_index import (
    SelectionIndexError,
    SelectionIndexStats,
    SelectionReadIndex,
)
from stratgauge.data_pipeline.selection_store._base import SelectionStoreError
from stratgauge.data_pipeline.selection_store.annotations import AnnotationsMixin
from stratgauge.data_pipeline.selection_store.formulas import FormulasMixin
from stratgauge.data_pipeline.selection_store.lifecycle import LifecycleMixin
from stratgauge.data_pipeline.selection_store.maturity import MaturityTasksMixin
from stratgauge.data_pipeline.selection_store.outcomes import OutcomesMixin
from stratgauge.data_pipeline.selection_store.reusable_tags import ReusableTagsMixin


class SelectionImportStore(
    ReusableTagsMixin,
    FormulasMixin,
    AnnotationsMixin,
    OutcomesMixin,
    MaturityTasksMixin,
    LifecycleMixin,
):
    """Persist raw exports and parsed candidates without overwriting evidence."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.raw_dir = self.root / "tdx" / "raw"
        self.import_dir = self.root / "tdx" / "imports"
        self.manifest_dir = self.root / "tdx" / "manifests"
        self.label_dir = self.root / "tdx" / "labels"
        self.annotation_dir = self.root / "tdx" / "annotations"
        self.exclusion_dir = self.root / "tdx" / "exclusions"
        self.outcome_dir = self.root / "tdx" / "outcomes"
        self.performance_dir = self.root / "tdx" / "performances"
        self.formula_dir = self.root / "tdx" / "formulas"
        self.reusable_tag_dir = self.root / "tdx" / "annotation_tags"
        self.reusable_tag_event_dir = self.root / "tdx" / "annotation_tag_events"
        self.lifecycle_dir = self.root / "tdx" / "lifecycle"
        self.maturity_task_dir = self.root / "tdx" / "maturity_tasks"
        self.maturity_event_dir = self.root / "tdx" / "maturity_task_events"
        for directory in (
            self.raw_dir,
            self.import_dir,
            self.manifest_dir,
            self.label_dir,
            self.annotation_dir,
            self.exclusion_dir,
            self.outcome_dir,
            self.performance_dir,
            self.formula_dir,
            self.reusable_tag_dir,
            self.reusable_tag_event_dir,
            self.lifecycle_dir,
            self.maturity_task_dir,
            self.maturity_event_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        try:
            self._read_index: SelectionReadIndex | None = SelectionReadIndex(self.root)
        except SelectionIndexError:
            self._read_index = None

    def rebuild_selection_index(self) -> SelectionIndexStats:
        """Rebuild the read projection from immutable files under the write lock."""

        with local_write_lock(self.root):
            try:
                index = SelectionReadIndex(self.root, initialize=False)
                stats = index.rebuild()
            except SelectionIndexError as exc:
                raise SelectionStoreError("failed to rebuild selection read index") from exc
            self._read_index = index
            return stats
