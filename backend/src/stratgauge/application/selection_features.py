"""Build point-in-time selection features from approved local market snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime

from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore, SnapshotStoreError
from stratgauge.domain.models import DailyBar, DomainModel
from stratgauge.domain.selections import TdxSelectionImport
from stratgauge.features.contract import FeatureSetVersion, default_feature_set
from stratgauge.features.engine import FeatureComputationError, compute_daily_features
from stratgauge.features.store import FeatureSnapshotStore, FeatureStoreError


class SelectionFeatureError(RuntimeError):
    """A selection feature batch cannot be built safely."""


class FeatureSnapshotBuildFailure(DomainModel):
    """A candidate-specific feature failure that does not discard the batch."""

    candidate_id: str
    reason: str


class SelectionFeatureSnapshotReport(DomainModel):
    """Content-addressed accounting for one feature-snapshot build."""

    report_id: str
    batch_id: str
    feature_set_version_id: str
    as_of: datetime
    source_snapshot_ids: tuple[str, ...]
    candidate_snapshot_ids: tuple[tuple[str, str], ...]
    failures: tuple[FeatureSnapshotBuildFailure, ...] = ()

    @property
    def feature_snapshot_ids(self) -> tuple[str, ...]:
        return tuple(snapshot_id for _, snapshot_id in self.candidate_snapshot_ids)


def build_selection_feature_snapshots(
    imported: TdxSelectionImport,
    *,
    market_store: SnapshotStore,
    feature_store: FeatureSnapshotStore,
    market_snapshot_ids: Sequence[str],
    as_of: datetime,
    feature_set: FeatureSetVersion | None = None,
    benchmark_symbol: str | None = "000300.SH",
) -> SelectionFeatureSnapshotReport:
    """Build one immutable feature snapshot per candidate from approved snapshots.

    Rows on or after the selection date are excluded before feature computation. The
    feature engine then applies its own availability guard, so the resulting report
    cannot silently use data that was not known at ``as_of``.
    """

    _require_aware(as_of)
    snapshot_ids = _unique_snapshot_ids(market_snapshot_ids)
    if not snapshot_ids:
        raise SelectionFeatureError("market_snapshot_ids must not be empty")
    manifests = []
    for snapshot_id in snapshot_ids:
        try:
            manifest = market_store.load_manifest(snapshot_id)
        except SnapshotStoreError as exc:
            raise SelectionFeatureError(str(exc)) from exc
        if manifest.status is not SnapshotStatus.APPROVED:
            raise SelectionFeatureError(f"market snapshot is not approved: {snapshot_id}")
        if manifest.request_kind != "market_daily":
            raise SelectionFeatureError(f"snapshot is not market-daily data: {snapshot_id}")
        manifests.append(manifest)
    try:
        bars = tuple(
            bar
            for snapshot_id in snapshot_ids
            for bar in market_store.load_bars(snapshot_id)
        )
    except SnapshotStoreError as exc:
        raise SelectionFeatureError(str(exc)) from exc
    resolved_set = feature_set or default_feature_set()
    source_ids = tuple(
        manifest.snapshot_id
        for manifest in manifests
        if manifest.start_date < imported.batch.selection_date
    )
    if not source_ids:
        raise SelectionFeatureError("market snapshots contain no pre-selection history")

    candidate_snapshot_ids: list[tuple[str, str]] = []
    failures: list[FeatureSnapshotBuildFailure] = []
    for candidate in imported.candidates:
        candidate_bars = _bars_for_symbol(bars, candidate.symbol, imported.batch.selection_date)
        benchmark_bars = (
            ()
            if benchmark_symbol is None
            else _bars_for_symbol(bars, benchmark_symbol, imported.batch.selection_date)
        )
        try:
            snapshot = compute_daily_features(
                candidate_bars,
                selection_date=imported.batch.selection_date,
                as_of=as_of,
                source_snapshot_ids=source_ids,
                feature_set=resolved_set,
                benchmark_bars=benchmark_bars,
                benchmark_source_snapshot_ids=(
                    source_ids if benchmark_bars else ()
                ),
            )
            feature_store.write(snapshot)
        except (FeatureComputationError, FeatureStoreError) as exc:
            failures.append(
                FeatureSnapshotBuildFailure(candidate_id=candidate.candidate_id, reason=str(exc))
            )
            continue
        candidate_snapshot_ids.append((candidate.candidate_id, snapshot.feature_snapshot_id))

    identity = {
        "as_of": as_of.isoformat(),
        "batch_id": imported.batch.batch_id,
        "candidate_snapshot_ids": candidate_snapshot_ids,
        "failures": [failure.model_dump(mode="json") for failure in failures],
        "feature_set_version_id": resolved_set.feature_set_version_id,
        "source_snapshot_ids": source_ids,
    }
    report_id = f"feature-report-{_sha256(identity)[:20]}"
    return SelectionFeatureSnapshotReport(
        report_id=report_id,
        batch_id=imported.batch.batch_id,
        feature_set_version_id=resolved_set.feature_set_version_id,
        as_of=as_of,
        source_snapshot_ids=source_ids,
        candidate_snapshot_ids=tuple(candidate_snapshot_ids),
        failures=tuple(failures),
    )


def _bars_for_symbol(
    bars: Sequence[DailyBar], symbol: str, selection_date: date
) -> tuple[DailyBar, ...]:
    return tuple(
        sorted(
            (
                bar
                for bar in bars
                if bar.symbol == symbol and bar.session_date < selection_date
            ),
            key=lambda bar: bar.session_date,
        )
    )


def _unique_snapshot_ids(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized:
            raise SelectionFeatureError("market snapshot identifiers must not be empty")
        if normalized in result:
            raise SelectionFeatureError(f"duplicate market snapshot identifier: {normalized}")
        result.append(normalized)
    return tuple(result)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SelectionFeatureError("as_of must include a timezone")


def _sha256(identity: Mapping[str, object]) -> str:
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
