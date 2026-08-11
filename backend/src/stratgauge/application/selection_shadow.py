"""Score selection feature snapshots through the non-authoritative shadow path."""

from __future__ import annotations

from datetime import datetime

from stratgauge.application.selection_features import SelectionFeatureSnapshotReport
from stratgauge.features.store import FeatureSnapshotStore, FeatureStoreError
from stratgauge.ml.calibration import CalibratedModel
from stratgauge.ml.registry import ModelRegistryStore
from stratgauge.ml.shadow import (
    ShadowScoreStore,
    ShadowScoringInput,
    ShadowScoringReport,
    score_shadow_batch,
    shadow_input_sha256,
)


class SelectionShadowError(RuntimeError):
    """Selection shadow scoring cannot safely complete."""


def score_selection_feature_report(
    report: SelectionFeatureSnapshotReport,
    *,
    feature_store: FeatureSnapshotStore,
    registry: ModelRegistryStore,
    score_store: ShadowScoreStore,
    model_version_id: str,
    scored_at: datetime,
    calibration_model: CalibratedModel | None = None,
) -> ShadowScoringReport:
    """Score successful feature rows while preserving candidate-level failures."""

    inputs: list[ShadowScoringInput] = []
    for candidate_id, snapshot_id in report.candidate_snapshot_ids:
        try:
            snapshot = feature_store.load(snapshot_id)
        except FeatureStoreError as exc:
            raise SelectionShadowError(str(exc)) from exc
        inputs.append(ShadowScoringInput(candidate_id=candidate_id, feature_snapshot=snapshot))
    if not inputs:
        raise SelectionShadowError("feature report contains no successful candidates")
    source_input_sha256 = shadow_input_sha256(inputs)
    try:
        return score_shadow_batch(
            inputs,
            registry=registry,
            score_store=score_store,
            model_version_id=model_version_id,
            scored_at=scored_at,
            source_input_sha256=source_input_sha256,
            calibration_model=calibration_model,
        )
    except Exception as exc:
        if isinstance(exc, SelectionShadowError):
            raise
        raise SelectionShadowError(str(exc)) from exc
