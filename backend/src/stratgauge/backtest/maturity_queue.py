"""Queue snapshot-backed maturity tasks from approved full-market data."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stratgauge.backtest.selection_outcomes import (
    SelectionTrackingPolicy,
    validate_selection_tracking_policy,
)
from stratgauge.backtest.selection_tasks import (
    SelectionMaturityTaskError,
    build_maturity_task,
    create_maturity_task,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotManifest, SnapshotStatus, SnapshotStore
from stratgauge.domain.selections import SelectionMaturityTask


class MaturityQueueError(RuntimeError):
    """Approved snapshots cannot be converted into maturity tasks safely."""


class MaturityQueueResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    scanned_snapshot_count: int = Field(ge=0)
    eligible_group_count: int = Field(ge=0)
    created_tasks: tuple[SelectionMaturityTask, ...] = ()
    existing_tasks: tuple[SelectionMaturityTask, ...] = ()


def queue_snapshot_maturity_tasks(
    selection_store: SelectionImportStore,
    market_store: SnapshotStore,
    *,
    provider: Literal["tushare", "akshare", "baostock", "waizao"],
    policy: SelectionTrackingPolicy,
    trading_sessions: Sequence[date],
    now: datetime,
    max_retries: int = 2,
    timeout_seconds: float = 900,
    universe_snapshot_id: str | None = None,
) -> MaturityQueueResult:
    """Create one idempotent task per eligible batch and universe snapshot."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise MaturityQueueError("now must include a timezone")
    validate_selection_tracking_policy(policy)
    if not trading_sessions:
        raise MaturityQueueError("trading_sessions must not be empty")
    if len(set(trading_sessions)) != len(trading_sessions):
        raise MaturityQueueError("trading_sessions must not contain duplicates")
    if tuple(sorted(trading_sessions)) != tuple(trading_sessions):
        raise MaturityQueueError("trading_sessions must be sorted in ascending order")
    approved = tuple(
        manifest
        for manifest in market_store.list_manifests(SnapshotStatus.APPROVED)
        if manifest.request_kind == "market_daily"
        and manifest.provider == provider
        and manifest.universe_snapshot_id is not None
        and manifest.created_at <= now
        and (universe_snapshot_id is None or manifest.universe_snapshot_id == universe_snapshot_id)
    )
    by_universe: dict[str, list[SnapshotManifest]] = defaultdict(list)
    for manifest in approved:
        assert manifest.universe_snapshot_id is not None
        by_universe[manifest.universe_snapshot_id].append(manifest)

    created: list[SelectionMaturityTask] = []
    existing: list[SelectionMaturityTask] = []
    eligible_groups = 0
    for batch_manifest in selection_store.list_manifests():
        imported = selection_store.load_import(batch_manifest.batch_id)
        for scoped_universe_id, manifests in sorted(by_universe.items()):
            target_dates = tuple(
                session
                for session in trading_sessions
                if imported.batch.selection_date < session <= now.date()
            )[: policy.horizon_sessions + 1]
            manifests_by_date = {manifest.start_date: manifest for manifest in manifests}
            if len(target_dates) < policy.horizon_sessions + 1 or any(
                session not in manifests_by_date for session in target_dates
            ):
                continue
            candidates = tuple(manifests_by_date[session] for session in target_dates)
            eligible_groups += 1
            input_snapshot_ids = tuple(manifest.snapshot_id for manifest in candidates)
            evaluated_at = max(manifest.created_at for manifest in candidates)
            try:
                task = build_maturity_task(
                    imported,
                    provider=provider,
                    evaluated_at=evaluated_at,
                    policy=policy,
                    market_data_mode="approved_snapshots",
                    universe_snapshot_id=scoped_universe_id,
                    input_market_snapshot_ids=input_snapshot_ids,
                    max_retries=max_retries,
                    timeout_seconds=timeout_seconds,
                    created_at=now.astimezone(UTC),
                )
                prior = _existing_task(selection_store, task.task_id)
                persisted = create_maturity_task(selection_store, task, now=now)
            except (SelectionMaturityTaskError, SelectionStoreError, ValueError) as exc:
                raise MaturityQueueError(str(exc)) from exc
            if prior is None:
                created.append(persisted)
            else:
                existing.append(persisted)

    return MaturityQueueResult(
        provider=provider,
        scanned_snapshot_count=len(approved),
        eligible_group_count=eligible_groups,
        created_tasks=tuple(created),
        existing_tasks=tuple(existing),
    )


def _existing_task(store: SelectionImportStore, task_id: str) -> SelectionMaturityTask | None:
    try:
        return store.load_maturity_task(task_id)
    except SelectionStoreError as exc:
        if "does not exist" in str(exc):
            return None
        raise
