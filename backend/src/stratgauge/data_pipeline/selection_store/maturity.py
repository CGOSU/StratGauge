"""Maturity task ledger persistence."""

from __future__ import annotations

import re
from datetime import datetime

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.data_pipeline.selection_store._base import SelectionStoreBase, SelectionStoreError
from stratgauge.data_pipeline.selection_store._io import _write_selection_text
from stratgauge.domain.selections import (
    SelectionMaturityTask,
    SelectionMaturityTaskEvent,
    SelectionMaturityTaskStatus,
)


def _maturity_event_sort_key(
    event: SelectionMaturityTaskEvent,
) -> tuple[datetime, int, int, str]:
    event_order = {"queued": 0, "running": 1, "attempt_result": 2, "requeue": 3}
    return (
        event.occurred_at,
        event.attempt_count,
        event_order[event.event_type],
        event.event_id,
    )


def _same_maturity_task_configuration(
    first: SelectionMaturityTask,
    second: SelectionMaturityTask,
) -> bool:
    return (
        first.task_id == second.task_id
        and first.batch_id == second.batch_id
        and first.provider == second.provider
        and first.market_data_mode == second.market_data_mode
        and first.universe_snapshot_id == second.universe_snapshot_id
        and first.input_market_snapshot_ids == second.input_market_snapshot_ids
        and first.evaluated_at == second.evaluated_at
        and first.target_evaluation_date == second.target_evaluation_date
        and first.tracking_policy_id == second.tracking_policy_id
        and first.performance_policy_id == second.performance_policy_id
        and first.performance_target_return == second.performance_target_return
        and first.horizon_sessions == second.horizon_sessions
        and first.position_notional == second.position_notional
        and first.max_retries == second.max_retries
        and first.timeout_seconds == second.timeout_seconds
    )


def _task_storage_json(task: SelectionMaturityTask) -> str:
    return task.model_copy(update={"events": ()}).model_dump_json(indent=2)


class MaturityTasksMixin(SelectionStoreBase):
    """Maturity task ledger operations."""

    def create_maturity_task(self, task: SelectionMaturityTask) -> SelectionMaturityTask:
        """Create one task or return it idempotently without changing its config."""

        with local_write_lock(self.root):
            return self._create_maturity_task_unlocked(task)

    def _create_maturity_task_unlocked(
        self,
        task: SelectionMaturityTask,
    ) -> SelectionMaturityTask:

        self.load_import(task.batch_id)
        if not self.is_entity_active("batch", task.batch_id):
            raise SelectionStoreError("selection batch is archived")
        path = self._maturity_task_path(task.task_id)
        if path.exists():
            existing = self.load_maturity_task(task.task_id)
            if not _same_maturity_task_configuration(existing, task):
                raise SelectionStoreError(
                    "maturity task identity already exists with different configuration"
                )
            return existing
        _write_selection_text(path, _task_storage_json(task))
        return self.load_maturity_task(task.task_id)

    def save_maturity_task(self, task: SelectionMaturityTask) -> SelectionMaturityTask:
        """Atomically materialize task state while keeping its first config immutable."""

        with local_write_lock(self.root):
            return self._save_maturity_task_unlocked(task)

    def _save_maturity_task_unlocked(
        self,
        task: SelectionMaturityTask,
    ) -> SelectionMaturityTask:

        path = self._maturity_task_path(task.task_id)
        if not path.exists():
            raise SelectionStoreError(f"maturity task does not exist: {task.task_id}")
        existing = self.load_maturity_task(task.task_id)
        if not _same_maturity_task_configuration(existing, task):
            raise SelectionStoreError(
                "maturity task configuration cannot be changed after creation"
            )
        _write_selection_text(path, _task_storage_json(task))
        return self.load_maturity_task(task.task_id)

    def load_maturity_task(self, task_id: str) -> SelectionMaturityTask:
        path = self._maturity_task_path(task_id)
        if not path.exists():
            raise SelectionStoreError(f"maturity task does not exist: {task_id}")
        try:
            task = SelectionMaturityTask.model_validate_json(path.read_text(encoding="utf-8"))
            events = self.list_maturity_task_events(task_id)
            return task.model_copy(update={"events": events})
        except SelectionStoreError:
            raise
        except Exception as exc:
            raise SelectionStoreError(f"maturity task is invalid: {task_id}") from exc

    def list_maturity_tasks(
        self,
        *,
        batch_id: str | None = None,
        status: str | SelectionMaturityTaskStatus | None = None,
    ) -> tuple[SelectionMaturityTask, ...]:
        tasks: list[SelectionMaturityTask] = []
        status_value = status.value if isinstance(status, SelectionMaturityTaskStatus) else status
        try:
            for path in sorted(self.maturity_task_dir.glob("maturity-task-*.json")):
                task = self.load_maturity_task(path.stem)
                if batch_id is not None and task.batch_id != batch_id:
                    continue
                if status_value is not None and task.status.value != status_value:
                    continue
                tasks.append(task)
        except SelectionStoreError:
            raise
        except Exception as exc:
            raise SelectionStoreError("maturity task is invalid") from exc
        return tuple(sorted(tasks, key=lambda task: (task.created_at, task.task_id)))

    def append_maturity_task_event(
        self, event: SelectionMaturityTaskEvent
    ) -> SelectionMaturityTaskEvent:
        with local_write_lock(self.root):
            return self._append_maturity_task_event_unlocked(event)

    def _append_maturity_task_event_unlocked(
        self,
        event: SelectionMaturityTaskEvent,
    ) -> SelectionMaturityTaskEvent:
        self._maturity_task_path(event.task_id)
        if not self._maturity_task_path(event.task_id).exists():
            raise SelectionStoreError(f"maturity task does not exist: {event.task_id}")
        event_id = event.event_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", event_id):
            raise SelectionStoreError("maturity task event identifier is invalid")
        path = self.maturity_event_dir / f"{event_id}.json"
        if path.exists():
            try:
                existing = SelectionMaturityTaskEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise SelectionStoreError(f"maturity task event is invalid: {event_id}") from exc
            if existing != event:
                raise SelectionStoreError(
                    f"maturity task event identity has different content: {event_id}"
                )
            return existing
        _write_selection_text(path, event.model_dump_json(indent=2))
        return event

    def list_maturity_task_events(self, task_id: str) -> tuple[SelectionMaturityTaskEvent, ...]:
        self._maturity_task_path(task_id)
        events: list[SelectionMaturityTaskEvent] = []
        try:
            for path in sorted(self.maturity_event_dir.glob("*.json")):
                event = SelectionMaturityTaskEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if event.task_id == task_id:
                    events.append(event)
        except Exception as exc:
            raise SelectionStoreError("maturity task event is invalid") from exc
        return tuple(sorted(events, key=_maturity_event_sort_key))
