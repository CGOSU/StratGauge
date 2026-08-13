"""Explicit daily-bar job for maturing imported selection candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from stratgauge.backtest.selection_outcomes import (
    SelectionPerformancePolicy,
    SelectionTrackingPolicy,
    evaluate_selection_outcome,
    evaluate_selection_performance,
    validate_selection_tracking_policy,
)
from stratgauge.data_pipeline.data_access import DataAccessPlan, build_data_access_plan
from stratgauge.data_pipeline.ingest import ingest
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_sources.base import (
    AdapterError,
    DailyBarRequest,
    MarketDataAdapter,
    RetryableAdapterError,
)
from stratgauge.domain.models import Security, canonical_symbol
from stratgauge.domain.selections import (
    SelectionOutcome,
    SelectionPerformance,
    TdxSelectionImport,
)


@dataclass(frozen=True)
class SelectionOutcomeJobResult:
    outcomes: tuple[SelectionOutcome, ...]
    rejected_candidates: tuple[tuple[str, str], ...]
    market_snapshot_ids: tuple[str, ...]
    performances: tuple[SelectionPerformance, ...] = ()
    retryable_rejections: tuple[tuple[str, str], ...] = ()
    access_plans: tuple[DataAccessPlan, ...] = ()


def run_selection_outcome_job(
    imported: TdxSelectionImport,
    *,
    adapter: MarketDataAdapter,
    selection_store: SelectionImportStore,
    market_store: SnapshotStore,
    evaluated_at: datetime,
    policy: SelectionTrackingPolicy,
    maturity_task_id: str | None = None,
    performance_policy: SelectionPerformancePolicy | None = None,
) -> SelectionOutcomeJobResult:
    """Fetch approved daily bars and persist only quality-safe outcomes.

    The adapter is injected so this job never silently chooses a provider. It is
    intentionally sequential because the current adapters are single-security APIs.
    When a task ID is supplied, a previously persisted candidate result is reused
    before touching the provider again.
    """

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must include a timezone")
    validate_selection_tracking_policy(policy)
    measurement_policy = performance_policy or SelectionPerformancePolicy()
    start_date = imported.batch.selection_date + timedelta(days=1)
    end_date = evaluated_at.date()
    if end_date < start_date:
        raise ValueError("evaluated_at must follow the selection date")

    outcomes: list[SelectionOutcome] = []
    rejected: list[tuple[str, str]] = []
    retryable_rejections: list[tuple[str, str]] = []
    market_snapshot_ids: list[str] = []
    performances: list[SelectionPerformance] = []
    access_plans: list[DataAccessPlan] = []
    for candidate in imported.candidates:
        if maturity_task_id is not None:
            existing = selection_store.find_outcome_for_task(
                maturity_task_id,
                candidate.candidate_id,
            )
            if existing is not None:
                outcomes.append(existing)
                existing_performance = selection_store.find_performance_for_task(
                    maturity_task_id,
                    candidate.candidate_id,
                )
                if existing_performance is not None:
                    performances.append(existing_performance)
                _append_unique(market_snapshot_ids, existing.source_snapshot_ids)
                continue

        security = Security(
            security_id=f"selection-{candidate.candidate_id}",
            local_code=candidate.local_code,
            symbol=canonical_symbol(candidate.local_code, candidate.exchange),
            exchange=candidate.exchange,
            name=candidate.name,
        )
        request = DailyBarRequest(
            security=security,
            start_date=start_date,
            end_date=end_date,
        )
        try:
            # One request per candidate; add a batch adapter when a provider supports it.
            ingested = ingest(
                adapter,
                request,
                market_store,
                expected_security_ids=[security.security_id],
            )
        except RetryableAdapterError as exc:
            reason = str(exc)
            rejected.append((candidate.candidate_id, reason))
            retryable_rejections.append((candidate.candidate_id, reason))
            continue
        except AdapterError as exc:
            rejected.append((candidate.candidate_id, str(exc)))
            continue
        except OSError as exc:
            # A protocol implementation may expose a transport error directly.
            reason = str(exc)
            rejected.append((candidate.candidate_id, reason))
            retryable_rejections.append((candidate.candidate_id, reason))
            continue
        access_plans.append(ingested.access_plan)
        if not ingested.quality.approved:
            rejected.append((candidate.candidate_id, "market_snapshot_quality_rejected"))
            continue

        _append_unique(market_snapshot_ids, (ingested.manifest.snapshot_id,))
        outcome = evaluate_selection_outcome(
            candidate,
            selection_date=imported.batch.selection_date,
            bars=ingested.bars,
            evaluated_at=evaluated_at,
            policy=policy,
            source_snapshot_ids=[ingested.manifest.snapshot_id],
        )
        if maturity_task_id is not None:
            outcome = outcome.model_copy(update={"maturity_task_id": maturity_task_id})
        persisted = selection_store.write_outcome(outcome)
        outcomes.append(persisted)
        performance = evaluate_selection_performance(
            candidate,
            selection_date=imported.batch.selection_date,
            bars=ingested.bars,
            evaluated_at=evaluated_at,
            policy=measurement_policy,
            source_snapshot_ids=[ingested.manifest.snapshot_id],
            maturity_task_id=maturity_task_id,
        )
        performances.append(selection_store.write_performance(performance))

    return SelectionOutcomeJobResult(
        outcomes=tuple(outcomes),
        performances=tuple(performances),
        rejected_candidates=tuple(rejected),
        market_snapshot_ids=tuple(market_snapshot_ids),
        retryable_rejections=tuple(retryable_rejections),
        access_plans=tuple(access_plans),
    )


def run_selection_outcome_snapshot_job(
    imported: TdxSelectionImport,
    *,
    market_store: SnapshotStore,
    market_snapshot_ids: tuple[str, ...],
    evaluated_at: datetime,
    policy: SelectionTrackingPolicy,
    selection_store: SelectionImportStore,
    maturity_task_id: str | None = None,
    performance_policy: SelectionPerformancePolicy | None = None,
    provider: str | None = None,
    universe_snapshot_id: str | None = None,
) -> SelectionOutcomeJobResult:
    """Replay approved full-market snapshots without contacting a provider."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must include a timezone")
    validate_selection_tracking_policy(policy)
    measurement_policy = performance_policy or SelectionPerformancePolicy()
    if not market_snapshot_ids:
        raise ValueError("snapshot-backed maturity requires market snapshots")
    manifests = tuple(
        market_store.load_manifest(snapshot_id) for snapshot_id in market_snapshot_ids
    )
    if len({manifest.start_date for manifest in manifests}) != len(manifests):
        raise ValueError("market snapshots must contain unique sessions")
    for manifest in manifests:
        if (
            manifest.status is not SnapshotStatus.APPROVED
            or manifest.request_kind != "market_daily"
            or manifest.start_date != manifest.end_date
            or manifest.created_at > evaluated_at
            or (provider is not None and manifest.provider != provider)
            or (
                universe_snapshot_id is not None
                and manifest.universe_snapshot_id != universe_snapshot_id
            )
        ):
            raise ValueError(
                "snapshot-backed maturity requires approved available market snapshots"
            )
    ordered_manifests = tuple(
        sorted(manifests, key=lambda item: (item.start_date, item.snapshot_id))
    )
    bars = tuple(
        bar
        for manifest in ordered_manifests
        for bar in market_store.load_bars(manifest.snapshot_id)
    )
    source_snapshot_ids = tuple(manifest.snapshot_id for manifest in ordered_manifests)
    access_plans = tuple(
        build_data_access_plan(
            request_kind=manifest.request_kind,
            provider=manifest.provider,
            cache_key=f"snapshot:{manifest.snapshot_id}",
            local_snapshot_id=manifest.snapshot_id,
            remote_allowed=False,
        )
        for manifest in ordered_manifests
    )
    outcomes: list[SelectionOutcome] = []
    performances: list[SelectionPerformance] = []
    rejected: list[tuple[str, str]] = []
    for candidate in imported.candidates:
        if maturity_task_id is not None:
            existing = selection_store.find_outcome_for_task(
                maturity_task_id,
                candidate.candidate_id,
            )
            if existing is not None:
                outcomes.append(existing)
                existing_performance = selection_store.find_performance_for_task(
                    maturity_task_id,
                    candidate.candidate_id,
                )
                if existing_performance is not None:
                    performances.append(existing_performance)
                continue
        outcome = evaluate_selection_outcome(
            candidate,
            selection_date=imported.batch.selection_date,
            bars=bars,
            evaluated_at=evaluated_at,
            policy=policy,
            source_snapshot_ids=source_snapshot_ids,
        )
        if maturity_task_id is not None:
            outcome = outcome.model_copy(update={"maturity_task_id": maturity_task_id})
        outcomes.append(selection_store.write_outcome(outcome))
        performance = evaluate_selection_performance(
            candidate,
            selection_date=imported.batch.selection_date,
            bars=bars,
            evaluated_at=evaluated_at,
            policy=measurement_policy,
            source_snapshot_ids=source_snapshot_ids,
            maturity_task_id=maturity_task_id,
        )
        performances.append(selection_store.write_performance(performance))
    return SelectionOutcomeJobResult(
        outcomes=tuple(outcomes),
        performances=tuple(performances),
        rejected_candidates=tuple(rejected),
        market_snapshot_ids=source_snapshot_ids,
        access_plans=access_plans,
    )


def _append_unique(destination: list[str], values: tuple[str, ...]) -> None:
    for value in values:
        if value not in destination:
            destination.append(value)
