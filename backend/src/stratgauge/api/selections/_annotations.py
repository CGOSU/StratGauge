"""Manual label, annotation and exclusion service methods."""

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
from stratgauge.data_pipeline.selection_store import SelectionStoreError
from stratgauge.domain.selections import (
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionCandidateExclusion,
    SelectionReusableTagSnapshot,
)


class AnnotationsMixin(SelectionServiceBase):
    """Label, annotation and exclusion methods."""

    def label_candidate(self, batch_id: str, request: SelectionLabelRequest) -> SelectionBatchView:
        label = ManualSelectionLabel(
            candidate_id=request.candidate_id,
            decision=request.decision,
            labeled_at=request.labeled_at,
            note=request.note,
        )
        try:
            if not self._store.is_entity_active("batch", batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
            self._store.write_label(batch_id, label)
            return self.get_batch(batch_id)
        except SelectionStoreError as exc:
            message = str(exc)
            if "label timestamp" in message:
                raise SelectionApiError(
                    "SELECTION_LABEL_NOT_ALLOWED",
                    "旧版人工标签必须在导出后、下一自然日开始前完成。",
                    422,
                ) from exc
            if "does not exist" in message or "does not belong" in message:
                raise SelectionApiError(
                    "SELECTION_CANDIDATE_NOT_FOUND", "候选股票不属于该选股批次。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", message, 500) from exc

    def annotate_candidate(
        self,
        batch_id: str,
        request: SelectionAnnotationRequest,
    ) -> SelectionBatchView:
        try:
            imported = self._store.load_import(batch_id)
            if not self._store.is_entity_active("batch", batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
            if request.reviewed_at < imported.batch.exported_at:
                raise SelectionApiError(
                    "SELECTION_ANNOTATION_NOT_ALLOWED",
                    "人工评审时间不能早于导出时间。",
                    422,
                )
            recorded_at = datetime.now(UTC)
            reusable_tag_snapshots = self._resolve_reusable_tag_snapshots(request.reusable_tag_ids)
            annotation = SelectionAnnotation(
                annotation_id=_content_id(
                    "annotation",
                    request.candidate_id,
                    request.reviewed_at.isoformat(),
                    str(request.overall_score),
                    request.outlook.value,
                    request.decision.value,
                    ",".join(item.value for item in request.position_tags),
                    ",".join(item.value for item in request.pattern_tags),
                    ",".join(item.value for item in request.price_volume_tags),
                    ",".join(item.value for item in request.sector_tags),
                    ",".join(item.tag_id for item in reusable_tag_snapshots),
                    request.note or "",
                    recorded_at.isoformat(),
                ),
                candidate_id=request.candidate_id,
                reviewed_at=request.reviewed_at,
                recorded_at=recorded_at,
                overall_score=request.overall_score,
                outlook=request.outlook,
                decision=request.decision,
                position_tags=request.position_tags,
                pattern_tags=request.pattern_tags,
                price_volume_tags=request.price_volume_tags,
                sector_tags=request.sector_tags,
                reusable_tag_snapshots=reusable_tag_snapshots,
                note=request.note,
            )
            self._store.write_annotation(batch_id, annotation)
            return self.get_batch(batch_id)
        except SelectionApiError:
            raise
        except SelectionStoreError as exc:
            message = str(exc)
            if "does not exist" in message or "does not belong" in message:
                raise SelectionApiError(
                    "SELECTION_CANDIDATE_NOT_FOUND", "候选股票不属于该选股批次。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", message, 500) from exc
        except ValueError as exc:
            raise SelectionApiError("SELECTION_ANNOTATION_INVALID", str(exc), 422) from exc

    def _resolve_reusable_tag_snapshots(
        self,
        tag_ids: tuple[str, ...],
    ) -> tuple[SelectionReusableTagSnapshot, ...]:
        if not tag_ids:
            return ()
        try:
            tags = {
                tag.tag_id: tag for tag in self._store.list_reusable_tags(include_archived=True)
            }
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        snapshots: list[SelectionReusableTagSnapshot] = []
        for tag_id in tag_ids:
            tag = tags.get(tag_id)
            if tag is None:
                raise SelectionApiError("ANNOTATION_TAG_NOT_FOUND", "评审标签不存在。", 404)
            if not tag.active:
                raise SelectionApiError("ANNOTATION_TAG_ARCHIVED", "评审标签已归档。", 422)
            snapshots.append(
                SelectionReusableTagSnapshot(
                    tag_id=tag.tag_id,
                    category=tag.category,
                    label=tag.label,
                )
            )
        return tuple(snapshots)

    def exclude_candidate(
        self,
        batch_id: str,
        request: SelectionExclusionRequest,
    ) -> SelectionBatchView:
        try:
            imported = self._store.load_import(batch_id)
            if not self._store.is_entity_active("batch", batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
            if request.excluded_at < imported.batch.exported_at:
                raise SelectionApiError(
                    "SELECTION_EXCLUSION_NOT_ALLOWED",
                    "排除时间不能早于导出时间。",
                    422,
                )
            recorded_at = datetime.now(UTC)
            exclusion = SelectionCandidateExclusion(
                exclusion_id=_content_id(
                    "exclusion",
                    request.candidate_id,
                    request.excluded_at.isoformat(),
                    request.reason,
                    recorded_at.isoformat(),
                ),
                candidate_id=request.candidate_id,
                excluded_at=request.excluded_at,
                recorded_at=recorded_at,
                reason=request.reason,
            )
            self._store.write_exclusion(batch_id, exclusion)
            return self.get_batch(batch_id)
        except SelectionApiError:
            raise
        except SelectionStoreError as exc:
            message = str(exc)
            if "does not exist" in message or "does not belong" in message:
                raise SelectionApiError(
                    "SELECTION_CANDIDATE_NOT_FOUND", "候选股票不属于该选股批次。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", message, 500) from exc
        except ValueError as exc:
            raise SelectionApiError("SELECTION_EXCLUSION_INVALID", str(exc), 422) from exc
