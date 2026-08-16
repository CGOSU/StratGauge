"""Purpose-aware, purged walk-forward validation for feature LightGBM models."""

from __future__ import annotations


class FeatureWalkForwardUnavailableError(RuntimeError):
    """The optional LightGBM or NumPy dependency is unavailable."""
