"""Deterministic, read-only feature-distribution drift evidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel

DRIFT_REPORT_VERSION: Literal["selection-feature-drift-v1"] = "selection-feature-drift-v1"


class DriftError(RuntimeError):
    """A drift report cannot be produced or safely read."""


class DriftPolicy(DomainModel):
    """Explicit thresholds; no threshold is inferred from observed data."""

    drift_policy_id: str
    feature_names: tuple[str, ...]
    minimum_samples: int = Field(default=100, gt=0)
    bin_count: int = Field(default=10, ge=2, le=50)
    psi_threshold: Decimal = Field(default=Decimal("0.2"), ge=0)
    mean_delta_threshold: Decimal = Field(default=Decimal("0"), ge=0)

    @field_validator("drift_policy_id")
    @classmethod
    def validate_policy_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("drift policy ID must not be empty")
        return value.strip()

    @field_validator("feature_names")
    @classmethod
    def validate_feature_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        names = tuple(item.strip() for item in value)
        if not names or any(not item for item in names):
            raise ValueError("drift policy requires feature names")
        if len(names) != len(set(names)):
            raise ValueError("drift policy feature names must be unique")
        return names


class FeatureDrift(DomainModel):
    """One feature's distribution comparison."""

    feature_name: str
    baseline_count: int = Field(ge=0)
    current_count: int = Field(ge=0)
    baseline_mean: Decimal
    current_mean: Decimal
    mean_delta: Decimal
    psi: Decimal = Field(ge=0)
    drifted: bool
    reason: str


class FeatureDriftReport(DomainModel):
    """Content-addressed drift evidence; it never changes model lifecycle."""

    report_id: str
    report_version: Literal["selection-feature-drift-v1"] = DRIFT_REPORT_VERSION
    policy: DriftPolicy
    baseline_source_sha256: str
    current_source_sha256: str
    baseline_sample_count: int = Field(ge=0)
    current_sample_count: int = Field(ge=0)
    generated_at: datetime
    features: tuple[FeatureDrift, ...]
    gate_passed: bool

    @field_validator(
        "report_id",
        "baseline_source_sha256",
        "current_source_sha256",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("drift report text must not be empty")
        return value.strip()

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("drift generated_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_report(self) -> FeatureDriftReport:
        if self.baseline_sample_count < self.policy.minimum_samples:
            raise ValueError("baseline sample count is below drift policy minimum")
        if self.current_sample_count < self.policy.minimum_samples:
            raise ValueError("current sample count is below drift policy minimum")
        if tuple(item.feature_name for item in self.features) != self.policy.feature_names:
            raise ValueError("drift report feature order does not match policy")
        expected = not any(item.drifted for item in self.features)
        if self.gate_passed != expected:
            raise ValueError("drift report gate result is inconsistent")
        return self


class FeatureDriftStore:
    """Write and read immutable drift reports."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.report_dir = self.root / "models" / "drift-reports"
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def write(self, report: FeatureDriftReport) -> FeatureDriftReport:
        path = self.report_dir / f"{report.report_id}.json"
        content = report.model_dump_json(indent=2)
        if path.exists():
            try:
                if path.read_text(encoding="utf-8") != content:
                    raise DriftError("drift report identity already has different content")
            except OSError as exc:
                raise DriftError("failed to read existing drift report") from exc
        else:
            temporary = path.with_suffix(".json.tmp")
            try:
                temporary.write_text(content, encoding="utf-8", newline="\n")
                temporary.replace(path)
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise DriftError("failed to write drift report") from exc
        return self.load(report.report_id)

    def load(self, report_id: str) -> FeatureDriftReport:
        path = self.report_dir / f"{report_id}.json"
        if not path.exists():
            raise DriftError(f"drift report does not exist: {report_id}")
        try:
            report = FeatureDriftReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise DriftError(f"drift report is invalid: {report_id}") from exc
        if report.report_id != _report_id(report):
            raise DriftError("drift report content does not match its identity")
        return report


def build_drift_report(
    *,
    baseline_rows: Sequence[Mapping[str, Any]],
    current_rows: Sequence[Mapping[str, Any]],
    baseline_source_sha256: str,
    current_source_sha256: str,
    policy: DriftPolicy,
    generated_at: datetime,
) -> FeatureDriftReport:
    if len(baseline_rows) < policy.minimum_samples:
        raise DriftError("baseline sample count is below drift policy minimum")
    if len(current_rows) < policy.minimum_samples:
        raise DriftError("current sample count is below drift policy minimum")
    baseline_values = {name: _values(baseline_rows, name) for name in policy.feature_names}
    current_values = {name: _values(current_rows, name) for name in policy.feature_names}
    features: list[FeatureDrift] = []
    for name in policy.feature_names:
        baseline = baseline_values[name]
        current = current_values[name]
        baseline_mean = _mean(baseline)
        current_mean = _mean(current)
        mean_delta = current_mean - baseline_mean
        psi = _psi(baseline, current, policy.bin_count)
        reasons: list[str] = []
        if psi > policy.psi_threshold:
            reasons.append(f"psi>{policy.psi_threshold}")
        if abs(mean_delta) > policy.mean_delta_threshold:
            reasons.append(f"mean_delta>{policy.mean_delta_threshold}")
        features.append(
            FeatureDrift(
                feature_name=name,
                baseline_count=len(baseline),
                current_count=len(current),
                baseline_mean=baseline_mean,
                current_mean=current_mean,
                mean_delta=mean_delta,
                psi=psi,
                drifted=bool(reasons),
                reason="; ".join(reasons) if reasons else "within policy",
            )
        )
    report = FeatureDriftReport(
        report_id="drift-pending",
        policy=policy,
        baseline_source_sha256=baseline_source_sha256,
        current_source_sha256=current_source_sha256,
        baseline_sample_count=len(baseline_rows),
        current_sample_count=len(current_rows),
        generated_at=generated_at,
        features=tuple(features),
        gate_passed=not any(item.drifted for item in features),
    )
    return report.model_copy(update={"report_id": _report_id(report)})


def _values(rows: Sequence[Mapping[str, Any]], feature_name: str) -> tuple[float, ...]:
    values: list[float] = []
    for row in rows:
        source: object = row.get("values", row)
        if not isinstance(source, Mapping) or feature_name not in source:
            raise DriftError(f"feature is missing from drift input: {feature_name}")
        try:
            value = float(Decimal(str(source[feature_name])))
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise DriftError(f"feature value is not numeric: {feature_name}") from exc
        if not math.isfinite(value):
            raise DriftError(f"feature value is not finite: {feature_name}")
        values.append(value)
    if not values:
        raise DriftError(f"feature has no values: {feature_name}")
    return tuple(values)


def _mean(values: Sequence[float]) -> Decimal:
    return Decimal(str(sum(values) / len(values))).quantize(Decimal("0.00000001"))


def _psi(baseline: Sequence[float], current: Sequence[float], bin_count: int) -> Decimal:
    low = min(baseline)
    high = max(baseline)
    if low == high:
        span = max(abs(low) * 0.01, 1.0)
        low -= span
        high += span
    width = (high - low) / bin_count
    baseline_counts = [0] * bin_count
    current_counts = [0] * bin_count
    for value in baseline:
        baseline_counts[_bin_index(value, low, high, width, bin_count)] += 1
    for value in current:
        current_counts[_bin_index(value, low, high, width, bin_count)] += 1
    epsilon = 1e-6
    total_baseline = len(baseline)
    total_current = len(current)
    result = 0.0
    for baseline_count, current_count in zip(baseline_counts, current_counts, strict=True):
        expected = max(baseline_count / total_baseline, epsilon)
        observed = max(current_count / total_current, epsilon)
        result += (observed - expected) * math.log(observed / expected)
    return Decimal(str(result)).quantize(Decimal("0.00000001"))


def _bin_index(value: float, low: float, high: float, width: float, bin_count: int) -> int:
    if value <= low:
        return 0
    if value >= high:
        return bin_count - 1
    return min(int((value - low) / width), bin_count - 1)


def _report_id(report: FeatureDriftReport) -> str:
    payload = report.model_dump(mode="json")
    payload.pop("report_id", None)
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"drift-{digest[:20]}"
