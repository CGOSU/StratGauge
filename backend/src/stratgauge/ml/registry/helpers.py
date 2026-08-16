"""Immutable local registration and manual lifecycle for selection models."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from stratgauge.ml._hashing import sha256_bytes
from stratgauge.ml.lightgbm_training import LightGBMModel
from stratgauge.ml.registry.core import (
    ModelRegistryError,
)
from stratgauge.ml.registry.models import (
    RegisteredModelMetadata,
)
from stratgauge.ml.selection_training import BaselineModel


def _validate_model_artifact(
    model: RegisteredModelMetadata,
    content: bytes,
    runtime_artifact: bytes | None,
) -> None:
    if isinstance(model, BaselineModel):
        if runtime_artifact is not None:
            raise ModelRegistryError("baseline model must not have a runtime artifact")
        try:
            parsed = BaselineModel.model_validate_json(content)
        except (TypeError, ValueError) as exc:
            raise ModelRegistryError("model artifact is not a valid baseline model") from exc
        if parsed != model:
            raise ModelRegistryError("model artifact does not match model metadata")
        return
    if runtime_artifact is None:
        raise ModelRegistryError("LightGBM model requires a runtime artifact")
    if sha256_bytes(runtime_artifact) != model.artifact_sha256:
        raise ModelRegistryError("LightGBM runtime artifact hash does not match model metadata")
    try:
        parsed_lightgbm = LightGBMModel.model_validate_json(content)
    except (TypeError, ValueError) as exc:
        raise ModelRegistryError("model artifact is not valid LightGBM metadata") from exc
    if parsed_lightgbm != model:
        raise ModelRegistryError("model artifact does not match model metadata")


def _validate_training_manifest(
    manifest: Mapping[str, Any], model: RegisteredModelMetadata
) -> None:
    expected = {
        "model_version_id": model.model_version_id,
        "feature_set_version_id": model.feature_set_version_id,
        "tracking_policy_id": model.tracking_policy_id,
        "source_file_sha256": model.source_file_sha256,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ModelRegistryError(f"training manifest does not match model: {key}")


def _validate_json_object(content: bytes, name: str) -> None:
    payload = _parse_json(content, name)
    if not isinstance(payload, dict):
        raise ModelRegistryError(f"{name} must be a JSON object")


def _validate_json(content: bytes, name: str) -> None:
    _parse_json(content, name)


def _parse_json_object(content: bytes, name: str) -> dict[str, Any]:
    payload = _parse_json(content, name)
    if not isinstance(payload, dict):
        raise ModelRegistryError(f"{name} must be a JSON object")
    return payload


def _parse_json(content: bytes, name: str) -> Any:
    try:
        return json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ModelRegistryError(f"{name} is not valid JSON") from exc


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(
        character in normalized for character in ("/", "\\", "\0", "\r", "\n")
    ):
        raise ModelRegistryError("model version identifier contains an invalid path character")
    return normalized


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ModelRegistryError(f"{name} must not be empty")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ModelRegistryError(f"{name} must include a timezone")
