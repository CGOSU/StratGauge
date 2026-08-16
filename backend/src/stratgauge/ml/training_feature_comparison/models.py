"""Purpose-aware immutable comparison evidence for feature models."""

from __future__ import annotations

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
from stratgauge.ml.training_feature_comparison.core import (
    FeatureComparisonError,
)
from stratgauge.ml.training_feature_lightgbm_calibration import (
    FeatureCalibrationOutput,
    FeatureLightGBMOutput,
)
from stratgauge.ml.training_feature_registry import (
    FeatureModelLifecycle,
    FeatureModelRegistration,
)
from stratgauge.ml.training_feature_walk_forward import (
    FeatureWalkForwardReport,
)

FEATURE_COMPARISON_REPORT_VERSION = "selection-purpose-feature-comparison-v1"


ComparisonDirection = Literal["minimize", "maximize"]


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


def _is_sha256(value: str | None) -> bool:
    return (
        value is not None and len(value) == 64 and all(char in "0123456789abcdef" for char in value)
    )


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


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(
        character in normalized for character in ("/", "\\", "\0", "\r", "\n")
    ):
        raise FeatureComparisonError("feature comparison identifier contains an invalid path")
    return normalized


def _validate_report_identity(report: FeatureComparisonReport) -> None:
    data = report.model_dump(mode="json")
    report_id = data.pop("report_id")
    expected = f"feature-comparison-{sha256_json(data)[:20]}"
    if report_id != expected:
        raise ValueError("feature comparison report identity does not match content")
