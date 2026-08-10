"""Build a time-stamped, outcome-aware dataset from selection storage."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from stratgauge.backtest.selection_outcomes import latest_selection_outcomes
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    SelectionOutcome,
    SelectionOutcomeStatus,
)


class SelectionDatasetRow(BaseModel):
    """One candidate/outcome observation suitable for later time-split training."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    batch_id: str
    symbol: str
    selection_date: str
    source_filename: str | None
    formula_id: str | None
    formula_name: str
    formula_version: str | None
    manual_decision: ManualSelectionDecision | None
    manual_labeled_at: str | None
    tracking_policy_id: str | None
    cost_schedule_id: str | None
    horizon_sessions: int | None
    position_notional: str | None
    outcome_status: SelectionOutcomeStatus | None
    outcome_evaluated_at: str | None
    net_return: str | None
    source_snapshot_ids: tuple[str, ...]


def build_selection_dataset(
    store: SelectionImportStore,
    batch_id: str,
) -> tuple[SelectionDatasetRow, ...]:
    """Build rows without dropping pending, no-entry or unresolved observations."""

    imported = store.load_import(batch_id)
    rows: list[SelectionDatasetRow] = []
    for candidate in imported.candidates:
        labels = store.list_labels(candidate.candidate_id)
        latest_label = labels[-1] if labels else None
        outcomes = latest_selection_outcomes(store.list_outcomes(candidate.candidate_id))
        observations: Iterable[SelectionOutcome | None] = outcomes or (None,)
        for outcome in observations:
            rows.append(
                SelectionDatasetRow(
                    candidate_id=candidate.candidate_id,
                    batch_id=batch_id,
                    symbol=candidate.symbol,
                    selection_date=imported.batch.selection_date.isoformat(),
                    source_filename=imported.batch.source_filename,
                    formula_id=imported.batch.formula_id,
                    formula_name=imported.batch.formula_name,
                    formula_version=imported.batch.formula_version,
                    manual_decision=latest_label.decision if latest_label else None,
                    manual_labeled_at=(
                        latest_label.labeled_at.isoformat() if latest_label else None
                    ),
                    tracking_policy_id=(outcome.tracking_policy_id if outcome else None),
                    cost_schedule_id=(outcome.cost_schedule_id if outcome else None),
                    horizon_sessions=(outcome.horizon_sessions if outcome else None),
                    position_notional=(
                        str(outcome.position_notional)
                        if outcome and outcome.position_notional is not None
                        else None
                    ),
                    outcome_status=outcome.status if outcome else None,
                    outcome_evaluated_at=(outcome.evaluated_at.isoformat() if outcome else None),
                    net_return=(
                        str(outcome.net_return)
                        if outcome and outcome.net_return is not None
                        else None
                    ),
                    source_snapshot_ids=outcome.source_snapshot_ids if outcome else (),
                )
            )
    return tuple(rows)


def serialize_selection_dataset(rows: Iterable[SelectionDatasetRow]) -> str:
    """Serialize dataset rows as newline-delimited JSON."""

    return "".join(f"{row.model_dump_json()}\n" for row in rows)


def write_selection_dataset(path: str | Path, rows: Iterable[SelectionDatasetRow]) -> None:
    """Write newline-delimited JSON without overwriting an existing dataset."""

    destination = Path(path)
    if destination.exists():
        raise FileExistsError(f"dataset already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_selection_dataset(rows)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
