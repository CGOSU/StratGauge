"""Explicit application workflows for ML research and lifecycle evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stratgauge.ml.training_feature_baseline import read_training_feature_split
from stratgauge.ml.training_feature_comparison import (
    FeatureComparisonError,
    FeatureComparisonPolicy,
    FeatureComparisonReport,
    FeatureComparisonStore,
    compare_registered_feature_models,
)
from stratgauge.ml.training_feature_lightgbm_calibration import (
    FeatureCalibrationError,
    FeatureCalibrationResult,
    calibrate_feature_lightgbm,
    read_feature_lightgbm_output,
    write_feature_calibration_result,
)
from stratgauge.ml.training_feature_registry import (
    FeatureModelLifecycle,
    FeatureModelRegistration,
    FeatureModelRegistryError,
    FeatureModelRegistryStore,
)
from stratgauge.ml.training_feature_shadow import (
    FeatureShadowCalibrationMode,
    FeatureShadowError,
    FeatureShadowReport,
    FeatureShadowScoreStore,
    feature_shadow_input_sha256,
    read_feature_shadow_inputs,
    score_feature_shadow_batch,
)
from stratgauge.ml.training_feature_walk_forward import (
    FeatureWalkForwardFoldSpec,
    FeatureWalkForwardReport,
    FeatureWalkForwardReportStore,
    FeatureWalkForwardUnavailableError,
    validate_feature_walk_forward,
)


@dataclass(frozen=True)
class CalibrateFeatureProbabilitiesRequest:
    """Inputs for one manually invoked validation-only calibration stage."""

    input_dir: Path
    output_dir: Path
    calibrated_at: datetime
    minimum_validation_samples: int = 100


@dataclass(frozen=True)
class CalibratedFeatureProbabilitiesResult:
    """Evidence returned after writing one calibration result."""

    output_dir: Path
    result: FeatureCalibrationResult


@dataclass(frozen=True)
class RegisterFeatureModelRequest:
    """Inputs for one manually invoked challenger registration stage."""

    root: Path
    training_dir: Path
    calibration_dir: Path | None
    registered_at: datetime
    registered_by: str


@dataclass(frozen=True)
class RegisteredFeatureModelResult:
    """Registration returned by one explicit registration stage."""

    registration: FeatureModelRegistration


@dataclass(frozen=True)
class TransitionFeatureModelRequest:
    """Inputs for one manually invoked lifecycle transition stage."""

    root: Path
    registration_id: str
    to_lifecycle: FeatureModelLifecycle
    changed_at: datetime
    reason: str
    operator: str


@dataclass(frozen=True)
class TransitionedFeatureModelResult:
    """Registration returned after one manual lifecycle transition."""

    registration: FeatureModelRegistration


@dataclass(frozen=True)
class ScoreFeatureShadowRequest:
    """Inputs for one manually invoked shadow scoring stage."""

    root: Path
    registration_id: str
    input_path: Path
    source_input_sha256: str
    scored_at: datetime
    calibration_mode: FeatureShadowCalibrationMode
    output_path: Path


@dataclass(frozen=True)
class ScoredFeatureShadowResult:
    """Report returned after one explicit shadow scoring stage."""

    report: FeatureShadowReport
    output_path: Path


@dataclass(frozen=True)
class ValidateFeatureWalkForwardRequest:
    """Inputs for one manually invoked walk-forward evidence stage."""

    root: Path
    input_dir: Path
    registration_id: str
    folds_path: Path
    evaluated_at: datetime


@dataclass(frozen=True)
class ValidatedFeatureWalkForwardResult:
    """Stored report returned after one walk-forward stage."""

    report: FeatureWalkForwardReport


@dataclass(frozen=True)
class CompareFeatureModelsRequest:
    """Inputs for one manually invoked model-comparison evidence stage."""

    root: Path
    policy_path: Path
    challenger_registration_id: str
    challenger_report_id: str
    reference_registration_id: str | None
    reference_report_id: str | None
    compared_at: datetime
    compared_by: str


@dataclass(frozen=True)
class ComparedFeatureModelsResult:
    """Report and exact stored bytes hash returned by comparison."""

    report: FeatureComparisonReport
    report_sha256: str


def run_calibrate_feature_probabilities(
    request: CalibrateFeatureProbabilitiesRequest,
) -> CalibratedFeatureProbabilitiesResult:
    """Calibrate one training output and write independent research evidence."""

    training = read_feature_lightgbm_output(request.input_dir)
    result = calibrate_feature_lightgbm(
        training.model,
        training.predictions,
        source_prediction_sha256=training.predictions_sha256,
        source_model_sha256=training.model_sha256,
        source_manifest_sha256=training.manifest_sha256,
        calibrated_at=request.calibrated_at,
        minimum_validation_samples=request.minimum_validation_samples,
    )
    write_feature_calibration_result(request.output_dir, result)
    return CalibratedFeatureProbabilitiesResult(output_dir=request.output_dir, result=result)


def run_register_feature_model(
    request: RegisterFeatureModelRequest,
) -> RegisteredFeatureModelResult:
    """Register verified evidence as a challenger without changing lifecycle."""

    registration = FeatureModelRegistryStore(request.root).register(
        request.training_dir,
        calibration_output_dir=request.calibration_dir,
        registered_at=request.registered_at,
        registered_by=request.registered_by,
    )
    return RegisteredFeatureModelResult(registration=registration)


def run_transition_feature_model(
    request: TransitionFeatureModelRequest,
) -> TransitionedFeatureModelResult:
    """Perform one explicitly requested model lifecycle transition."""

    registration = FeatureModelRegistryStore(request.root).transition(
        request.registration_id,
        request.to_lifecycle,
        changed_at=request.changed_at,
        reason=request.reason,
        operator=request.operator,
    )
    return TransitionedFeatureModelResult(registration=registration)


def run_score_feature_shadow(
    request: ScoreFeatureShadowRequest,
) -> ScoredFeatureShadowResult:
    """Score one explicit input through one already enabled shadow model."""

    inputs = read_feature_shadow_inputs(request.input_path.read_bytes())
    if feature_shadow_input_sha256(inputs) != request.source_input_sha256:
        raise FeatureShadowError("feature shadow input hash does not match --source-input-sha256")
    report = score_feature_shadow_batch(
        inputs,
        registry=FeatureModelRegistryStore(request.root),
        score_store=FeatureShadowScoreStore(request.root),
        registration_id=request.registration_id,
        scored_at=request.scored_at,
        calibration_mode=request.calibration_mode,
    )
    _write_shadow_report(request.output_path, report.model_dump_json(indent=2) + "\n")
    return ScoredFeatureShadowResult(report=report, output_path=request.output_path)


def run_validate_feature_walk_forward(
    request: ValidateFeatureWalkForwardRequest,
) -> ValidatedFeatureWalkForwardResult:
    """Validate one registered model over explicitly supplied walk-forward folds."""

    split_input = read_training_feature_split(request.input_dir)
    registry = FeatureModelRegistryStore(request.root)
    registration, training_output, calibration_output = registry.load_outputs(
        request.registration_id
    )
    fold_payload = json.loads(request.folds_path.read_text(encoding="utf-8"))
    if not isinstance(fold_payload, list):
        raise ValueError("--folds must contain a JSON array")
    folds = tuple(FeatureWalkForwardFoldSpec.model_validate(item) for item in fold_payload)
    report = validate_feature_walk_forward(
        split_input,
        registration=registration,
        training_output=training_output,
        calibration_output=calibration_output,
        folds=folds,
        evaluated_at=request.evaluated_at,
    )
    stored = FeatureWalkForwardReportStore(request.root).write(report)
    return ValidatedFeatureWalkForwardResult(report=stored)


def run_compare_feature_models(
    request: CompareFeatureModelsRequest,
) -> ComparedFeatureModelsResult:
    """Compare registered evidence and write independent review evidence."""

    policy = FeatureComparisonPolicy.model_validate_json(request.policy_path.read_bytes())
    comparison_store = FeatureComparisonStore(request.root)
    report = compare_registered_feature_models(
        registry=FeatureModelRegistryStore(request.root),
        walk_forward_store=FeatureWalkForwardReportStore(request.root),
        comparison_store=comparison_store,
        challenger_registration_id=request.challenger_registration_id,
        challenger_report_id=request.challenger_report_id,
        reference_registration_id=request.reference_registration_id,
        reference_report_id=request.reference_report_id,
        policy=policy,
        compared_at=request.compared_at,
        compared_by=request.compared_by,
    )
    _, report_sha256 = comparison_store.load_with_sha256(report.report_id)
    return ComparedFeatureModelsResult(report=report, report_sha256=report_sha256)


def _write_shadow_report(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"feature shadow report already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "CalibrateFeatureProbabilitiesRequest",
    "CalibratedFeatureProbabilitiesResult",
    "CompareFeatureModelsRequest",
    "ComparedFeatureModelsResult",
    "FeatureCalibrationError",
    "FeatureComparisonError",
    "FeatureModelLifecycle",
    "FeatureModelRegistryError",
    "FeatureShadowCalibrationMode",
    "FeatureShadowError",
    "FeatureWalkForwardUnavailableError",
    "RegisterFeatureModelRequest",
    "RegisteredFeatureModelResult",
    "ScoreFeatureShadowRequest",
    "ScoredFeatureShadowResult",
    "TransitionFeatureModelRequest",
    "TransitionedFeatureModelResult",
    "ValidateFeatureWalkForwardRequest",
    "ValidatedFeatureWalkForwardResult",
    "run_calibrate_feature_probabilities",
    "run_compare_feature_models",
    "run_register_feature_model",
    "run_score_feature_shadow",
    "run_transition_feature_model",
    "run_validate_feature_walk_forward",
]
