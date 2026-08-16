"""Manual labels, annotations and candidate exclusion persistence."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime, time, timedelta

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.data_pipeline.selection_store._base import SelectionStoreBase, SelectionStoreError
from stratgauge.data_pipeline.selection_store._io import _write_selection_text
from stratgauge.domain.selections import (
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionCandidateExclusion,
)


class AnnotationsMixin(SelectionStoreBase):
    """Label, annotation and exclusion operations."""

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
