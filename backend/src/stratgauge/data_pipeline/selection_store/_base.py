"""Shared base: store errors, manifest model, attributes and index helpers."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field

from stratgauge.data_pipeline.selection_index import (
    SelectionIndexError,
    SelectionReadIndex,
)
from stratgauge.domain.models import DomainModel
from stratgauge.domain.selections import (
    SelectionFormula,
    SelectionLifecycleEvent,
    SelectionReusableTagEvent,
    TdxSelectionImport,
)


class SelectionStoreError(RuntimeError):
    """A selection import cannot be safely persisted or read."""


class SelectionImportManifest(DomainModel):
    """File references and identity for one imported selection batch."""

    batch_id: str
    source_file_sha256: str
    candidate_count: int = Field(ge=0)
    raw_path: str
    import_path: str


class SelectionStoreBase:
    """Attributes and read-index plumbing shared by all store mixins."""

    root: Path
    raw_dir: Path
    import_dir: Path
    manifest_dir: Path
    label_dir: Path
    annotation_dir: Path
    exclusion_dir: Path
    dismissal_dir: Path
    outcome_dir: Path
    performance_dir: Path
    formula_dir: Path
    reusable_tag_dir: Path
    reusable_tag_event_dir: Path
    lifecycle_dir: Path
    maturity_task_dir: Path
    maturity_event_dir: Path
    _read_index: SelectionReadIndex | None

    # Cross-mixin reader surface: implementations live in the domain mixins and
    # are combined by SelectionImportStore; declared here for type checking.
    def load_import(self, batch_id: str) -> TdxSelectionImport:
        raise NotImplementedError

    def load_formula(self, formula_id: str) -> SelectionFormula:
        raise NotImplementedError

    def is_entity_active(self, entity_type: Literal["formula", "batch"], entity_id: str) -> bool:
        raise NotImplementedError

    def _usable_read_index(self) -> SelectionReadIndex | None:
        index = self._read_index
        if index is None:
            return None
        if not index.is_ready():
            self._read_index = None
            return None
        return index

    def _indexed_paths(
        self,
        query: Callable[[SelectionReadIndex], tuple[str, ...]],
    ) -> tuple[str, ...] | None:
        index = self._usable_read_index()
        if index is None:
            return None
        try:
            return query(index)
        except SelectionIndexError:
            self._read_index = None
            return None

    def _update_read_index(
        self,
        update: Callable[[SelectionReadIndex], None],
    ) -> None:
        index = self._read_index
        if index is None:
            return
        try:
            update(index)
        except SelectionIndexError:
            try:
                index.mark_stale()
            except SelectionIndexError:
                pass
            self._read_index = None

    def _source_path(self, source_path: str | Path) -> Path:
        path = Path(source_path)
        return path if path.is_absolute() else self.root / path

    def _load_lifecycle_event(self, source_path: str | Path) -> SelectionLifecycleEvent:
        path = self._source_path(source_path)
        try:
            return SelectionLifecycleEvent.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"selection lifecycle event is invalid: {path.name}") from exc

    def _load_reusable_tag_event(self, source_path: str | Path) -> SelectionReusableTagEvent:
        path = self._source_path(source_path)
        try:
            return SelectionReusableTagEvent.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"reusable tag event is invalid: {path.name}") from exc

    def _maturity_task_path(self, task_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", task_id):
            raise SelectionStoreError(f"invalid maturity task id: {task_id}")
        return self.maturity_task_dir / f"{task_id}.json"

    def load_manifest(self, batch_id: str) -> SelectionImportManifest:
        path = self.manifest_dir / f"{batch_id}.json"
        if not path.exists():
            raise SelectionStoreError(f"selection manifest does not exist: {batch_id}")
        try:
            return SelectionImportManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"selection manifest is invalid: {batch_id}") from exc
