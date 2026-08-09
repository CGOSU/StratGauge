"""Descriptive statistics for manually tracked selection outcomes."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from stratgauge.domain.selections import SelectionOutcome, SelectionOutcomeStatus


class SelectionOutcomeStats(BaseModel):
    """Counts and descriptive returns; no predictive claim is implied."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    flats: int = Field(ge=0)
    no_entry: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    win_rate: Decimal | None = Field(default=None, ge=0, le=1)
    average_net_return: Decimal | None = None


def summarize_selection_outcomes(
    outcomes: Iterable[SelectionOutcome],
) -> SelectionOutcomeStats:
    """Summarize completed outcomes separately from unresolved observations."""

    rows = tuple(outcomes)
    completed_statuses = {
        SelectionOutcomeStatus.WIN,
        SelectionOutcomeStatus.LOSS,
        SelectionOutcomeStatus.FLAT,
    }
    completed = tuple(row for row in rows if row.status in completed_statuses)
    wins = sum(row.status == SelectionOutcomeStatus.WIN for row in rows)
    losses = sum(row.status == SelectionOutcomeStatus.LOSS for row in rows)
    flats = sum(row.status == SelectionOutcomeStatus.FLAT for row in rows)
    no_entry = sum(row.status == SelectionOutcomeStatus.NO_ENTRY for row in rows)
    unresolved = sum(row.status == SelectionOutcomeStatus.UNRESOLVED for row in rows)
    returns = tuple(row.net_return for row in completed if row.net_return is not None)
    completed_count = len(completed)
    return SelectionOutcomeStats(
        total=len(rows),
        completed=completed_count,
        wins=wins,
        losses=losses,
        flats=flats,
        no_entry=no_entry,
        unresolved=unresolved,
        win_rate=(Decimal(wins) / Decimal(completed_count) if completed_count else None),
        average_net_return=(
            sum(returns, Decimal("0")) / Decimal(len(returns)) if returns else None
        ),
    )
