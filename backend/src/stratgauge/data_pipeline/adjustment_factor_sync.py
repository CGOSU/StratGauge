"""Quality-gated synchronization of same-day adjustment factors."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from stratgauge.data_pipeline.data_access import DataAccessPlan, build_data_access_plan
from stratgauge.data_pipeline.quality import check_adjustment_factors, quality_run_id
from stratgauge.data_pipeline.store import SnapshotManifest, SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.universe import UniverseSnapshot
from stratgauge.data_sources.base import (
    AdapterError,
    AdjustmentFactorDataAdapter,
    AdjustmentFactorRequest,
)


class AdjustmentFactorSyncError(RuntimeError):
    """Adjustment factors cannot be safely bound to market data."""


class AdjustmentFactorBackfillStatus(StrEnum):
    APPROVED = "approved"
    MISSING_DAILY_SNAPSHOT = "missing_daily_snapshot"
    REJECTED = "rejected"
    FETCH_FAILED = "fetch_failed"


class AdjustmentFactorBackfillSessionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    status: AdjustmentFactorBackfillStatus
    source_daily_snapshot_id: str | None = None
    snapshot_id: str | None = None
    skipped: bool = False
    error: str | None = None
    access_plan: DataAccessPlan | None = None


class AdjustmentFactorBackfillReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    universe_snapshot_id: str
    session_dates: tuple[date, ...]
    results: tuple[AdjustmentFactorBackfillSessionResult, ...]
    approved_snapshot_ids: tuple[str, ...] = ()
    rejected_snapshot_ids: tuple[str, ...] = ()
    missing_daily_sessions: tuple[date, ...] = ()
    fetch_failed_sessions: tuple[date, ...] = ()
    blockers: tuple[str, ...] = ()
    ready: bool = False


class AdjustmentFactorSessionSyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    universe_snapshot_id: str
    provider: str
    source_daily_snapshot_id: str
    snapshot_id: str
    status: SnapshotStatus
    quality_run_id: str | None
    fetched_rows: int = Field(ge=0)
    expected_securities: int = Field(ge=1)
    actual_securities: int = Field(ge=0)
    coverage: Decimal = Field(ge=0, le=1)
    access_plan: DataAccessPlan
    skipped: bool = False


def sync_adjustment_factor_sessions(
    *,
    adapter: AdjustmentFactorDataAdapter,
    session_dates: Sequence[date],
    universe: UniverseSnapshot,
    store: SnapshotStore,
    minimum_security_coverage: Decimal = Decimal("0.90"),
) -> AdjustmentFactorBackfillReport:
    """Backfill factors only for sessions with approved daily snapshots."""

    ordered_sessions = tuple(session_dates)
    if not ordered_sessions:
        raise AdjustmentFactorSyncError("session_dates must not be empty")
    if len(set(ordered_sessions)) != len(ordered_sessions):
        raise AdjustmentFactorSyncError("session_dates must not contain duplicates")
    if tuple(sorted(ordered_sessions)) != ordered_sessions:
        raise AdjustmentFactorSyncError("session_dates must be sorted in ascending order")

    results: list[AdjustmentFactorBackfillSessionResult] = []
    approved_snapshot_ids: list[str] = []
    rejected_snapshot_ids: list[str] = []
    missing_daily_sessions: list[date] = []
    fetch_failed_sessions: list[date] = []
    blockers: list[str] = []
    for session_date in ordered_sessions:
        daily_manifest = store.find_approved_market_snapshot(
            provider=adapter.provider,
            session_date=session_date,
            universe_snapshot_id=universe.universe_snapshot_id,
        )
        if daily_manifest is None:
            missing_daily_sessions.append(session_date)
            blockers.append(f"missing approved market snapshot: {session_date}")
            results.append(
                AdjustmentFactorBackfillSessionResult(
                    session_date=session_date,
                    status=AdjustmentFactorBackfillStatus.MISSING_DAILY_SNAPSHOT,
                )
            )
            continue
        try:
            synced = sync_adjustment_factor_session(
                adapter=adapter,
                session_date=session_date,
                universe=universe,
                source_daily_snapshot_id=daily_manifest.snapshot_id,
                store=store,
                minimum_security_coverage=minimum_security_coverage,
            )
        except AdapterError as exc:
            fetch_failed_sessions.append(session_date)
            blockers.append(f"factor fetch failed: {session_date}")
            results.append(
                AdjustmentFactorBackfillSessionResult(
                    session_date=session_date,
                    status=AdjustmentFactorBackfillStatus.FETCH_FAILED,
                    source_daily_snapshot_id=daily_manifest.snapshot_id,
                    error=str(exc),
                )
            )
            continue
        status = (
            AdjustmentFactorBackfillStatus.APPROVED
            if synced.status is SnapshotStatus.APPROVED
            else AdjustmentFactorBackfillStatus.REJECTED
        )
        if status is AdjustmentFactorBackfillStatus.APPROVED:
            approved_snapshot_ids.append(synced.snapshot_id)
        else:
            rejected_snapshot_ids.append(synced.snapshot_id)
            blockers.append(f"rejected adjustment-factor snapshot: {session_date}")
        results.append(
            AdjustmentFactorBackfillSessionResult(
                session_date=session_date,
                status=status,
                source_daily_snapshot_id=synced.source_daily_snapshot_id,
                snapshot_id=synced.snapshot_id,
                skipped=synced.skipped,
                access_plan=synced.access_plan,
            )
        )

    return AdjustmentFactorBackfillReport(
        provider=adapter.provider,
        universe_snapshot_id=universe.universe_snapshot_id,
        session_dates=ordered_sessions,
        results=tuple(results),
        approved_snapshot_ids=tuple(approved_snapshot_ids),
        rejected_snapshot_ids=tuple(rejected_snapshot_ids),
        missing_daily_sessions=tuple(missing_daily_sessions),
        fetch_failed_sessions=tuple(fetch_failed_sessions),
        blockers=tuple(blockers),
        ready=not blockers,
    )


def sync_adjustment_factor_session(
    *,
    adapter: AdjustmentFactorDataAdapter,
    session_date: date,
    universe: UniverseSnapshot,
    source_daily_snapshot_id: str,
    store: SnapshotStore,
    minimum_security_coverage: Decimal = Decimal("0.90"),
) -> AdjustmentFactorSessionSyncResult:
    """Fetch and publish factors only when an approved same-day bar snapshot exists."""

    if not 0 <= minimum_security_coverage <= 1:
        raise AdjustmentFactorSyncError("minimum_security_coverage must be between 0 and 1")
    if not source_daily_snapshot_id.strip():
        raise AdjustmentFactorSyncError("source_daily_snapshot_id must not be empty")
    daily_manifest = store.load_manifest(source_daily_snapshot_id)
    if (
        daily_manifest.status is not SnapshotStatus.APPROVED
        or daily_manifest.request_kind != "market_daily"
        or daily_manifest.provider != adapter.provider
        or daily_manifest.start_date != session_date
        or daily_manifest.end_date != session_date
        or daily_manifest.universe_snapshot_id != universe.universe_snapshot_id
    ):
        raise AdjustmentFactorSyncError(
            "adjustment factors require an approved same-day market snapshot "
            "from the same provider and universe"
        )
    if universe.as_of.date() > session_date:
        raise AdjustmentFactorSyncError(
            "universe snapshot was created after the requested adjustment-factor session"
        )
    expected_security_ids = tuple(sorted(security.security_id for security in universe.securities))
    if not expected_security_ids:
        raise AdjustmentFactorSyncError("universe contains no securities")
    existing = store.find_approved_adjustment_factor_snapshot(
        provider=adapter.provider,
        session_date=session_date,
        universe_snapshot_id=universe.universe_snapshot_id,
        source_daily_snapshot_id=source_daily_snapshot_id,
    )
    access_plan = build_data_access_plan(
        request_kind="adjustment_factor",
        provider=adapter.provider,
        cache_key=(
            f"adjustment_factor|{adapter.provider}|{session_date.isoformat()}|"
            f"{universe.universe_snapshot_id}|{source_daily_snapshot_id}"
        ),
        local_snapshot_id=existing.snapshot_id if existing is not None else None,
        remote_allowed=True,
    )
    if existing is not None:
        return _result_from_manifest(
            existing,
            expected_securities=len(expected_security_ids),
            access_plan=access_plan,
            skipped=True,
        )

    request = AdjustmentFactorRequest(
        session_date=session_date,
        universe_snapshot_id=universe.universe_snapshot_id,
        source_daily_snapshot_id=source_daily_snapshot_id,
        expected_security_ids=expected_security_ids,
    )
    fetched = adapter.fetch_adjustment_factors(request, universe.securities)
    manifest = store.write(fetched)
    quality = check_adjustment_factors(
        fetched.factors,
        snapshot_id=manifest.snapshot_id,
        expected_sessions=(session_date,),
        expected_security_ids=expected_security_ids,
        missing_security_severity="warning",
        minimum_security_coverage=minimum_security_coverage,
    )
    run_id = quality_run_id(quality)
    store.save_quality_report(quality, run_id)
    manifest = store.update_status(
        manifest.snapshot_id,
        SnapshotStatus.APPROVED if quality.approved else SnapshotStatus.REJECTED,
        run_id,
    )
    return _result_from_manifest(
        manifest,
        expected_securities=len(expected_security_ids),
        actual_securities=len({factor.security_id for factor in fetched.factors}),
        access_plan=access_plan,
        skipped=False,
    )


def _result_from_manifest(
    manifest: SnapshotManifest,
    *,
    expected_securities: int,
    actual_securities: int | None = None,
    access_plan: DataAccessPlan,
    skipped: bool,
) -> AdjustmentFactorSessionSyncResult:
    if manifest.source_daily_snapshot_id is None:
        raise AdjustmentFactorSyncError("adjustment-factor manifest has no source daily snapshot")
    actual = actual_securities if actual_securities is not None else manifest.row_count
    coverage = Decimal(actual) / Decimal(expected_securities)
    return AdjustmentFactorSessionSyncResult(
        session_date=manifest.start_date,
        universe_snapshot_id=manifest.universe_snapshot_id or "unknown",
        provider=manifest.provider,
        source_daily_snapshot_id=manifest.source_daily_snapshot_id,
        snapshot_id=manifest.snapshot_id,
        status=manifest.status,
        quality_run_id=manifest.quality_run_id,
        fetched_rows=manifest.row_count,
        expected_securities=expected_securities,
        actual_securities=actual,
        coverage=coverage,
        access_plan=access_plan,
        skipped=skipped,
    )
