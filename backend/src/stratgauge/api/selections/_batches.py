"""Batch listing, import and manual creation service methods."""

from __future__ import annotations

from datetime import UTC, datetime

from stratgauge.api.selections._base import (  # noqa: F401
    SelectionServiceBase,
    _content_id,
    _decode_base64,
)
from stratgauge.api.selections.requests import (  # noqa: F401
    ManualSelectionCandidateRequest,
    ManualSelectionRequest,
    SelectionAnnotationRequest,
    SelectionExclusionRequest,
    SelectionFormulaCreateRequest,
    SelectionImportRequest,
    SelectionLabelRequest,
    SelectionLifecycleChangeRequest,
    SelectionMatureRequest,
    SelectionReusableTagCreateRequest,
    SelectionReusableTagRenameRequest,
)
from stratgauge.api.selections.views import (  # noqa: F401
    SelectionBatchPage,
    SelectionBatchView,
    SelectionCandidateView,
    SelectionFormulaView,
    SelectionManagedBatchDetailView,
    SelectionManagedBatchPage,
    SelectionManagedBatchView,
    SelectionManagedFormulaView,
    SelectionManagementStatsView,
    SelectionMatureView,
    SelectionObservationPage,
    SelectionObservationView,
    SelectionStatsView,
)
from stratgauge.application.selection_management import SelectionApiError as SelectionApiError
from stratgauge.application.selection_management import (
    page_slice,
    validate_page,
)
from stratgauge.data_pipeline.manual_selection import (
    ManualSelectionCandidateInput,
    ManualSelectionError,
    build_manual_selection,
)
from stratgauge.data_pipeline.selection_store import SelectionStoreError
from stratgauge.data_pipeline.tdx_import import TdxImportError, import_tdx_export
from stratgauge.domain.selections import (
    SelectionBatch,
    SelectionCandidate,
    SelectionCandidateReviewState,
)


class BatchesMixin(SelectionServiceBase):
    """Batch query and import methods."""

    def list_observation_pool(
        self,
        *,
        review_state: SelectionCandidateReviewState | None = None,
        batch_id: str | None = None,
        include_excluded: bool = False,
    ) -> tuple[SelectionObservationView, ...]:
        try:
            views: list[SelectionObservationView] = []
            imports = tuple(
                self._store.load_import(manifest.batch_id)
                for manifest in self._store.list_manifests()
                if batch_id is None or manifest.batch_id == batch_id
            )
            candidates = tuple(
                candidate for imported in imports for candidate in imported.candidates
            )
            candidate_views = {
                view.candidate.candidate_id: view for view in self._candidate_views(candidates)
            }
            for imported in imports:
                for candidate in imported.candidates:
                    candidate_view = candidate_views[candidate.candidate_id]
                    if candidate_view.excluded and not include_excluded:
                        continue
                    if review_state is not None and candidate_view.review_state != review_state:
                        continue
                    views.append(
                        SelectionObservationView(
                            batch=imported.batch,
                            candidate=candidate_view,
                        )
                    )
            return tuple(
                sorted(
                    views,
                    key=lambda item: (
                        item.batch.selection_date,
                        item.batch.batch_id,
                        item.candidate.candidate.symbol,
                    ),
                    reverse=True,
                )
            )
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def list_batches(self) -> tuple[SelectionBatch, ...]:
        return self._management.list_batches()

    def list_observation_pool_page(
        self,
        *,
        review_state: SelectionCandidateReviewState | None = None,
        batch_id: str | None = None,
        include_excluded: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> SelectionObservationPage:
        validate_page(page, page_size)
        items = self.list_observation_pool(
            review_state=review_state,
            batch_id=batch_id,
            include_excluded=include_excluded,
        )
        page_items, total, has_next = page_slice(items, page, page_size)
        return SelectionObservationPage(
            items=page_items,
            page=page,
            page_size=page_size,
            total=total,
            has_next=has_next,
        )

    def list_batches_page(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> SelectionBatchPage:
        return self._management.list_batches_page(page=page, page_size=page_size)

    def get_batch(self, batch_id: str) -> SelectionBatchView:
        try:
            imported = self._store.load_import(batch_id)
            return self._view(imported)
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def get_candidate(self, candidate_id: str) -> SelectionCandidate:
        try:
            for manifest in self._store.list_manifests(include_archived=True):
                imported = self._store.load_import(manifest.batch_id)
                for candidate in imported.candidates:
                    if candidate.candidate_id == candidate_id:
                        return candidate
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        raise SelectionApiError("SELECTION_CANDIDATE_NOT_FOUND", "候选股票不存在。", 404)

    def import_batch(self, request: SelectionImportRequest) -> SelectionBatchView:
        content = _decode_base64(request.content_base64)
        try:
            formula_name, formula_version, formula_id = self._formula_metadata(request)
            imported = import_tdx_export(
                content,
                selection_date=request.selection_date,
                exported_at=request.exported_at,
                imported_at=datetime.now(UTC),
                source_filename=request.source_filename,
                formula_name=formula_name,
                formula_version=formula_version,
                formula_id=formula_id,
                formula_sha256=request.formula_sha256,
            )
            self._store.write(content, imported)
            return self._view(imported)
        except TdxImportError as exc:
            raise SelectionApiError("INVALID_TDX_EXPORT", str(exc), 422) from exc
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        except ValueError as exc:
            raise SelectionApiError("INVALID_SELECTION_METADATA", str(exc), 422) from exc

    def create_manual_batch(self, request: ManualSelectionRequest) -> SelectionBatchView:
        formula = self._load_active_formula(request.formula_id)
        try:
            result = build_manual_selection(
                selection_date=request.selection_date,
                recorded_at=request.recorded_at,
                imported_at=datetime.now(UTC),
                formula_id=formula.formula_id,
                formula_name=formula.name,
                formula_version=formula.version,
                candidates=tuple(
                    ManualSelectionCandidateInput(
                        local_code=candidate.local_code,
                        exchange=candidate.exchange,
                        name=candidate.name,
                        note=candidate.note,
                    )
                    for candidate in request.candidates
                ),
            )
            self._store.write(result.raw_content, result.imported)
            return self._view(self._store.load_import(result.imported.batch.batch_id))
        except ManualSelectionError as exc:
            raise SelectionApiError("INVALID_MANUAL_SELECTION", str(exc), 422) from exc
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        except ValueError as exc:
            raise SelectionApiError("INVALID_MANUAL_SELECTION", str(exc), 422) from exc
