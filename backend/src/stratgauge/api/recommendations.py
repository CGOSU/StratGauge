"""Read-only recommendation workbench assembled from existing evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from stratgauge.backtest.recommendations import (
    RecommendationWorkbench,
    build_recommendation_workbench,
)
from stratgauge.backtest.selection_outcomes import latest_selection_outcomes
from stratgauge.backtest.selection_stats import SelectionOutcomeStats, summarize_selection_outcomes
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore, SnapshotStoreError
from stratgauge.domain.selections import ManualSelectionLabel
from stratgauge.ml.shadow import ShadowScore, ShadowScoreError, ShadowScoreStore


class RecommendationApiError(Exception):
    """Expected recommendation workbench failure."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class RecommendationWorkbenchService:
    """Assemble evidence without ranking, ordering, or creating an order."""

    def __init__(
        self,
        selection_store: SelectionImportStore,
        *,
        score_store: ShadowScoreStore,
        market_store: SnapshotStore,
    ) -> None:
        self._selection_store = selection_store
        self._score_store = score_store
        self._market_store = market_store

    def get(self, batch_id: str) -> RecommendationWorkbench:
        try:
            imported = self._selection_store.load_import(batch_id)
            candidates = imported.candidates
            model_scores = {
                candidate.candidate_id: self._latest_score(candidate.candidate_id)
                for candidate in candidates
            }
            quality_states = {
                candidate.candidate_id: self._quality_state(model_scores[candidate.candidate_id])
                for candidate in candidates
            }
            historical_stats = {
                candidate.candidate_id: self._historical_stats(candidate.candidate_id)
                for candidate in candidates
            }
            labels = {
                candidate.candidate_id: self._latest_label(candidate.candidate_id)
                for candidate in candidates
            }
            return build_recommendation_workbench(
                batch=imported.batch,
                candidates=candidates,
                quality_states=quality_states,
                historical_stats=historical_stats,
                model_scores=model_scores,
                labels=labels,
                generated_at=datetime.now(UTC),
            )
        except (SelectionStoreError, ShadowScoreError, SnapshotStoreError) as exc:
            if "does not exist" in str(exc):
                raise RecommendationApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise RecommendationApiError("RECOMMENDATION_READ_FAILED", str(exc), 409) from exc
        except ValueError as exc:
            raise RecommendationApiError("RECOMMENDATION_BUILD_FAILED", str(exc), 409) from exc

    def _quality_state(self, score: ShadowScore | None) -> str:
        if score is None:
            return "not_assessed"
        try:
            manifests = tuple(
                self._market_store.load_manifest(item) for item in score.source_snapshot_ids
            )
        except SnapshotStoreError:
            return "quality_failed"
        return (
            "approved"
            if manifests and all(item.status is SnapshotStatus.APPROVED for item in manifests)
            else "quality_failed"
        )

    def _historical_stats(self, candidate_id: str) -> SelectionOutcomeStats | None:
        outcomes = latest_selection_outcomes(self._selection_store.list_outcomes(candidate_id))
        return summarize_selection_outcomes(outcomes) if outcomes else None

    def _latest_score(self, candidate_id: str) -> ShadowScore | None:
        scores = self._score_store.list_scores(candidate_id=candidate_id)
        return scores[-1] if scores else None

    def _latest_label(self, candidate_id: str) -> ManualSelectionLabel | None:
        labels = self._selection_store.list_labels(candidate_id)
        return labels[-1] if labels else None
