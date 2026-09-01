"""Explicit, quality-gated market evidence preparation for selection ML."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.backtest.selection_dataset import order_selection_batch_ids
from stratgauge.data_pipeline.adjustment_factor_sync import (
    AdjustmentFactorBackfillReport,
    AdjustmentFactorSyncError,
    sync_adjustment_factor_sessions,
)
from stratgauge.data_pipeline.calendar_cache import (
    CalendarCacheError,
    CalendarCacheResult,
    TradeCalendarCache,
)
from stratgauge.data_pipeline.data_access import DataAccessDecision, DataAccessPlan
from stratgauge.data_pipeline.ingest import IngestResult, ingest
from stratgauge.data_pipeline.market_daily_sync import (
    MarketDailySyncError,
    MarketSessionSyncResult,
    sync_market_session,
)
from stratgauge.data_pipeline.quality import QualityReport
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore, SnapshotStoreError
from stratgauge.data_pipeline.universe import UniverseSnapshot, UniverseSnapshotStore
from stratgauge.data_sources.base import (
    INDEX_DAILY_BENCHMARK_SECURITY_ID,
    INDEX_DAILY_BENCHMARK_SYMBOL,
    AdapterError,
    AdjustmentFactorDataAdapter,
    IndexDailyRequest,
    MarketDailyDataAdapter,
    MarketDataAdapter,
)
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.data_sources.tushare_reference import (
    TushareReferenceClient,
    TushareReferenceStore,
    TushareReferenceStoreError,
)
from stratgauge.domain.models import DomainModel, Exchange, Security

SELECTION_ML_MARKET_EVIDENCE_REPORT_VERSION = "selection-ml-market-evidence-v1"


class SelectionMLMarketEvidenceRequest(DomainModel):
    """Explicit scope and policy for one manually invoked evidence preparation."""

    batch_ids: tuple[str, ...]
    universe_snapshot_id: str
    lookback_sessions: int = Field(default=20, gt=0)
    maturity_horizon_sessions: int = Field(default=10, gt=0)
    calendar_exchange: str = "SSE"
    minimum_security_coverage: Decimal = Field(default=Decimal("0.90"), ge=0, le=1)
    now: datetime

    @field_validator("batch_ids")
    @classmethod
    def validate_batch_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if not normalized or any(not item for item in normalized):
            raise ValueError("market evidence request requires batch IDs")
        if len(set(normalized)) != len(normalized):
            raise ValueError("market evidence request batch IDs must be unique")
        return normalized

    @field_validator("universe_snapshot_id")
    @classmethod
    def validate_universe_snapshot_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("market evidence request requires a universe snapshot ID")
        return normalized

    @field_validator("calendar_exchange")
    @classmethod
    def normalize_calendar_exchange(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("calendar_exchange must not be empty")
        return normalized

    @field_validator("now")
    @classmethod
    def validate_now(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("market evidence request now must include a timezone")
        return value.astimezone(UTC)


class EvidenceSessionFailure(DomainModel):
    """One requested session that could not be synchronized."""

    session_date: date
    error: str

    @field_validator("error")
    @classmethod
    def validate_error(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("evidence failure error must not be empty")
        return normalized


EvidenceStageStatus = Literal["not_run", "completed", "partial", "blocked", "failed"]


class CalendarEvidenceReport(DomainModel):
    """Auditable calendar-cache stage result."""

    stage: Literal["calendar"] = "calendar"
    status: EvidenceStageStatus
    exchange: str
    cache_path: str
    requested_start_date: date
    requested_end_date: date
    covered_start_date: date | None = None
    covered_end_date: date | None = None
    session_count: int = Field(ge=0)
    sessions: tuple[date, ...] = ()
    fetched_ranges: tuple[tuple[date, date], ...] = ()
    source_snapshot_ids: tuple[str, ...] = ()
    source_providers: tuple[str, ...] = ()
    skipped: bool = False
    error: str | None = None
    blockers: tuple[str, ...] = ()


class MarketDailyEvidenceReport(DomainModel):
    """Per-session full-market synchronization result with failures preserved."""

    stage: Literal["market_daily"] = "market_daily"
    status: EvidenceStageStatus
    provider: str
    requested_sessions: tuple[date, ...]
    results: tuple[MarketSessionSyncResult, ...] = ()
    failures: tuple[EvidenceSessionFailure, ...] = ()
    approved_snapshot_ids: tuple[str, ...] = ()
    rejected_snapshot_ids: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


class AdjustmentFactorEvidenceReport(DomainModel):
    """Same-provider adjustment-factor stage result."""

    stage: Literal["adjustment_factor"] = "adjustment_factor"
    status: EvidenceStageStatus
    provider: str
    requested_sessions: tuple[date, ...]
    report: AdjustmentFactorBackfillReport | None = None
    approved_snapshot_ids: tuple[str, ...] = ()
    rejected_snapshot_ids: tuple[str, ...] = ()
    missing_daily_sessions: tuple[date, ...] = ()
    fetch_failed_sessions: tuple[date, ...] = ()
    error: str | None = None
    blockers: tuple[str, ...] = ()


class BenchmarkIndexEvidenceReport(DomainModel):
    """Independent ``index_daily`` benchmark stage result."""

    stage: Literal["benchmark_index_daily"] = "benchmark_index_daily"
    request_kind: Literal["index_daily"] = "index_daily"
    status: EvidenceStageStatus
    provider: str
    symbol: str = INDEX_DAILY_BENCHMARK_SYMBOL
    security_id: str = INDEX_DAILY_BENCHMARK_SECURITY_ID
    requested_sessions: tuple[date, ...]
    snapshot_id: str | None = None
    snapshot_status: SnapshotStatus | None = None
    quality: QualityReport | None = None
    access_plan: DataAccessPlan | None = None
    skipped: bool = False
    error: str | None = None
    blockers: tuple[str, ...] = ()


class PointInTimeEvidenceReport(DomainModel):
    """Explicitly states that fetching evidence did not prove historical visibility."""

    status: Literal["not_guaranteed"] = "not_guaranteed"
    feature_visibility_guaranteed: bool = False
    warnings: tuple[str, ...] = (
        "market evidence preparation does not establish historical feature visibility; "
        "the feature snapshot stage must revalidate available_at against each observation",
    )


class SelectionMLMarketEvidenceReport(DomainModel):
    """Complete structured report for the four evidence-preparation stages."""

    report_version: str = SELECTION_ML_MARKET_EVIDENCE_REPORT_VERSION
    provider: str
    batch_ids: tuple[str, ...]
    selection_date_from: date
    selection_date_to: date
    universe_snapshot_id: str
    universe_as_of: datetime
    lookback_sessions: int = Field(gt=0)
    maturity_horizon_sessions: int = Field(gt=0)
    required_history_sessions: tuple[date, ...] = ()
    required_maturity_sessions: tuple[date, ...] = ()
    required_sessions: tuple[date, ...] = ()
    universe_invalid_sessions: tuple[date, ...] = ()
    calendar: CalendarEvidenceReport
    market_daily: MarketDailyEvidenceReport
    adjustment_factors: AdjustmentFactorEvidenceReport
    benchmark_index_daily: BenchmarkIndexEvidenceReport
    point_in_time: PointInTimeEvidenceReport
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    ready: bool = False

    @field_validator("universe_as_of")
    @classmethod
    def validate_universe_as_of(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("universe_as_of must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_batch_scope(self) -> SelectionMLMarketEvidenceReport:
        if not self.batch_ids or len(set(self.batch_ids)) != len(self.batch_ids):
            raise ValueError("market evidence report batch IDs must be unique and non-empty")
        if self.selection_date_to < self.selection_date_from:
            raise ValueError("market evidence report selection date range is invalid")
        if (
            self.required_sessions
            and tuple(sorted(self.required_sessions)) != self.required_sessions
        ):
            raise ValueError("market evidence report required sessions must be sorted")
        return self


@dataclass(frozen=True)
class _SessionPlan:
    calendar_result: CalendarCacheResult
    history_sessions: tuple[date, ...]
    maturity_sessions: tuple[date, ...]
    required_sessions: tuple[date, ...]


def resolve_selection_batch_ids(
    store: SelectionImportStore,
    *,
    batch_ids: Sequence[str] | None = None,
    all_batches: bool = False,
    selection_date_from: date | None = None,
    selection_date_to: date | None = None,
) -> tuple[str, ...]:
    """Resolve one explicit batch scope, always including archived research evidence."""

    if batch_ids is not None and (
        all_batches or selection_date_from is not None or selection_date_to is not None
    ):
        raise ValueError("batch IDs cannot be combined with all-batches or date-range selection")
    if all_batches and (selection_date_from is not None or selection_date_to is not None):
        raise ValueError("all-batches cannot be combined with a date range")
    if selection_date_from is not None or selection_date_to is not None:
        if selection_date_from is None or selection_date_to is None:
            raise ValueError("date-range selection requires both dates")
        if selection_date_to < selection_date_from:
            raise ValueError("selection date range is invalid")
    if batch_ids is None and not all_batches and selection_date_from is None:
        raise ValueError("one selection batch scope is required")

    if batch_ids is not None:
        return order_selection_batch_ids(store, batch_ids)

    manifests = store.list_manifests(include_archived=True)
    if all_batches:
        candidates = tuple(manifest.batch_id for manifest in manifests)
    else:
        assert selection_date_from is not None
        assert selection_date_to is not None
        candidates = tuple(
            manifest.batch_id
            for manifest in manifests
            if selection_date_from
            <= store.load_import(manifest.batch_id).batch.selection_date
            <= selection_date_to
        )
    if not candidates:
        raise ValueError("no selection batches match the requested scope")
    return order_selection_batch_ids(store, candidates)


def prepare_selection_ml_market_evidence(
    selection_store: SelectionImportStore,
    universe_store: UniverseSnapshotStore,
    market_store: SnapshotStore,
    *,
    request: SelectionMLMarketEvidenceRequest,
    calendar_path: str | Path,
    calendar_reference_root: str | Path,
    calendar_client: TushareReferenceClient | None = None,
    calendar_reference_store: TushareReferenceStore | None = None,
    market_adapter: MarketDailyDataAdapter | None = None,
    adjustment_factor_adapter: AdjustmentFactorDataAdapter | None = None,
    benchmark_adapter: MarketDataAdapter | None = None,
) -> SelectionMLMarketEvidenceReport:
    """Prepare calendar, market, factor, and benchmark evidence without research side effects."""

    ordered_batch_ids = order_selection_batch_ids(selection_store, request.batch_ids)
    imports = tuple(selection_store.load_import(batch_id) for batch_id in ordered_batch_ids)
    selection_dates = tuple(imported.batch.selection_date for imported in imports)
    selection_date_from = min(selection_dates)
    selection_date_to = max(selection_dates)
    universe = universe_store.load(request.universe_snapshot_id)
    provider = "tushare"
    point_in_time = PointInTimeEvidenceReport()

    cache = TradeCalendarCache(calendar_path)
    client = calendar_client or TushareReferenceClient()
    references = calendar_reference_store or TushareReferenceStore(calendar_reference_root)
    try:
        calendar_stage, plan = _prepare_calendar(
            cache=cache,
            client=client,
            references=references,
            request=request,
            selection_date_from=selection_date_from,
            selection_date_to=selection_date_to,
        )
    except (
        AdapterError,
        CalendarCacheError,
        OSError,
        TushareReferenceStoreError,
        ValueError,
    ) as exc:
        requested_start, requested_end = _calendar_request_range(
            request,
            selection_date_from,
            selection_date_to,
        )
        calendar_stage = CalendarEvidenceReport(
            status="failed",
            exchange=request.calendar_exchange,
            cache_path=str(cache.output_path),
            requested_start_date=requested_start,
            requested_end_date=requested_end,
            session_count=0,
            error=str(exc),
            blockers=(f"calendar preparation failed: {exc}",),
        )
        plan = None
    if plan is None:
        return _build_report(
            request=request,
            batch_ids=ordered_batch_ids,
            selection_date_from=selection_date_from,
            selection_date_to=selection_date_to,
            universe=universe,
            calendar=calendar_stage,
            required_history_sessions=(),
            required_maturity_sessions=(),
            required_sessions=(),
            market_daily=_not_run_market_stage(provider, (), calendar_stage.blockers),
            adjustment_factors=_not_run_factor_stage(provider, (), calendar_stage.blockers),
            benchmark_index_daily=_not_run_index_stage(provider, (), calendar_stage.blockers),
            point_in_time=point_in_time,
            provider=provider,
        )

    universe_invalid_sessions = tuple(
        session for session in plan.required_sessions if universe.as_of.date() > session
    )
    if universe_invalid_sessions:
        blockers = (
            "universe snapshot was created after required market sessions: "
            + ", ".join(session.isoformat() for session in universe_invalid_sessions),
        )
        return _build_report(
            request=request,
            batch_ids=ordered_batch_ids,
            selection_date_from=selection_date_from,
            selection_date_to=selection_date_to,
            universe=universe,
            calendar=calendar_stage,
            required_history_sessions=plan.history_sessions,
            required_maturity_sessions=plan.maturity_sessions,
            required_sessions=plan.required_sessions,
            market_daily=_not_run_market_stage(
                provider,
                plan.required_sessions,
                blockers,
                status="blocked",
            ),
            adjustment_factors=_not_run_factor_stage(
                provider,
                plan.required_sessions,
                blockers,
                status="blocked",
            ),
            benchmark_index_daily=_not_run_index_stage(
                provider,
                plan.required_sessions,
                blockers,
                status="blocked",
            ),
            point_in_time=point_in_time,
            universe_invalid_sessions=universe_invalid_sessions,
            provider=provider,
        )

    default_adapter: TushareAdapter | None = None
    if market_adapter is None or adjustment_factor_adapter is None or benchmark_adapter is None:
        default_adapter = TushareAdapter()
        market_adapter = market_adapter or default_adapter
        adjustment_factor_adapter = adjustment_factor_adapter or default_adapter
        benchmark_adapter = benchmark_adapter or default_adapter

    try:
        assert market_adapter is not None
        assert adjustment_factor_adapter is not None
        assert benchmark_adapter is not None
        market_stage = _run_market_stage(
            adapter=market_adapter,
            session_dates=plan.required_sessions,
            universe=universe,
            store=market_store,
            minimum_security_coverage=request.minimum_security_coverage,
        )
        if adjustment_factor_adapter.provider != market_adapter.provider:
            factor_stage = _not_run_factor_stage(
                adjustment_factor_adapter.provider,
                plan.required_sessions,
                ("adjustment-factor provider must match market_daily provider",),
                status="blocked",
            )
        else:
            factor_stage = _run_factor_stage(
                adapter=adjustment_factor_adapter,
                session_dates=plan.required_sessions,
                universe=universe,
                store=market_store,
                minimum_security_coverage=request.minimum_security_coverage,
            )
        index_stage = _run_index_stage(
            adapter=benchmark_adapter,
            session_dates=plan.required_sessions,
            store=market_store,
        )
    finally:
        if default_adapter is not None:
            default_adapter.close()

    return _build_report(
        request=request,
        batch_ids=ordered_batch_ids,
        selection_date_from=selection_date_from,
        selection_date_to=selection_date_to,
        universe=universe,
        calendar=calendar_stage,
        required_history_sessions=plan.history_sessions,
        required_maturity_sessions=plan.maturity_sessions,
        required_sessions=plan.required_sessions,
        market_daily=market_stage,
        adjustment_factors=factor_stage,
        benchmark_index_daily=index_stage,
        point_in_time=point_in_time,
        provider=market_stage.provider,
    )


def write_selection_ml_market_evidence_report(
    path: str | Path,
    report: SelectionMLMarketEvidenceReport,
) -> None:
    """Write one report immutably, accepting an identical replay only."""

    destination = Path(path)
    content = (report.model_dump_json(indent=2) + "\n").encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != content:
            raise FileExistsError(
                f"evidence report already exists with different content: {destination}"
            )
        return
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _calendar_request_range(
    request: SelectionMLMarketEvidenceRequest,
    selection_date_from: date,
    selection_date_to: date,
) -> tuple[date, date]:
    history_padding_days = max(request.lookback_sessions * 5, 30)
    maturity_padding_days = max(request.maturity_horizon_sessions * 5, 30)
    return (
        selection_date_from - timedelta(days=history_padding_days),
        selection_date_to + timedelta(days=maturity_padding_days),
    )


def _prepare_calendar(
    *,
    cache: TradeCalendarCache,
    client: TushareReferenceClient,
    references: TushareReferenceStore,
    request: SelectionMLMarketEvidenceRequest,
    selection_date_from: date,
    selection_date_to: date,
) -> tuple[CalendarEvidenceReport, _SessionPlan | None]:
    start_date, end_date = _calendar_request_range(
        request,
        selection_date_from,
        selection_date_to,
    )
    history_padding_days = max(request.lookback_sessions * 5, 30)
    maturity_padding_days = max(request.maturity_horizon_sessions * 5, 30)
    last_result: CalendarCacheResult | None = None
    for _ in range(8):
        last_result = cache.ensure(
            start_date=start_date,
            end_date=end_date,
            exchange=request.calendar_exchange,
            client=client,
            reference_store=references,
            now=request.now,
        )
        sessions = last_result.sessions
        history_sessions = tuple(session for session in sessions if session < selection_date_from)
        maturity_sessions = tuple(session for session in sessions if session > selection_date_to)
        if (
            len(history_sessions) >= request.lookback_sessions
            and len(maturity_sessions) >= request.maturity_horizon_sessions
        ):
            selected_history = history_sessions[-request.lookback_sessions :]
            selected_maturity = maturity_sessions[: request.maturity_horizon_sessions]
            required_start = selected_history[0]
            required_end = selected_maturity[-1]
            required_sessions = tuple(
                session for session in sessions if required_start <= session <= required_end
            )
            return (
                _calendar_report(
                    cache=cache,
                    result=last_result,
                    requested_start_date=start_date,
                    requested_end_date=end_date,
                    status="completed",
                ),
                _SessionPlan(
                    calendar_result=last_result,
                    history_sessions=selected_history,
                    maturity_sessions=selected_maturity,
                    required_sessions=required_sessions,
                ),
            )
        if len(history_sessions) < request.lookback_sessions:
            start_date -= timedelta(days=history_padding_days)
        if len(maturity_sessions) < request.maturity_horizon_sessions:
            end_date += timedelta(days=maturity_padding_days)

    blockers = (
        "calendar does not cover the requested history and maturity windows: "
        f"history={len(history_sessions)}/{request.lookback_sessions}, "
        f"maturity={len(maturity_sessions)}/{request.maturity_horizon_sessions}",
    )
    assert last_result is not None
    return (
        _calendar_report(
            cache=cache,
            result=last_result,
            requested_start_date=start_date,
            requested_end_date=end_date,
            status="blocked",
            blockers=blockers,
        ),
        None,
    )


def _calendar_report(
    *,
    cache: TradeCalendarCache,
    result: CalendarCacheResult,
    requested_start_date: date,
    requested_end_date: date,
    status: EvidenceStageStatus,
    blockers: tuple[str, ...] = (),
    error: str | None = None,
) -> CalendarEvidenceReport:
    manifest = result.manifest
    return CalendarEvidenceReport(
        status=status,
        exchange=manifest.exchange,
        cache_path=str(cache.output_path),
        requested_start_date=requested_start_date,
        requested_end_date=requested_end_date,
        covered_start_date=manifest.start_date,
        covered_end_date=manifest.end_date,
        session_count=len(result.sessions),
        sessions=result.sessions,
        fetched_ranges=result.fetched_ranges,
        source_snapshot_ids=manifest.source_snapshot_ids,
        source_providers=manifest.source_providers,
        skipped=result.skipped,
        error=error,
        blockers=blockers,
    )


def _run_market_stage(
    *,
    adapter: MarketDailyDataAdapter,
    session_dates: tuple[date, ...],
    universe: UniverseSnapshot,
    store: SnapshotStore,
    minimum_security_coverage: Decimal,
) -> MarketDailyEvidenceReport:
    results: list[MarketSessionSyncResult] = []
    failures: list[EvidenceSessionFailure] = []
    for session_date in session_dates:
        try:
            results.append(
                sync_market_session(
                    adapter=adapter,
                    session_date=session_date,
                    universe=universe,
                    store=store,
                    minimum_security_coverage=minimum_security_coverage,
                )
            )
        except (AdapterError, MarketDailySyncError, SnapshotStoreError, ValueError) as exc:
            failures.append(EvidenceSessionFailure(session_date=session_date, error=str(exc)))
    approved = tuple(
        result.snapshot_id for result in results if result.status is SnapshotStatus.APPROVED
    )
    rejected = tuple(
        result.snapshot_id for result in results if result.status is SnapshotStatus.REJECTED
    )
    blockers = tuple(
        [
            *(f"rejected market_daily snapshot: {snapshot_id}" for snapshot_id in rejected),
            *(
                f"market_daily sync failed for {failure.session_date}: {failure.error}"
                for failure in failures
            ),
        ]
    )
    status: EvidenceStageStatus = (
        "completed" if not blockers else "failed" if not results else "partial"
    )
    return MarketDailyEvidenceReport(
        status=status,
        provider=adapter.provider,
        requested_sessions=session_dates,
        results=tuple(results),
        failures=tuple(failures),
        approved_snapshot_ids=approved,
        rejected_snapshot_ids=rejected,
        blockers=blockers,
    )


def _run_factor_stage(
    *,
    adapter: AdjustmentFactorDataAdapter,
    session_dates: tuple[date, ...],
    universe: UniverseSnapshot,
    store: SnapshotStore,
    minimum_security_coverage: Decimal,
) -> AdjustmentFactorEvidenceReport:
    try:
        report = sync_adjustment_factor_sessions(
            adapter=adapter,
            session_dates=session_dates,
            universe=universe,
            store=store,
            minimum_security_coverage=minimum_security_coverage,
        )
    except (AdapterError, AdjustmentFactorSyncError, SnapshotStoreError, ValueError) as exc:
        return AdjustmentFactorEvidenceReport(
            status="failed",
            provider=adapter.provider,
            requested_sessions=session_dates,
            error=str(exc),
            blockers=(f"adjustment-factor sync failed: {exc}",),
        )
    status: EvidenceStageStatus = "completed" if report.ready else "partial"
    return AdjustmentFactorEvidenceReport(
        status=status,
        provider=report.provider,
        requested_sessions=session_dates,
        report=report,
        approved_snapshot_ids=report.approved_snapshot_ids,
        rejected_snapshot_ids=report.rejected_snapshot_ids,
        missing_daily_sessions=report.missing_daily_sessions,
        fetch_failed_sessions=report.fetch_failed_sessions,
        blockers=report.blockers,
    )


def _run_index_stage(
    *,
    adapter: MarketDataAdapter,
    session_dates: tuple[date, ...],
    store: SnapshotStore,
) -> BenchmarkIndexEvidenceReport:
    provider = adapter.provider
    request = IndexDailyRequest(
        security=_benchmark_security(),
        start_date=session_dates[0],
        end_date=session_dates[-1],
    )
    try:
        result = _ingest_index(adapter, request, store, session_dates)
    except (AdapterError, SnapshotStoreError, ValueError) as exc:
        return BenchmarkIndexEvidenceReport(
            status="failed",
            provider=provider,
            requested_sessions=session_dates,
            error=str(exc),
            blockers=(f"benchmark index_daily sync failed: {exc}",),
        )
    blockers = tuple(
        f"benchmark index_daily quality error: {issue.code}: {issue.message}"
        for issue in result.quality.errors
    )
    return BenchmarkIndexEvidenceReport(
        status="completed" if result.quality.approved else "partial",
        provider=result.manifest.provider,
        requested_sessions=session_dates,
        snapshot_id=result.manifest.snapshot_id,
        snapshot_status=result.manifest.status,
        quality=result.quality,
        access_plan=result.access_plan,
        skipped=result.access_plan.decision is DataAccessDecision.CACHE_HIT,
        blockers=blockers,
    )


def _ingest_index(
    adapter: MarketDataAdapter,
    request: IndexDailyRequest,
    store: SnapshotStore,
    session_dates: tuple[date, ...],
) -> IngestResult:
    return ingest(
        adapter,
        request,
        store,
        expected_sessions=session_dates,
        expected_security_ids=(INDEX_DAILY_BENCHMARK_SECURITY_ID,),
    )


def _benchmark_security() -> Security:
    return Security(
        security_id=INDEX_DAILY_BENCHMARK_SECURITY_ID,
        local_code=INDEX_DAILY_BENCHMARK_SYMBOL.split(".", 1)[0],
        symbol=INDEX_DAILY_BENCHMARK_SYMBOL,
        exchange=Exchange.SH,
        name="沪深300指数",
    )


def _not_run_market_stage(
    provider: str,
    session_dates: tuple[date, ...],
    blockers: tuple[str, ...],
    *,
    status: EvidenceStageStatus = "not_run",
) -> MarketDailyEvidenceReport:
    return MarketDailyEvidenceReport(
        status=status,
        provider=provider,
        requested_sessions=session_dates,
        blockers=blockers,
    )


def _not_run_factor_stage(
    provider: str,
    session_dates: tuple[date, ...],
    blockers: tuple[str, ...],
    *,
    status: EvidenceStageStatus = "not_run",
) -> AdjustmentFactorEvidenceReport:
    return AdjustmentFactorEvidenceReport(
        status=status,
        provider=provider,
        requested_sessions=session_dates,
        blockers=blockers,
    )


def _not_run_index_stage(
    provider: str,
    session_dates: tuple[date, ...],
    blockers: tuple[str, ...],
    *,
    status: EvidenceStageStatus = "not_run",
) -> BenchmarkIndexEvidenceReport:
    return BenchmarkIndexEvidenceReport(
        status=status,
        provider=provider,
        requested_sessions=session_dates,
        blockers=blockers,
    )


def _build_report(
    *,
    request: SelectionMLMarketEvidenceRequest,
    provider: str,
    batch_ids: tuple[str, ...],
    selection_date_from: date,
    selection_date_to: date,
    universe: UniverseSnapshot,
    calendar: CalendarEvidenceReport,
    required_history_sessions: tuple[date, ...],
    required_maturity_sessions: tuple[date, ...],
    required_sessions: tuple[date, ...],
    market_daily: MarketDailyEvidenceReport,
    adjustment_factors: AdjustmentFactorEvidenceReport,
    benchmark_index_daily: BenchmarkIndexEvidenceReport,
    point_in_time: PointInTimeEvidenceReport,
    universe_invalid_sessions: tuple[date, ...] = (),
) -> SelectionMLMarketEvidenceReport:
    blockers = tuple(
        dict.fromkeys(
            (
                *calendar.blockers,
                *market_daily.blockers,
                *adjustment_factors.blockers,
                *benchmark_index_daily.blockers,
            )
        )
    )
    warnings = point_in_time.warnings
    ready = (
        not blockers
        and calendar.status == "completed"
        and market_daily.status == "completed"
        and adjustment_factors.status == "completed"
        and benchmark_index_daily.status == "completed"
    )
    return SelectionMLMarketEvidenceReport(
        provider=provider,
        batch_ids=batch_ids,
        selection_date_from=selection_date_from,
        selection_date_to=selection_date_to,
        universe_snapshot_id=request.universe_snapshot_id,
        universe_as_of=universe.as_of,
        lookback_sessions=request.lookback_sessions,
        maturity_horizon_sessions=request.maturity_horizon_sessions,
        required_history_sessions=required_history_sessions,
        required_maturity_sessions=required_maturity_sessions,
        required_sessions=required_sessions,
        universe_invalid_sessions=universe_invalid_sessions,
        calendar=calendar,
        market_daily=market_daily,
        adjustment_factors=adjustment_factors,
        benchmark_index_daily=benchmark_index_daily,
        point_in_time=point_in_time,
        blockers=blockers,
        warnings=warnings,
        ready=ready,
    )


__all__ = [
    "BenchmarkIndexEvidenceReport",
    "CalendarEvidenceReport",
    "EvidenceSessionFailure",
    "MarketDailyEvidenceReport",
    "AdjustmentFactorEvidenceReport",
    "PointInTimeEvidenceReport",
    "SELECTION_ML_MARKET_EVIDENCE_REPORT_VERSION",
    "SelectionMLMarketEvidenceReport",
    "SelectionMLMarketEvidenceRequest",
    "prepare_selection_ml_market_evidence",
    "resolve_selection_batch_ids",
    "write_selection_ml_market_evidence_report",
]
