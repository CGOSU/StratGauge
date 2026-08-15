"""Immutable storage for manually imported selection exports."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field

from stratgauge._files import atomic_write_text
from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.data_pipeline.selection_index import (
    SelectionIndexError,
    SelectionIndexStats,
    SelectionReadIndex,
)
from stratgauge.domain.models import DomainModel
from stratgauge.domain.selections import (
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionCandidateExclusion,
    SelectionFormula,
    SelectionLifecycleEvent,
    SelectionLifecycleStatus,
    SelectionMaturityTask,
    SelectionMaturityTaskEvent,
    SelectionMaturityTaskStatus,
    SelectionOutcome,
    SelectionPerformance,
    SelectionReusableTag,
    SelectionReusableTagCategory,
    SelectionReusableTagEvent,
    SelectionReusableTagEventType,
    SelectionReusableTagView,
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


class SelectionImportStore:
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

    def create_formula(
        self,
        *,
        name: str,
        version: str | None = None,
        description: str | None = None,
    ) -> SelectionFormula:
        with local_write_lock(self.root):
            return self._create_formula_unlocked(
                name=name,
                version=version,
                description=description,
            )

    def _create_formula_unlocked(
        self,
        *,
        name: str,
        version: str | None = None,
        description: str | None = None,
    ) -> SelectionFormula:
        normalized_name = name.strip()
        normalized_version = version.strip() if version else None
        identity = f"{normalized_name}\0{normalized_version or ''}"
        formula_id = f"formula-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"
        formula = SelectionFormula(
            formula_id=formula_id,
            name=normalized_name,
            version=normalized_version,
            description=description,
            created_at=datetime.now(UTC),
        )
        path = self.formula_dir / f"{formula_id}.json"
        if path.exists():
            existing = self.load_formula(formula_id)
            if (
                existing.name != formula.name
                or existing.version != formula.version
                or existing.description != formula.description
            ):
                raise SelectionStoreError("formula identity already exists with different metadata")
            return existing
        _write_selection_text(path, formula.model_dump_json(indent=2))
        return formula

    def list_formulas(self, *, include_archived: bool = False) -> tuple[SelectionFormula, ...]:
        try:
            formulas = tuple(
                SelectionFormula.model_validate_json(path.read_text(encoding="utf-8"))
                for path in self.formula_dir.glob("formula-*.json")
            )
        except Exception as exc:
            raise SelectionStoreError("selection formula is invalid") from exc
        ordered = tuple(
            sorted(
                formulas, key=lambda formula: (formula.created_at, formula.formula_id), reverse=True
            )
        )
        if include_archived:
            return ordered
        return tuple(
            formula for formula in ordered if self.is_entity_active("formula", formula.formula_id)
        )

    def load_formula(self, formula_id: str) -> SelectionFormula:
        if not re.fullmatch(r"formula-[0-9a-f]{20}", formula_id):
            raise SelectionStoreError(f"invalid selection formula id: {formula_id}")
        path = self.formula_dir / f"{formula_id}.json"
        if not path.exists():
            raise SelectionStoreError(f"selection formula does not exist: {formula_id}")
        try:
            return SelectionFormula.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"selection formula is invalid: {formula_id}") from exc

    def write(
        self,
        raw_content: bytes,
        parsed: TdxSelectionImport,
    ) -> SelectionImportManifest:
        with local_write_lock(self.root):
            return self._write_unlocked(raw_content, parsed)

    def _write_unlocked(
        self,
        raw_content: bytes,
        parsed: TdxSelectionImport,
    ) -> SelectionImportManifest:
        actual_hash = hashlib.sha256(raw_content).hexdigest()
        if actual_hash != parsed.batch.source_file_sha256:
            raise SelectionStoreError("raw content does not match parsed import hash")

        manifest_path = self.manifest_dir / f"{parsed.batch.batch_id}.json"
        if manifest_path.exists():
            manifest = self.load_manifest(parsed.batch.batch_id)
            if manifest.source_file_sha256 != actual_hash:
                raise SelectionStoreError("existing selection manifest has a different hash")
            return manifest

        raw_path = self.raw_dir / f"{actual_hash}.bin"
        import_path = self.import_dir / f"{parsed.batch.batch_id}.json"
        _atomic_write_bytes(raw_path, raw_content)
        _write_selection_text(import_path, parsed.model_dump_json(indent=2))
        manifest = SelectionImportManifest(
            batch_id=parsed.batch.batch_id,
            source_file_sha256=actual_hash,
            candidate_count=len(parsed.candidates),
            raw_path=str(raw_path.relative_to(self.root)),
            import_path=str(import_path.relative_to(self.root)),
        )
        _write_selection_text(manifest_path, manifest.model_dump_json(indent=2))
        return manifest

    def load_import(self, batch_id: str) -> TdxSelectionImport:
        manifest = self.load_manifest(batch_id)
        path = self.root / manifest.import_path
        if not path.exists():
            raise SelectionStoreError(f"selection import does not exist: {batch_id}")
        try:
            return TdxSelectionImport.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"selection import is invalid: {batch_id}") from exc

    def write_label(self, batch_id: str, label: ManualSelectionLabel) -> ManualSelectionLabel:
        with local_write_lock(self.root):
            return self._write_label_unlocked(batch_id, label)

    def _write_label_unlocked(
        self,
        batch_id: str,
        label: ManualSelectionLabel,
    ) -> ManualSelectionLabel:
        imported = self.load_import(batch_id)
        if not any(
            candidate.candidate_id == label.candidate_id for candidate in imported.candidates
        ):
            raise SelectionStoreError(
                f"candidate does not belong to selection batch: {label.candidate_id}"
            )
        if label.labeled_at < imported.batch.exported_at:
            raise SelectionStoreError("label timestamp precedes selection export")
        freeze_deadline = datetime.combine(
            imported.batch.selection_date + timedelta(days=1),
            time.min,
            tzinfo=imported.batch.exported_at.tzinfo,
        )
        if label.labeled_at >= freeze_deadline:
            raise SelectionStoreError("label timestamp is after the selection freeze deadline")
        label_id = (
            f"label-{hashlib.sha256(label.model_dump_json().encode('utf-8')).hexdigest()[:20]}"
        )
        path = self.label_dir / f"{label_id}.json"
        if path.exists():
            try:
                return ManualSelectionLabel.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SelectionStoreError(f"selection label is invalid: {label_id}") from exc
        _write_selection_text(path, label.model_dump_json(indent=2))
        return label

    def write_annotation(
        self,
        batch_id: str,
        annotation: SelectionAnnotation,
    ) -> SelectionAnnotation:
        with local_write_lock(self.root):
            return self._write_annotation_unlocked(batch_id, annotation)

    def _write_annotation_unlocked(
        self,
        batch_id: str,
        annotation: SelectionAnnotation,
    ) -> SelectionAnnotation:
        imported = self.load_import(batch_id)
        if not any(
            candidate.candidate_id == annotation.candidate_id for candidate in imported.candidates
        ):
            raise SelectionStoreError(
                f"candidate does not belong to selection batch: {annotation.candidate_id}"
            )
        path = self.annotation_dir / f"{annotation.annotation_id}.json"
        if path.exists():
            try:
                existing = SelectionAnnotation.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SelectionStoreError(
                    f"selection annotation is invalid: {annotation.annotation_id}"
                ) from exc
            if existing != annotation:
                raise SelectionStoreError(
                    "selection annotation identity already has different content"
                )
            return existing
        _write_selection_text(path, annotation.model_dump_json(indent=2))
        return annotation

    def list_labels_for_candidates(
        self,
        candidate_ids: Iterable[str],
    ) -> dict[str, tuple[ManualSelectionLabel, ...]]:
        requested = tuple(dict.fromkeys(candidate_ids))
        if not requested:
            return {}
        labels_by_candidate: dict[str, list[ManualSelectionLabel]] = {
            candidate_id: [] for candidate_id in requested
        }
        requested_set = set(requested)
        try:
            for path in sorted(self.label_dir.glob("label-*.json")):
                label = ManualSelectionLabel.model_validate_json(path.read_text(encoding="utf-8"))
                if label.candidate_id in requested_set:
                    labels_by_candidate[label.candidate_id].append(label)
        except Exception as exc:
            raise SelectionStoreError("selection label is invalid") from exc
        return {
            candidate_id: tuple(sorted(labels, key=lambda label: label.labeled_at))
            for candidate_id, labels in labels_by_candidate.items()
        }

    def list_annotations(self, candidate_id: str) -> tuple[SelectionAnnotation, ...]:
        annotations: list[SelectionAnnotation] = []
        try:
            for path in sorted(self.annotation_dir.glob("annotation-*.json")):
                annotation = SelectionAnnotation.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if annotation.candidate_id == candidate_id:
                    annotations.append(annotation)
        except Exception as exc:
            raise SelectionStoreError("selection annotation is invalid") from exc
        return tuple(sorted(annotations, key=lambda item: (item.recorded_at, item.annotation_id)))

    def list_annotations_for_candidates(
        self,
        candidate_ids: Iterable[str],
    ) -> dict[str, tuple[SelectionAnnotation, ...]]:
        requested = tuple(dict.fromkeys(candidate_ids))
        if not requested:
            return {}
        annotations_by_candidate: dict[str, list[SelectionAnnotation]] = {
            candidate_id: [] for candidate_id in requested
        }
        requested_set = set(requested)
        try:
            for path in sorted(self.annotation_dir.glob("annotation-*.json")):
                annotation = SelectionAnnotation.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if annotation.candidate_id in requested_set:
                    annotations_by_candidate[annotation.candidate_id].append(annotation)
        except Exception as exc:
            raise SelectionStoreError("selection annotation is invalid") from exc
        return {
            candidate_id: tuple(
                sorted(annotations, key=lambda item: (item.recorded_at, item.annotation_id))
            )
            for candidate_id, annotations in annotations_by_candidate.items()
        }

    def latest_annotation(
        self,
        candidate_id: str,
        *,
        recorded_before: datetime | None = None,
    ) -> SelectionAnnotation | None:
        annotations = self.list_annotations(candidate_id)
        if recorded_before is not None:
            annotations = tuple(item for item in annotations if item.recorded_at <= recorded_before)
        return annotations[-1] if annotations else None

    def write_exclusion(
        self,
        batch_id: str,
        exclusion: SelectionCandidateExclusion,
    ) -> SelectionCandidateExclusion:
        with local_write_lock(self.root):
            return self._write_exclusion_unlocked(batch_id, exclusion)

    def _write_exclusion_unlocked(
        self,
        batch_id: str,
        exclusion: SelectionCandidateExclusion,
    ) -> SelectionCandidateExclusion:
        imported = self.load_import(batch_id)
        if not any(
            candidate.candidate_id == exclusion.candidate_id for candidate in imported.candidates
        ):
            raise SelectionStoreError(
                f"candidate does not belong to selection batch: {exclusion.candidate_id}"
            )
        path = self.exclusion_dir / f"{exclusion.exclusion_id}.json"
        if path.exists():
            try:
                existing = SelectionCandidateExclusion.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise SelectionStoreError(
                    f"selection exclusion is invalid: {exclusion.exclusion_id}"
                ) from exc
            if existing != exclusion:
                raise SelectionStoreError(
                    "selection exclusion identity already has different content"
                )
            return existing
        _write_selection_text(path, exclusion.model_dump_json(indent=2))
        return exclusion

    def list_exclusions(self, candidate_id: str) -> tuple[SelectionCandidateExclusion, ...]:
        exclusions: list[SelectionCandidateExclusion] = []
        try:
            for path in sorted(self.exclusion_dir.glob("exclusion-*.json")):
                exclusion = SelectionCandidateExclusion.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if exclusion.candidate_id == candidate_id:
                    exclusions.append(exclusion)
        except Exception as exc:
            raise SelectionStoreError("selection exclusion is invalid") from exc
        return tuple(sorted(exclusions, key=lambda item: (item.recorded_at, item.exclusion_id)))

    def list_exclusions_for_candidates(
        self,
        candidate_ids: Iterable[str],
    ) -> dict[str, tuple[SelectionCandidateExclusion, ...]]:
        requested = tuple(dict.fromkeys(candidate_ids))
        if not requested:
            return {}
        exclusions_by_candidate: dict[str, list[SelectionCandidateExclusion]] = {
            candidate_id: [] for candidate_id in requested
        }
        requested_set = set(requested)
        try:
            for path in sorted(self.exclusion_dir.glob("exclusion-*.json")):
                exclusion = SelectionCandidateExclusion.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if exclusion.candidate_id in requested_set:
                    exclusions_by_candidate[exclusion.candidate_id].append(exclusion)
        except Exception as exc:
            raise SelectionStoreError("selection exclusion is invalid") from exc
        return {
            candidate_id: tuple(
                sorted(exclusions, key=lambda item: (item.recorded_at, item.exclusion_id))
            )
            for candidate_id, exclusions in exclusions_by_candidate.items()
        }

    def latest_exclusion(
        self,
        candidate_id: str,
        *,
        recorded_before: datetime | None = None,
    ) -> SelectionCandidateExclusion | None:
        exclusions = self.list_exclusions(candidate_id)
        if recorded_before is not None:
            exclusions = tuple(item for item in exclusions if item.recorded_at <= recorded_before)
        return exclusions[-1] if exclusions else None

    def write_outcome(self, outcome: SelectionOutcome) -> SelectionOutcome:
        with local_write_lock(self.root):
            return self._write_outcome_unlocked(outcome)

    def _write_outcome_unlocked(self, outcome: SelectionOutcome) -> SelectionOutcome:
        imported = self.load_import(outcome.batch_id)
        if not any(
            candidate.candidate_id == outcome.candidate_id for candidate in imported.candidates
        ):
            raise SelectionStoreError(
                f"candidate does not belong to selection batch: {outcome.candidate_id}"
            )

        if outcome.maturity_task_id is not None:
            existing = self.find_outcome_for_task(
                outcome.maturity_task_id,
                outcome.candidate_id,
            )
            if existing is not None:
                if existing == outcome:
                    return existing
                raise SelectionStoreError(
                    "maturity task candidate already has a different outcome: "
                    f"{outcome.maturity_task_id}/{outcome.candidate_id}"
                )

        outcome_id = (
            f"outcome-{hashlib.sha256(outcome.model_dump_json().encode('utf-8')).hexdigest()[:20]}"
        )
        path = self.outcome_dir / f"{outcome_id}.json"
        if path.exists():
            try:
                existing = SelectionOutcome.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SelectionStoreError(f"selection outcome is invalid: {outcome_id}") from exc
            if existing != outcome:
                raise SelectionStoreError(
                    f"selection outcome identity has different content: {outcome_id}"
                )
            self._update_read_index(lambda index: index.upsert_outcome(existing, path))
            return existing
        _write_selection_text(path, outcome.model_dump_json(indent=2))
        self._update_read_index(lambda index: index.upsert_outcome(outcome, path))
        return outcome

    def write_performance(self, performance: SelectionPerformance) -> SelectionPerformance:
        with local_write_lock(self.root):
            return self._write_performance_unlocked(performance)

    def _write_performance_unlocked(
        self,
        performance: SelectionPerformance,
    ) -> SelectionPerformance:
        imported = self.load_import(performance.batch_id)
        if not any(
            candidate.candidate_id == performance.candidate_id for candidate in imported.candidates
        ):
            raise SelectionStoreError(
                f"candidate does not belong to selection batch: {performance.candidate_id}"
            )
        if performance.maturity_task_id is not None:
            existing = self.find_performance_for_task(
                performance.maturity_task_id,
                performance.candidate_id,
            )
            if existing is not None:
                if existing == performance:
                    return existing
                raise SelectionStoreError(
                    "maturity task candidate already has a different performance: "
                    f"{performance.maturity_task_id}/{performance.candidate_id}"
                )
        performance_id = (
            "performance-"
            f"{hashlib.sha256(performance.model_dump_json().encode('utf-8')).hexdigest()[:20]}"
        )
        path = self.performance_dir / f"{performance_id}.json"
        if path.exists():
            try:
                existing = SelectionPerformance.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise SelectionStoreError(
                    f"selection performance is invalid: {performance_id}"
                ) from exc
            if existing != performance:
                raise SelectionStoreError(
                    f"selection performance identity has different content: {performance_id}"
                )
            self._update_read_index(lambda index: index.upsert_performance(existing, path))
            return existing
        _write_selection_text(path, performance.model_dump_json(indent=2))
        self._update_read_index(lambda index: index.upsert_performance(performance, path))
        return performance

    def find_performance_for_task(
        self,
        maturity_task_id: str,
        candidate_id: str,
    ) -> SelectionPerformance | None:
        indexed_paths = self._indexed_paths(
            lambda index: index.find_performance_paths_for_task(
                maturity_task_id,
                candidate_id,
            )
        )
        if indexed_paths is None:
            matches = tuple(
                performance
                for performance in self._list_all_performances()
                if performance.maturity_task_id == maturity_task_id
                and performance.candidate_id == candidate_id
            )
        else:
            matches = tuple(self._load_performance(path) for path in indexed_paths)
        if len(matches) > 1:
            raise SelectionStoreError(
                "maturity task candidate has more than one performance: "
                f"{maturity_task_id}/{candidate_id}"
            )
        return matches[0] if matches else None

    def list_performances(self, candidate_id: str) -> tuple[SelectionPerformance, ...]:
        indexed_paths = self._indexed_paths(
            lambda index: index.list_performance_paths(candidate_id)
        )
        if indexed_paths is None:
            performances = [
                performance
                for performance in self._list_all_performances()
                if performance.candidate_id == candidate_id
            ]
        else:
            performances = [self._load_performance(path) for path in indexed_paths]
        return tuple(performances)

    def list_performances_for_candidates(
        self,
        candidate_ids: Iterable[str],
    ) -> dict[str, tuple[SelectionPerformance, ...]]:
        requested = tuple(dict.fromkeys(candidate_ids))
        if not requested:
            return {}
        performances_by_candidate: dict[str, list[SelectionPerformance]] = {
            candidate_id: [] for candidate_id in requested
        }
        index = self._usable_read_index()
        if index is not None:
            try:
                return {
                    candidate_id: tuple(
                        self._load_performance(path)
                        for path in index.list_performance_paths(candidate_id)
                    )
                    for candidate_id in requested
                }
            except SelectionIndexError:
                self._read_index = None
        for performance in self._list_all_performances():
            if performance.candidate_id in performances_by_candidate:
                performances_by_candidate[performance.candidate_id].append(performance)
        return {
            candidate_id: tuple(
                sorted(
                    rows,
                    key=lambda performance: (
                        performance.performance_policy_id,
                        performance.evaluated_at,
                    ),
                )
            )
            for candidate_id, rows in performances_by_candidate.items()
        }

    def _list_all_performances(self) -> tuple[SelectionPerformance, ...]:
        performances: list[SelectionPerformance] = []
        for path in sorted(self.performance_dir.glob("performance-*.json")):
            performances.append(self._load_performance(path))
        return tuple(performances)

    def _load_performance(self, source_path: str | Path) -> SelectionPerformance:
        path = self._source_path(source_path)
        try:
            return SelectionPerformance.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"selection performance is invalid: {path.name}") from exc

    def find_outcome_for_task(
        self,
        maturity_task_id: str,
        candidate_id: str,
    ) -> SelectionOutcome | None:
        indexed_paths = self._indexed_paths(
            lambda index: index.find_outcome_paths_for_task(
                maturity_task_id,
                candidate_id,
            )
        )
        if indexed_paths is None:
            matches = tuple(
                outcome
                for outcome in self._list_all_outcomes()
                if outcome.maturity_task_id == maturity_task_id
                and outcome.candidate_id == candidate_id
            )
        else:
            matches = tuple(self._load_outcome(path) for path in indexed_paths)
        if len(matches) > 1:
            raise SelectionStoreError(
                "maturity task candidate has more than one outcome: "
                f"{maturity_task_id}/{candidate_id}"
            )
        return matches[0] if matches else None

    def list_maturity_task_outcomes(self, maturity_task_id: str) -> tuple[SelectionOutcome, ...]:
        indexed_paths = self._indexed_paths(
            lambda index: index.list_maturity_task_outcome_paths(maturity_task_id)
        )
        if indexed_paths is None:
            outcomes = tuple(
                outcome
                for outcome in self._list_all_outcomes()
                if outcome.maturity_task_id == maturity_task_id
            )
        else:
            outcomes = tuple(self._load_outcome(path) for path in indexed_paths)
        return outcomes

    def list_outcomes(self, candidate_id: str) -> tuple[SelectionOutcome, ...]:
        indexed_paths = self._indexed_paths(lambda index: index.list_outcome_paths(candidate_id))
        if indexed_paths is None:
            outcomes = [
                outcome
                for outcome in self._list_all_outcomes()
                if outcome.candidate_id == candidate_id
            ]
        else:
            outcomes = [self._load_outcome(path) for path in indexed_paths]
        return tuple(sorted(outcomes, key=lambda outcome: outcome.evaluated_at))

    def list_outcomes_for_candidates(
        self,
        candidate_ids: Iterable[str],
    ) -> dict[str, tuple[SelectionOutcome, ...]]:
        requested = tuple(dict.fromkeys(candidate_ids))
        if not requested:
            return {}
        outcomes_by_candidate: dict[str, list[SelectionOutcome]] = {
            candidate_id: [] for candidate_id in requested
        }
        index = self._usable_read_index()
        if index is not None:
            try:
                return {
                    candidate_id: tuple(
                        self._load_outcome(path) for path in index.list_outcome_paths(candidate_id)
                    )
                    for candidate_id in requested
                }
            except SelectionIndexError:
                self._read_index = None
        for outcome in self._list_all_outcomes():
            if outcome.candidate_id in outcomes_by_candidate:
                outcomes_by_candidate[outcome.candidate_id].append(outcome)
        return {
            candidate_id: tuple(sorted(rows, key=lambda outcome: outcome.evaluated_at))
            for candidate_id, rows in outcomes_by_candidate.items()
        }

    def _list_all_outcomes(self) -> tuple[SelectionOutcome, ...]:
        outcomes: list[SelectionOutcome] = []
        for path in sorted(self.outcome_dir.glob("outcome-*.json")):
            outcomes.append(self._load_outcome(path))
        return tuple(outcomes)

    def _load_outcome(self, source_path: str | Path) -> SelectionOutcome:
        path = self._source_path(source_path)
        try:
            return SelectionOutcome.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"selection outcome is invalid: {path.name}") from exc

    def list_labels(self, candidate_id: str) -> tuple[ManualSelectionLabel, ...]:
        labels: list[ManualSelectionLabel] = []
        for path in sorted(self.label_dir.glob("label-*.json")):
            try:
                label = ManualSelectionLabel.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SelectionStoreError(f"selection label is invalid: {path.name}") from exc
            if label.candidate_id == candidate_id:
                labels.append(label)
        return tuple(sorted(labels, key=lambda label: label.labeled_at))

    def create_maturity_task(self, task: SelectionMaturityTask) -> SelectionMaturityTask:
        """Create one task or return it idempotently without changing its config."""

        with local_write_lock(self.root):
            return self._create_maturity_task_unlocked(task)

    def _create_maturity_task_unlocked(
        self,
        task: SelectionMaturityTask,
    ) -> SelectionMaturityTask:

        self.load_import(task.batch_id)
        if not self.is_entity_active("batch", task.batch_id):
            raise SelectionStoreError("selection batch is archived")
        path = self._maturity_task_path(task.task_id)
        if path.exists():
            existing = self.load_maturity_task(task.task_id)
            if not _same_maturity_task_configuration(existing, task):
                raise SelectionStoreError(
                    "maturity task identity already exists with different configuration"
                )
            return existing
        _write_selection_text(path, _task_storage_json(task))
        return self.load_maturity_task(task.task_id)

    def save_maturity_task(self, task: SelectionMaturityTask) -> SelectionMaturityTask:
        """Atomically materialize task state while keeping its first config immutable."""

        with local_write_lock(self.root):
            return self._save_maturity_task_unlocked(task)

    def _save_maturity_task_unlocked(
        self,
        task: SelectionMaturityTask,
    ) -> SelectionMaturityTask:

        path = self._maturity_task_path(task.task_id)
        if not path.exists():
            raise SelectionStoreError(f"maturity task does not exist: {task.task_id}")
        existing = self.load_maturity_task(task.task_id)
        if not _same_maturity_task_configuration(existing, task):
            raise SelectionStoreError(
                "maturity task configuration cannot be changed after creation"
            )
        _write_selection_text(path, _task_storage_json(task))
        return self.load_maturity_task(task.task_id)

    def load_maturity_task(self, task_id: str) -> SelectionMaturityTask:
        path = self._maturity_task_path(task_id)
        if not path.exists():
            raise SelectionStoreError(f"maturity task does not exist: {task_id}")
        try:
            task = SelectionMaturityTask.model_validate_json(path.read_text(encoding="utf-8"))
            events = self.list_maturity_task_events(task_id)
            return task.model_copy(update={"events": events})
        except SelectionStoreError:
            raise
        except Exception as exc:
            raise SelectionStoreError(f"maturity task is invalid: {task_id}") from exc

    def list_maturity_tasks(
        self,
        *,
        batch_id: str | None = None,
        status: str | SelectionMaturityTaskStatus | None = None,
    ) -> tuple[SelectionMaturityTask, ...]:
        tasks: list[SelectionMaturityTask] = []
        status_value = status.value if isinstance(status, SelectionMaturityTaskStatus) else status
        try:
            for path in sorted(self.maturity_task_dir.glob("maturity-task-*.json")):
                task = self.load_maturity_task(path.stem)
                if batch_id is not None and task.batch_id != batch_id:
                    continue
                if status_value is not None and task.status.value != status_value:
                    continue
                tasks.append(task)
        except SelectionStoreError:
            raise
        except Exception as exc:
            raise SelectionStoreError("maturity task is invalid") from exc
        return tuple(sorted(tasks, key=lambda task: (task.created_at, task.task_id)))

    def append_maturity_task_event(
        self, event: SelectionMaturityTaskEvent
    ) -> SelectionMaturityTaskEvent:
        with local_write_lock(self.root):
            return self._append_maturity_task_event_unlocked(event)

    def _append_maturity_task_event_unlocked(
        self,
        event: SelectionMaturityTaskEvent,
    ) -> SelectionMaturityTaskEvent:
        self._maturity_task_path(event.task_id)
        if not self._maturity_task_path(event.task_id).exists():
            raise SelectionStoreError(f"maturity task does not exist: {event.task_id}")
        event_id = event.event_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", event_id):
            raise SelectionStoreError("maturity task event identifier is invalid")
        path = self.maturity_event_dir / f"{event_id}.json"
        if path.exists():
            try:
                existing = SelectionMaturityTaskEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise SelectionStoreError(f"maturity task event is invalid: {event_id}") from exc
            if existing != event:
                raise SelectionStoreError(
                    f"maturity task event identity has different content: {event_id}"
                )
            return existing
        _write_selection_text(path, event.model_dump_json(indent=2))
        return event

    def list_maturity_task_events(self, task_id: str) -> tuple[SelectionMaturityTaskEvent, ...]:
        self._maturity_task_path(task_id)
        events: list[SelectionMaturityTaskEvent] = []
        try:
            for path in sorted(self.maturity_event_dir.glob("*.json")):
                event = SelectionMaturityTaskEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if event.task_id == task_id:
                    events.append(event)
        except Exception as exc:
            raise SelectionStoreError("maturity task event is invalid") from exc
        return tuple(sorted(events, key=_maturity_event_sort_key))

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


def _maturity_event_sort_key(
    event: SelectionMaturityTaskEvent,
) -> tuple[datetime, int, int, str]:
    event_order = {"queued": 0, "running": 1, "attempt_result": 2, "requeue": 3}
    return (
        event.occurred_at,
        event.attempt_count,
        event_order[event.event_type],
        event.event_id,
    )


def _same_maturity_task_configuration(
    first: SelectionMaturityTask,
    second: SelectionMaturityTask,
) -> bool:
    return (
        first.task_id == second.task_id
        and first.batch_id == second.batch_id
        and first.provider == second.provider
        and first.market_data_mode == second.market_data_mode
        and first.universe_snapshot_id == second.universe_snapshot_id
        and first.input_market_snapshot_ids == second.input_market_snapshot_ids
        and first.evaluated_at == second.evaluated_at
        and first.target_evaluation_date == second.target_evaluation_date
        and first.tracking_policy_id == second.tracking_policy_id
        and first.performance_policy_id == second.performance_policy_id
        and first.performance_target_return == second.performance_target_return
        and first.horizon_sessions == second.horizon_sessions
        and first.position_notional == second.position_notional
        and first.max_retries == second.max_retries
        and first.timeout_seconds == second.timeout_seconds
    )


def _task_storage_json(task: SelectionMaturityTask) -> str:
    return task.model_copy(update={"events": ()}).model_dump_json(indent=2)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SelectionStoreError(f"failed to write selection file: {path}") from exc


def _write_selection_text(path: Path, content: str) -> None:
    try:
        atomic_write_text(path, content)
    except OSError as exc:
        raise SelectionStoreError(f"failed to write selection file: {path}") from exc
