"""Objective outcome indexing and fold metrics for walk-forward reports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from stratgauge.backtest.selection_dataset import SelectionDatasetRow
from stratgauge.domain.selections import SelectionOutcomeStatus
from stratgauge.ml.selection_training import SelectionFeatureRow


@dataclass(frozen=True)
class _OutcomeMetrics:
    coverage: Decimal
    execution_rate: Decimal | None
    average_net_return: Decimal | None
    max_drawdown: Decimal | None
    turnover: Decimal | None
    completed_count: int
    no_entry_count: int
    unresolved_count: int


def _index_outcomes(
    rows: Sequence[SelectionDatasetRow],
    *,
    expected_tracking_policy_id: str,
) -> dict[tuple[str, date], SelectionDatasetRow]:
    policies = {row.tracking_policy_id for row in rows if row.tracking_policy_id is not None}
    if policies and policies != {expected_tracking_policy_id}:
        raise ValueError("outcome dataset contains a different tracking policy")
    indexed: dict[tuple[str, date], SelectionDatasetRow] = {}
    for row in rows:
        if row.tracking_policy_id is None or row.outcome_status is None:
            continue
        key = (row.candidate_id, _parse_date(row.selection_date, "selection_date"))
        if key in indexed:
            raise ValueError("outcome dataset contains duplicate candidate/date rows")
        indexed[key] = row
    return indexed


def _outcome_metrics(
    validation_rows: Sequence[SelectionFeatureRow],
    outcome_map: dict[tuple[str, date], SelectionDatasetRow],
) -> _OutcomeMetrics:
    known = tuple(
        outcome_map[(row.candidate_id, row.selection_date)]
        for row in validation_rows
        if (row.candidate_id, row.selection_date) in outcome_map
    )
    coverage = Decimal(len(known)) / Decimal(len(validation_rows))
    completed_statuses = {
        SelectionOutcomeStatus.WIN,
        SelectionOutcomeStatus.LOSS,
        SelectionOutcomeStatus.FLAT,
    }
    completed = tuple(row for row in known if row.outcome_status in completed_statuses)
    no_entry_count = sum(row.outcome_status is SelectionOutcomeStatus.NO_ENTRY for row in known)
    unresolved_count = sum(row.outcome_status is SelectionOutcomeStatus.UNRESOLVED for row in known)
    returns = tuple(_parse_decimal(row.net_return, "net_return") for row in completed)
    average_net_return = (
        sum((value for value in returns if value is not None), Decimal("0")) / Decimal(len(returns))
        if returns and all(value is not None for value in returns)
        else None
    )
    ordered_returns = tuple(
        _parse_decimal(row.net_return, "net_return")
        for row in sorted(completed, key=_outcome_sort_key)
    )
    max_drawdown = (
        _max_drawdown(tuple(value for value in ordered_returns if value is not None))
        if ordered_returns and all(value is not None for value in ordered_returns)
        else None
    )
    resolved = completed + tuple(
        row for row in known if row.outcome_status is SelectionOutcomeStatus.NO_ENTRY
    )
    notionals = tuple(
        _parse_decimal(row.position_notional, "position_notional") for row in resolved
    )
    turnover = (
        sum(
            (
                Decimal("2") * value
                for value in (
                    _parse_decimal(row.position_notional, "position_notional") for row in completed
                )
                if value is not None
            ),
            Decimal("0"),
        )
        / sum((value for value in notionals if value is not None), Decimal("0"))
        if resolved
        and all(value is not None for value in notionals)
        and sum((value for value in notionals if value is not None), Decimal("0")) > 0
        else None
    )
    denominator = len(completed) + no_entry_count
    execution_rate = Decimal(len(completed)) / Decimal(denominator) if denominator else None
    return _OutcomeMetrics(
        coverage=coverage,
        execution_rate=execution_rate,
        average_net_return=average_net_return,
        max_drawdown=max_drawdown,
        turnover=turnover,
        completed_count=len(completed),
        no_entry_count=no_entry_count,
        unresolved_count=unresolved_count,
    )


def _outcome_sort_key(row: SelectionDatasetRow) -> tuple[datetime, str, str]:
    evaluated_at = (
        _parse_datetime(row.outcome_evaluated_at)
        if row.outcome_evaluated_at is not None
        else datetime.combine(
            _parse_date(row.selection_date, "selection_date"), datetime.min.time(), tzinfo=UTC
        )
    )
    return evaluated_at, row.selection_date, row.candidate_id


def _parse_decimal(value: str | None, field_name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"outcome {field_name} is not a decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"outcome {field_name} must be finite")
    return parsed


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"outcome {field_name} is not an ISO date") from exc


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("outcome_evaluated_at is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("outcome_evaluated_at must include a timezone")
    return parsed


def _max_drawdown(returns: Sequence[Decimal]) -> Decimal | None:
    if not returns:
        return None
    equity = Decimal("1")
    peak = equity
    drawdown = Decimal("0")
    for value in returns:
        equity *= Decimal("1") + value
        if equity > peak:
            peak = equity
        elif peak > 0:
            drawdown = max(drawdown, (peak - equity) / peak)
    return drawdown
