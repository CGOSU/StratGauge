"""Purpose-aware, non-authoritative shadow scoring for feature LightGBM models."""

from __future__ import annotations


class FeatureShadowError(RuntimeError):
    """A purpose-aware feature shadow score cannot be safely generated."""
