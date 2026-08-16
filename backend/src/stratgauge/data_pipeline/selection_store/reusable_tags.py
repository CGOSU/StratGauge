"""Reusable tag catalog persistence."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.data_pipeline.selection_index import SelectionIndexError
from stratgauge.data_pipeline.selection_store._base import SelectionStoreBase, SelectionStoreError
from stratgauge.data_pipeline.selection_store._io import _write_selection_text
from stratgauge.domain.selections import (
    SelectionLifecycleStatus,
    SelectionReusableTag,
    SelectionReusableTagCategory,
    SelectionReusableTagEvent,
    SelectionReusableTagEventType,
    SelectionReusableTagView,
)


class ReusableTagsMixin(SelectionStoreBase):
    """Append-only reusable tag catalog operations."""

    def create_reusable_tag(
        self,
        *,
        category: SelectionReusableTagCategory,
        label: str,
        created_at: datetime | None = None,
    ) -> SelectionReusableTag:
        with local_write_lock(self.root):
            normalized_label = label.strip()
            identity = f"{category.value}\0{normalized_label}"
            tag_id = f"annotation-tag-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"
            tag = SelectionReusableTag(
                tag_id=tag_id,
                category=category,
                label=normalized_label,
                created_at=created_at or datetime.now(UTC),
            )
            path = self.reusable_tag_dir / f"{tag_id}.json"
            if path.exists():
                try:
                    existing = SelectionReusableTag.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                except Exception as exc:
                    raise SelectionStoreError(f"reusable tag is invalid: {tag_id}") from exc
                if existing.category != tag.category or existing.label != tag.label:
                    raise SelectionStoreError("reusable tag identity already has different content")
                return existing
            _write_selection_text(path, tag.model_dump_json(indent=2))
            return tag

    def load_reusable_tag(self, tag_id: str) -> SelectionReusableTag:
        if not re.fullmatch(r"annotation-tag-[0-9a-f]{20}", tag_id):
            raise SelectionStoreError(f"invalid reusable tag id: {tag_id}")
        path = self.reusable_tag_dir / f"{tag_id}.json"
        if not path.exists():
            raise SelectionStoreError(f"reusable tag does not exist: {tag_id}")
        try:
            return SelectionReusableTag.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"reusable tag is invalid: {tag_id}") from exc

    def list_reusable_tags(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[SelectionReusableTagView, ...]:
        try:
            definitions = tuple(
                self.load_reusable_tag(path.stem)
                for path in sorted(self.reusable_tag_dir.glob("annotation-tag-*.json"))
            )
            events_by_tag = self.list_reusable_tag_events_for_tags(
                definition.tag_id for definition in definitions
            )
            views = tuple(
                SelectionReusableTagView.project(
                    definition,
                    events_by_tag[definition.tag_id],
                )
                for definition in definitions
            )
        except Exception as exc:
            if isinstance(exc, SelectionStoreError):
                raise
            raise SelectionStoreError("reusable tag is invalid") from exc
        ordered = tuple(sorted(views, key=lambda item: (item.created_at, item.tag_id)))
        if include_archived:
            return ordered
        return tuple(item for item in ordered if item.active)

    def list_reusable_tag_events(
        self,
        tag_id: str,
    ) -> tuple[SelectionReusableTagEvent, ...]:
        return self.list_reusable_tag_events_for_tags((tag_id,))[tag_id]

    def list_reusable_tag_events_for_tags(
        self,
        tag_ids: Iterable[str],
    ) -> dict[str, tuple[SelectionReusableTagEvent, ...]]:
        requested = tuple(dict.fromkeys(tag_ids))
        for tag_id in requested:
            self.load_reusable_tag(tag_id)
        if not requested:
            return {}
        events_by_tag: dict[str, list[SelectionReusableTagEvent]] = {
            tag_id: [] for tag_id in requested
        }
        index = self._usable_read_index()
        if index is not None:
            try:
                return {
                    tag_id: tuple(
                        self._load_reusable_tag_event(path)
                        for path in index.list_reusable_tag_event_paths(tag_id)
                    )
                    for tag_id in requested
                }
            except SelectionIndexError:
                self._read_index = None
        requested_set = set(requested)
        try:
            for path in sorted(self.reusable_tag_event_dir.glob("annotation-tag-event-*.json")):
                event = SelectionReusableTagEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if event.tag_id in requested_set:
                    events_by_tag[event.tag_id].append(event)
        except Exception as exc:
            raise SelectionStoreError("reusable tag event is invalid") from exc
        return {
            tag_id: tuple(sorted(events, key=lambda event: (event.occurred_at, event.event_id)))
            for tag_id, events in events_by_tag.items()
        }

    def set_reusable_tag_label(
        self,
        tag_id: str,
        *,
        label: str,
        occurred_at: datetime,
        reason: str | None = None,
    ) -> SelectionReusableTagEvent:
        with local_write_lock(self.root):
            definition = self.load_reusable_tag(tag_id)
            events = self.list_reusable_tag_events(tag_id)
            current = SelectionReusableTagView.project(definition, events)
            if not current.active:
                raise SelectionStoreError("archived reusable tag cannot be renamed")
            normalized_label = label.strip()
            if normalized_label == current.label:
                raise SelectionStoreError("reusable tag rename must change the label")
            return self._write_reusable_tag_event_unlocked(
                tag_id=tag_id,
                category=definition.category,
                event_type=SelectionReusableTagEventType.RENAME,
                occurred_at=occurred_at,
                new_label=normalized_label,
                reason=reason,
            )

    def set_reusable_tag_status(
        self,
        tag_id: str,
        status: SelectionLifecycleStatus,
        *,
        occurred_at: datetime,
        reason: str | None = None,
    ) -> SelectionReusableTagEvent:
        with local_write_lock(self.root):
            definition = self.load_reusable_tag(tag_id)
            events = self.list_reusable_tag_events(tag_id)
            current = SelectionReusableTagView.project(definition, events)
            if status is SelectionLifecycleStatus.ARCHIVED:
                event_type = SelectionReusableTagEventType.ARCHIVE
                if not current.active:
                    raise SelectionStoreError("reusable tag is already archived")
            else:
                event_type = SelectionReusableTagEventType.RESTORE
                if current.active:
                    raise SelectionStoreError("reusable tag is already active")
            return self._write_reusable_tag_event_unlocked(
                tag_id=tag_id,
                category=definition.category,
                event_type=event_type,
                occurred_at=occurred_at,
                reason=reason,
            )

    def _write_reusable_tag_event_unlocked(
        self,
        *,
        tag_id: str,
        category: SelectionReusableTagCategory,
        event_type: SelectionReusableTagEventType,
        occurred_at: datetime,
        new_label: str | None = None,
        reason: str | None = None,
    ) -> SelectionReusableTagEvent:
        events = self.list_reusable_tag_events(tag_id)
        if events and occurred_at <= events[-1].occurred_at:
            raise SelectionStoreError("reusable tag event time must be after previous event")
        identity = "\0".join(
            (
                tag_id,
                category.value,
                event_type.value,
                occurred_at.isoformat(),
                new_label or "",
                reason or "",
            )
        )
        event_id = (
            f"annotation-tag-event-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"
        )
        event = SelectionReusableTagEvent(
            event_id=event_id,
            tag_id=tag_id,
            category=category,
            event_type=event_type,
            occurred_at=occurred_at,
            new_label=new_label,
            reason=reason,
        )
        path = self.reusable_tag_event_dir / f"{event_id}.json"
        if path.exists():
            try:
                existing = SelectionReusableTagEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise SelectionStoreError(f"reusable tag event is invalid: {event_id}") from exc
            if existing != event:
                raise SelectionStoreError(
                    "reusable tag event identity already has different content"
                )
            self._update_read_index(lambda index: index.upsert_reusable_tag_event(existing, path))
            return existing
        _write_selection_text(path, event.model_dump_json(indent=2))
        self._update_read_index(lambda index: index.upsert_reusable_tag_event(event, path))
        return event
