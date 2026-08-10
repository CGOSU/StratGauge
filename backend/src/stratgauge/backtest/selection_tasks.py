"""Explicit, idempotent maturity-task ledger for imported selections."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Literal

from stratgauge.backtest.selection_job import SelectionOutcomeJobResult, run_selection_outcome_job
from stratgauge.backtest.selection_outcomes import (
    SelectionTrackingPolicy,
    selection_tracking_policy_id,
    validate_selection_tracking_policy,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_sources.base import (
    AdapterError,
    MarketDataAdapter,
    is_retryable_adapter_exception,
)
from stratgauge.domain.selections import (
    SelectionMaturityTask,
    SelectionMaturityTaskEvent,
    SelectionMaturityTaskStatus,
    TdxSelectionImport,
)


class SelectionMaturityTaskError(RuntimeError):
    """A task cannot be created or executed safely."""


def build_maturity_task(
    imported: TdxSelectionImport,
    *,
    provider: Literal["akshare", "baostock", "waizao"],
    evaluated_at: datetime,
    policy: SelectionTrackingPolicy,
    max_retries: int = 2,
    timeout_seconds: float = 900,
    created_at: datetime,
) -> SelectionMaturityTask:
    """Build the immutable task configuration and deterministic identity."""

    _require_aware(evaluated_at, "evaluated_at")
    _require_aware(created_at, "created_at")
    validate_selection_tracking_policy(policy)
    if evaluated_at.date() <= imported.batch.selection_date:
        raise SelectionMaturityTaskError("evaluated_at must follow the selection date")
    if not provider.strip():
        raise SelectionMaturityTaskError("provider must not be empty")
    if policy.position_notional is None:
        raise SelectionMaturityTaskError("position_notional is required")
    if not 0 <= max_retries <= 5:
        raise SelectionMaturityTaskError("max_retries must be between 0 and 5")
    if not 0 < timeout_seconds <= 86400:
        raise SelectionMaturityTaskError("timeout_seconds must be between 1 and 86400")
    task_id = maturity_task_id(
        imported.batch.batch_id,
        provider=provider,
        evaluated_at=evaluated_at,
        tracking_policy_id=selection_tracking_policy_id(policy),
    )
    return SelectionMaturityTask(
        task_id=task_id,
        batch_id=imported.batch.batch_id,
        provider=provider,
        evaluated_at=evaluated_at,
        target_evaluation_date=evaluated_at.date(),
        tracking_policy_id=selection_tracking_policy_id(policy),
        horizon_sessions=policy.horizon_sessions,
        position_notional=policy.position_notional,
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
        created_at=created_at,
        updated_at=created_at,
    )


def maturity_task_id(
    batch_id: str,
    *,
    provider: str,
    evaluated_at: datetime,
    tracking_policy_id: str,
) -> str:
    """Return the content identity required for task creation idempotency."""

    _require_aware(evaluated_at, "evaluated_at")
    identity = json.dumps(
        {
            "batch_id": batch_id,
            "evaluated_at": evaluated_at.astimezone(UTC).isoformat(),
            "provider": provider.strip().lower(),
            "tracking_policy_id": tracking_policy_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"maturity-task-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def create_maturity_task(
    store: SelectionImportStore,
    task: SelectionMaturityTask,
    *,
    now: datetime,
) -> SelectionMaturityTask:
    """Persist a task once and append its initial queue event once."""

    _require_aware(now, "now")
    try:
        existing = store.create_maturity_task(task)
        if not existing.events:
            _append_event(
                store,
                task_id=existing.task_id,
                event_type="queued",
                occurred_at=now,
                attempt_count=0,
                status=SelectionMaturityTaskStatus.QUEUED,
                reason="task_created",
            )
        return store.load_maturity_task(existing.task_id)
    except SelectionStoreError as exc:
        raise SelectionMaturityTaskError(str(exc)) from exc


def run_maturity_task(
    task: SelectionMaturityTask,
    *,
    imported: TdxSelectionImport,
    adapter: MarketDataAdapter,
    selection_store: SelectionImportStore,
    market_store: SnapshotStore,
    now: datetime,
) -> SelectionMaturityTask:
    """Run one queued task synchronously and materialize every state transition."""

    _require_aware(now, "now")
    current = task
    if current.status in {
        SelectionMaturityTaskStatus.COMPLETED,
        SelectionMaturityTaskStatus.PARTIAL,
        SelectionMaturityTaskStatus.FAILED,
    }:
        return current
    if current.status is SelectionMaturityTaskStatus.RUNNING:
        current = _recover_stale_task(current, selection_store, now)
        if current.status is SelectionMaturityTaskStatus.FAILED:
            return current
    if current.status is not SelectionMaturityTaskStatus.QUEUED:
        raise SelectionMaturityTaskError(f"maturity task is not runnable: {current.status.value}")

    attempt = current.attempt_count + 1
    running = current.model_copy(
        update={
            "status": SelectionMaturityTaskStatus.RUNNING,
            "attempt_count": attempt,
            "updated_at": now,
            "failure_reason": None,
            "retryable": False,
        }
    )
    selection_store.save_maturity_task(running)
    _append_event(
        selection_store,
        task_id=running.task_id,
        event_type="running",
        occurred_at=now,
        attempt_count=attempt,
        status=SelectionMaturityTaskStatus.RUNNING,
        reason="attempt_started",
    )

    try:
        result = run_selection_outcome_job(
            imported,
            adapter=adapter,
            selection_store=selection_store,
            market_store=market_store,
            evaluated_at=running.evaluated_at,
            policy=SelectionTrackingPolicy(
                horizon_sessions=running.horizon_sessions,
                position_notional=running.position_notional,
            ),
            maturity_task_id=running.task_id,
        )
    except Exception as exc:
        return _finish_exception(running, selection_store, exc, now)

    return _finish_result(running, selection_store, result, now)


def _finish_result(
    task: SelectionMaturityTask,
    store: SelectionImportStore,
    result: SelectionOutcomeJobResult,
    now: datetime,
) -> SelectionMaturityTask:
    outcomes = store.list_maturity_task_outcomes(task.task_id)
    retryable = bool(result.retryable_rejections)
    has_rejections = bool(result.rejected_candidates)
    retries_left = task.attempt_count <= task.max_retries
    if retryable and retries_left:
        status = SelectionMaturityTaskStatus.QUEUED
        final_retryable = True
    elif not has_rejections:
        status = SelectionMaturityTaskStatus.COMPLETED
        final_retryable = False
    elif outcomes:
        status = SelectionMaturityTaskStatus.PARTIAL
        final_retryable = False
    else:
        status = SelectionMaturityTaskStatus.FAILED
        final_retryable = False
    failure_reason = result.rejected_candidates[0][1] if has_rejections else None
    finished = task.model_copy(
        update={
            "status": status,
            "updated_at": now,
            "outcomes_written": len(outcomes),
            "rejected_candidates": result.rejected_candidates,
            "market_snapshot_ids": result.market_snapshot_ids,
            "failure_reason": failure_reason,
            "retryable": final_retryable,
        }
    )
    store.save_maturity_task(finished)
    _append_event(
        store,
        task_id=task.task_id,
        event_type="attempt_result",
        occurred_at=now,
        attempt_count=task.attempt_count,
        status=status
        if status is not SelectionMaturityTaskStatus.QUEUED
        else SelectionMaturityTaskStatus.PARTIAL,
        outcomes_written=len(outcomes),
        rejected_candidates=result.rejected_candidates,
        market_snapshot_ids=result.market_snapshot_ids,
        failure_reason=failure_reason,
        retryable=retryable,
        reason="attempt_finished",
    )
    if status is SelectionMaturityTaskStatus.QUEUED:
        _append_event(
            store,
            task_id=task.task_id,
            event_type="requeue",
            occurred_at=now,
            attempt_count=task.attempt_count,
            status=status,
            outcomes_written=len(outcomes),
            rejected_candidates=result.rejected_candidates,
            market_snapshot_ids=result.market_snapshot_ids,
            failure_reason=failure_reason,
            retryable=True,
            reason="retry_available",
        )
    return store.load_maturity_task(task.task_id)


def _finish_exception(
    task: SelectionMaturityTask,
    store: SelectionImportStore,
    error: Exception,
    now: datetime,
) -> SelectionMaturityTask:
    retryable = isinstance(error, AdapterError) and is_retryable_adapter_exception(error)
    retryable = retryable or isinstance(error, (TimeoutError, ConnectionError, OSError))
    retries_left = task.attempt_count <= task.max_retries
    status = (
        SelectionMaturityTaskStatus.QUEUED
        if retryable and retries_left
        else SelectionMaturityTaskStatus.FAILED
    )
    final_retryable = retryable and retries_left
    finished = task.model_copy(
        update={
            "status": status,
            "updated_at": now,
            "failure_reason": str(error),
            "retryable": final_retryable,
        }
    )
    store.save_maturity_task(finished)
    _append_event(
        store,
        task_id=task.task_id,
        event_type="attempt_result",
        occurred_at=now,
        attempt_count=task.attempt_count,
        status=SelectionMaturityTaskStatus.PARTIAL
        if status is SelectionMaturityTaskStatus.QUEUED
        else status,
        failure_reason=str(error),
        retryable=retryable,
        reason="attempt_failed",
    )
    if status is SelectionMaturityTaskStatus.QUEUED:
        _append_event(
            store,
            task_id=task.task_id,
            event_type="requeue",
            occurred_at=now,
            attempt_count=task.attempt_count,
            status=status,
            failure_reason=str(error),
            retryable=True,
            reason="retry_available",
        )
    return store.load_maturity_task(task.task_id)


def _recover_stale_task(
    task: SelectionMaturityTask,
    store: SelectionImportStore,
    now: datetime,
) -> SelectionMaturityTask:
    if now - task.updated_at < timedelta(seconds=task.timeout_seconds):
        raise SelectionMaturityTaskError("maturity task is already running")
    retries_left = task.attempt_count <= task.max_retries
    status = (
        SelectionMaturityTaskStatus.QUEUED if retries_left else SelectionMaturityTaskStatus.FAILED
    )
    recovered = task.model_copy(
        update={
            "status": status,
            "updated_at": now,
            "failure_reason": "previous attempt exceeded timeout",
            "retryable": retries_left,
        }
    )
    store.save_maturity_task(recovered)
    _append_event(
        store,
        task_id=task.task_id,
        event_type="requeue" if retries_left else "attempt_result",
        occurred_at=now,
        attempt_count=task.attempt_count,
        status=status,
        failure_reason=recovered.failure_reason,
        retryable=retries_left,
        reason="stale_running_recovered",
    )
    return store.load_maturity_task(task.task_id)


def _append_event(
    store: SelectionImportStore,
    *,
    task_id: str,
    event_type: Literal["queued", "running", "attempt_result", "requeue"],
    occurred_at: datetime,
    attempt_count: int,
    status: SelectionMaturityTaskStatus,
    outcomes_written: int = 0,
    rejected_candidates: tuple[tuple[str, str], ...] = (),
    market_snapshot_ids: tuple[str, ...] = (),
    failure_reason: str | None = None,
    retryable: bool = False,
    reason: str | None = None,
) -> None:
    identity = json.dumps(
        {
            "attempt_count": attempt_count,
            "event_type": event_type,
            "failure_reason": failure_reason,
            "occurred_at": occurred_at.isoformat(),
            "reason": reason,
            "status": status.value,
            "task_id": task_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    event = SelectionMaturityTaskEvent(
        event_id=f"maturity-event-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}",
        task_id=task_id,
        event_type=event_type,
        occurred_at=occurred_at,
        attempt_count=attempt_count,
        status=status,
        outcomes_written=outcomes_written,
        rejected_candidates=rejected_candidates,
        market_snapshot_ids=market_snapshot_ids,
        failure_reason=failure_reason,
        retryable=retryable,
        reason=reason,
    )
    store.append_maturity_task_event(event)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SelectionMaturityTaskError(f"{name} must include a timezone")
