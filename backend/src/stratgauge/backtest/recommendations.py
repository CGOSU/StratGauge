"""Manual recommendation-workbench evidence contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from stratgauge.backtest.selection_stats import SelectionOutcomeStats
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    ManualSelectionLabel,
    SelectionBatch,
    SelectionCandidate,
)
from stratgauge.ml.shadow import ShadowScore

RecommendationState = Literal["reviewable", "blocked"]


class RecommendationCandidateEvidence(SelectionCandidate):
    """Candidate evidence with no order or trading action."""

    model_config = ConfigDict(frozen=True)

    quality_state: str
    historical_stats: SelectionOutcomeStats | None = None
    model_score: ShadowScore | None = None
    manual_decision: ManualSelectionDecision | None = None
    state: RecommendationState
    blocking_reasons: tuple[str, ...] = ()
    authoritative: Literal[False] = False


class RecommendationWorkbench(BaseModel):
    """A point-in-time, human-reviewable evidence bundle."""

    model_config = ConfigDict(frozen=True)

    batch: SelectionBatch
    generated_at: datetime
    candidates: tuple[RecommendationCandidateEvidence, ...]
    automatic_action: Literal[False] = False


def build_recommendation_workbench(
    *,
    batch: SelectionBatch,
    candidates: tuple[SelectionCandidate, ...],
    quality_states: dict[str, str],
    historical_stats: dict[str, SelectionOutcomeStats | None],
    model_scores: dict[str, ShadowScore | None],
    labels: dict[str, ManualSelectionLabel | None],
    generated_at: datetime,
) -> RecommendationWorkbench:
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("recommendation generated_at must include a timezone")
    if not candidates:
        raise ValueError("recommendation workbench requires candidates")
    evidence: list[RecommendationCandidateEvidence] = []
    for candidate in candidates:
        if candidate.batch_id != batch.batch_id:
            raise ValueError("recommendation candidate does not belong to batch")
        quality_state = quality_states.get(candidate.candidate_id, "unknown")
        stats = historical_stats.get(candidate.candidate_id)
        score = model_scores.get(candidate.candidate_id)
        label = labels.get(candidate.candidate_id)
        if score is not None and score.used_for_decision:
            raise ValueError("shadow score cannot be used for a decision")
        reasons: list[str] = []
        if quality_state != "approved":
            reasons.append(f"quality:{quality_state}")
        if stats is None:
            reasons.append("historical_evidence_missing")
        if score is None:
            reasons.append("model_score_missing")
        elif score.model_lifecycle != "active":
            reasons.append("approved_model_score_missing")
        evidence.append(
            RecommendationCandidateEvidence(
                **candidate.model_dump(),
                quality_state=quality_state,
                historical_stats=stats,
                model_score=score,
                manual_decision=None if label is None else label.decision,
                state="reviewable" if not reasons else "blocked",
                blocking_reasons=tuple(reasons),
            )
        )
    return RecommendationWorkbench(
        batch=batch,
        generated_at=generated_at,
        candidates=tuple(evidence),
    )
