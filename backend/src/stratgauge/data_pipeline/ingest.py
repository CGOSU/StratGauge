"""Idempotent adapter ingestion with a publish-blocking quality gate."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from stratgauge.data_pipeline.quality import QualityReport, check_bars, quality_run_id
from stratgauge.data_pipeline.store import SnapshotManifest, SnapshotStatus, SnapshotStore
from stratgauge.data_sources.base import DailyBarRequest, MarketDataAdapter
from stratgauge.domain.models import DailyBar


@dataclass(frozen=True)
class IngestResult:
    manifest: SnapshotManifest
    quality: QualityReport
    bars: tuple[DailyBar, ...]


def ingest(
    adapter: MarketDataAdapter,
    request: DailyBarRequest,
    store: SnapshotStore,
    *,
    expected_sessions: Iterable[date] = (),
    expected_security_ids: Iterable[str] = (),
) -> IngestResult:
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
    return IngestResult(manifest=manifest, quality=quality, bars=result.bars)
