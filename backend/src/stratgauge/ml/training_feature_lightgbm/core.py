"""Train purpose-isolated LightGBM models from verified feature splits."""

from __future__ import annotations


class FeatureLightGBMUnavailableError(RuntimeError):
    """The optional LightGBM or NumPy dependency is unavailable."""
