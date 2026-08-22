"""Build auditable point-in-time feature snapshots for prepared ML labels."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from pathlib import Path

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import (
    SnapshotManifest,
    SnapshotStatus,
    SnapshotStore,
    SnapshotStoreError,
)
from stratgauge.data_sources.base import INDEX_DAILY_BENCHMARK_SYMBOL
from stratgauge.domain.models import AdjustmentFactor, DailyBar
from stratgauge.domain.selections import TdxSelectionImport
from stratgauge.features.contract import FeatureSetVersion
from stratgauge.features.engine import FeatureComputationError, compute_daily_features
from stratgauge.features.store import FeatureSnapshotStore, FeatureStoreError
from stratgauge.ml._hashing import sha256_bytes, sha256_json
from stratgauge.ml.training_datasets import (
    SelectionTrainingLabelRow,
    TrainingDatasetPurpose,
    serialize_training_label_dataset,
)
from stratgauge.ml.training_feature_observations.models import (
    TrainingFeatureSnapshotAsOfSource,
    TrainingFeatureSnapshotBuildFailure,
    TrainingFeatureSnapshotBuildReport,
    TrainingFeatureSnapshotMapping,
)


class TrainingFeatureSnapshotError(RuntimeError):
    """Training feature snapshot inputs cannot satisfy the frozen evidence contract."""


def build_training_feature_snapshots(
    labels: Sequence[SelectionTrainingLabelRow],
    *,
    selection_store: SelectionImportStore,
    market_store: SnapshotStore,
    feature_store: FeatureSnapshotStore,
    market_snapshot_ids: Sequence[str],
    benchmark_snapshot_ids: Sequence[str] = (),
    adjustment_factor_snapshot_ids: Sequence[str] = (),
    benchmark_adjustment_factor_snapshot_ids: Sequence[str] = (),
    feature_set: FeatureSetVersion,
    benchmark_symbol: str | None = INDEX_DAILY_BENCHMARK_SYMBOL,
    as_of_cutoff: datetime | None = None,
) -> TrainingFeatureSnapshotBuildReport:
    """Build one safe feature snapshot for every supplied label row.

    Source snapshot identifiers and feature definitions are caller-owned inputs. The
    function never scans the market root and never contacts a provider. Invalid source
    manifests fail the request; row-specific time, history, or selection-lineage errors
    remain in the immutable report so valid rows can still be persisted.
    """

    ordered_labels = _validate_labels(labels)
    _validate_as_of_cutoff(as_of_cutoff)
    source_groups = _validate_source_ids(
        market_snapshot_ids=market_snapshot_ids,
        benchmark_snapshot_ids=benchmark_snapshot_ids,
        adjustment_factor_snapshot_ids=adjustment_factor_snapshot_ids,
        benchmark_adjustment_factor_snapshot_ids=benchmark_adjustment_factor_snapshot_ids,
    )
    resolved_benchmark_symbol = _validate_benchmark_contract(
        feature_set,
        benchmark_snapshot_ids=source_groups.benchmark_snapshot_ids,
        benchmark_symbol=benchmark_symbol,
    )
    _, market_bars = _load_bar_sources(
        market_store,
        source_groups.market_snapshot_ids,
        request_kind="market_daily",
        source_name="market",
    )
    _, benchmark_bars = _load_bar_sources(
        market_store,
        source_groups.benchmark_snapshot_ids,
        request_kind="index_daily",
        source_name="benchmark",
    )
    _validate_benchmark_bars(benchmark_bars, resolved_benchmark_symbol)
    _, factors = _load_factor_sources(
        market_store,
        source_groups.adjustment_factor_snapshot_ids,
        expected_daily_snapshot_ids=source_groups.market_snapshot_ids,
        source_name="candidate adjustment factor",
    )
    _, benchmark_factors = _load_factor_sources(
        market_store,
        source_groups.benchmark_adjustment_factor_snapshot_ids,
        expected_daily_snapshot_ids=source_groups.benchmark_snapshot_ids,
        source_name="benchmark adjustment factor",
    )

    source_snapshot_ids = _unique_ids(
        (
            *source_groups.market_snapshot_ids,
            *source_groups.benchmark_snapshot_ids,
            *source_groups.adjustment_factor_snapshot_ids,
            *source_groups.benchmark_adjustment_factor_snapshot_ids,
        )
    )
    mappings: list[TrainingFeatureSnapshotMapping] = []
    failures: list[TrainingFeatureSnapshotBuildFailure] = []
    imported_cache: dict[str, TdxSelectionImport] = {}

    for row in ordered_labels:
        as_of: datetime | None = None
        as_of_source: TrainingFeatureSnapshotAsOfSource | None = None
        try:
            imported = imported_cache.get(row.batch_id)
            if imported is None:
                imported = selection_store.load_import(row.batch_id)
                imported_cache[row.batch_id] = imported
            _validate_label_selection_lineage(row, imported)
            as_of, as_of_source = _resolve_as_of(row, imported.batch.exported_at)
            if as_of_cutoff is not None and as_of > as_of_cutoff:
                raise ValueError("resolved label as_of follows the explicit as_of cutoff")
            candidate_bars = _bars_for_symbol(market_bars, row.symbol, row.observation_date)
            benchmark = (
                ()
                if resolved_benchmark_symbol is None
                else _bars_for_symbol(
                    benchmark_bars,
                    resolved_benchmark_symbol,
                    row.observation_date,
                )
            )
            candidate_factors = _factors_for_security(factors, candidate_bars)
            benchmark_factors = _factors_for_security(benchmark_factors, benchmark)
            if source_groups.adjustment_factor_snapshot_ids and not candidate_factors:
                raise FeatureComputationError(
                    "missing_adjustment_factors",
                    f"candidate adjustment factors do not cover {row.symbol}",
                )
            if (
                source_groups.benchmark_adjustment_factor_snapshot_ids
                and resolved_benchmark_symbol is not None
                and not benchmark_factors
            ):
                raise FeatureComputationError(
                    "missing_benchmark_adjustment_factors",
                    f"benchmark adjustment factors do not cover {resolved_benchmark_symbol}",
                )
            snapshot = compute_daily_features(
                candidate_bars,
                selection_date=row.observation_date,
                as_of=as_of,
                source_snapshot_ids=source_groups.market_snapshot_ids,
                feature_set=feature_set,
                benchmark_bars=benchmark,
                benchmark_source_snapshot_ids=(
                    source_groups.benchmark_snapshot_ids if benchmark else ()
                ),
                adjustment_factors=candidate_factors,
                adjustment_factor_source_snapshot_ids=(
                    source_groups.adjustment_factor_snapshot_ids if candidate_factors else ()
                ),
                benchmark_adjustment_factors=benchmark_factors,
                benchmark_adjustment_factor_source_snapshot_ids=(
                    source_groups.benchmark_adjustment_factor_snapshot_ids
                    if benchmark_factors
                    else ()
                ),
            )
            feature_store.write(snapshot)
        except (
            FeatureComputationError,
            FeatureStoreError,
            SelectionStoreError,
            ValueError,
        ) as exc:
            failures.append(
                TrainingFeatureSnapshotBuildFailure(
                    sample_id=row.sample_id,
                    candidate_id=row.candidate_id,
                    symbol=row.symbol,
                    observation_date=row.observation_date,
                    source_label_row_sha256=row.source_row_sha256,
                    reason=str(exc),
                    as_of=as_of,
                    as_of_source=as_of_source,
                )
            )
            continue
        mappings.append(
            TrainingFeatureSnapshotMapping(
                sample_id=row.sample_id,
                source_label_row_sha256=row.source_row_sha256,
                candidate_id=row.candidate_id,
                batch_id=row.batch_id,
                symbol=row.symbol,
                observation_date=row.observation_date,
                observation_at=row.observation_at,
                as_of=as_of,
                as_of_source=as_of_source,
                feature_snapshot_id=snapshot.feature_snapshot_id,
                source_snapshot_ids=snapshot.source_snapshot_ids,
            )
        )

    label_bytes = serialize_training_label_dataset(ordered_labels).encode("utf-8")
    identity = {
        "feature_set_version_id": feature_set.feature_set_version_id,
        "failures": [failure.model_dump(mode="json") for failure in failures],
        "mappings": [mapping.model_dump(mode="json") for mapping in mappings],
        "purpose": ordered_labels[0].purpose.value,
        "source_adjustment_factor_snapshot_ids": source_groups.adjustment_factor_snapshot_ids,
        "source_benchmark_adjustment_factor_snapshot_ids": (
            source_groups.benchmark_adjustment_factor_snapshot_ids
        ),
        "source_benchmark_snapshot_ids": source_groups.benchmark_snapshot_ids,
        "source_label_dataset_sha256": sha256_bytes(label_bytes),
        "source_market_snapshot_ids": source_groups.market_snapshot_ids,
    }
    report_id = f"training-feature-snapshot-report-{sha256_json(identity)[:20]}"
    return TrainingFeatureSnapshotBuildReport(
        report_id=report_id,
        purpose=ordered_labels[0].purpose,
        label_contract_id=ordered_labels[0].label_contract_id,
        feature_set_version_id=feature_set.feature_set_version_id,
        source_label_dataset_sha256=sha256_bytes(label_bytes),
        source_label_row_count=len(ordered_labels),
        snapshot_count=len(mappings),
        failure_count=len(failures),
        source_market_snapshot_ids=source_groups.market_snapshot_ids,
        source_benchmark_snapshot_ids=source_groups.benchmark_snapshot_ids,
        source_adjustment_factor_snapshot_ids=source_groups.adjustment_factor_snapshot_ids,
        source_benchmark_adjustment_factor_snapshot_ids=(
            source_groups.benchmark_adjustment_factor_snapshot_ids
        ),
        source_snapshot_ids=source_snapshot_ids,
        mappings=tuple(mappings),
        failures=tuple(failures),
    )


def write_training_feature_snapshot_report(
    path: str | Path,
    report: TrainingFeatureSnapshotBuildReport,
) -> TrainingFeatureSnapshotBuildReport:
    """Write one report immutably, accepting only byte-identical replays."""

    destination = Path(path)
    content = (report.model_dump_json(indent=2) + "\n").encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with local_write_lock(destination.parent, name="training-feature-snapshot-reports"):
        if destination.exists():
            try:
                existing = destination.read_bytes()
            except OSError as exc:
                raise TrainingFeatureSnapshotError(
                    f"failed to read existing training feature snapshot report: {destination}"
                ) from exc
            if existing != content:
                raise TrainingFeatureSnapshotError(
                    "training feature snapshot report already exists with different content: "
                    f"{destination}"
                )
            return report
        temporary = destination.with_name(f".{destination.name}.tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(destination)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise TrainingFeatureSnapshotError(
                f"failed to write training feature snapshot report: {destination}"
            ) from exc
    return report


def read_training_feature_snapshot_report(
    path: str | Path,
) -> TrainingFeatureSnapshotBuildReport:
    """Read and validate one immutable feature snapshot report."""

    destination = Path(path)
    try:
        return TrainingFeatureSnapshotBuildReport.model_validate_json(
            destination.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise TrainingFeatureSnapshotError(
            f"training feature snapshot report is invalid: {destination}"
        ) from exc


def _validate_labels(
    labels: Sequence[SelectionTrainingLabelRow],
) -> tuple[SelectionTrainingLabelRow, ...]:
    if not labels:
        raise TrainingFeatureSnapshotError("training feature snapshot labels must not be empty")
    ordered = tuple(sorted(labels, key=lambda row: (row.observation_date, row.sample_id)))
    purposes = {row.purpose for row in ordered}
    if len(purposes) != 1:
        raise TrainingFeatureSnapshotError(
            "training feature snapshot build accepts one purpose per request"
        )
    sample_ids = tuple(row.sample_id for row in ordered)
    if len(sample_ids) != len(set(sample_ids)):
        raise TrainingFeatureSnapshotError(
            "training feature snapshot labels contain duplicate samples"
        )
    return ordered


class _SourceGroups:
    def __init__(
        self,
        *,
        market_snapshot_ids: tuple[str, ...],
        benchmark_snapshot_ids: tuple[str, ...],
        adjustment_factor_snapshot_ids: tuple[str, ...],
        benchmark_adjustment_factor_snapshot_ids: tuple[str, ...],
    ) -> None:
        self.market_snapshot_ids = market_snapshot_ids
        self.benchmark_snapshot_ids = benchmark_snapshot_ids
        self.adjustment_factor_snapshot_ids = adjustment_factor_snapshot_ids
        self.benchmark_adjustment_factor_snapshot_ids = benchmark_adjustment_factor_snapshot_ids


def _validate_source_ids(
    *,
    market_snapshot_ids: Sequence[str],
    benchmark_snapshot_ids: Sequence[str],
    adjustment_factor_snapshot_ids: Sequence[str],
    benchmark_adjustment_factor_snapshot_ids: Sequence[str],
) -> _SourceGroups:
    groups = {
        "market": _normalize_ids(market_snapshot_ids, "market_snapshot_ids", required=True),
        "benchmark": _normalize_ids(
            benchmark_snapshot_ids, "benchmark_snapshot_ids", required=False
        ),
        "adjustment": _normalize_ids(
            adjustment_factor_snapshot_ids,
            "adjustment_factor_snapshot_ids",
            required=False,
        ),
        "benchmark_adjustment": _normalize_ids(
            benchmark_adjustment_factor_snapshot_ids,
            "benchmark_adjustment_factor_snapshot_ids",
            required=False,
        ),
    }
    all_ids: list[str] = []
    for group_name, values in groups.items():
        for value in values:
            if value in all_ids:
                raise TrainingFeatureSnapshotError(
                    f"snapshot identifier is repeated across source groups: {value} ({group_name})"
                )
            all_ids.append(value)
    return _SourceGroups(
        market_snapshot_ids=groups["market"],
        benchmark_snapshot_ids=groups["benchmark"],
        adjustment_factor_snapshot_ids=groups["adjustment"],
        benchmark_adjustment_factor_snapshot_ids=groups["benchmark_adjustment"],
    )


def _normalize_ids(
    values: Sequence[str],
    name: str,
    *,
    required: bool,
) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in values)
    if required and not normalized:
        raise TrainingFeatureSnapshotError(f"{name} must not be empty")
    if any(not value for value in normalized):
        raise TrainingFeatureSnapshotError(f"{name} must not contain empty identifiers")
    if len(normalized) != len(set(normalized)):
        raise TrainingFeatureSnapshotError(f"{name} must not contain duplicate identifiers")
    return normalized


def _validate_as_of_cutoff(as_of_cutoff: datetime | None) -> None:
    if as_of_cutoff is not None and (
        as_of_cutoff.tzinfo is None or as_of_cutoff.utcoffset() is None
    ):
        raise TrainingFeatureSnapshotError("as_of_cutoff must include a timezone")


def _validate_benchmark_contract(
    feature_set: FeatureSetVersion,
    *,
    benchmark_snapshot_ids: tuple[str, ...],
    benchmark_symbol: str | None,
) -> str | None:
    requires_benchmark = any(
        definition.requires_benchmark for definition in feature_set.definitions
    )
    if requires_benchmark and not benchmark_snapshot_ids:
        raise TrainingFeatureSnapshotError(
            "feature set requires an explicit benchmark_snapshot_ids source"
        )
    if requires_benchmark and benchmark_symbol is None:
        raise TrainingFeatureSnapshotError("feature set requires a benchmark symbol")
    if benchmark_symbol is not None and not benchmark_symbol.strip():
        raise TrainingFeatureSnapshotError("benchmark_symbol must not be empty")
    return benchmark_symbol.strip() if benchmark_symbol is not None else None


def _load_bar_sources(
    store: SnapshotStore,
    snapshot_ids: Sequence[str],
    *,
    request_kind: str,
    source_name: str,
) -> tuple[tuple[SnapshotManifest, ...], tuple[DailyBar, ...]]:
    manifests: list[SnapshotManifest] = []
    bars: list[DailyBar] = []
    for snapshot_id in snapshot_ids:
        try:
            manifest = store.load_manifest(snapshot_id)
        except SnapshotStoreError as exc:
            raise TrainingFeatureSnapshotError(str(exc)) from exc
        if manifest.status is not SnapshotStatus.APPROVED:
            raise TrainingFeatureSnapshotError(
                f"{source_name} snapshot is not approved: {snapshot_id}"
            )
        if manifest.request_kind != request_kind:
            raise TrainingFeatureSnapshotError(
                f"{source_name} snapshot has request_kind {manifest.request_kind!r}, "
                f"expected {request_kind!r}: {snapshot_id}"
            )
        try:
            loaded = store.load_bars(snapshot_id)
        except SnapshotStoreError as exc:
            raise TrainingFeatureSnapshotError(str(exc)) from exc
        manifests.append(manifest)
        bars.extend(loaded)
    return tuple(manifests), tuple(bars)


def _validate_benchmark_bars(
    bars: Sequence[DailyBar],
    benchmark_symbol: str | None,
) -> None:
    if benchmark_symbol is None:
        return
    unexpected_symbols = sorted({bar.symbol for bar in bars if bar.symbol != benchmark_symbol})
    if unexpected_symbols:
        raise TrainingFeatureSnapshotError(
            "benchmark snapshots contain unexpected symbols: " + ", ".join(unexpected_symbols)
        )


def _load_factor_sources(
    store: SnapshotStore,
    snapshot_ids: Sequence[str],
    *,
    expected_daily_snapshot_ids: Sequence[str],
    source_name: str,
) -> tuple[tuple[SnapshotManifest, ...], tuple[AdjustmentFactor, ...]]:
    manifests: list[SnapshotManifest] = []
    factors: list[AdjustmentFactor] = []
    for snapshot_id in snapshot_ids:
        try:
            manifest = store.load_manifest(snapshot_id)
        except SnapshotStoreError as exc:
            raise TrainingFeatureSnapshotError(str(exc)) from exc
        if manifest.status is not SnapshotStatus.APPROVED:
            raise TrainingFeatureSnapshotError(
                f"{source_name} snapshot is not approved: {snapshot_id}"
            )
        if manifest.request_kind != "adjustment_factor":
            raise TrainingFeatureSnapshotError(
                f"{source_name} snapshot has request_kind {manifest.request_kind!r}, "
                f"expected 'adjustment_factor': {snapshot_id}"
            )
        if manifest.source_daily_snapshot_id not in expected_daily_snapshot_ids:
            raise TrainingFeatureSnapshotError(
                f"{source_name} snapshot has mismatched source_daily_snapshot_id: {snapshot_id}"
            )
        try:
            loaded = store.load_adjustment_factors(snapshot_id)
        except SnapshotStoreError as exc:
            raise TrainingFeatureSnapshotError(str(exc)) from exc
        manifests.append(manifest)
        factors.extend(loaded)
    return tuple(manifests), tuple(factors)


def _validate_label_selection_lineage(
    row: SelectionTrainingLabelRow,
    imported: TdxSelectionImport,
) -> None:
    candidates = imported.candidates
    candidate = next(
        (candidate for candidate in candidates if candidate.candidate_id == row.candidate_id),
        None,
    )
    if candidate is None:
        raise SelectionStoreError(f"label candidate does not exist in batch: {row.candidate_id}")
    if candidate.batch_id != row.batch_id or candidate.symbol != row.symbol:
        raise SelectionStoreError(
            "training label candidate lineage does not match selection import"
        )
    if row.purpose is TrainingDatasetPurpose.CANDIDATE_SUCCESS:
        if imported.batch.selection_date != row.observation_date:
            raise SelectionStoreError(
                "candidate success observation date does not match selection batch"
            )


def _resolve_as_of(
    row: SelectionTrainingLabelRow,
    batch_exported_at: datetime,
) -> tuple[datetime, TrainingFeatureSnapshotAsOfSource]:
    if row.observation_at is not None:
        return row.observation_at, "observation_at"
    if row.purpose is TrainingDatasetPurpose.CANDIDATE_SUCCESS:
        return batch_exported_at, "batch_exported_at"
    raise ValueError(
        f"{row.purpose.value} labels require observation_at for point-in-time features"
    )


def _bars_for_symbol(
    bars: Iterable[DailyBar],
    symbol: str,
    selection_date: date,
) -> tuple[DailyBar, ...]:
    return tuple(
        sorted(
            (bar for bar in bars if bar.symbol == symbol and bar.session_date < selection_date),
            key=lambda bar: bar.session_date,
        )
    )


def _factors_for_security(
    factors: Iterable[AdjustmentFactor],
    bars: Sequence[DailyBar],
) -> tuple[AdjustmentFactor, ...]:
    security_ids = {bar.security_id for bar in bars}
    symbols = {bar.symbol for bar in bars}
    return tuple(
        sorted(
            (
                factor
                for factor in factors
                if factor.security_id in security_ids and factor.symbol in symbols
            ),
            key=lambda factor: (factor.symbol, factor.session_date),
        )
    )


def _unique_ids(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


__all__ = [
    "TrainingFeatureSnapshotError",
    "build_training_feature_snapshots",
    "read_training_feature_snapshot_report",
    "write_training_feature_snapshot_report",
]
