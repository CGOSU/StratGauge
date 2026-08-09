"""Explicit daily-bar job for maturing imported selection candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from stratgauge.backtest.selection_outcomes import (
    SelectionTrackingPolicy,
    evaluate_selection_outcome,
    validate_selection_tracking_policy,
)
from stratgauge.data_pipeline.ingest import ingest
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_sources.base import AdapterError, DailyBarRequest, MarketDataAdapter
from stratgauge.domain.models import Security, canonical_symbol
from stratgauge.domain.selections import SelectionOutcome, TdxSelectionImport


@dataclass(frozen=True)
class SelectionOutcomeJobResult:
    outcomes: tuple[SelectionOutcome, ...]
    rejected_candidates: tuple[tuple[str, str], ...]
    market_snapshot_ids: tuple[str, ...]


def run_selection_outcome_job(
    imported: TdxSelectionImport,
    *,
    adapter: MarketDataAdapter,
    selection_store: SelectionImportStore,
    market_store: SnapshotStore,
    evaluated_at: datetime,
    policy: SelectionTrackingPolicy,
) -> SelectionOutcomeJobResult:
    """Fetch approved daily bars and persist only quality-safe outcomes.

    The adapter is injected so this job never silently chooses a provider. It is
    intentionally sequential because the current adapters are single-security APIs.
    """

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must include a timezone")
    validate_selection_tracking_policy(policy)
    start_date = imported.batch.selection_date + timedelta(days=1)
    end_date = evaluated_at.date()
    if end_date < start_date:
        raise ValueError("evaluated_at must follow the selection date")

    outcomes: list[SelectionOutcome] = []
    rejected: list[tuple[str, str]] = []
    market_snapshot_ids: list[str] = []
    for candidate in imported.candidates:
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
            # ponytail: one request per candidate; add a batch adapter when a provider supports it.
            ingested = ingest(
                adapter,
                request,
                market_store,
                expected_security_ids=[security.security_id],
            )
        except (AdapterError, OSError) as exc:
            rejected.append((candidate.candidate_id, str(exc)))
            continue
        if not ingested.quality.approved:
            rejected.append((candidate.candidate_id, "market_snapshot_quality_rejected"))
            continue
        market_snapshot_ids.append(ingested.manifest.snapshot_id)
        outcome = evaluate_selection_outcome(
            candidate,
            selection_date=imported.batch.selection_date,
            bars=ingested.bars,
            evaluated_at=evaluated_at,
            policy=policy,
            source_snapshot_ids=[ingested.manifest.snapshot_id],
        )
        selection_store.write_outcome(outcome)
        outcomes.append(outcome)

    return SelectionOutcomeJobResult(
        outcomes=tuple(outcomes),
        rejected_candidates=tuple(rejected),
        market_snapshot_ids=tuple(market_snapshot_ids),
    )
