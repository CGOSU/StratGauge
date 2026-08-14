"""Purpose-aware immutable comparison evidence for feature models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.domain.models import DomainModel
from stratgauge.ml._hashing import sha256_bytes, sha256_json
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    TrainingLabel,
    label_contract_id_for,
)
from stratgauge.ml.training_feature_lightgbm_calibration import (
    FeatureCalibrationOutput,
    FeatureLightGBMOutput,
)
from stratgauge.ml.training_feature_registry import (
    FeatureModelLifecycle,
    FeatureModelRegistration,
    FeatureModelRegistryError,
    FeatureModelRegistryStore,
)
from stratgauge.ml.training_feature_walk_forward import (
    FeatureWalkForwardReport,
    FeatureWalkForwardReportStore,
)

FEATURE_COMPARISON_REPORT_VERSION = "selection-purpose-feature-comparison-v1"
ComparisonDirection = Literal["minimize", "maximize"]


class FeatureComparisonError(RuntimeError):
    """A purpose-aware model comparison cannot produce trustworthy evidence."""


class FeatureComparisonMetricRule(DomainModel):
    """One explicit absolute and relative gate for an aggregate walk-forward metric."""

    metric_name: str
    direction: ComparisonDirection
    minimum_value: Decimal | None = None
    maximum_value: Decimal | None = None
    max_relative_regression: Decimal = Field(default=Decimal("0"), ge=0)

    @field_validator("metric_name")
    @classmethod
    def validate_metric_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature comparison metric name must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_bounds(self) -> FeatureComparisonMetricRule:
        if (
            self.minimum_value is None
            and self.maximum_value is None
            and self.max_relative_regression == 0
        ):
            raise ValueError("feature comparison metric rule must define a gate")
        if (
            self.minimum_value is not None
            and self.maximum_value is not None
            and self.minimum_value > self.maximum_value
        ):
            raise ValueError("feature comparison metric minimum must not exceed maximum")
        return self


class FeatureComparisonPolicy(DomainModel):
    """Caller-supplied comparison policy with no implicit model threshold."""

    comparison_policy_id: str
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    feature_names: tuple[str, ...]
    class_labels: tuple[TrainingLabel, ...]
    rules: tuple[FeatureComparisonMetricRule, ...]
    require_reference_model: bool
    require_calibrated: bool

    @field_validator(
        "comparison_policy_id",
        "label_contract_id",
        "feature_set_version_id",
    )
    @classmethod
    def validate_policy_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature comparison policy text must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_contract(self) -> FeatureComparisonPolicy:
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("feature comparison policy label contract does not match purpose")
        if self.class_labels != _labels_for(self.purpose):
            raise ValueError("feature comparison policy classes do not match purpose")
        if not self.feature_names or tuple(sorted(set(self.feature_names))) != self.feature_names:
            raise ValueError("feature comparison policy feature names must be sorted and non-empty")
        if not self.rules:
            raise ValueError("feature comparison policy requires at least one metric rule")
        names = tuple(rule.metric_name for rule in self.rules)
        if len(names) != len(set(names)):
            raise ValueError("feature comparison policy metric names must be unique")
        return self

    @property
    def policy_sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class FeatureMetricComparison(DomainModel):
    """Auditable result of applying one explicit rule."""

    metric_name: str
    direction: ComparisonDirection
    challenger_value: Decimal | None
    reference_value: Decimal | None
    minimum_value: Decimal | None
    maximum_value: Decimal | None
    max_relative_regression: Decimal
    relative_regression: Decimal | None
    passed: bool
    reason: str

    @field_validator("metric_name", "reason")
    @classmethod
    def validate_result_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature comparison result text must not be empty")
        return normalized


class FeatureComparisonReport(DomainModel):
    """Immutable human-review evidence; it never changes model lifecycle."""

    report_id: str
    report_version: Literal["selection-purpose-feature-comparison-v1"] = (
        "selection-purpose-feature-comparison-v1"
    )
    policy: FeatureComparisonPolicy
    policy_sha256: str
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    feature_names: tuple[str, ...]
    class_labels: tuple[TrainingLabel, ...]
    challenger_registration_id: str
    challenger_model_version_id: str
    challenger_lifecycle: FeatureModelLifecycle
    challenger_walk_forward_report_id: str
    challenger_walk_forward_report_sha256: str
    challenger_registered_model_sha256: str
    challenger_registered_training_manifest_sha256: str
    challenger_metrics: dict[str, Decimal]
    reference_registration_id: str | None = None
    reference_model_version_id: str | None = None
    reference_lifecycle: FeatureModelLifecycle | None = None
    reference_walk_forward_report_id: str | None = None
    reference_walk_forward_report_sha256: str | None = None
    reference_registered_model_sha256: str | None = None
    reference_registered_training_manifest_sha256: str | None = None
    reference_metrics: dict[str, Decimal] = Field(default_factory=dict)
    compared_at: datetime
    compared_by: str
    challenger_calibrated: bool
    reference_calibrated: bool | None
    calibration_required: bool
    calibration_passed: bool
    calibration_reason: str | None = None
    checks: tuple[FeatureMetricComparison, ...]
    gate_passed: bool

    @field_validator(
        "report_id",
        "policy_sha256",
        "label_contract_id",
        "feature_set_version_id",
        "challenger_registration_id",
        "challenger_model_version_id",
        "challenger_walk_forward_report_id",
        "compared_by",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature comparison report text must not be empty")
        return normalized

    @field_validator(
        "challenger_walk_forward_report_sha256",
        "challenger_registered_model_sha256",
        "challenger_registered_training_manifest_sha256",
        "reference_walk_forward_report_sha256",
        "reference_registered_model_sha256",
        "reference_registered_training_manifest_sha256",
    )
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not _is_sha256(normalized):
            raise ValueError("feature comparison evidence must be a SHA-256 digest")
        return normalized

    @field_validator("compared_at")
    @classmethod
    def validate_compared_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("compared_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_report(self) -> FeatureComparisonReport:
        if self.report_version != FEATURE_COMPARISON_REPORT_VERSION:
            raise ValueError("unsupported feature comparison report version")
        if self.policy_sha256 != self.policy.policy_sha256:
            raise ValueError("feature comparison policy hash does not match report")
        if self.purpose is not self.policy.purpose:
            raise ValueError("feature comparison report purpose does not match policy")
        if self.label_contract_id != self.policy.label_contract_id:
            raise ValueError("feature comparison report contract does not match policy")
        if self.feature_set_version_id != self.policy.feature_set_version_id:
            raise ValueError("feature comparison report feature set does not match policy")
        if self.feature_names != self.policy.feature_names:
            raise ValueError("feature comparison report feature names do not match policy")
        if self.class_labels != self.policy.class_labels:
            raise ValueError("feature comparison report classes do not match policy")
        if self.calibration_required != self.policy.require_calibrated:
            raise ValueError("feature comparison calibration requirement does not match policy")
        if (
            self.calibration_required
            and not self.calibration_passed
            and self.calibration_reason is None
        ):
            raise ValueError("failed feature comparison calibration gate requires a reason")
        has_reference = self.reference_registration_id is not None
        reference_fields = (
            self.reference_model_version_id,
            self.reference_lifecycle,
            self.reference_walk_forward_report_id,
            self.reference_walk_forward_report_sha256,
            self.reference_registered_model_sha256,
            self.reference_registered_training_manifest_sha256,
        )
        if has_reference and any(value is None for value in reference_fields):
            raise ValueError("feature comparison reference evidence is incomplete")
        if not has_reference and any(value is not None for value in reference_fields):
            raise ValueError("feature comparison has unexpected reference evidence")
        if has_reference and not self.reference_metrics:
            raise ValueError("feature comparison reference metrics are missing")
        if self.policy.require_reference_model and not has_reference:
            raise ValueError("feature comparison policy requires reference evidence")
        expected_gate = all(check.passed for check in self.checks) and self.calibration_passed
        if self.gate_passed != expected_gate:
            raise ValueError("feature comparison gate result is inconsistent")
        return self


@dataclass(frozen=True)
class FeatureComparisonModelEvidence:
    """Verified registration, training artifact and walk-forward evidence."""

    registration: FeatureModelRegistration
    training: FeatureLightGBMOutput
    calibration: FeatureCalibrationOutput | None
    walk_forward: FeatureWalkForwardReport
    walk_forward_sha256: str


class FeatureComparisonStore:
    """Persist feature comparison reports under the independent feature registry."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.comparison_dir = self.root / "models" / "feature-registry" / "comparisons"
        self.comparison_dir.mkdir(parents=True, exist_ok=True)

    def write(self, report: FeatureComparisonReport) -> FeatureComparisonReport:
        """Write immutable comparison evidence with byte-identical idempotency."""

        _validate_report_identity(report)
        content = (report.model_dump_json(indent=2) + "\n").encode("utf-8")
        path = self._path(report.report_id)
        with local_write_lock(self.root, name="feature-comparison"):
            if path.exists():
                try:
                    if path.read_bytes() != content:
                        raise FeatureComparisonError(
                            "feature comparison report identity already has different content"
                        )
                except OSError as exc:
                    raise FeatureComparisonError(
                        "failed to read existing feature comparison report"
                    ) from exc
            else:
                temporary = path.with_suffix(".json.tmp")
                try:
                    temporary.write_bytes(content)
                    temporary.replace(path)
                except OSError as exc:
                    temporary.unlink(missing_ok=True)
                    raise FeatureComparisonError(
                        "failed to write feature comparison report"
                    ) from exc
        return self.load(report.report_id)

    def load(self, report_id: str) -> FeatureComparisonReport:
        """Load and verify one immutable comparison report."""

        normalized = _safe_identifier(report_id)
        path = self._path(normalized)
        try:
            report = FeatureComparisonReport.model_validate_json(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise FeatureComparisonError(
                f"feature comparison report does not exist: {normalized}"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise FeatureComparisonError(
                f"feature comparison report is invalid: {normalized}"
            ) from exc
        if report.report_id != normalized:
            raise FeatureComparisonError(
                "feature comparison report identity does not match filename"
            )
        try:
            _validate_report_identity(report)
        except ValueError as exc:
            raise FeatureComparisonError(str(exc)) from exc
        return report

    def load_with_sha256(self, report_id: str) -> tuple[FeatureComparisonReport, str]:
        """Load a report and return the exact stored-byte SHA-256."""

        normalized = _safe_identifier(report_id)
        path = self._path(normalized)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise FeatureComparisonError(
                f"feature comparison report does not exist: {normalized}"
            ) from exc
        return self.load(normalized), sha256_bytes(content)

    def _path(self, report_id: str) -> Path:
        return self.comparison_dir / f"{_safe_identifier(report_id)}.json"


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


def _compare_metric(
    rule: FeatureComparisonMetricRule,
    challenger_metrics: Mapping[str, Decimal],
    reference_metrics: Mapping[str, Decimal] | None,
) -> FeatureMetricComparison:
    challenger_value = challenger_metrics.get(rule.metric_name)
    reference_value = None
    reasons: list[str] = []
    if challenger_value is None:
        return FeatureMetricComparison(
            metric_name=rule.metric_name,
            direction=rule.direction,
            challenger_value=None,
            reference_value=(
                None if reference_metrics is None else reference_metrics.get(rule.metric_name)
            ),
            minimum_value=rule.minimum_value,
            maximum_value=rule.maximum_value,
            max_relative_regression=rule.max_relative_regression,
            relative_regression=None,
            passed=False,
            reason="challenger aggregate metric is missing",
        )
    if rule.minimum_value is not None and challenger_value < rule.minimum_value:
        reasons.append("challenger is below minimum")
    if rule.maximum_value is not None and challenger_value > rule.maximum_value:
        reasons.append("challenger is above maximum")
    relative_regression = None
    if reference_metrics is not None:
        reference_value = reference_metrics.get(rule.metric_name)
        if reference_value is None:
            return FeatureMetricComparison(
                metric_name=rule.metric_name,
                direction=rule.direction,
                challenger_value=challenger_value,
                reference_value=None,
                minimum_value=rule.minimum_value,
                maximum_value=rule.maximum_value,
                max_relative_regression=rule.max_relative_regression,
                relative_regression=None,
                passed=False,
                reason="reference aggregate metric is missing",
            )
        relative_regression = _relative_regression(
            challenger_value,
            reference_value,
            rule.direction,
        )
        if relative_regression is None:
            reasons.append("reference metric is zero; relative regression is undefined")
        elif relative_regression > rule.max_relative_regression:
            reasons.append("challenger relative regression exceeds policy")
    elif rule.max_relative_regression > 0:
        raise FeatureComparisonError(f"reference aggregate metric is required: {rule.metric_name}")
    return FeatureMetricComparison(
        metric_name=rule.metric_name,
        direction=rule.direction,
        challenger_value=challenger_value,
        reference_value=reference_value,
        minimum_value=rule.minimum_value,
        maximum_value=rule.maximum_value,
        max_relative_regression=rule.max_relative_regression,
        relative_regression=relative_regression,
        passed=not reasons,
        reason="; ".join(reasons) if reasons else "passed",
    )


def _relative_regression(
    challenger: Decimal,
    reference: Decimal,
    direction: ComparisonDirection,
) -> Decimal | None:
    unfavorable = challenger - reference if direction == "minimize" else reference - challenger
    if unfavorable <= 0:
        return Decimal("0")
    denominator = abs(reference)
    if denominator == 0:
        return None
    return unfavorable / denominator


def _calibration_gate(
    challenger: FeatureComparisonModelEvidence,
    reference: FeatureComparisonModelEvidence | None,
    required: bool,
) -> tuple[bool, str | None]:
    if not required:
        return True, None
    if not challenger.registration.calibrated:
        return False, "challenger registration is not calibrated"
    if reference is not None and not reference.registration.calibrated:
        return False, "reference registration is not calibrated"
    return True, None


def _weighted_metric(
    report: FeatureWalkForwardReport,
    field_name: Literal["log_loss", "normalized_brier_score", "accuracy", "coverage"],
    total_rows: int,
) -> Decimal:
    value = sum(
        Decimal(str(getattr(fold.metrics, field_name))) * Decimal(fold.metrics.row_count)
        for fold in report.folds
    )
    return value / Decimal(total_rows)


def _validate_report_identity(report: FeatureComparisonReport) -> None:
    data = report.model_dump(mode="json")
    report_id = data.pop("report_id")
    expected = f"feature-comparison-{sha256_json(data)[:20]}"
    if report_id != expected:
        raise ValueError("feature comparison report identity does not match content")


def _labels_for(purpose: TrainingDatasetPurpose) -> tuple[TrainingLabel, ...]:
    return {
        TrainingDatasetPurpose.CANDIDATE_SUCCESS: (
            TrainingLabel.NOT_SUCCESS,
            TrainingLabel.SUCCESS,
        ),
        TrainingDatasetPurpose.ANNOTATION_DECISION: (
            TrainingLabel.DISCARD,
            TrainingLabel.WATCH,
            TrainingLabel.PREPARE_BUY,
        ),
        TrainingDatasetPurpose.BUY_TIMING: (
            TrainingLabel.NOT_PURCHASED,
            TrainingLabel.PURCHASED,
        ),
        TrainingDatasetPurpose.SELL_TIMING: (TrainingLabel.NOT_SOLD, TrainingLabel.SOLD),
    }[purpose]


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeatureComparisonError(f"{name} must include a timezone")


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise FeatureComparisonError(f"{name} must not be empty")


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(
        character in normalized for character in ("/", "\\", "\0", "\r", "\n")
    ):
        raise FeatureComparisonError("feature comparison identifier contains an invalid path")
    return normalized


def _is_sha256(value: str | None) -> bool:
    return (
        value is not None and len(value) == 64 and all(char in "0123456789abcdef" for char in value)
    )
