"""Audit whether a selection dataset is ready for machine-learning experiments."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from stratgauge.backtest.selection_dataset import (
    DATASET_SCHEMA_VERSION,
    SelectionDatasetRow,
    read_selection_dataset,
)
from stratgauge.domain.selections import SelectionOutcomeStatus

ML_READINESS_REPORT_VERSION = "selection-ml-readiness-v1"
COMPLETED_STATUSES = frozenset(
    {
        SelectionOutcomeStatus.WIN,
        SelectionOutcomeStatus.LOSS,
        SelectionOutcomeStatus.FLAT,
    }
)
NON_TRAINABLE_STATUSES = frozenset(
    {
        SelectionOutcomeStatus.NO_ENTRY,
        SelectionOutcomeStatus.UNRESOLVED,
    }
)


class SelectionMLReadinessReport(BaseModel):
    """Immutable audit result for one candidate dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_version: str = ML_READINESS_REPORT_VERSION
    dataset_schema_version: str = DATASET_SCHEMA_VERSION
    source_file_sha256: str
    strategy_parameters_frozen: bool
    minimum_completed_samples: int = Field(gt=0)
    tracking_policy_id: str | None
    row_count: int = Field(ge=0)
    scoped_row_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    completed_outcome_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    non_trainable_outcome_count: int = Field(ge=0)
    duplicate_row_count: int = Field(ge=0)
    missing_manual_decision_count: int = Field(ge=0)
    policy_mismatch_count: int = Field(ge=0)
    target_status_counts: dict[str, int] = Field(default_factory=dict)
    selection_date_from: date | None = None
    selection_date_to: date | None = None
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    eligible: bool = False


def audit_selection_dataset(
    content: bytes,
    *,
    strategy_parameters_frozen: bool,
    minimum_completed_samples: int = 100,
    tracking_policy_id: str | None = None,
) -> SelectionMLReadinessReport:
    """Audit labels, policies, lineage, duplicates and sample volume without training."""

    if minimum_completed_samples <= 0:
        raise ValueError("minimum_completed_samples must be positive")
    rows = read_selection_dataset(content)
    return audit_selection_rows(
        rows,
        source_file_sha256=hashlib.sha256(content).hexdigest(),
        strategy_parameters_frozen=strategy_parameters_frozen,
        minimum_completed_samples=minimum_completed_samples,
        tracking_policy_id=tracking_policy_id,
    )


def audit_selection_rows(
    rows: Iterable[SelectionDatasetRow],
    *,
    source_file_sha256: str,
    strategy_parameters_frozen: bool,
    minimum_completed_samples: int = 100,
    tracking_policy_id: str | None = None,
) -> SelectionMLReadinessReport:
    """Audit already parsed rows so tests and callers can avoid filesystem access."""

    if minimum_completed_samples <= 0:
        raise ValueError("minimum_completed_samples must be positive")
    all_rows = tuple(rows)
    outcome_rows = tuple(row for row in all_rows if row.outcome_status is not None)
    policies = tuple(
        sorted({row.tracking_policy_id for row in outcome_rows if row.tracking_policy_id})
    )
    scoped_rows = tuple(
        row
        for row in all_rows
        if row.outcome_status is None
        or tracking_policy_id is None
        or row.tracking_policy_id == tracking_policy_id
    )
    scoped_outcomes = tuple(row for row in scoped_rows if row.outcome_status is not None)
    policy_mismatch_count = len(outcome_rows) - len(scoped_outcomes)
    completed_rows = tuple(
        row for row in scoped_outcomes if row.outcome_status in COMPLETED_STATUSES
    )
    non_trainable_rows = tuple(
        row for row in scoped_outcomes if row.outcome_status in NON_TRAINABLE_STATUSES
    )
    pending_count = sum(row.outcome_status is None for row in scoped_rows)
    duplicate_row_count = _duplicate_count(scoped_rows)
    target_status_counts = dict(
        sorted(
            Counter(
                row.outcome_status.value for row in completed_rows if row.outcome_status is not None
            ).items()
        )
    )
    candidate_ids = {row.candidate_id for row in scoped_rows}
    selection_dates = [date.fromisoformat(row.selection_date) for row in scoped_rows]
    blockers: list[str] = []
    warnings: list[str] = []

    if not strategy_parameters_frozen:
        blockers.append("strategy parameters are not declared frozen")
    if tracking_policy_id is None and len(policies) > 1:
        blockers.append("dataset contains multiple tracking policies; select one explicitly")
    if tracking_policy_id is not None and not scoped_outcomes:
        blockers.append(f"tracking policy has no outcome rows: {tracking_policy_id}")
    if tracking_policy_id is None and "legacy-unknown" in policies:
        blockers.append("outcomes use legacy-unknown tracking policy")
    if duplicate_row_count:
        blockers.append(f"dataset contains {duplicate_row_count} duplicate candidate/policy rows")
    if len(completed_rows) < minimum_completed_samples:
        blockers.append(
            f"completed outcome samples {len(completed_rows)} are below minimum "
            f"{minimum_completed_samples}"
        )
    if len(target_status_counts) < 2:
        blockers.append("completed outcomes do not contain at least two target statuses")

    for row in completed_rows:
        if not row.source_snapshot_ids:
            blockers.append(f"completed outcome lacks source snapshot: {row.candidate_id}")
        if row.outcome_evaluated_at is None or row.net_return is None:
            blockers.append(f"completed outcome lacks evaluation evidence: {row.candidate_id}")
        elif not _outcome_is_after_selection(row):
            blockers.append(f"outcome is not strictly after selection date: {row.candidate_id}")
    if pending_count:
        warnings.append(f"{pending_count} candidates have no mature outcome")
    if non_trainable_rows:
        warnings.append(f"{len(non_trainable_rows)} outcomes are no-entry or unresolved")
    missing_manual_decision_count = sum(row.manual_decision is None for row in scoped_rows)
    if missing_manual_decision_count:
        warnings.append(f"{missing_manual_decision_count} candidates have no manual decision")
    if policy_mismatch_count:
        warnings.append(f"{policy_mismatch_count} outcome rows are outside the selected policy")
    if not scoped_rows:
        blockers.append("dataset contains no rows")

    return SelectionMLReadinessReport(
        source_file_sha256=source_file_sha256,
        strategy_parameters_frozen=strategy_parameters_frozen,
        minimum_completed_samples=minimum_completed_samples,
        tracking_policy_id=tracking_policy_id,
        row_count=len(all_rows),
        scoped_row_count=len(scoped_rows),
        candidate_count=len(candidate_ids),
        completed_outcome_count=len(completed_rows),
        pending_count=pending_count,
        non_trainable_outcome_count=len(non_trainable_rows),
        duplicate_row_count=duplicate_row_count,
        missing_manual_decision_count=missing_manual_decision_count,
        policy_mismatch_count=policy_mismatch_count,
        target_status_counts=target_status_counts,
        selection_date_from=min(selection_dates) if selection_dates else None,
        selection_date_to=max(selection_dates) if selection_dates else None,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        eligible=not blockers,
    )


def audit_selection_dataset_file(
    path: str | Path,
    *,
    strategy_parameters_frozen: bool,
    minimum_completed_samples: int = 100,
    tracking_policy_id: str | None = None,
) -> SelectionMLReadinessReport:
    """Audit one saved JSONL dataset and retain its source hash in the report."""

    content = Path(path).read_bytes()
    return audit_selection_dataset(
        content,
        strategy_parameters_frozen=strategy_parameters_frozen,
        minimum_completed_samples=minimum_completed_samples,
        tracking_policy_id=tracking_policy_id,
    )


def _outcome_is_after_selection(row: SelectionDatasetRow) -> bool:
    if row.outcome_evaluated_at is None:
        return False
    try:
        evaluated_at = datetime.fromisoformat(row.outcome_evaluated_at)
        selection_date = date.fromisoformat(row.selection_date)
    except ValueError:
        return False
    return (
        evaluated_at.tzinfo is not None
        and evaluated_at.utcoffset() is not None
        and evaluated_at.date() > selection_date
    )


def _duplicate_count(rows: tuple[SelectionDatasetRow, ...]) -> int:
    keys = [(row.candidate_id, row.tracking_policy_id or "pending") for row in rows]
    return len(keys) - len(set(keys))
