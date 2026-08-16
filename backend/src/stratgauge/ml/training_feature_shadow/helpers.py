"""Purpose-aware, non-authoritative shadow scoring for feature LightGBM models."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from importlib import import_module
from typing import Any

from stratgauge.features.contract import FeatureSnapshot
from stratgauge.ml._hashing import sha256_bytes
from stratgauge.ml.isotonic import IsotonicBin
from stratgauge.ml.training_datasets import (
    TrainingLabel,
)
from stratgauge.ml.training_feature_lightgbm_calibration import (
    FeatureCalibrationBin,
    FeatureCalibrationOutput,
)
from stratgauge.ml.training_feature_registry import (
    FeatureModelRegistration,
    FeatureModelRegistryError,
    FeatureModelRegistryStore,
)
from stratgauge.ml.training_feature_shadow.core import (
    FeatureShadowError,
)
from stratgauge.ml.training_feature_shadow.models import (
    FeatureShadowCalibrationMode,
    FeatureShadowFailure,
    FeatureShadowInput,
    FeatureShadowReport,
    FeatureShadowScore,
    FeatureShadowScoreStore,
    _predicted_label,
    _report_id,
    _score_id,
)


def read_feature_shadow_inputs(content: bytes) -> tuple[FeatureShadowInput, ...]:
    """Read candidate/feature JSONL and reject outcome-bearing fields."""

    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise FeatureShadowError("feature shadow input is not valid UTF-8") from exc
    rows: list[FeatureShadowInput] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("feature shadow input row must be a JSON object")
            if any(
                field in payload
                for field in (
                    "label",
                    "target",
                    "label_available_at",
                    "outcome_status",
                    "outcome_evaluated_at",
                    "purchased_within_window",
                    "sold_within_window",
                )
            ):
                raise ValueError("feature shadow input must not contain outcome labels")
            rows.append(FeatureShadowInput.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise FeatureShadowError(
                f"invalid feature shadow input at line {line_number}: {exc}"
            ) from exc
    _validate_inputs(rows)
    return tuple(rows)


def serialize_feature_shadow_inputs(inputs: Iterable[FeatureShadowInput]) -> bytes:
    """Serialize feature shadow inputs canonically for reproducible run identity."""

    rows = tuple(inputs)
    _validate_inputs(rows)
    return b"".join(
        (
            json.dumps(
                row.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for row in sorted(rows, key=lambda item: item.candidate_id)
    )


def feature_shadow_input_sha256(inputs: Iterable[FeatureShadowInput]) -> str:
    """Return the canonical identity of one feature shadow input set."""

    return sha256_bytes(serialize_feature_shadow_inputs(inputs))


def score_feature_shadow_batch(
    inputs: Iterable[FeatureShadowInput],
    *,
    registry: FeatureModelRegistryStore,
    score_store: FeatureShadowScoreStore,
    registration_id: str,
    scored_at: datetime,
    calibration_mode: FeatureShadowCalibrationMode = "none",
) -> FeatureShadowReport:
    """Score verified feature snapshots through a manually enabled shadow model."""

    _require_aware(scored_at, "scored_at")
    if calibration_mode not in {"none", "registered"}:
        raise FeatureShadowError(f"unsupported feature shadow calibration mode: {calibration_mode}")
    rows = tuple(inputs)
    _validate_inputs(rows)
    source_input_sha256 = feature_shadow_input_sha256(rows)
    try:
        registration, training, calibration = registry.load_outputs(registration_id)
    except FeatureModelRegistryError as exc:
        raise FeatureShadowError(str(exc)) from exc
    if registration.lifecycle != "shadow":
        raise FeatureShadowError("feature shadow scoring requires a model in shadow lifecycle")
    if registration.registered_at > scored_at:
        raise FeatureShadowError("feature shadow scoring cannot use a future registration")
    shadow_events = tuple(
        event for event in registry.list_events(registration_id) if event.to_lifecycle == "shadow"
    )
    if not shadow_events or shadow_events[-1].changed_at > scored_at:
        raise FeatureShadowError("feature shadow scoring cannot precede shadow authorization")
    if any(
        row.purpose is not registration.purpose
        or row.label_contract_id != registration.label_contract_id
        for row in rows
    ):
        raise FeatureShadowError("feature shadow input purpose does not match registration")
    if calibration_mode == "registered":
        if not registration.calibrated or calibration is None:
            raise FeatureShadowError(
                "registered calibration mode requires a registered calibration artifact"
            )
        if calibration.model.calibration_id != registration.calibration_id:
            raise FeatureShadowError("registered calibration identity does not match registration")
        if calibration.model.calibrated_at > scored_at:
            raise FeatureShadowError("feature shadow scoring cannot use a future calibration")
    else:
        calibration = None
    runtime = _load_lightgbm_runtime(registry.read_runtime_artifact(registration_id))
    score_ids: list[str] = []
    failures: list[FeatureShadowFailure] = []
    for row in rows:
        try:
            score = _score_one(
                row,
                registration,
                training.model.feature_names,
                runtime,
                scored_at,
                calibration,
            )
        except (FeatureShadowError, KeyError, TypeError, ValueError) as exc:
            failures.append(
                FeatureShadowFailure(
                    candidate_id=row.candidate_id,
                    feature_snapshot_id=row.feature_snapshot.feature_snapshot_id,
                    reason=str(exc),
                )
            )
            continue
        score_store.write_score(score)
        score_ids.append(score.score_id)
    report = FeatureShadowReport(
        report_id="pending",
        registration_id=registration.registration_id,
        model_version_id=registration.model_version_id,
        purpose=registration.purpose,
        label_contract_id=registration.label_contract_id,
        calibration_mode=calibration_mode,
        calibration_id=None if calibration is None else calibration.model.calibration_id,
        scored_at=scored_at,
        source_input_sha256=source_input_sha256,
        input_count=len(rows),
        score_ids=tuple(score_ids),
        failures=tuple(failures),
    )
    return score_store.write_report(report.model_copy(update={"report_id": _report_id(report)}))


def _score_one(
    row: FeatureShadowInput,
    registration: FeatureModelRegistration,
    feature_names: Sequence[str],
    runtime: tuple[Any, Any],
    scored_at: datetime,
    calibration: FeatureCalibrationOutput | None,
) -> FeatureShadowScore:
    snapshot = row.feature_snapshot
    if snapshot.feature_set_version_id != registration.feature_set_version_id:
        raise FeatureShadowError("feature snapshot does not match registered feature set")
    if tuple(sorted(snapshot.values)) != tuple(feature_names):
        raise FeatureShadowError("feature snapshot columns do not match registered model")
    if snapshot.as_of > scored_at or snapshot.available_at > scored_at:
        raise FeatureShadowError("feature snapshot is not available at scored_at")
    if snapshot.as_of.date() > snapshot.selection_date:
        raise FeatureShadowError("feature snapshot as_of follows selection date")
    if snapshot.selection_date > scored_at.date():
        raise FeatureShadowError("feature snapshot selection date follows scored_at")
    booster, numpy = runtime
    raw_probabilities = _predict_probabilities(
        booster,
        numpy,
        snapshot,
        feature_names,
        registration.class_labels,
    )
    calibrated_probabilities = (
        None
        if calibration is None
        else _apply_registered_calibration(
            raw_probabilities, calibration, registration.class_labels
        )
    )
    score = FeatureShadowScore(
        score_id="pending",
        registration_id=registration.registration_id,
        model_version_id=registration.model_version_id,
        purpose=registration.purpose,
        label_contract_id=registration.label_contract_id,
        candidate_id=row.candidate_id,
        symbol=snapshot.symbol,
        selection_date=snapshot.selection_date,
        feature_set_version_id=snapshot.feature_set_version_id,
        feature_snapshot_id=snapshot.feature_snapshot_id,
        feature_as_of=snapshot.as_of,
        feature_available_at=snapshot.available_at,
        source_snapshot_ids=snapshot.source_snapshot_ids,
        scored_at=scored_at,
        class_labels=registration.class_labels,
        predicted_label=_predicted_label(
            raw_probabilities if calibrated_probabilities is None else calibrated_probabilities,
            registration.class_labels,
        ),
        raw_predicted_label=_predicted_label(raw_probabilities, registration.class_labels),
        raw_probabilities=raw_probabilities,
        calibration_mode="registered" if calibration is not None else "none",
        calibration_id=None if calibration is None else calibration.model.calibration_id,
        calibrated_probabilities=calibrated_probabilities,
        calibrated=calibration is not None,
    )
    return score.model_copy(update={"score_id": _score_id(score)})


def _predict_probabilities(
    booster: Any,
    numpy: Any,
    snapshot: FeatureSnapshot,
    feature_names: Sequence[str],
    labels: Sequence[TrainingLabel],
) -> dict[str, Decimal]:
    try:
        matrix = numpy.asarray(
            [[float(snapshot.values[name]) for name in feature_names]],
            dtype=float,
        )
        values = booster.predict(matrix)
        values = values.tolist() if hasattr(values, "tolist") else values
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        raise FeatureShadowError("LightGBM could not score the feature snapshot") from exc
    probability_values = _probability_values(values, len(labels))
    probabilities: list[Decimal] = []
    for value in probability_values:
        try:
            probability = Decimal(str(float(value)))
        except (TypeError, ValueError) as exc:
            raise FeatureShadowError("LightGBM produced an invalid probability") from exc
        if not probability.is_finite() or not 0 <= probability <= 1:
            raise FeatureShadowError("LightGBM produced an invalid probability")
        probabilities.append(probability)
    total = sum(probabilities, Decimal("0"))
    if total <= 0 or abs(total - Decimal("1")) > Decimal("1e-6"):
        raise FeatureShadowError("LightGBM class probabilities must sum to one")
    normalized = tuple(value / total for value in probabilities)
    return {label.value: value for label, value in zip(labels, normalized, strict=True)}


def _probability_values(values: Any, class_count: int) -> Sequence[Any]:
    if class_count == 2:
        if isinstance(values, (list, tuple)) and len(values) == 1:
            values = values[0]
        if isinstance(values, (list, tuple)):
            if len(values) == 2 and not any(isinstance(item, (list, tuple)) for item in values):
                return values
            if len(values) == 1 and not isinstance(values[0], (list, tuple)):
                values = values[0]
        if isinstance(values, (int, float, Decimal)):
            return (1 - values, values)
        raise FeatureShadowError("LightGBM binary prediction shape is invalid")
    if isinstance(values, (list, tuple)) and len(values) == 1:
        values = values[0]
    if not isinstance(values, (list, tuple)) or len(values) != class_count:
        raise FeatureShadowError("LightGBM multiclass prediction shape is invalid")
    return values


def _apply_registered_calibration(
    raw_probabilities: Mapping[str, Decimal],
    calibration: FeatureCalibrationOutput,
    labels: Sequence[TrainingLabel],
) -> dict[str, Decimal]:
    bins_by_label = {
        label: tuple(item for item in calibration.model.bins if item.label is label)
        for label in labels
    }
    if len(labels) == 2:
        positive = labels[-1]
        calibrated_positive = _apply_bins(
            raw_probabilities[positive.value], bins_by_label[positive]
        )
        return {
            labels[0].value: Decimal("1") - calibrated_positive,
            positive.value: calibrated_positive,
        }
    calibrated = {
        label.value: _apply_bins(raw_probabilities[label.value], bins_by_label[label])
        for label in labels
    }
    total = sum(calibrated.values(), Decimal("0"))
    if total <= 0:
        raise FeatureShadowError("registered calibration produced an empty probability vector")
    return {name: value / total for name, value in calibrated.items()}


def _apply_bins(
    probability: Decimal,
    bins: Sequence[IsotonicBin | FeatureCalibrationBin],
) -> Decimal:
    if not bins:
        raise FeatureShadowError("registered calibration has no bins for model class")
    return next(
        (item.calibrated_probability for item in bins if probability <= item.upper_probability),
        bins[-1].calibrated_probability,
    )


def _validate_inputs(inputs: Sequence[FeatureShadowInput]) -> None:
    if not inputs:
        raise FeatureShadowError("feature shadow input contains no rows")
    candidate_ids = tuple(row.candidate_id for row in inputs)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise FeatureShadowError("feature shadow input contains duplicate candidate IDs")


def _load_lightgbm_runtime(content: bytes) -> tuple[Any, Any]:
    try:
        lightgbm = import_module("lightgbm")
        numpy = import_module("numpy")
        return lightgbm.Booster(model_str=content.decode("utf-8")), numpy
    except (ImportError, OSError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise FeatureShadowError("LightGBM runtime is unavailable or invalid") from exc


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeatureShadowError(f"{name} must include a timezone")


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise FeatureShadowError(f"{name} must not be empty")
