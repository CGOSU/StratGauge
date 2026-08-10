"""Read-only API for model shadow evidence attached to selection candidates."""

from __future__ import annotations

import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.ml.registry import ModelRegistryError, ModelRegistryStore
from stratgauge.ml.shadow import ShadowScore, ShadowScoreError, ShadowScoreStore


class ModelScoreApiError(Exception):
    """Expected model-score API failure."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ModelScoreView(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_version_id: str
    model_type: str
    lifecycle: str
    tracking_policy_id: str
    scored_at: str
    uncalibrated_model_score: str
    calibrated_model_id: str | None
    calibrated_model_score: str | None
    calibrated: bool
    sample_count: int | None = Field(default=None, ge=0)
    calibration_sample_count: int | None = Field(default=None, ge=0)
    applicable_date_start: date | None = None
    applicable_date_end: date | None = None
    authoritative: Literal[False] = False


class ModelScoreFailureView(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_version_id: str
    reason: str


class CandidateModelScoresView(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_id: str
    candidate_id: str
    symbol: str
    scores: tuple[ModelScoreView, ...] = ()
    failures: tuple[ModelScoreFailureView, ...] = ()


class ModelScoreService:
    """Resolve immutable shadow scores without creating a decision signal."""

    def __init__(
        self,
        selection_store: SelectionImportStore,
        *,
        model_registry: ModelRegistryStore,
        score_store: ShadowScoreStore,
    ) -> None:
        self._selection_store = selection_store
        self._model_registry = model_registry
        self._score_store = score_store

    def get_candidate_scores(
        self,
        batch_id: str,
        candidate_id: str,
    ) -> CandidateModelScoresView:
        try:
            imported = self._selection_store.load_import(batch_id)
        except SelectionStoreError as exc:
            if "does not exist" in str(exc):
                raise ModelScoreApiError(
                    "SELECTION_BATCH_NOT_FOUND", "选股批次不存在。", 404
                ) from exc
            raise ModelScoreApiError("MODEL_SCORE_STORAGE_ERROR", str(exc), 500) from exc
        candidate = next(
            (item for item in imported.candidates if item.candidate_id == candidate_id),
            None,
        )
        if candidate is None:
            raise ModelScoreApiError(
                "SELECTION_CANDIDATE_NOT_FOUND", "候选股票不属于该选股批次。", 404
            )
        try:
            shadow_scores = self._score_store.list_scores(candidate_id=candidate_id)
        except ShadowScoreError as exc:
            raise ModelScoreApiError("MODEL_SCORE_STORAGE_ERROR", str(exc), 500) from exc

        scores: list[ModelScoreView] = []
        failures: list[ModelScoreFailureView] = []
        for score in shadow_scores:
            try:
                scores.append(self._to_view(score))
            except (ModelRegistryError, OSError, TypeError, ValueError, KeyError) as exc:
                failures.append(
                    ModelScoreFailureView(
                        model_version_id=score.model_version_id,
                        reason=str(exc),
                    )
                )
        return CandidateModelScoresView(
            batch_id=batch_id,
            candidate_id=candidate_id,
            symbol=candidate.symbol,
            scores=tuple(scores),
            failures=tuple(failures),
        )

    def _to_view(self, score: ShadowScore) -> ModelScoreView:
        registered = self._model_registry.load(score.model_version_id)
        manifest = _read_manifest(
            self._model_registry.read_training_manifest(score.model_version_id)
        )
        validation_count = _validation_row_count(manifest)
        manifest_start = _optional_date(manifest.get("train_end"))
        manifest_end = _optional_date(manifest.get("validation_end"))
        return ModelScoreView(
            model_version_id=score.model_version_id,
            model_type=registered.model_type,
            lifecycle=registered.lifecycle.value,
            tracking_policy_id=score.tracking_policy_id,
            scored_at=score.scored_at.isoformat(),
            uncalibrated_model_score=str(score.uncalibrated_model_score),
            calibrated_model_id=score.calibrated_model_id,
            calibrated_model_score=(
                None if score.calibrated_model_score is None else str(score.calibrated_model_score)
            ),
            calibrated=score.calibrated,
            sample_count=(score.calibration_sample_count if score.calibrated else validation_count),
            calibration_sample_count=score.calibration_sample_count,
            applicable_date_start=(
                score.calibration_date_start if score.calibrated else manifest_start
            ),
            applicable_date_end=score.calibration_date_end if score.calibrated else manifest_end,
        )


def _read_manifest(content: bytes) -> dict[str, object]:
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("model training manifest must be a JSON object")
    return payload


def _validation_row_count(manifest: dict[str, object]) -> int | None:
    row_counts = manifest.get("row_counts")
    if not isinstance(row_counts, dict):
        return None
    value = row_counts.get("validation")
    return value if isinstance(value, int) and value >= 0 else None


def _optional_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return date.fromisoformat(value)
