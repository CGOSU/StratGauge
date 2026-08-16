"""Import manifests, lifecycle events and status transitions."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from typing import Literal

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.data_pipeline.selection_index import SelectionIndexError
from stratgauge.data_pipeline.selection_store._base import (
    SelectionImportManifest,
    SelectionStoreBase,
    SelectionStoreError,
)
from stratgauge.data_pipeline.selection_store._io import _write_selection_text
from stratgauge.domain.selections import (
    SelectionLifecycleEvent,
    SelectionLifecycleStatus,
)


class LifecycleMixin(SelectionStoreBase):
    """Manifest, lifecycle and archive status operations."""

    def list_manifests(
        self, *, include_archived: bool = False
    ) -> tuple[SelectionImportManifest, ...]:
        manifests = tuple(
            self.load_manifest(path.stem) for path in sorted(self.manifest_dir.glob("*.json"))
        )
        if include_archived:
            return manifests
        return tuple(
            manifest for manifest in manifests if self.is_entity_active("batch", manifest.batch_id)
        )

    def list_lifecycle_events(
        self,
        entity_type: Literal["formula", "batch"],
        entity_id: str,
    ) -> tuple[SelectionLifecycleEvent, ...]:
        indexed_paths = self._indexed_paths(
            lambda index: index.list_lifecycle_event_paths(entity_type, entity_id)
        )
        if indexed_paths is not None:
            return tuple(self._load_lifecycle_event(path) for path in indexed_paths)
        events: list[SelectionLifecycleEvent] = []
        try:
            for path in self.lifecycle_dir.glob("lifecycle-*.json"):
                event = SelectionLifecycleEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if event.entity_type == entity_type and event.entity_id == entity_id:
                    events.append(event)
        except Exception as exc:
            raise SelectionStoreError("selection lifecycle event is invalid") from exc
        return tuple(sorted(events, key=lambda event: (event.changed_at, event.event_id)))

    def list_lifecycle_events_for_entities(
        self,
        entity_type: Literal["formula", "batch"],
        entity_ids: Iterable[str],
    ) -> dict[str, tuple[SelectionLifecycleEvent, ...]]:
        requested = tuple(dict.fromkeys(entity_ids))
        if not requested:
            return {}
        events_by_entity: dict[str, list[SelectionLifecycleEvent]] = {
            entity_id: [] for entity_id in requested
        }
        index = self._usable_read_index()
        if index is not None:
            try:
                return {
                    entity_id: tuple(
                        self._load_lifecycle_event(path)
                        for path in index.list_lifecycle_event_paths(entity_type, entity_id)
                    )
                    for entity_id in requested
                }
            except SelectionIndexError:
                self._read_index = None
        requested_set = set(requested)
        try:
            for path in self.lifecycle_dir.glob("lifecycle-*.json"):
                event = SelectionLifecycleEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if event.entity_type == entity_type and event.entity_id in requested_set:
                    events_by_entity[event.entity_id].append(event)
        except Exception as exc:
            raise SelectionStoreError("selection lifecycle event is invalid") from exc
        return {
            entity_id: tuple(sorted(events, key=lambda event: (event.changed_at, event.event_id)))
            for entity_id, events in events_by_entity.items()
        }

    def is_entity_active(self, entity_type: Literal["formula", "batch"], entity_id: str) -> bool:
        events = self.list_lifecycle_events(entity_type, entity_id)
        return not events or events[-1].status is SelectionLifecycleStatus.ACTIVE

    def set_formula_status(
        self,
        formula_id: str,
        status: SelectionLifecycleStatus,
        *,
        changed_at: datetime,
        reason: str | None = None,
    ) -> SelectionLifecycleEvent:
        with local_write_lock(self.root):
            self.load_formula(formula_id)
            return self._set_entity_status(
                entity_type="formula",
                entity_id=formula_id,
                status=status,
                changed_at=changed_at,
                reason=reason,
            )

    def set_batch_status(
        self,
        batch_id: str,
        status: SelectionLifecycleStatus,
        *,
        changed_at: datetime,
        reason: str | None = None,
    ) -> SelectionLifecycleEvent:
        with local_write_lock(self.root):
            self.load_import(batch_id)
            return self._set_entity_status(
                entity_type="batch",
                entity_id=batch_id,
                status=status,
                changed_at=changed_at,
                reason=reason,
            )

    def load_raw(self, batch_id: str) -> bytes:
        manifest = self.load_manifest(batch_id)
        path = self.root / manifest.raw_path
        if not path.exists():
            raise SelectionStoreError(f"selection raw file does not exist: {batch_id}")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise SelectionStoreError(f"failed to read selection raw file: {batch_id}") from exc

    def _set_entity_status(
        self,
        *,
        entity_type: Literal["formula", "batch"],
        entity_id: str,
        status: SelectionLifecycleStatus,
        changed_at: datetime,
        reason: str | None,
    ) -> SelectionLifecycleEvent:
        events = self.list_lifecycle_events(entity_type, entity_id)
        if events and events[-1].status is status:
            return events[-1]
        identity = "\0".join(
            (entity_type, entity_id, status.value, changed_at.isoformat(), reason or "")
        )
        event_id = f"lifecycle-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"
        event = SelectionLifecycleEvent(
            event_id=event_id,
            entity_type=entity_type,
            entity_id=entity_id,
            status=status,
            changed_at=changed_at,
            reason=reason,
        )
        path = self.lifecycle_dir / f"{event_id}.json"
        if path.exists():
            try:
                existing = SelectionLifecycleEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise SelectionStoreError(
                    f"selection lifecycle event is invalid: {event_id}"
                ) from exc
            self._update_read_index(lambda index: index.upsert_lifecycle_event(existing, path))
            return existing
        _write_selection_text(path, event.model_dump_json(indent=2))
        self._update_read_index(lambda index: index.upsert_lifecycle_event(event, path))
        return event
