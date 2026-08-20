"""HTTP routes for main-board market synchronization and its durable tasks."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Query

from stratgauge.api.market_sync.models import (
    MarketSyncApiError,
    MarketSyncRequest,
    MarketSyncResponse,
    MarketSyncTaskCreateRequest,
    MarketSyncTaskPage,
    MarketSyncTaskRunRequest,
    MarketSyncTaskRunResponse,
    MarketSyncTaskView,
    paginate_market_sync_tasks,
    to_market_sync_task_api_error,
    to_market_sync_task_view,
)
from stratgauge.api.market_sync.sync import sync_main_board_close, to_market_sync_api_error
from stratgauge.application.job_runner import JobRecord, JobStatus, LocalJobError, LocalJobStore
from stratgauge.application.market_sync_jobs import (
    MAIN_BOARD_MARKET_SYNC_JOB_KIND,
    MarketSyncAdapterFactory,
    enqueue_main_board_market_sync,
    run_main_board_market_sync_jobs,
)
from stratgauge.data_sources.base import MarketDataAdapter
from stratgauge.data_sources.tushare import TushareAdapter


def create_market_sync_router(
    *,
    market_root: str | Path,
    universe_root: str | Path,
    job_store: LocalJobStore,
    adapter_factory: Callable[[str], MarketDataAdapter],
) -> APIRouter:
    """FastAPI router for main-board close sync and durable sync tasks."""
    router = APIRouter(tags=["market-sync"])

    @router.post("/v1/market-sync/main-board", response_model=MarketSyncResponse)
    def sync_main_board_market_close(request: MarketSyncRequest) -> MarketSyncResponse:
        try:
            return sync_main_board_close(
                request,
                market_root=market_root,
                universe_root=universe_root,
                adapter=cast(TushareAdapter, adapter_factory("tushare")),
            )
        except MarketSyncApiError:
            raise
        except Exception as exc:
            raise to_market_sync_api_error(exc) from exc

    def market_sync_adapter_factory(_: JobRecord) -> TushareAdapter:
        return cast(TushareAdapter, adapter_factory("tushare"))

    @router.post(
        "/v1/market-sync/tasks",
        response_model=MarketSyncTaskView,
        status_code=202,
    )
    def enqueue_market_sync_task(request: MarketSyncTaskCreateRequest) -> MarketSyncTaskView:
        try:
            queued = enqueue_main_board_market_sync(
                job_store,
                MarketSyncRequest.model_validate(
                    request.model_dump(exclude={"max_retries", "timeout_seconds"})
                ),
                market_root=market_root,
                universe_root=universe_root,
                max_retries=request.max_retries,
                timeout_seconds=request.timeout_seconds,
            )
            return to_market_sync_task_view(queued.job)
        except MarketSyncApiError:
            raise
        except Exception as exc:
            raise to_market_sync_task_api_error(exc) from exc

    @router.post(
        "/v1/market-sync/tasks/run",
        response_model=MarketSyncTaskRunResponse,
    )
    def run_market_sync_tasks(
        request: MarketSyncTaskRunRequest | None = None,
    ) -> MarketSyncTaskRunResponse:
        try:
            jobs = run_main_board_market_sync_jobs(
                job_store,
                limit=request.limit if request else 1,
                adapter_factory=cast(MarketSyncAdapterFactory, market_sync_adapter_factory),
            )
            return MarketSyncTaskRunResponse(
                jobs=tuple(to_market_sync_task_view(job) for job in jobs)
            )
        except MarketSyncApiError:
            raise
        except Exception as exc:
            raise to_market_sync_task_api_error(exc) from exc

    @router.get(
        "/v1/market-sync/tasks",
        response_model=MarketSyncTaskPage,
    )
    def list_market_sync_tasks(
        status: JobStatus | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> MarketSyncTaskPage:
        jobs = job_store.list(
            status=status,
            kind=MAIN_BOARD_MARKET_SYNC_JOB_KIND,
        )
        return paginate_market_sync_tasks(jobs, page=page, page_size=page_size)

    @router.get(
        "/v1/market-sync/tasks/{job_id}",
        response_model=MarketSyncTaskView,
    )
    def get_market_sync_task(job_id: str) -> MarketSyncTaskView:
        try:
            job = job_store.load(job_id)
            if job.kind != MAIN_BOARD_MARKET_SYNC_JOB_KIND:
                raise ValueError("job is not a main-board market synchronization task")
            return to_market_sync_task_view(job)
        except MarketSyncApiError:
            raise
        except Exception as exc:
            if isinstance(exc, ValueError):
                raise to_market_sync_task_api_error(LocalJobError("job does not exist")) from exc
            raise to_market_sync_task_api_error(exc) from exc

    @router.post(
        "/v1/market-sync/tasks/{job_id}/cancel",
        response_model=MarketSyncTaskView,
    )
    def cancel_market_sync_task(job_id: str) -> MarketSyncTaskView:
        try:
            existing = job_store.load(job_id)
            if existing.kind != MAIN_BOARD_MARKET_SYNC_JOB_KIND:
                raise LocalJobError("job does not exist")
            cancelled = job_store.cancel(job_id, now=datetime.now(UTC))
            return to_market_sync_task_view(cancelled)
        except MarketSyncApiError:
            raise
        except Exception as exc:
            raise to_market_sync_task_api_error(exc) from exc

    return router
