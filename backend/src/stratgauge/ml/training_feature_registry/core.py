"""Immutable registration evidence for purpose-aware feature LightGBM models."""

from __future__ import annotations


class FeatureModelRegistryError(RuntimeError):
    """A purpose-aware feature model cannot be registered or verified."""
