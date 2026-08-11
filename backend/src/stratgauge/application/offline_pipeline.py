"""Run the approved-snapshot selection maturity pipeline fully offline."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from stratgauge.application.job_runner import (
    JobRecord,
    JobStatus,
    LocalJobStore,
    job_id,
)
from stratgauge.backtest.maturity_queue import (
    MaturityQueueResult,
    queue_snapshot_maturity_tasks,
)
from stratgauge.backtest.selection_outcomes import SelectionTrackingPolicy
from stratgauge.backtest.selection_tasks import (
    SelectionMaturityTaskError,
    run_maturity_task,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotStore, SnapshotVerificationReport
from stratgauge.data_sources.base import DailyBarRequest
from stratgauge.domain.selections import SelectionMaturityTask, SelectionMaturityTaskStatus


class OfflinePipelineError(RuntimeError):
    """The local-only pipeline cannot safely complete."""


class _RemoteAccessBlockedAdapter:
    """Adapter sentinel that makes an accidental remote call fail loudly."""

    provider = "offline"
    adapter_version = "offline-v1"

    def fetch_daily_bars(self, request: DailyBarRequest) -> Any:
        raise OfflinePipelineError(
            f"offline maturity task attempted a remote fetch for {request.security.symbol}"
        )


class OfflineMaturityPipelineResult:
    """Durable evidence emitted by one queue, run, and verify cycle."""

    def __init__(
        self,
        *,
        queue: MaturityQueueResult,
        jobs: tuple[JobRecord, ...],
        tasks: tuple[SelectionMaturityTask, ...],
        verification: SnapshotVerificationReport,
    ) -> None:
        self.queue = queue
        self.jobs = jobs
        self.tasks = tasks
        self.verification = verification


def run_offline_maturity_pipeline(
    selection_root: str | Path,
    market_root: str | Path,
    *,
    provider: Literal["tushare", "akshare", "baostock", "waizao"],
    policy: SelectionTrackingPolicy,
    trading_sessions: Sequence[date],
    now: datetime,
    limit: int | None = None,
) -> OfflineMaturityPipelineResult:
    """Queue and run maturity tasks using approved local snapshots only.

    The sentinel adapter is intentionally incapable of fetching data. Snapshot-backed
    tasks must therefore obtain all bars from ``market_root``; any remote access is a
    correctness failure rather than a recoverable provider error.
    """

    if limit is not None and limit <= 0:
        raise OfflinePipelineError("limit must be positive when provided")
    selection_store = SelectionImportStore(selection_root)
    market_store = SnapshotStore(market_root)
    queue = queue_snapshot_maturity_tasks(
        selection_store,
        market_store,
        provider=provider,
        policy=policy,
        trading_sessions=trading_sessions,
        now=now,
    )
    pending = tuple(
        task
        for task in selection_store.list_maturity_tasks(status=SelectionMaturityTaskStatus.QUEUED)
        if task.market_data_mode == "approved_snapshots"
    )
    if limit is not None:
        pending = pending[:limit]

    job_store = LocalJobStore(selection_root)
    adapter = _RemoteAccessBlockedAdapter()
    finished_jobs: list[JobRecord] = []
    finished_tasks: list[SelectionMaturityTask] = []
    for task in pending:
        payload = {
            "task_id": task.task_id,
            "attempt": task.attempt_count + 1,
            "market_root": str(Path(market_root).resolve()),
            "mode": "approved_snapshots",
        }
        record = job_store.create(
            JobRecord(
                job_id=job_id(kind="selection_maturity_offline", payload=payload),
                kind="selection_maturity_offline",
                payload=payload,
                max_retries=task.max_retries,
                timeout_seconds=task.timeout_seconds,
                created_at=now,
                updated_at=now,
            )
        )
        running = job_store.start(record.job_id, now=now)
        if running.status in {
            JobStatus.COMPLETED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            finished_jobs.append(running)
            finished_tasks.append(task)
            continue
        try:
            result = run_maturity_task(
                task,
                imported=selection_store.load_import(task.batch_id),
                adapter=adapter,
                selection_store=selection_store,
                market_store=market_store,
                now=now,
            )
            status = _job_status(result.status)
            finished = job_store.finish(
                record.job_id,
                status=status,
                now=now,
                result={"task": result.model_dump(mode="json")},
                error=result.failure_reason,
            )
        except (OSError, SelectionStoreError, SelectionMaturityTaskError, ValueError) as exc:
            finished = job_store.finish(
                record.job_id,
                status=JobStatus.FAILED,
                now=now,
                error=str(exc),
            )
            raise OfflinePipelineError(str(exc)) from exc
        finished_jobs.append(finished)
        finished_tasks.append(result)

    return OfflineMaturityPipelineResult(
        queue=queue,
        jobs=tuple(finished_jobs),
        tasks=tuple(finished_tasks),
        verification=market_store.verify(),
    )


def _job_status(status: SelectionMaturityTaskStatus) -> JobStatus:
    mapping = {
        SelectionMaturityTaskStatus.COMPLETED: JobStatus.COMPLETED,
        SelectionMaturityTaskStatus.PARTIAL: JobStatus.PARTIAL,
        SelectionMaturityTaskStatus.FAILED: JobStatus.FAILED,
        SelectionMaturityTaskStatus.QUEUED: JobStatus.RETRYING,
    }
    try:
        return mapping[status]
    except KeyError as exc:
        raise OfflinePipelineError(f"offline task returned unsupported status: {status}") from exc
