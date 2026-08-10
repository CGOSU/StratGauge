"""Versioned, explicit evaluation evidence for registered selection models."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Final, Literal

from pydantic import field_validator

from stratgauge.domain.models import DomainModel

MODEL_EVALUATION_VERSION: Final[Literal["selection-model-evaluation-v1"]] = (
    "selection-model-evaluation-v1"
)


class ModelEvaluationEvidence(DomainModel):
    """Metrics and scope metadata that a lifecycle gate may safely consume."""

    evaluation_version: Literal["selection-model-evaluation-v1"] = MODEL_EVALUATION_VERSION
    model_version_id: str
    model_type: str
    feature_set_version_id: str
    tracking_policy_id: str
    source_file_sha256: str
    calibrated: bool = False
    metrics: dict[str, Decimal | None]

    @field_validator(
        "model_version_id",
        "model_type",
        "feature_set_version_id",
        "tracking_policy_id",
        "source_file_sha256",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model evaluation text must not be empty")
        return value.strip()

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, value: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
        if not value:
            raise ValueError("model evaluation must contain metrics")
        for name, metric in value.items():
            if not name.strip():
                raise ValueError("model evaluation metric name must not be empty")
            if metric is not None and not metric.is_finite():
                raise ValueError(f"model evaluation metric is not finite: {name}")
        return {name.strip(): metric for name, metric in value.items()}


def read_model_evaluation(content: bytes) -> ModelEvaluationEvidence:
    """Parse one standardized evaluation report without accepting unknown shapes."""

    try:
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("model evaluation report must be a JSON object")
        return ModelEvaluationEvidence.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid model evaluation report: {exc}") from exc
