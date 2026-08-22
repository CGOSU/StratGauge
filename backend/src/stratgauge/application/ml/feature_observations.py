"""Explicit application service for building training feature observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.features.contract import FeatureSetVersion, default_feature_set
from stratgauge.features.store import FeatureSnapshotStore
from stratgauge.ml.training_datasets import SelectionTrainingLabelRow
from stratgauge.ml.training_feature_observations import (
    TrainingFeatureSnapshotBuildReport,
    TrainingFeatureSnapshotError,
    build_training_feature_snapshots,
    write_training_feature_snapshot_report,
)


@dataclass(frozen=True)
class BuildTrainingFeatureSnapshotsRequest:
    """Inputs for one manually invoked point-in-time feature stage."""

    labels: tuple[SelectionTrainingLabelRow, ...]
    selection_root: Path
    market_root: Path
    feature_root: Path
    report_path: Path
    market_snapshot_ids: tuple[str, ...]
    benchmark_snapshot_ids: tuple[str, ...] = ()
    adjustment_factor_snapshot_ids: tuple[str, ...] = ()
    benchmark_adjustment_factor_snapshot_ids: tuple[str, ...] = ()
    feature_set: FeatureSetVersion | None = None
    benchmark_symbol: str | None = "000300.SH"
    as_of_cutoff: datetime | None = None


@dataclass(frozen=True)
class BuiltTrainingFeatureSnapshotsResult:
    """Evidence returned after writing training feature snapshots and its report."""

    report: TrainingFeatureSnapshotBuildReport
    report_path: Path


def run_build_training_feature_snapshots(
    request: BuildTrainingFeatureSnapshotsRequest,
) -> BuiltTrainingFeatureSnapshotsResult:
    """Build only the explicitly requested feature stage; never train or join."""

    report = build_training_feature_snapshots(
        request.labels,
        selection_store=SelectionImportStore(request.selection_root),
        market_store=SnapshotStore(request.market_root),
        feature_store=FeatureSnapshotStore(request.feature_root),
        market_snapshot_ids=request.market_snapshot_ids,
        benchmark_snapshot_ids=request.benchmark_snapshot_ids,
        adjustment_factor_snapshot_ids=request.adjustment_factor_snapshot_ids,
        benchmark_adjustment_factor_snapshot_ids=request.benchmark_adjustment_factor_snapshot_ids,
        feature_set=request.feature_set or default_feature_set(),
        benchmark_symbol=request.benchmark_symbol,
        as_of_cutoff=request.as_of_cutoff,
    )
    write_training_feature_snapshot_report(request.report_path, report)
    return BuiltTrainingFeatureSnapshotsResult(report=report, report_path=request.report_path)


__all__ = [
    "BuildTrainingFeatureSnapshotsRequest",
    "TrainingFeatureSnapshotError",
    "BuiltTrainingFeatureSnapshotsResult",
    "run_build_training_feature_snapshots",
]
