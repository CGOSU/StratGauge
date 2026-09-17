"""Strategy-signal dismissal command methods."""

from __future__ import annotations

from datetime import UTC, date, datetime

from stratgauge.api.selections._base import SelectionServiceBase, _content_id
from stratgauge.api.selections.requests import SelectionDismissalRequest
from stratgauge.api.selections.views import SelectionDismissalView
from stratgauge.application.selection_management import SelectionApiError
from stratgauge.data_pipeline.selection_store import SelectionStoreError
from stratgauge.domain.selections import (
    DEFAULT_SELECTION_CANDIDATE_DISMISSAL_REASON,
    SelectionAnnotation,
    SelectionCandidateDismissal,
)


class DismissalsMixin(SelectionServiceBase):
    """Append-only dismissal commands for unreviewed strategy signals."""

    def dismiss_unreviewed_candidate(
        self,
        batch_id: str,
        request: SelectionDismissalRequest,
    ) -> SelectionDismissalView:
        try:
            imported = self._store.load_import(batch_id)
            if not self._store.is_entity_active("batch", batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
            candidate = next(
                (
                    candidate
                    for candidate in imported.candidates
                    if candidate.candidate_id == request.candidate_id
                ),
                None,
            )
            if candidate is None:
                raise SelectionApiError(
                    "SELECTION_CANDIDATE_NOT_FOUND",
                    "候选股票不属于该选股批次。",
                    404,
                )

            existing = self._store.latest_dismissal(candidate.candidate_id)
            if existing is not None:
                return SelectionDismissalView(dismissal=existing, created=False)

            dismissed_at = datetime.now(UTC)
            if self._has_visible_shared_annotation(
                selection_date=imported.batch.selection_date,
                symbol=candidate.symbol,
                as_of=dismissed_at,
            ):
                raise SelectionApiError(
                    "SELECTION_CANDIDATE_ALREADY_REVIEWED",
                    "同一选股日的该股票已有人工判断，不能删除策略信号。",
                    422,
                )

            dismissal = SelectionCandidateDismissal(
                dismissal_id=_content_id("dismissal", candidate.candidate_id),
                candidate_id=candidate.candidate_id,
                dismissed_at=dismissed_at,
                recorded_at=dismissed_at,
                reason=DEFAULT_SELECTION_CANDIDATE_DISMISSAL_REASON,
            )
            stored = self._store.write_dismissal(batch_id, dismissal)
            return SelectionDismissalView(
                dismissal=stored,
                created=stored.dismissal_id == dismissal.dismissal_id
                and stored.recorded_at == dismissal.recorded_at,
            )
        except SelectionApiError:
            raise
        except SelectionStoreError as exc:
            message = str(exc)
            if "does not exist" in message or "does not belong" in message:
                raise SelectionApiError(
                    "SELECTION_CANDIDATE_NOT_FOUND",
                    "候选股票不属于该选股批次。",
                    404,
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", message, 500) from exc
        except ValueError as exc:
            raise SelectionApiError("SELECTION_DISMISSAL_INVALID", str(exc), 422) from exc

    def _has_visible_shared_annotation(
        self,
        *,
        selection_date: date,
        symbol: str,
        as_of: datetime,
    ) -> bool:
        candidate_ids: list[str] = []
        for manifest in self._store.list_manifests():
            imported = self._store.load_import(manifest.batch_id)
            if (
                imported.batch.selection_date != selection_date
                or imported.batch.exported_at > as_of
            ):
                continue
            candidate_ids.extend(
                candidate.candidate_id
                for candidate in imported.candidates
                if candidate.symbol == symbol
            )
        if not candidate_ids:
            return False
        annotations_by_candidate: dict[str, tuple[SelectionAnnotation, ...]] = (
            self._store.list_annotations_for_candidates(candidate_ids)
        )
        return any(
            annotation.recorded_at <= as_of and annotation.reviewed_at <= as_of
            for annotations in annotations_by_candidate.values()
            for annotation in annotations
        )
