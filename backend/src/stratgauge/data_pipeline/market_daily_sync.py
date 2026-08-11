"""Incremental full-market daily-bar synchronization."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from stratgauge.data_pipeline.data_access import (
    DataAccessDecision,
    DataAccessPlan,
    build_data_access_plan,
)
from stratgauge.data_pipeline.quality import check_bars, quality_run_id
from stratgauge.data_pipeline.store import SnapshotManifest, SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.universe import UniverseSnapshot
from stratgauge.data_sources.base import MarketDailyDataAdapter, MarketDailyRequest


class MarketDailySyncError(RuntimeError):
    """A market session cannot be synchronized safely."""


class MarketSessionSyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    universe_snapshot_id: str
    provider: str
    snapshot_id: str
    status: SnapshotStatus
    quality_run_id: str | None
    fetched_rows: int = Field(ge=0)
    expected_securities: int = Field(ge=1)
    actual_securities: int = Field(ge=0)
    coverage: Decimal = Field(ge=0, le=1)
    access_plan: DataAccessPlan
    skipped: bool = False


def sync_market_session(
    *,
    adapter: MarketDailyDataAdapter,
    session_date: date,
    universe: UniverseSnapshot,
    store: SnapshotStore,
    minimum_security_coverage: Decimal = Decimal("0.90"),
) -> MarketSessionSyncResult:
    """Fetch, quality-check and publish one explicit trading session."""

    if not 0 <= minimum_security_coverage <= 1:
        raise MarketDailySyncError("minimum_security_coverage must be between 0 and 1")
    if universe.as_of.date() > session_date:
        raise MarketDailySyncError(
            "universe snapshot was created after the requested market session"
        )
    expected_security_ids = tuple(sorted(security.security_id for security in universe.securities))
    if not expected_security_ids:
        raise MarketDailySyncError("universe contains no securities")
    existing = store.find_approved_market_snapshot(
        provider=adapter.provider,
        session_date=session_date,
        universe_snapshot_id=universe.universe_snapshot_id,
    )
    access_plan = build_data_access_plan(
        request_kind="market_daily",
        provider=adapter.provider,
        cache_key=(
            f"market_daily|{adapter.provider}|{session_date.isoformat()}|"
            f"{universe.universe_snapshot_id}"
        ),
        local_snapshot_id=existing.snapshot_id if existing is not None else None,
        remote_allowed=True,
    )
    if access_plan.decision is DataAccessDecision.CACHE_HIT:
        assert existing is not None
        return _result_from_manifest(
            existing,
            expected_securities=len(expected_security_ids),
            access_plan=access_plan,
            skipped=True,
        )

    request = MarketDailyRequest(
        session_date=session_date,
        universe_snapshot_id=universe.universe_snapshot_id,
        expected_security_ids=expected_security_ids,
    )
    fetched = adapter.fetch_market_daily(request, universe.securities)
    manifest = store.write(fetched)
    quality = check_bars(
        fetched.bars,
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
        actual_securities=len({bar.security_id for bar in fetched.bars}),
        access_plan=access_plan,
        skipped=False,
    )


def sync_market_sessions(
    *,
    adapter: MarketDailyDataAdapter,
    session_dates: tuple[date, ...],
    universe: UniverseSnapshot,
    store: SnapshotStore,
    minimum_security_coverage: Decimal = Decimal("0.90"),
) -> tuple[MarketSessionSyncResult, ...]:
    """Synchronize an explicit ordered set of trading sessions."""

    if not session_dates:
        raise MarketDailySyncError("session_dates must not be empty")
    if len(set(session_dates)) != len(session_dates):
        raise MarketDailySyncError("session_dates must not contain duplicates")
    if tuple(sorted(session_dates)) != session_dates:
        raise MarketDailySyncError("session_dates must be sorted in ascending order")
    return tuple(
        sync_market_session(
            adapter=adapter,
            session_date=session_date,
            universe=universe,
            store=store,
            minimum_security_coverage=minimum_security_coverage,
        )
        for session_date in session_dates
    )


def _result_from_manifest(
    manifest: SnapshotManifest,
    *,
    expected_securities: int,
    actual_securities: int | None = None,
    access_plan: DataAccessPlan,
    skipped: bool,
) -> MarketSessionSyncResult:
    actual = actual_securities if actual_securities is not None else manifest.row_count
    coverage = Decimal(actual) / Decimal(expected_securities)
    return MarketSessionSyncResult(
        session_date=manifest.start_date,
        universe_snapshot_id=manifest.universe_snapshot_id or "unknown",
        provider=manifest.provider,
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
