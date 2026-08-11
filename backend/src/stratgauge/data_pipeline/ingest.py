"""Idempotent adapter ingestion with a publish-blocking quality gate."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from stratgauge.data_pipeline.data_access import (
    DataAccessDecision,
    DataAccessPlan,
    build_data_access_plan,
)
from stratgauge.data_pipeline.quality import QualityReport, check_bars, quality_run_id
from stratgauge.data_pipeline.store import (
    SnapshotManifest,
    SnapshotStatus,
    SnapshotStore,
    SnapshotStoreError,
)
from stratgauge.data_sources.base import DailyBarRequest, MarketDataAdapter
from stratgauge.domain.models import DailyBar


@dataclass(frozen=True)
class IngestResult:
    manifest: SnapshotManifest
    quality: QualityReport
    bars: tuple[DailyBar, ...]
    access_plan: DataAccessPlan


def ingest(
    adapter: MarketDataAdapter,
    request: DailyBarRequest,
    store: SnapshotStore,
    *,
    expected_sessions: Iterable[date] = (),
    expected_security_ids: Iterable[str] = (),
    remote_allowed: bool = True,
    force_refresh: bool = False,
) -> IngestResult:
    existing = store.find_approved_security_snapshot(
        provider=adapter.provider,
        request=request,
    )
    access_plan = build_data_access_plan(
        request_kind=request.request_kind,
        provider=adapter.provider,
        cache_key=(
            f"{request.request_kind}|{adapter.provider}|{request.security.symbol}|"
            f"{request.start_date.isoformat()}|{request.end_date.isoformat()}"
        ),
        local_snapshot_id=existing.snapshot_id if existing is not None else None,
        remote_allowed=remote_allowed,
        force_refresh=force_refresh,
    )
    if access_plan.decision is DataAccessDecision.CACHE_HIT:
        assert existing is not None
        if existing.quality_run_id is None:
            raise SnapshotStoreError(
                f"approved snapshot has no quality report: {existing.snapshot_id}"
            )
        return IngestResult(
            manifest=existing,
            quality=store.load_quality_report(existing.quality_run_id),
            bars=store.load_bars(existing.snapshot_id),
            access_plan=access_plan,
        )
    if access_plan.decision in {
        DataAccessDecision.LOCAL_ONLY,
        DataAccessDecision.BLOCKED,
    }:
        raise SnapshotStoreError(
            f"approved local snapshot does not exist for request: {access_plan.cache_key}"
        )
    result = adapter.fetch_daily_bars(request)
    manifest = store.write(result)
    quality = check_bars(
        result.bars,
        snapshot_id=manifest.snapshot_id,
        expected_sessions=expected_sessions,
        expected_security_ids=expected_security_ids,
    )
    run_id = quality_run_id(quality)
    store.save_quality_report(quality, run_id)
    status = SnapshotStatus.APPROVED if quality.approved else SnapshotStatus.REJECTED
    manifest = store.update_status(manifest.snapshot_id, status, run_id)
    return IngestResult(
        manifest=manifest,
        quality=quality,
        bars=result.bars,
        access_plan=access_plan,
    )
