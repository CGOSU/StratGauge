"""Formula and reusable tag catalog service methods."""

from __future__ import annotations

from collections.abc import Iterator

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
from stratgauge.domain.selections import (
    SelectionFormula,
    SelectionLifecycleStatus,
)
from stratgauge.domain.selections import (
    SelectionReusableTagView as DomainSelectionReusableTagView,
)


class CatalogMixin(SelectionServiceBase):
    """Catalog and data-management delegation methods."""

    def list_formulas(self) -> tuple[SelectionFormula, ...]:
        return self._management.list_formulas()

    def create_formula(self, request: SelectionFormulaCreateRequest) -> SelectionFormulaView:
        formula = self._management.create_formula(
            name=request.name,
            version=request.version,
            description=request.description,
        )
        return SelectionFormulaView(formula=formula)

    def list_reusable_tags(self) -> tuple[DomainSelectionReusableTagView, ...]:
        return self._management.list_reusable_tags()

    def list_managed_reusable_tags(self) -> tuple[DomainSelectionReusableTagView, ...]:
        return self._management.list_reusable_tags(include_archived=True)

    def create_reusable_tag(
        self,
        request: SelectionReusableTagCreateRequest,
    ) -> DomainSelectionReusableTagView:
        return self._management.create_reusable_tag(
            category=request.category,
            label=request.label,
        )

    def rename_reusable_tag(
        self,
        tag_id: str,
        request: SelectionReusableTagRenameRequest,
    ) -> DomainSelectionReusableTagView:
        return self._management.rename_reusable_tag(tag_id=tag_id, label=request.label)

    def set_reusable_tag_status(
        self,
        tag_id: str,
        status: SelectionLifecycleStatus,
        request: SelectionLifecycleChangeRequest,
    ) -> DomainSelectionReusableTagView:
        return self._management.set_reusable_tag_status(
            tag_id=tag_id,
            status=status,
            reason=request.reason,
        )

    def list_managed_formulas(self) -> tuple[SelectionManagedFormulaView, ...]:
        return self._management.list_managed_formulas()

    def list_managed_batches(self) -> tuple[SelectionManagedBatchView, ...]:
        return self._management.list_managed_batches()

    def list_managed_batches_page(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> SelectionManagedBatchPage:
        return self._management.list_managed_batches_page(page=page, page_size=page_size)

    def get_managed_batch(self, batch_id: str) -> SelectionManagedBatchDetailView:
        return self._management.get_managed_batch(batch_id)

    def set_formula_status(
        self,
        formula_id: str,
        status: SelectionLifecycleStatus,
        request: SelectionLifecycleChangeRequest,
    ) -> SelectionManagedFormulaView:
        return self._management.set_formula_status(
            formula_id=formula_id,
            status=status,
            reason=request.reason,
        )

    def set_batch_status(
        self,
        batch_id: str,
        status: SelectionLifecycleStatus,
        request: SelectionLifecycleChangeRequest,
    ) -> SelectionManagedBatchDetailView:
        return self._management.set_batch_status(
            batch_id=batch_id,
            status=status,
            reason=request.reason,
        )

    def load_raw_batch(self, batch_id: str) -> tuple[bytes, str]:
        return self._management.load_raw_batch(batch_id)

    def export_dataset(self, batch_id: str) -> bytes:
        return self._management.export_dataset(batch_id)

    def export_annotation_dataset(self, batch_id: str) -> bytes:
        return self._management.export_annotation_dataset(batch_id)

    def stream_dataset(self, batch_id: str) -> Iterator[bytes]:
        return self._management.stream_dataset(batch_id)

    def stream_annotation_dataset(self, batch_id: str) -> Iterator[bytes]:
        return self._management.stream_annotation_dataset(batch_id)
