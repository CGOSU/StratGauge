"""Purpose-aware immutable comparison evidence for feature models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from stratgauge.ml._hashing import sha256_bytes, sha256_json
from stratgauge.ml.training_feature_comparison.core import (
    FeatureComparisonError,
)
from stratgauge.ml.training_feature_comparison.helpers_b import (
    _calibration_gate,
    _compare_metric,
    _require_aware,
    _require_text,
    _weighted_metric,
)
from stratgauge.ml.training_feature_comparison.models import (
    FEATURE_COMPARISON_REPORT_VERSION,
    FeatureComparisonModelEvidence,
    FeatureComparisonPolicy,
    FeatureComparisonReport,
    FeatureComparisonStore,
    _is_sha256,
)
from stratgauge.ml.training_feature_registry import (
    FeatureModelRegistryError,
    FeatureModelRegistryStore,
)
from stratgauge.ml.training_feature_walk_forward import (
    FeatureWalkForwardReport,
    FeatureWalkForwardReportStore,
)


def compare_registered_feature_models(
    *,
    registry: FeatureModelRegistryStore,
    walk_forward_store: FeatureWalkForwardReportStore,
    comparison_store: FeatureComparisonStore,
    challenger_registration_id: str,
    challenger_report_id: str,
    reference_registration_id: str | None,
    reference_report_id: str | None,
    policy: FeatureComparisonPolicy,
    compared_at: datetime,
    compared_by: str,
) -> FeatureComparisonReport:
    """Load independent registry/report evidence and produce a manual gate report."""

    challenger = _load_model_evidence(
        registry,
        walk_forward_store,
        challenger_registration_id,
        challenger_report_id,
    )
    reference = None
    if reference_registration_id is not None or reference_report_id is not None:
        if reference_registration_id is None or reference_report_id is None:
            raise FeatureComparisonError("reference registration and report are both required")
        reference = _load_model_evidence(
            registry,
            walk_forward_store,
            reference_registration_id,
            reference_report_id,
        )
    report = compare_feature_model_evidence(
        challenger=challenger,
        reference=reference,
        policy=policy,
        compared_at=compared_at,
        compared_by=compared_by,
    )
    return comparison_store.write(report)


def compare_feature_model_evidence(
    *,
    challenger: FeatureComparisonModelEvidence,
    reference: FeatureComparisonModelEvidence | None,
    policy: FeatureComparisonPolicy,
    compared_at: datetime,
    compared_by: str,
) -> FeatureComparisonReport:
    """Compare same-purpose walk-forward evidence without changing lifecycle state."""

    _require_aware(compared_at, "compared_at")
    _require_text(compared_by, "compared_by")
    _validate_model_evidence(challenger, compared_at)
    if reference is not None:
        _validate_model_evidence(reference, compared_at)
    _validate_policy_and_shared_scope(challenger, reference, policy)
    if reference is None and any(rule.max_relative_regression > 0 for rule in policy.rules):
        raise FeatureComparisonError("relative regression rules require a reference model")
    challenger_metrics = aggregate_feature_walk_forward_metrics(challenger.walk_forward)
    reference_metrics = (
        {} if reference is None else aggregate_feature_walk_forward_metrics(reference.walk_forward)
    )
    checks = tuple(
        _compare_metric(
            rule,
            challenger_metrics,
            reference_metrics if reference is not None else None,
        )
        for rule in policy.rules
    )
    calibration_passed, calibration_reason = _calibration_gate(
        challenger,
        reference,
        policy.require_calibrated,
    )
    report_data = {
        "calibration_passed": calibration_passed,
        "calibration_reason": calibration_reason,
        "calibration_required": policy.require_calibrated,
        "challenger_calibrated": challenger.registration.calibrated,
        "challenger_lifecycle": challenger.registration.lifecycle,
        "challenger_metrics": {name: str(value) for name, value in challenger_metrics.items()},
        "challenger_model_version_id": challenger.registration.model_version_id,
        "challenger_registered_model_sha256": challenger.registration.training_model_sha256,
        "challenger_registered_training_manifest_sha256": (
            challenger.registration.training_manifest_sha256
        ),
        "challenger_registration_id": challenger.registration.registration_id,
        "challenger_walk_forward_report_id": challenger.walk_forward.report_id,
        "challenger_walk_forward_report_sha256": challenger.walk_forward_sha256,
        "checks": [check.model_dump(mode="json") for check in checks],
        "class_labels": [label.value for label in policy.class_labels],
        "compared_at": compared_at.isoformat(),
        "compared_by": compared_by.strip(),
        "feature_names": list(policy.feature_names),
        "feature_set_version_id": policy.feature_set_version_id,
        "label_contract_id": policy.label_contract_id,
        "policy": policy.model_dump(mode="json"),
        "purpose": policy.purpose.value,
        "reference_calibrated": (None if reference is None else reference.registration.calibrated),
        "reference_lifecycle": None if reference is None else reference.registration.lifecycle,
        "reference_metrics": {name: str(value) for name, value in reference_metrics.items()},
        "reference_model_version_id": (
            None if reference is None else reference.registration.model_version_id
        ),
        "reference_registered_model_sha256": (
            None if reference is None else reference.registration.training_model_sha256
        ),
        "reference_registered_training_manifest_sha256": (
            None if reference is None else reference.registration.training_manifest_sha256
        ),
        "reference_registration_id": (
            None if reference is None else reference.registration.registration_id
        ),
        "reference_walk_forward_report_id": (
            None if reference is None else reference.walk_forward.report_id
        ),
        "reference_walk_forward_report_sha256": (
            None if reference is None else reference.walk_forward_sha256
        ),
        "report_version": FEATURE_COMPARISON_REPORT_VERSION,
    }
    gate_passed = all(check.passed for check in checks) and calibration_passed
    report = FeatureComparisonReport(
        report_id="pending",
        policy=policy,
        policy_sha256=policy.policy_sha256,
        purpose=policy.purpose,
        label_contract_id=policy.label_contract_id,
        feature_set_version_id=policy.feature_set_version_id,
        feature_names=policy.feature_names,
        class_labels=policy.class_labels,
        challenger_registration_id=challenger.registration.registration_id,
        challenger_model_version_id=challenger.registration.model_version_id,
        challenger_lifecycle=challenger.registration.lifecycle,
        challenger_walk_forward_report_id=challenger.walk_forward.report_id,
        challenger_walk_forward_report_sha256=challenger.walk_forward_sha256,
        challenger_registered_model_sha256=challenger.registration.training_model_sha256,
        challenger_registered_training_manifest_sha256=(
            challenger.registration.training_manifest_sha256
        ),
        challenger_metrics=challenger_metrics,
        reference_registration_id=(
            None if reference is None else reference.registration.registration_id
        ),
        reference_model_version_id=(
            None if reference is None else reference.registration.model_version_id
        ),
        reference_lifecycle=(None if reference is None else reference.registration.lifecycle),
        reference_walk_forward_report_id=(
            None if reference is None else reference.walk_forward.report_id
        ),
        reference_walk_forward_report_sha256=(
            None if reference is None else reference.walk_forward_sha256
        ),
        reference_registered_model_sha256=(
            None if reference is None else reference.registration.training_model_sha256
        ),
        reference_registered_training_manifest_sha256=(
            None if reference is None else reference.registration.training_manifest_sha256
        ),
        reference_metrics=reference_metrics,
        compared_at=compared_at,
        compared_by=compared_by,
        challenger_calibrated=challenger.registration.calibrated,
        reference_calibrated=(None if reference is None else reference.registration.calibrated),
        calibration_required=policy.require_calibrated,
        calibration_passed=calibration_passed,
        calibration_reason=calibration_reason,
        checks=checks,
        gate_passed=gate_passed,
    )
    report_data = report.model_dump(mode="json")
    report_data.pop("report_id")
    report_id = f"feature-comparison-{sha256_json(report_data)[:20]}"
    return report.model_copy(update={"report_id": report_id})


def aggregate_feature_walk_forward_metrics(
    report: FeatureWalkForwardReport,
) -> dict[str, Decimal]:
    """Aggregate validation metrics by validation-row count, never using final test."""

    total_rows = sum(fold.metrics.row_count for fold in report.folds)
    if total_rows <= 0:
        raise FeatureComparisonError("walk-forward report contains no validation metrics")
    return {
        "validation_log_loss": _weighted_metric(report, "log_loss", total_rows),
        "validation_normalized_brier_score": _weighted_metric(
            report,
            "normalized_brier_score",
            total_rows,
        ),
        "validation_accuracy": _weighted_metric(report, "accuracy", total_rows),
        "validation_coverage": _weighted_metric(report, "coverage", total_rows),
        "validation_row_count": Decimal(total_rows),
        "validation_fold_count": Decimal(len(report.folds)),
    }


def _load_model_evidence(
    registry: FeatureModelRegistryStore,
    walk_forward_store: FeatureWalkForwardReportStore,
    registration_id: str,
    report_id: str,
) -> FeatureComparisonModelEvidence:
    try:
        registration, training, calibration = registry.load_outputs(registration_id)
        report = walk_forward_store.load(report_id)
        report_path = walk_forward_store.report_dir / f"{report_id}.json"
        report_sha256 = sha256_bytes(report_path.read_bytes())
    except (FeatureModelRegistryError, OSError, TypeError, ValueError) as exc:
        raise FeatureComparisonError(
            f"feature comparison evidence could not be loaded: {registration_id}/{report_id}"
        ) from exc
    return FeatureComparisonModelEvidence(
        registration=registration,
        training=training,
        calibration=calibration,
        walk_forward=report,
        walk_forward_sha256=report_sha256,
    )


def _validate_model_evidence(
    evidence: FeatureComparisonModelEvidence,
    compared_at: datetime,
) -> None:
    registration = evidence.registration
    model = evidence.training.model
    report = evidence.walk_forward
    report_data = report.model_dump(mode="json")
    report_id = report_data.pop("report_id")
    expected_report_id = f"feature-walk-forward-{sha256_json(report_data)[:20]}"
    if report_id != expected_report_id:
        raise FeatureComparisonError("walk-forward report identity does not match its content")
    if registration.registration_id != report.registration_id:
        raise FeatureComparisonError("walk-forward report registration does not match registry")
    if registration.model_version_id != report.model_version_id:
        raise FeatureComparisonError("walk-forward report model does not match registry")
    if registration.lifecycle != report.registration_lifecycle:
        raise FeatureComparisonError("walk-forward report lifecycle does not match registry")
    if registration.registered_at != report.registered_at:
        raise FeatureComparisonError(
            "walk-forward report registration time does not match registry"
        )
    if registration.purpose is not report.purpose or model.purpose is not report.purpose:
        raise FeatureComparisonError("walk-forward report purpose does not match registry")
    if registration.label_contract_id != report.label_contract_id:
        raise FeatureComparisonError("walk-forward report label contract does not match registry")
    if registration.feature_set_version_id != report.feature_set_version_id:
        raise FeatureComparisonError("walk-forward report feature set does not match registry")
    if registration.feature_names != report.feature_names:
        raise FeatureComparisonError("walk-forward report feature names do not match registry")
    if registration.class_labels != report.class_labels:
        raise FeatureComparisonError("walk-forward report classes do not match registry")
    lineage = (
        ("registered model", report.registered_model_sha256, registration.training_model_sha256),
        (
            "training manifest",
            report.registered_training_manifest_sha256,
            registration.training_manifest_sha256,
        ),
        (
            "feature manifest",
            report.feature_dataset_manifest_sha256,
            registration.feature_dataset_manifest_sha256,
        ),
        (
            "source labels",
            report.source_label_dataset_sha256,
            registration.source_label_dataset_sha256,
        ),
        (
            "label split",
            report.label_split_manifest_sha256,
            registration.label_split_manifest_sha256,
        ),
    )
    for name, actual, expected in lineage:
        if actual != expected:
            raise FeatureComparisonError(f"walk-forward {name} hash does not match registry")
    if model.model_version_id != registration.model_version_id:
        raise FeatureComparisonError("training model version does not match registry")
    if model.artifact_sha256 != registration.training_runtime_artifact_sha256:
        raise FeatureComparisonError("training runtime artifact does not match registry")
    if evidence.training.model_sha256 != registration.training_model_sha256:
        raise FeatureComparisonError("training model artifact does not match registry")
    if evidence.training.metrics_sha256 != registration.training_metrics_sha256:
        raise FeatureComparisonError("training metrics do not match registry")
    if evidence.training.predictions_sha256 != registration.training_predictions_sha256:
        raise FeatureComparisonError("training predictions do not match registry")
    if evidence.training.manifest_sha256 != registration.training_manifest_sha256:
        raise FeatureComparisonError("training manifest does not match registry")
    if registration.calibrated:
        if evidence.calibration is None:
            raise FeatureComparisonError("calibrated registration is missing calibration output")
        if registration.calibration_id != evidence.calibration.model.calibration_id:
            raise FeatureComparisonError("calibration identity does not match registry")
        if registration.calibrated_at != evidence.calibration.model.calibrated_at:
            raise FeatureComparisonError("calibration time does not match registry")
        calibration_hashes = (
            (registration.calibration_sha256, evidence.calibration.calibration_sha256),
            (registration.calibration_metrics_sha256, evidence.calibration.metrics_sha256),
            (
                registration.calibrated_predictions_sha256,
                evidence.calibration.calibrated_predictions_sha256,
            ),
            (registration.calibration_manifest_sha256, evidence.calibration.manifest_sha256),
        )
        if any(actual != expected for actual, expected in calibration_hashes):
            raise FeatureComparisonError("calibration evidence hash does not match registry")
    elif evidence.calibration is not None:
        raise FeatureComparisonError("uncalibrated registration has calibration output")
    if report.registered_calibrated != registration.calibrated:
        raise FeatureComparisonError("walk-forward calibration state does not match registry")
    if report.registered_calibration_id != registration.calibration_id:
        raise FeatureComparisonError("walk-forward calibration identity does not match registry")
    if report.registered_calibrated_at != registration.calibrated_at:
        raise FeatureComparisonError("walk-forward calibration time does not match registry")
    if report.evaluated_at > compared_at:
        raise FeatureComparisonError("comparison time precedes walk-forward evaluation")
    if registration.registered_at > compared_at:
        raise FeatureComparisonError("comparison time precedes model registration")
    if registration.calibrated_at is not None and registration.calibrated_at > compared_at:
        raise FeatureComparisonError("comparison time precedes model calibration")
    if not _is_sha256(evidence.walk_forward_sha256):
        raise FeatureComparisonError("walk-forward report hash is invalid")


def _validate_policy_and_shared_scope(
    challenger: FeatureComparisonModelEvidence,
    reference: FeatureComparisonModelEvidence | None,
    policy: FeatureComparisonPolicy,
) -> None:
    challenger_report = challenger.walk_forward
    if policy.purpose is not challenger_report.purpose:
        raise FeatureComparisonError("comparison policy purpose does not match challenger")
    if policy.label_contract_id != challenger_report.label_contract_id:
        raise FeatureComparisonError("comparison policy label contract does not match challenger")
    if policy.feature_set_version_id != challenger_report.feature_set_version_id:
        raise FeatureComparisonError("comparison policy feature set does not match challenger")
    if policy.feature_names != challenger_report.feature_names:
        raise FeatureComparisonError("comparison policy feature names do not match challenger")
    if policy.class_labels != challenger_report.class_labels:
        raise FeatureComparisonError("comparison policy classes do not match challenger")
    if reference is None:
        if policy.require_reference_model:
            raise FeatureComparisonError("comparison policy requires a reference model")
        return
    reference_report = reference.walk_forward
    if challenger.registration.registration_id == reference.registration.registration_id:
        raise FeatureComparisonError("challenger and reference registrations must differ")
    if challenger.registration.model_version_id == reference.registration.model_version_id:
        raise FeatureComparisonError("challenger and reference models must differ")
    shared_fields = (
        ("purpose", challenger_report.purpose, reference_report.purpose),
        ("label contract", challenger_report.label_contract_id, reference_report.label_contract_id),
        (
            "feature set",
            challenger_report.feature_set_version_id,
            reference_report.feature_set_version_id,
        ),
        ("feature names", challenger_report.feature_names, reference_report.feature_names),
        ("class labels", challenger_report.class_labels, reference_report.class_labels),
        (
            "source labels",
            challenger_report.source_label_dataset_sha256,
            reference_report.source_label_dataset_sha256,
        ),
        (
            "label split",
            challenger_report.label_split_manifest_sha256,
            reference_report.label_split_manifest_sha256,
        ),
        (
            "final test feature partition",
            challenger_report.final_test_feature_partition_sha256,
            reference_report.final_test_feature_partition_sha256,
        ),
        (
            "final test label partition",
            challenger_report.final_test_label_partition_sha256,
            reference_report.final_test_label_partition_sha256,
        ),
        (
            "purged partition",
            challenger_report.purged_partition_sha256,
            reference_report.purged_partition_sha256,
        ),
        ("final test start", challenger_report.final_test_start, reference_report.final_test_start),
        (
            "final test row count",
            challenger_report.final_test_row_count,
            reference_report.final_test_row_count,
        ),
    )
    for name, challenger_value, reference_value in shared_fields:
        if challenger_value != reference_value:
            raise FeatureComparisonError(f"challenger and reference {name} differ")
    if not _same_fold_scope(challenger_report, reference_report):
        raise FeatureComparisonError("challenger and reference fold scope differs")


def _same_fold_scope(
    challenger: FeatureWalkForwardReport,
    reference: FeatureWalkForwardReport,
) -> bool:
    if len(challenger.folds) != len(reference.folds):
        return False
    for challenger_fold, reference_fold in zip(challenger.folds, reference.folds, strict=True):
        challenger_scope = (
            challenger_fold.fold_id,
            challenger_fold.train_start,
            challenger_fold.train_end,
            challenger_fold.validation_start,
            challenger_fold.validation_end,
            challenger_fold.train_candidate_row_count,
            challenger_fold.train_row_count,
            challenger_fold.validation_row_count,
            challenger_fold.purged_train_row_count,
            tuple(prediction.sample_id for prediction in challenger_fold.predictions),
        )
        reference_scope = (
            reference_fold.fold_id,
            reference_fold.train_start,
            reference_fold.train_end,
            reference_fold.validation_start,
            reference_fold.validation_end,
            reference_fold.train_candidate_row_count,
            reference_fold.train_row_count,
            reference_fold.validation_row_count,
            reference_fold.purged_train_row_count,
            tuple(prediction.sample_id for prediction in reference_fold.predictions),
        )
        if challenger_scope != reference_scope:
            return False
    return True
