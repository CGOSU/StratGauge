"""Explicit, immutable model-comparison gates for manual promotion."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel, ModelLifecycle
from stratgauge.ml.evaluation import ModelEvaluationEvidence, read_model_evaluation
from stratgauge.ml.registry import ModelRegistryError, ModelRegistryStore, RegisteredModel

MODEL_COMPARISON_VERSION: Final[Literal["selection-model-comparison-v1"]] = (
    "selection-model-comparison-v1"
)
ComparisonDirection = Literal["minimize", "maximize"]


class ModelComparisonError(RuntimeError):
    """A comparison cannot produce trustworthy promotion evidence."""


class ComparisonMetricRule(DomainModel):
    """One explicit absolute and relative gate for a named evaluation metric."""

    metric_name: str
    direction: ComparisonDirection
    minimum_value: Decimal | None = None
    maximum_value: Decimal | None = None
    max_regression: Decimal = Field(default=Decimal("0"), ge=0)

    @field_validator("metric_name")
    @classmethod
    def validate_metric_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("comparison metric name must not be empty")
        return value.strip()

    @model_validator(mode="after")
    def validate_bounds(self) -> ComparisonMetricRule:
        if self.minimum_value is None and self.maximum_value is None and self.max_regression == 0:
            raise ValueError("comparison metric rule must define a gate")
        if (
            self.minimum_value is not None
            and self.maximum_value is not None
            and self.minimum_value > self.maximum_value
        ):
            raise ValueError("comparison metric minimum must not exceed maximum")
        return self


class ComparisonPolicy(DomainModel):
    """A caller-supplied policy; no promotion threshold is guessed in code."""

    comparison_policy_id: str
    tracking_policy_id: str
    rules: tuple[ComparisonMetricRule, ...]
    require_incumbent: bool = False
    require_calibrated: bool = False

    @field_validator("comparison_policy_id", "tracking_policy_id")
    @classmethod
    def validate_policy_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("comparison policy text must not be empty")
        return value.strip()

    @model_validator(mode="after")
    def validate_rules(self) -> ComparisonPolicy:
        if not self.rules:
            raise ValueError("comparison policy requires at least one metric rule")
        names = tuple(rule.metric_name for rule in self.rules)
        if len(names) != len(set(names)):
            raise ValueError("comparison policy metric names must be unique")
        return self

    @property
    def policy_sha256(self) -> str:
        return _sha256(_canonical_json(self.model_dump(mode="json")))


class MetricComparison(DomainModel):
    """One auditable result of applying a metric rule."""

    metric_name: str
    direction: ComparisonDirection
    challenger_value: Decimal | None
    incumbent_value: Decimal | None
    minimum_value: Decimal | None
    maximum_value: Decimal | None
    max_regression: Decimal
    passed: bool
    reason: str

    @field_validator("metric_name", "reason")
    @classmethod
    def validate_result_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("comparison result text must not be empty")
        return value.strip()


class ModelComparisonReport(DomainModel):
    """Content-addressed evidence that is required before manual promotion."""

    report_id: str
    report_version: Literal["selection-model-comparison-v1"] = MODEL_COMPARISON_VERSION
    policy: ComparisonPolicy
    policy_sha256: str
    tracking_policy_id: str
    challenger_model_version_id: str
    incumbent_model_version_id: str | None = None
    challenger_evaluation_sha256: str
    incumbent_evaluation_sha256: str | None = None
    compared_at: datetime
    compared_by: str
    challenger_calibrated: bool
    calibration_required: bool
    calibration_reason: str | None = None
    checks: tuple[MetricComparison, ...]
    gate_passed: bool

    @field_validator(
        "report_id",
        "policy_sha256",
        "tracking_policy_id",
        "challenger_model_version_id",
        "challenger_evaluation_sha256",
        "compared_by",
    )
    @classmethod
    def validate_report_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("comparison report text must not be empty")
        return value.strip()

    @field_validator("compared_at")
    @classmethod
    def validate_compared_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("compared_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_report_consistency(self) -> ModelComparisonReport:
        if self.policy_sha256 != self.policy.policy_sha256:
            raise ValueError("comparison policy hash does not match report")
        if self.tracking_policy_id != self.policy.tracking_policy_id:
            raise ValueError("comparison report tracking policy does not match policy")
        if self.calibration_required != self.policy.require_calibrated:
            raise ValueError("comparison report calibration requirement does not match policy")
        if self.calibration_required and self.calibration_reason is None:
            raise ValueError("failed calibration gate requires a reason")
        expected = all(check.passed for check in self.checks) and (
            not self.calibration_required or self.challenger_calibrated
        )
        if self.gate_passed != expected:
            raise ValueError("comparison report gate result is inconsistent")
        return self


class ModelComparisonStore:
    """Persist comparison reports once and expose their content hash."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.comparison_dir = self.root / "models" / "comparisons"
        self.comparison_dir.mkdir(parents=True, exist_ok=True)

    def write(self, report: ModelComparisonReport) -> ModelComparisonReport:
        path = self.comparison_dir / f"{_safe_identifier(report.report_id)}.json"
        content = report.model_dump_json(indent=2).encode("utf-8")
        if path.exists():
            try:
                if path.read_bytes() != content:
                    raise ModelComparisonError(
                        "comparison report identity already has different content"
                    )
            except OSError as exc:
                raise ModelComparisonError("failed to read existing comparison report") from exc
        else:
            temporary = path.with_suffix(f"{path.suffix}.tmp")
            try:
                temporary.write_bytes(content)
                temporary.replace(path)
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise ModelComparisonError("failed to write comparison report") from exc
        return self.load(report.report_id)

    def load(self, report_id: str) -> ModelComparisonReport:
        path = self.comparison_dir / f"{_safe_identifier(report_id)}.json"
        if not path.exists():
            raise ModelComparisonError(f"comparison report does not exist: {report_id}")
        try:
            report = ModelComparisonReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise ModelComparisonError(f"comparison report is invalid: {report_id}") from exc
        if report.report_id != _report_id_from_report(report):
            raise ModelComparisonError("comparison report content does not match its identity")
        return report

    def load_with_sha256(self, report_id: str) -> tuple[ModelComparisonReport, str]:
        path = self.comparison_dir / f"{_safe_identifier(report_id)}.json"
        if not path.exists():
            raise ModelComparisonError(f"comparison report does not exist: {report_id}")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ModelComparisonError(f"failed to read comparison report: {report_id}") from exc
        return self.load(report_id), _sha256(content)


def compare_models(
    *,
    registry: ModelRegistryStore,
    comparison_store: ModelComparisonStore,
    challenger_model_version_id: str,
    policy: ComparisonPolicy,
    compared_at: datetime,
    compared_by: str,
    incumbent_model_version_id: str | None = None,
) -> ModelComparisonReport:
    """Apply an explicit policy to registered evaluation evidence."""

    _require_aware(compared_at, "compared_at")
    _require_text(compared_by, "compared_by")
    challenger = _load_registered(registry, challenger_model_version_id)
    if challenger.lifecycle is not ModelLifecycle.SHADOW:
        raise ModelComparisonError("challenger model must be in shadow lifecycle")
    if challenger.tracking_policy_id != policy.tracking_policy_id:
        raise ModelComparisonError("comparison policy does not match challenger tracking policy")
    if incumbent_model_version_id == challenger_model_version_id:
        raise ModelComparisonError("challenger and incumbent must be different models")

    incumbent: RegisteredModel | None = None
    active = registry.active_models(tracking_policy_id=policy.tracking_policy_id)
    if incumbent_model_version_id is None:
        if policy.require_incumbent:
            raise ModelComparisonError("comparison policy requires an incumbent model")
        if active:
            raise ModelComparisonError("active model exists; incumbent model must be explicit")
    else:
        incumbent = _load_registered(registry, incumbent_model_version_id)
        if incumbent.lifecycle is not ModelLifecycle.ACTIVE:
            raise ModelComparisonError("incumbent model must be active at comparison time")
        if incumbent.tracking_policy_id != policy.tracking_policy_id:
            raise ModelComparisonError("incumbent and policy tracking policies differ")
        if active != (incumbent,):
            raise ModelComparisonError("incumbent must be the sole active model for the policy")

    challenger_evaluation = _load_evaluation(registry, challenger)
    incumbent_evaluation = None if incumbent is None else _load_evaluation(registry, incumbent)
    checks = tuple(
        _compare_metric(
            rule,
            challenger_evaluation.metrics,
            None if incumbent_evaluation is None else incumbent_evaluation.metrics,
        )
        for rule in policy.rules
    )
    calibration_reason = (
        "challenger evaluation is not calibrated"
        if policy.require_calibrated and not challenger_evaluation.calibrated
        else None
    )
    identity = {
        "calibration_reason": calibration_reason,
        "challenger_calibrated": challenger_evaluation.calibrated,
        "challenger_evaluation_sha256": challenger.evaluation_sha256,
        "challenger_model_version_id": challenger.model_version_id,
        "checks": [check.model_dump(mode="json") for check in checks],
        "compared_at": compared_at.isoformat(),
        "compared_by": compared_by.strip(),
        "incumbent_evaluation_sha256": (None if incumbent is None else incumbent.evaluation_sha256),
        "incumbent_model_version_id": (None if incumbent is None else incumbent.model_version_id),
        "policy": policy.model_dump(mode="json"),
        "report_version": MODEL_COMPARISON_VERSION,
        "tracking_policy_id": policy.tracking_policy_id,
    }
    gate_passed = all(check.passed for check in checks) and calibration_reason is None
    report = ModelComparisonReport(
        report_id=f"comparison-{_sha256(_canonical_json(identity))[:20]}",
        policy=policy,
        policy_sha256=policy.policy_sha256,
        tracking_policy_id=policy.tracking_policy_id,
        challenger_model_version_id=challenger.model_version_id,
        incumbent_model_version_id=(None if incumbent is None else incumbent.model_version_id),
        challenger_evaluation_sha256=challenger.evaluation_sha256,
        incumbent_evaluation_sha256=(None if incumbent is None else incumbent.evaluation_sha256),
        compared_at=compared_at,
        compared_by=compared_by,
        challenger_calibrated=challenger_evaluation.calibrated,
        calibration_required=policy.require_calibrated,
        calibration_reason=calibration_reason,
        checks=checks,
        gate_passed=gate_passed,
    )
    return comparison_store.write(report)


def _load_registered(registry: ModelRegistryStore, model_version_id: str) -> RegisteredModel:
    try:
        return registry.load(model_version_id)
    except ModelRegistryError as exc:
        raise ModelComparisonError(str(exc)) from exc


def _load_evaluation(
    registry: ModelRegistryStore,
    registered: RegisteredModel,
) -> ModelEvaluationEvidence:
    try:
        evaluation = read_model_evaluation(
            registry.read_evaluation_report(registered.model_version_id)
        )
    except (ModelRegistryError, ValueError) as exc:
        raise ModelComparisonError(
            f"registered evaluation report is not standardized: {registered.model_version_id}"
        ) from exc
    if (
        evaluation.model_version_id != registered.model_version_id
        or evaluation.model_type != registered.model_type
        or evaluation.feature_set_version_id != registered.feature_set_version_id
        or evaluation.tracking_policy_id != registered.tracking_policy_id
        or evaluation.source_file_sha256 != registered.source_file_sha256
    ):
        raise ModelComparisonError(
            f"registered evaluation metadata does not match model: {registered.model_version_id}"
        )
    return evaluation


def _compare_metric(
    rule: ComparisonMetricRule,
    challenger_metrics: dict[str, Decimal | None],
    incumbent_metrics: dict[str, Decimal | None] | None,
) -> MetricComparison:
    challenger_value = challenger_metrics.get(rule.metric_name)
    incumbent_value = None if incumbent_metrics is None else incumbent_metrics.get(rule.metric_name)
    reasons: list[str] = []
    if challenger_value is None:
        reasons.append("challenger metric is missing")
    else:
        if rule.minimum_value is not None and challenger_value < rule.minimum_value:
            reasons.append("challenger is below minimum")
        if rule.maximum_value is not None and challenger_value > rule.maximum_value:
            reasons.append("challenger is above maximum")
    if incumbent_metrics is not None:
        if incumbent_value is None:
            reasons.append("incumbent metric is missing")
        elif challenger_value is not None:
            regression = (
                challenger_value - incumbent_value
                if rule.direction == "minimize"
                else incumbent_value - challenger_value
            )
            if regression > rule.max_regression:
                reasons.append("challenger regression exceeds policy")
    return MetricComparison(
        metric_name=rule.metric_name,
        direction=rule.direction,
        challenger_value=challenger_value,
        incumbent_value=incumbent_value,
        minimum_value=rule.minimum_value,
        maximum_value=rule.maximum_value,
        max_regression=rule.max_regression,
        passed=not reasons,
        reason="; ".join(reasons) if reasons else "passed",
    )


def _report_id_from_report(report: ModelComparisonReport) -> str:
    identity = {
        "calibration_reason": report.calibration_reason,
        "challenger_calibrated": report.challenger_calibrated,
        "challenger_evaluation_sha256": report.challenger_evaluation_sha256,
        "challenger_model_version_id": report.challenger_model_version_id,
        "checks": [check.model_dump(mode="json") for check in report.checks],
        "compared_at": report.compared_at.isoformat(),
        "compared_by": report.compared_by,
        "incumbent_evaluation_sha256": report.incumbent_evaluation_sha256,
        "incumbent_model_version_id": report.incumbent_model_version_id,
        "policy": report.policy.model_dump(mode="json"),
        "report_version": report.report_version,
        "tracking_policy_id": report.tracking_policy_id,
    }
    return f"comparison-{_sha256(_canonical_json(identity))[:20]}"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ModelComparisonError(f"{name} must include a timezone")


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ModelComparisonError(f"{name} must not be empty")


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(character in normalized for character in ("/", "\\", "\0")):
        raise ModelComparisonError("comparison identifier contains an invalid path character")
    return normalized


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
