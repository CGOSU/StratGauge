"""Formula and reusable tag management methods."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stratgauge.data_pipeline.selection_store import SelectionStoreError
from stratgauge.domain.selections import (
    SelectionFormula,
    SelectionLifecycleStatus,
    SelectionReusableTagCategory,
)
from stratgauge.domain.selections import (
    SelectionReusableTagView as DomainSelectionReusableTagView,
)

if TYPE_CHECKING:
    pass


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
    page_slice,
    validate_page,
)


class CatalogMixin(SelectionManagementBase):
    """Formula and reusable tag operations."""

    def list_formulas(self) -> tuple[SelectionFormula, ...]:
        try:
            return self._store.list_formulas()
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def create_formula(
        self,
        *,
        name: str,
        version: str | None,
        description: str | None,
    ) -> SelectionFormula:
        try:
            return self._store.create_formula(
                name=name,
                version=version,
                description=description,
            )
        except (SelectionStoreError, ValueError) as exc:
            raise SelectionApiError("SELECTION_FORMULA_INVALID", str(exc), 422) from exc

    def list_reusable_tags(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[DomainSelectionReusableTagView, ...]:
        try:
            return self._store.list_reusable_tags(include_archived=include_archived)
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def create_reusable_tag(
        self,
        *,
        category: SelectionReusableTagCategory,
        label: str,
    ) -> DomainSelectionReusableTagView:
        try:
            tag = self._store.create_reusable_tag(
                category=category,
                label=label,
                created_at=datetime.now(UTC),
            )
            return self._get_reusable_tag(tag.tag_id)
        except (SelectionStoreError, ValueError) as exc:
            raise SelectionApiError("ANNOTATION_TAG_INVALID", str(exc), 422) from exc

    def rename_reusable_tag(
        self,
        *,
        tag_id: str,
        label: str,
    ) -> DomainSelectionReusableTagView:
        try:
            self._store.set_reusable_tag_label(
                tag_id,
                label=label,
                occurred_at=datetime.now(UTC),
            )
            return self._get_reusable_tag(tag_id)
        except SelectionStoreError as exc:
            raise self._tag_error(exc, tag_id) from exc
        except ValueError as exc:
            raise SelectionApiError("ANNOTATION_TAG_INVALID", str(exc), 422) from exc

    def set_reusable_tag_status(
        self,
        *,
        tag_id: str,
        status: SelectionLifecycleStatus,
        reason: str | None,
    ) -> DomainSelectionReusableTagView:
        try:
            self._store.set_reusable_tag_status(
                tag_id,
                status,
                occurred_at=datetime.now(UTC),
                reason=reason,
            )
            return self._get_reusable_tag(tag_id)
        except SelectionStoreError as exc:
            raise self._tag_error(exc, tag_id) from exc
        except ValueError as exc:
            raise SelectionApiError("ANNOTATION_TAG_INVALID", str(exc), 422) from exc

    def _get_reusable_tag(self, tag_id: str) -> DomainSelectionReusableTagView:
        for tag in self.list_reusable_tags(include_archived=True):
            if tag.tag_id == tag_id:
                return tag
        raise SelectionApiError("ANNOTATION_TAG_NOT_FOUND", "评审标签不存在。", 404)

    @staticmethod
    def _tag_error(exc: SelectionStoreError, tag_id: str) -> SelectionApiError:
        message = str(exc)
        if "does not exist" in message or "invalid reusable tag id" in message:
            return SelectionApiError("ANNOTATION_TAG_NOT_FOUND", "评审标签不存在。", 404)
        return SelectionApiError("ANNOTATION_TAG_INVALID", message, 422)
