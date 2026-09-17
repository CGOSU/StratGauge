"""Shared service base, view builders and module helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib

from stratgauge.api.selections.requests import (  # noqa: F401
    ManualSelectionCandidateRequest,
    ManualSelectionRequest,
    SelectionAnnotationRequest,
    SelectionDismissalRequest,
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
    SelectionDismissalView,
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
    SelectionManagementService,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.domain.selections import (
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionAnnotationDecision,
    SelectionCandidate,
    SelectionCandidateDismissal,
    SelectionCandidateExclusion,
    SelectionCandidateReviewState,
    SelectionFormula,
    SelectionOutcome,
    SelectionPerformance,
    TdxSelectionImport,
)


class SelectionServiceBase:
    """Attributes and helpers shared by all selection service mixins."""

    _store: SelectionImportStore

    # Cross-mixin surface: implementations live in domain mixins and are
    # combined by SelectionService; declared here for type checking.
    def get_batch(self, batch_id: str) -> SelectionBatchView:
        raise NotImplementedError

    _management: SelectionManagementService

    def _formula_metadata(
        self,
        request: SelectionImportRequest,
    ) -> tuple[str, str | None, str | None]:
        if request.formula_id is not None:
            formula = self._load_active_formula(request.formula_id)
            return formula.name, formula.version, formula.formula_id
        if request.formula_name is None:
            raise SelectionApiError("SELECTION_FORMULA_REQUIRED", "必须选择一个选股公式。", 422)
        return request.formula_name, request.formula_version, None

    def _load_active_formula(self, formula_id: str) -> SelectionFormula:
        try:
            formula = self._store.load_formula(formula_id)
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_FORMULA_NOT_FOUND", "选股公式不存在。", 404) from exc
        if not self._store.is_entity_active("formula", formula.formula_id):
            raise SelectionApiError("SELECTION_FORMULA_ARCHIVED", "选股公式已停用。", 422)
        return formula

    def _candidate_views(
        self,
        candidates: tuple[SelectionCandidate, ...],
    ) -> tuple[SelectionCandidateView, ...]:
        candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
        labels_by_candidate = self._store.list_labels_for_candidates(candidate_ids)
        annotations_by_candidate = self._store.list_annotations_for_candidates(candidate_ids)
        exclusions_by_candidate = self._store.list_exclusions_for_candidates(candidate_ids)
        dismissals_by_candidate = self._store.list_dismissals_for_candidates(candidate_ids)
        outcomes_by_candidate = self._store.list_outcomes_for_candidates(candidate_ids)
        performances_by_candidate = self._store.list_performances_for_candidates(candidate_ids)
        return tuple(
            self._candidate_view(
                candidate,
                labels=labels_by_candidate[candidate.candidate_id],
                annotations=annotations_by_candidate[candidate.candidate_id],
                exclusions=exclusions_by_candidate[candidate.candidate_id],
                dismissals=dismissals_by_candidate[candidate.candidate_id],
                outcomes=outcomes_by_candidate[candidate.candidate_id],
                performances=performances_by_candidate[candidate.candidate_id],
            )
            for candidate in candidates
        )

    def _candidate_view(
        self,
        candidate: SelectionCandidate,
        *,
        labels: tuple[ManualSelectionLabel, ...],
        annotations: tuple[SelectionAnnotation, ...],
        exclusions: tuple[SelectionCandidateExclusion, ...],
        dismissals: tuple[SelectionCandidateDismissal, ...],
        outcomes: tuple[SelectionOutcome, ...],
        performances: tuple[SelectionPerformance, ...],
    ) -> SelectionCandidateView:
        latest_annotation = annotations[-1] if annotations else None
        latest_exclusion = exclusions[-1] if exclusions else None
        latest_dismissal = dismissals[-1] if dismissals else None
        return SelectionCandidateView(
            candidate=candidate,
            labels=labels,
            annotations=annotations,
            exclusions=exclusions,
            dismissals=dismissals,
            latest_annotation=latest_annotation,
            latest_exclusion=latest_exclusion,
            latest_dismissal=latest_dismissal,
            review_state=_review_state(latest_annotation),
            excluded=latest_exclusion is not None,
            dismissed=latest_dismissal is not None,
            outcomes=outcomes,
            performances=performances,
        )

    def _view(self, imported: TdxSelectionImport) -> SelectionBatchView:
        try:
            candidates = self._candidate_views(imported.candidates)
        except SelectionStoreError as exc:
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        return SelectionBatchView(batch=imported.batch, candidates=candidates)


def _content_id(prefix: str, *parts: str) -> str:
    identity = "\0".join(parts)
    return f"{prefix}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def _review_state(
    annotation: SelectionAnnotation | None,
) -> SelectionCandidateReviewState:
    if annotation is None:
        return SelectionCandidateReviewState.UNREVIEWED
    states = {
        SelectionAnnotationDecision.DISCARD: SelectionCandidateReviewState.DISCARDED,
        SelectionAnnotationDecision.WATCH: SelectionCandidateReviewState.WATCHING,
        SelectionAnnotationDecision.PREPARE_BUY: SelectionCandidateReviewState.PREPARING,
    }
    return states[annotation.decision]


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SelectionApiError(
            "INVALID_SELECTION_FILE", "导出文件不是有效的 Base64 内容。", 422
        ) from exc
