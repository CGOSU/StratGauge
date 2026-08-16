"""Maturity task service methods."""

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
from stratgauge.backtest.selection_job import run_selection_outcome_job
from stratgauge.backtest.selection_outcomes import (
    SelectionPerformancePolicy,
    SelectionTrackingPolicy,
)
from stratgauge.backtest.selection_tasks import (
    SelectionMaturityTaskError,
    build_maturity_task,
    create_maturity_task,
    run_maturity_task,
)
from stratgauge.data_pipeline.selection_store import SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_sources.base import MarketDataAdapter
from stratgauge.domain.selections import (
    SelectionMaturityTask,
)


class MaturityMixin(SelectionServiceBase):
    """Maturity task lifecycle methods."""

    def create_maturity_task(
        self,
        batch_id: str,
        request: SelectionMatureRequest,
    ) -> SelectionMaturityTask:
        try:
            imported = self._store.load_import(batch_id)
            if not self._store.is_entity_active("batch", batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
            policy = SelectionTrackingPolicy(
                horizon_sessions=request.horizon_sessions,
                position_notional=request.position_notional,
            )
            task = build_maturity_task(
                imported,
                provider=request.provider,
                evaluated_at=request.evaluated_at,
                policy=policy,
                performance_policy=SelectionPerformancePolicy(
                    target_return=request.target_return,
                ),
                max_retries=request.max_retries,
                timeout_seconds=request.timeout_seconds,
                created_at=datetime.now(UTC),
            )
            return create_maturity_task(self._store, task, now=datetime.now(UTC))
        except SelectionApiError:
            raise
        except SelectionMaturityTaskError as exc:
            raise SelectionApiError("SELECTION_MATURITY_TASK_INVALID", str(exc), 422) from exc
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        except ValueError as exc:
            raise SelectionApiError("SELECTION_MATURITY_TASK_INVALID", str(exc), 422) from exc

    def list_maturity_tasks(self, batch_id: str) -> tuple[SelectionMaturityTask, ...]:
        try:
            self._store.load_import(batch_id)
            return self._store.list_maturity_tasks(batch_id=batch_id)
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def get_maturity_task(self, task_id: str) -> SelectionMaturityTask:
        try:
            return self._store.load_maturity_task(task_id)
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_MATURITY_TASK_NOT_FOUND", "成熟任务不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc

    def run_maturity_task(
        self,
        task_id: str,
        *,
        adapter: MarketDataAdapter,
        market_store: SnapshotStore,
    ) -> SelectionMaturityTask:
        try:
            task = self._store.load_maturity_task(task_id)
            imported = self._store.load_import(task.batch_id)
            if not self._store.is_entity_active("batch", task.batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
            return run_maturity_task(
                task,
                imported=imported,
                adapter=adapter,
                selection_store=self._store,
                market_store=market_store,
                now=datetime.now(UTC),
            )
        except SelectionApiError:
            raise
        except SelectionMaturityTaskError as exc:
            raise SelectionApiError("SELECTION_MATURITY_TASK_FAILED", str(exc), 409) from exc
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_MATURITY_TASK_NOT_FOUND", "成熟任务不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        except (OSError, ValueError) as exc:
            raise SelectionApiError("SELECTION_MATURITY_TASK_FAILED", str(exc), 422) from exc

    def mature_batch(
        self,
        batch_id: str,
        request: SelectionMatureRequest,
        *,
        adapter: MarketDataAdapter,
        market_store: SnapshotStore,
    ) -> SelectionMatureView:
        try:
            imported = self._store.load_import(batch_id)
            if not self._store.is_entity_active("batch", batch_id):
                raise SelectionApiError("SELECTION_BATCH_ARCHIVED", "选股批次已归档。", 422)
            result = run_selection_outcome_job(
                imported,
                adapter=adapter,
                selection_store=self._store,
                market_store=market_store,
                evaluated_at=request.evaluated_at,
                policy=SelectionTrackingPolicy(
                    horizon_sessions=request.horizon_sessions,
                    position_notional=request.position_notional,
                ),
                performance_policy=SelectionPerformancePolicy(
                    target_return=request.target_return,
                ),
            )
            return SelectionMatureView(
                batch_id=batch_id,
                provider=request.provider,
                outcomes_written=len(result.outcomes),
                performances_written=len(result.performances),
                rejected_candidates=result.rejected_candidates,
                market_snapshot_ids=result.market_snapshot_ids,
            )
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise SelectionApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise SelectionApiError("SELECTION_STORAGE_ERROR", str(exc), 500) from exc
        except (OSError, ValueError) as exc:
            raise SelectionApiError("SELECTION_MATURE_FAILED", str(exc), 422) from exc
