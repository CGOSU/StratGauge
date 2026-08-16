"""Candidate analysis and statistics service methods."""

from __future__ import annotations

from datetime import date
from typing import Literal

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
from stratgauge.backtest.selection_analysis import (
    SelectionAnalysisError,
    SelectionCandidateAnalysis,
    analyze_selection_candidate,
)
from stratgauge.backtest.selection_outcomes import (
    latest_selection_outcomes,
)
from stratgauge.backtest.selection_stats import summarize_selection_outcomes
from stratgauge.data_pipeline.selection_store import SelectionStoreError
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    SelectionOutcome,
)


class AnalysisMixin(SelectionServiceBase):
    """Analysis and summary methods."""

    def analyze_candidate(
        self,
        batch_id: str,
        candidate_id: str,
        *,
        tracking_policy_id: str | None = None,
    ) -> SelectionCandidateAnalysis:
        try:
            return analyze_selection_candidate(
                self._store,
                batch_id=batch_id,
                candidate_id=candidate_id,
                tracking_policy_id=tracking_policy_id,
            )
        except SelectionAnalysisError as exc:
            message = str(exc)
            if "candidate does not belong" in message:
                raise SelectionApiError(
                    "SELECTION_CANDIDATE_NOT_FOUND", "候选股票不属于该选股批次。", 404
                ) from exc
            if "does not exist" in message:
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            if "multiple tracking policies" in message:
                raise SelectionApiError(
                    "TRACKING_POLICY_REQUIRED", "历史样本包含多个结果政策，请明确指定政策。", 422
                ) from exc
            raise SelectionApiError("SELECTION_ANALYSIS_FAILED", message, 500) from exc
        except ValueError as exc:
            raise SelectionApiError("SELECTION_ANALYSIS_INVALID", str(exc), 422) from exc

    def summarize_batch(self, batch_id: str) -> SelectionStatsView:
        try:
            imported = self._store.load_import(batch_id)
            outcomes_by_candidate = self._store.list_outcomes_for_candidates(
                candidate.candidate_id for candidate in imported.candidates
            )
            outcomes: list[SelectionOutcome] = []
            pending = 0
            for candidate in imported.candidates:
                candidate_outcomes = outcomes_by_candidate[candidate.candidate_id]
                if not candidate_outcomes:
                    pending += 1
                outcomes.extend(candidate_outcomes)
            latest = latest_selection_outcomes(outcomes)
            return SelectionStatsView(
                batch_id=batch_id,
                candidate_count=len(imported.candidates),
                pending=pending,
                latest_outcome_count=len(latest),
                summary=summarize_selection_outcomes(latest),
            )
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def summarize_managed_batches(
        self,
        *,
        formula_version: str | None = None,
        source: Literal["tongdaxin", "manual"] | None = None,
        selection_date_from: date | None = None,
        selection_date_to: date | None = None,
        manual_decision: ManualSelectionDecision | None = None,
        tracking_policy_id: str | None = None,
        lifecycle: Literal["all", "active", "archived"] = "all",
    ) -> SelectionManagementStatsView:
        return self._management.summarize_managed_batches(
            formula_version=formula_version,
            source=source,
            selection_date_from=selection_date_from,
            selection_date_to=selection_date_to,
            manual_decision=manual_decision,
            tracking_policy_id=tracking_policy_id,
            lifecycle=lifecycle,
        )
