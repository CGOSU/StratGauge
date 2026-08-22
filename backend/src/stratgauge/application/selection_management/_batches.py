"""Managed batch, lifecycle and export methods."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stratgauge.backtest.selection_dataset import (
    build_annotation_dataset,
    build_selection_dataset,
    serialize_annotation_dataset,
    serialize_selection_dataset,
)
from stratgauge.backtest.selection_outcomes import latest_selection_outcomes
from stratgauge.data_pipeline.selection_store import SelectionStoreError
from stratgauge.domain.selections import (
    SelectionLifecycleStatus,
    TdxSelectionImport,
)

if TYPE_CHECKING:
    from stratgauge.api.selections import (
        SelectionManagedBatchDetailView,
        SelectionManagedBatchPage,
        SelectionManagedBatchView,
        SelectionManagedFormulaView,
    )


from stratgauge.application.selection_management._base import (
    SelectionManagementBase,
)
from stratgauge.application.selection_management.errors import SelectionApiError  # noqa: F401
from stratgauge.application.selection_management.export_rows import (  # noqa: F401
    annotation_dataset_row,
    latest_outcome_after_annotation,
    selection_dataset_row,
)
from stratgauge.application.selection_management.pagination import (  # noqa: F401
    is_entity_active,
    validate_page,
)


class BatchesMixin(SelectionManagementBase):
    """Managed batch lifecycle and export operations."""

    def list_managed_formulas(self) -> tuple[SelectionManagedFormulaView, ...]:
        from stratgauge.api.selections import SelectionManagedFormulaView

        try:
            formulas = self._store.list_formulas(include_archived=True)
            lifecycle_by_formula = self._store.list_lifecycle_events_for_entities(
                "formula",
                (formula.formula_id for formula in formulas),
            )
            return tuple(
                SelectionManagedFormulaView(
                    formula=formula,
                    active=is_entity_active(lifecycle_by_formula[formula.formula_id]),
                    lifecycle=lifecycle_by_formula[formula.formula_id],
                )
                for formula in formulas
            )
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def _managed_batch_views(
        self,
        imports: tuple[TdxSelectionImport, ...],
    ) -> tuple[SelectionManagedBatchView, ...]:
        from stratgauge.api.selections import SelectionManagedBatchView

        candidate_ids = tuple(
            candidate.candidate_id for imported in imports for candidate in imported.candidates
        )
        labels_by_candidate = self._store.list_labels_for_candidates(candidate_ids)
        annotations_by_candidate = self._store.list_annotations_for_candidates(candidate_ids)
        exclusions_by_candidate = self._store.list_exclusions_for_candidates(candidate_ids)
        outcomes_by_candidate = self._store.list_outcomes_for_candidates(candidate_ids)
        lifecycle_by_batch = self._store.list_lifecycle_events_for_entities(
            "batch",
            (imported.batch.batch_id for imported in imports),
        )
        managed = tuple(
            SelectionManagedBatchView(
                batch=imported.batch,
                active=is_entity_active(lifecycle_by_batch[imported.batch.batch_id]),
                label_count=sum(
                    len(labels_by_candidate[candidate.candidate_id])
                    for candidate in imported.candidates
                ),
                annotation_count=sum(
                    len(annotations_by_candidate[candidate.candidate_id])
                    for candidate in imported.candidates
                ),
                exclusion_count=sum(
                    len(exclusions_by_candidate[candidate.candidate_id])
                    for candidate in imported.candidates
                ),
                outcome_count=sum(
                    len(outcomes_by_candidate[candidate.candidate_id])
                    for candidate in imported.candidates
                ),
                lifecycle=lifecycle_by_batch[imported.batch.batch_id],
            )
            for imported in imports
        )
        return tuple(
            sorted(
                managed,
                key=lambda item: (item.batch.selection_date, item.batch.batch_id),
                reverse=True,
            )
        )

    def list_managed_batches(self) -> tuple[SelectionManagedBatchView, ...]:
        try:
            manifests = self._store.list_manifests(include_archived=True)
            imports = tuple(self._store.load_import(manifest.batch_id) for manifest in manifests)
            return self._managed_batch_views(imports)
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def list_managed_batches_page(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> SelectionManagedBatchPage:
        from stratgauge.api.selections import SelectionManagedBatchPage

        validate_page(page, page_size)
        try:
            manifests = self._store.list_manifests(include_archived=True)
            imports = tuple(self._store.load_import(manifest.batch_id) for manifest in manifests)
            ordered_imports = tuple(
                sorted(
                    imports,
                    key=lambda imported: (
                        imported.batch.selection_date,
                        imported.batch.batch_id,
                    ),
                    reverse=True,
                )
            )
            start = (page - 1) * page_size
            page_imports = ordered_imports[start : start + page_size]
            page_items = self._managed_batch_views(page_imports)
            total = len(ordered_imports)
            return SelectionManagedBatchPage(
                items=page_items,
                page=page,
                page_size=page_size,
                total=total,
                has_next=start + page_size < total,
            )
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def get_managed_batch(self, batch_id: str) -> SelectionManagedBatchDetailView:
        from stratgauge.api.selections import SelectionManagedBatchDetailView

        try:
            imported = self._store.load_import(batch_id)
            return SelectionManagedBatchDetailView(
                view=self._view_factory(imported),
                active=self._store.is_entity_active("batch", batch_id),
                lifecycle=self._store.list_lifecycle_events("batch", batch_id),
            )
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def set_formula_status(
        self,
        *,
        formula_id: str,
        status: SelectionLifecycleStatus,
        reason: str | None,
    ) -> SelectionManagedFormulaView:
        try:
            self._store.set_formula_status(
                formula_id,
                status,
                changed_at=datetime.now(UTC),
                reason=reason,
            )
            return next(
                item
                for item in self.list_managed_formulas()
                if item.formula.formula_id == formula_id
            )
        except StopIteration as exc:
            raise SelectionApiError("SELECTION_FORMULA_NOT_FOUND", "选股公式不存在。", 404) from exc
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_FORMULA_NOT_FOUND", "选股公式不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def set_batch_status(
        self,
        *,
        batch_id: str,
        status: SelectionLifecycleStatus,
        reason: str | None,
    ) -> SelectionManagedBatchDetailView:
        try:
            self._store.set_batch_status(
                batch_id,
                status,
                changed_at=datetime.now(UTC),
                reason=reason,
            )
            return self.get_managed_batch(batch_id)
        except SelectionApiError:
            raise
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def load_raw_batch(self, batch_id: str) -> tuple[bytes, str]:
        try:
            imported = self._store.load_import(batch_id)
            filename = imported.batch.source_filename or f"selection-{batch_id}.txt"
            return self._store.load_raw(batch_id), filename
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def export_dataset(self, batch_id: str) -> bytes:
        try:
            rows = build_selection_dataset(self._store, batch_id)
            return serialize_selection_dataset(rows).encode("utf-8")
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def export_annotation_dataset(self, batch_id: str) -> bytes:
        try:
            rows = build_annotation_dataset(self._store, batch_id)
            return serialize_annotation_dataset(rows).encode("utf-8")
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        except ValueError as exc:
            raise SelectionApiError("SELECTION_ANNOTATION_DATASET_INVALID", str(exc), 422) from exc

    def stream_dataset(self, batch_id: str) -> Iterator[bytes]:
        try:
            imported = self._store.load_import(batch_id)
            candidate_ids = tuple(candidate.candidate_id for candidate in imported.candidates)
            labels_by_candidate = self._store.list_labels_for_candidates(candidate_ids)
            outcomes_by_candidate = self._store.list_outcomes_for_candidates(candidate_ids)
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

        def generate() -> Iterator[bytes]:
            for candidate in imported.candidates:
                labels = labels_by_candidate[candidate.candidate_id]
                latest_label = labels[-1] if labels else None
                for outcome in latest_selection_outcomes(
                    outcomes_by_candidate[candidate.candidate_id]
                ) or (None,):
                    row = selection_dataset_row(
                        imported.batch,
                        candidate,
                        latest_label,
                        outcome,
                    )
                    yield f"{row.model_dump_json()}\n".encode()

        return generate()

    def stream_annotation_dataset(self, batch_id: str) -> Iterator[bytes]:
        try:
            imported = self._store.load_import(batch_id)
            candidate_ids = tuple(candidate.candidate_id for candidate in imported.candidates)
            annotations_by_candidate = self._store.list_annotations_for_candidates(candidate_ids)
            outcomes_by_candidate = self._store.list_outcomes_for_candidates(candidate_ids)
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

        annotated_rows = [
            (
                annotation.recorded_at,
                annotation.annotation_id,
                imported.batch,
                candidate,
                annotation,
                latest_outcome_after_annotation(
                    latest_selection_outcomes(outcomes_by_candidate[candidate.candidate_id]),
                    annotation,
                ),
            )
            for candidate in imported.candidates
            for annotation in annotations_by_candidate[candidate.candidate_id]
        ]

        def generate() -> Iterator[bytes]:
            for _, _, batch, candidate, annotation, outcome in sorted(
                annotated_rows,
                key=lambda item: (item[0], item[1]),
            ):
                row = annotation_dataset_row(batch, candidate, annotation, outcome)
                yield f"{row.model_dump_json()}\n".encode()

        return generate()
