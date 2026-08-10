"""Independent shadow scoring for registered baseline and LightGBM models."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, field_validator

from stratgauge.domain.models import DomainModel, ModelLifecycle
from stratgauge.features.contract import FeatureSnapshot
from stratgauge.ml.calibration import CalibratedModel
from stratgauge.ml.lightgbm_training import LightGBMModel
from stratgauge.ml.registry import (
    ModelRegistryError,
    ModelRegistryStore,
    RegisteredModel,
)
from stratgauge.ml.selection_training import BaselineModel

SHADOW_SCORE_VERSION = "selection-shadow-score-v1"
SHADOW_REPORT_VERSION = "selection-shadow-report-v1"


class ShadowScoreError(RuntimeError):
    """A shadow score cannot be safely generated or persisted."""


class ShadowScoringInput(DomainModel):
    """A candidate identity paired with a point-in-time feature snapshot."""

    candidate_id: str
    feature_snapshot: FeatureSnapshot

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("shadow scoring candidate_id must not be empty")
        return value.strip()


class ShadowScore(DomainModel):
    """One immutable model score kept separate from decisions and outcomes."""

    score_id: str
    score_version: Literal["selection-shadow-score-v1"] = "selection-shadow-score-v1"
    model_version_id: str
    model_lifecycle: Literal["shadow", "active"] = "shadow"
    candidate_id: str
    symbol: str
    selection_date: date
    feature_set_version_id: str
    feature_snapshot_id: str
    tracking_policy_id: str
    source_snapshot_ids: tuple[str, ...]
    scored_at: datetime
    uncalibrated_model_score: Decimal = Field(ge=0, le=1)
    calibrated_model_id: str | None = None
    calibrated_model_score: Decimal | None = Field(default=None, ge=0, le=1)
    calibration_sample_count: int | None = Field(default=None, ge=0)
    calibration_date_start: date | None = None
    calibration_date_end: date | None = None
    calibrated: bool = False
    used_for_decision: Literal[False] = False


class ShadowScoreFailure(DomainModel):
    """A candidate-specific shadow failure that does not alter its batch."""

    candidate_id: str
    feature_snapshot_id: str
    reason: str


class ShadowScoringReport(DomainModel):
    """Content-addressed accounting for one independent model scoring run."""

    report_id: str
    report_version: Literal["selection-shadow-report-v1"] = "selection-shadow-report-v1"
    model_lifecycle: Literal["shadow", "active"] = "shadow"
    model_version_id: str
    tracking_policy_id: str
    scored_at: datetime
    source_input_sha256: str
    score_ids: tuple[str, ...]
    failures: tuple[ShadowScoreFailure, ...]


class ShadowScoreStore:
    """Persist shadow scores and reports without overwriting prior evidence."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.score_dir = self.root / "shadow-scores" / "scores"
        self.report_dir = self.root / "shadow-scores" / "reports"
        self.score_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def write_score(self, score: ShadowScore) -> ShadowScore:
        path = self.score_dir / f"{_safe_identifier(score.score_id)}.json"
        _write_once(path, score.model_dump_json(indent=2).encode("utf-8"), "shadow score")
        return self.load_score(score.score_id)

    def load_score(self, score_id: str) -> ShadowScore:
        path = self.score_dir / f"{_safe_identifier(score_id)}.json"
        if not path.exists():
            raise ShadowScoreError(f"shadow score does not exist: {score_id}")
        try:
            return ShadowScore.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise ShadowScoreError(f"shadow score is invalid: {score_id}") from exc

    def list_scores(self, *, candidate_id: str | None = None) -> tuple[ShadowScore, ...]:
        scores = tuple(self.load_score(path.stem) for path in self.score_dir.glob("*.json"))
        if candidate_id is not None:
            scores = tuple(score for score in scores if score.candidate_id == candidate_id)
        return tuple(sorted(scores, key=lambda score: (score.scored_at, score.score_id)))

    def write_report(self, report: ShadowScoringReport) -> ShadowScoringReport:
        path = self.report_dir / f"{_safe_identifier(report.report_id)}.json"
        _write_once(path, report.model_dump_json(indent=2).encode("utf-8"), "shadow report")
        return self.load_report(report.report_id)

    def load_report(self, report_id: str) -> ShadowScoringReport:
        path = self.report_dir / f"{_safe_identifier(report_id)}.json"
        if not path.exists():
            raise ShadowScoreError(f"shadow report does not exist: {report_id}")
        try:
            return ShadowScoringReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise ShadowScoreError(f"shadow report is invalid: {report_id}") from exc


def read_shadow_inputs(content: bytes) -> tuple[ShadowScoringInput, ...]:
    """Read candidate/feature JSONL without accepting outcome labels."""

    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ShadowScoreError("shadow input is not valid UTF-8") from exc
    inputs: list[ShadowScoringInput] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("shadow input row must be a JSON object")
            if "label" in payload or "label_evaluated_at" in payload:
                raise ValueError("shadow input must not contain outcome labels")
            inputs.append(ShadowScoringInput.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ShadowScoreError(f"invalid shadow input at line {line_number}: {exc}") from exc
    if not inputs:
        raise ShadowScoreError("shadow input contains no rows")
    return tuple(inputs)


def score_shadow_batch(
    inputs: Iterable[ShadowScoringInput],
    *,
    registry: ModelRegistryStore,
    score_store: ShadowScoreStore,
    model_version_id: str,
    scored_at: datetime,
    source_input_sha256: str,
    calibration_model: CalibratedModel | None = None,
    required_lifecycle: ModelLifecycle = ModelLifecycle.SHADOW,
) -> ShadowScoringReport:
    """Score feature snapshots independently and record candidate-level failures."""

    if scored_at.tzinfo is None or scored_at.utcoffset() is None:
        raise ShadowScoreError("scored_at must include a timezone")
    if not source_input_sha256.strip():
        raise ShadowScoreError("shadow input hash must not be empty")
    rows = tuple(inputs)
    if not rows:
        raise ShadowScoreError("shadow scoring requires at least one input")
    try:
        registered, model = registry.load_model(model_version_id)
    except ModelRegistryError as exc:
        raise ShadowScoreError(str(exc)) from exc
    if registered.lifecycle is not required_lifecycle:
        raise ShadowScoreError(
            f"model scoring requires a model in {required_lifecycle.value} lifecycle"
        )
    if calibration_model is not None:
        _validate_calibration(registered, model, calibration_model)
    runtime_model = (
        _load_lightgbm_runtime(registry.read_runtime_artifact(model_version_id))
        if isinstance(model, LightGBMModel)
        else None
    )

    score_ids: list[str] = []
    failures: list[ShadowScoreFailure] = []
    for row in rows:
        try:
            score = _score_one(
                row,
                registered,
                model,
                runtime_model,
                scored_at,
                calibration_model,
            )
        except (ShadowScoreError, ValueError) as exc:
            failures.append(
                ShadowScoreFailure(
                    candidate_id=row.candidate_id,
                    feature_snapshot_id=row.feature_snapshot.feature_snapshot_id,
                    reason=str(exc),
                )
            )
            continue
        score_store.write_score(score)
        score_ids.append(score.score_id)
    identity = {
        "failures": [failure.model_dump(mode="json") for failure in failures],
        "calibration_model_id": (
            None if calibration_model is None else calibration_model.calibration_id
        ),
        "model_lifecycle": required_lifecycle.value,
        "model_version_id": model_version_id,
        "report_version": SHADOW_REPORT_VERSION,
        "score_ids": score_ids,
        "scored_at": scored_at.isoformat(),
        "source_input_sha256": source_input_sha256,
        "tracking_policy_id": registered.tracking_policy_id,
    }
    report_id = f"shadow-report-{_sha256(_canonical_json(identity))[:20]}"
    report = ShadowScoringReport(
        report_id=report_id,
        model_version_id=model_version_id,
        model_lifecycle=_lifecycle_value(required_lifecycle),
        tracking_policy_id=registered.tracking_policy_id,
        scored_at=scored_at,
        source_input_sha256=source_input_sha256,
        score_ids=tuple(score_ids),
        failures=tuple(failures),
    )
    return score_store.write_report(report)


def _score_one(
    row: ShadowScoringInput,
    registered: RegisteredModel,
    model: BaselineModel | LightGBMModel,
    runtime_model: tuple[Any, Any] | None,
    scored_at: datetime,
    calibration_model: CalibratedModel | None,
) -> ShadowScore:
    feature_snapshot = row.feature_snapshot
    if feature_snapshot.feature_set_version_id != registered.feature_set_version_id:
        raise ShadowScoreError("feature snapshot does not match registered feature set")
    if feature_snapshot.as_of > scored_at:
        raise ShadowScoreError("feature snapshot is not available at scored_at")
    score_value = _raw_score(model, feature_snapshot, runtime_model)
    calibrated_score = (
        None if calibration_model is None else _apply_calibration(score_value, calibration_model)
    )
    identity = {
        "calibrated_model_id": (
            None if calibration_model is None else calibration_model.calibration_id
        ),
        "calibrated_score": None if calibrated_score is None else str(calibrated_score),
        "candidate_id": row.candidate_id,
        "feature_set_version_id": feature_snapshot.feature_set_version_id,
        "feature_snapshot_id": feature_snapshot.feature_snapshot_id,
        "model_lifecycle": registered.lifecycle.value,
        "model_score": str(score_value),
        "model_version_id": registered.model_version_id,
        "scored_at": scored_at.isoformat(),
        "tracking_policy_id": registered.tracking_policy_id,
    }
    return ShadowScore(
        score_id=f"shadow-score-{_sha256(_canonical_json(identity))[:20]}",
        model_version_id=registered.model_version_id,
        model_lifecycle=_lifecycle_value(registered.lifecycle),
        candidate_id=row.candidate_id,
        symbol=feature_snapshot.symbol,
        selection_date=feature_snapshot.selection_date,
        feature_set_version_id=feature_snapshot.feature_set_version_id,
        feature_snapshot_id=feature_snapshot.feature_snapshot_id,
        tracking_policy_id=registered.tracking_policy_id,
        source_snapshot_ids=feature_snapshot.source_snapshot_ids,
        scored_at=scored_at,
        uncalibrated_model_score=score_value,
        calibrated_model_id=(
            None if calibration_model is None else calibration_model.calibration_id
        ),
        calibrated_model_score=calibrated_score,
        calibration_sample_count=(
            None if calibration_model is None else calibration_model.calibration_row_count
        ),
        calibration_date_start=(
            None if calibration_model is None else calibration_model.calibration_date_start
        ),
        calibration_date_end=(
            None if calibration_model is None else calibration_model.calibration_date_end
        ),
        calibrated=calibration_model is not None,
    )


def _lifecycle_value(value: ModelLifecycle) -> Literal["shadow", "active"]:
    return cast(Literal["shadow", "active"], value.value)


def _raw_score(
    model: BaselineModel | LightGBMModel,
    feature_snapshot: FeatureSnapshot,
    runtime_model: tuple[Any, Any] | None,
) -> Decimal:
    if isinstance(model, BaselineModel):
        return model.positive_probability
    if runtime_model is None:
        raise ShadowScoreError("LightGBM runtime artifact is unavailable")
    booster, numpy = runtime_model
    try:
        matrix = numpy.asarray(
            [[float(feature_snapshot.values[name]) for name in model.feature_names]],
            dtype=float,
        )
        value = Decimal(str(float(booster.predict(matrix)[0])))
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        raise ShadowScoreError("LightGBM could not score the feature snapshot") from exc
    if not value.is_finite() or not 0 <= value <= 1:
        raise ShadowScoreError("LightGBM produced an invalid probability")
    return value


def _load_lightgbm_runtime(content: bytes) -> tuple[Any, Any]:
    try:
        lightgbm = import_module("lightgbm")
        numpy = import_module("numpy")
        return lightgbm.Booster(model_str=content.decode("utf-8")), numpy
    except (ImportError, OSError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise ShadowScoreError("LightGBM runtime is unavailable or invalid") from exc


def _validate_calibration(
    registered: RegisteredModel,
    model: BaselineModel | LightGBMModel,
    calibration_model: CalibratedModel,
) -> None:
    if not isinstance(model, LightGBMModel):
        raise ShadowScoreError("calibration is only supported for LightGBM models")
    if calibration_model.base_model_version_id != registered.model_version_id:
        raise ShadowScoreError("calibration model does not match the registered model")
    if calibration_model.feature_set_version_id != registered.feature_set_version_id:
        raise ShadowScoreError("calibration model does not match the registered feature set")
    if calibration_model.tracking_policy_id != registered.tracking_policy_id:
        raise ShadowScoreError("calibration model does not match the tracking policy")


def _apply_calibration(probability: Decimal, model: CalibratedModel) -> Decimal:
    return next(
        (
            item.calibrated_probability
            for item in model.bins
            if probability <= item.upper_probability
        ),
        model.bins[-1].calibrated_probability,
    )


def _write_once(path: Path, content: bytes, name: str) -> None:
    if path.exists():
        try:
            if path.read_bytes() != content:
                raise ShadowScoreError(f"{name} identity already has different content")
        except OSError as exc:
            raise ShadowScoreError(f"failed to read existing {name}") from exc
        return
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ShadowScoreError(f"failed to write {name}") from exc


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(
        character in normalized for character in ("/", "\\", "\0", "\r", "\n")
    ):
        raise ShadowScoreError("shadow identifier contains an invalid path character")
    return normalized


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
