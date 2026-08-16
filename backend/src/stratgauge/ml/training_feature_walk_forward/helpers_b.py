"""Purpose-aware, purged walk-forward validation for feature LightGBM models."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from importlib import import_module
from typing import Any

from stratgauge.ml.training_feature_walk_forward.core import (
    FeatureWalkForwardUnavailableError,
)


def _load_lightgbm() -> Any:
    try:
        return import_module("lightgbm")
    except (ImportError, OSError) as exc:
        raise FeatureWalkForwardUnavailableError(
            "LightGBM is unavailable; run `uv sync --extra ml` before walk-forward validation"
        ) from exc


def _load_numpy() -> Any:
    try:
        return import_module("numpy")
    except (ImportError, OSError) as exc:
        raise FeatureWalkForwardUnavailableError(
            "NumPy is unavailable; run `uv sync --extra ml` before walk-forward validation"
        ) from exc


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


def _matrix(rows: Sequence[Any], feature_names: Sequence[str]) -> list[list[float]]:
    return [[float(row.features[name]) for name in feature_names] for row in rows]
