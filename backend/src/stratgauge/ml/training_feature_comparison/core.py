"""Purpose-aware immutable comparison evidence for feature models."""

from __future__ import annotations


class FeatureComparisonError(RuntimeError):
    """A purpose-aware model comparison cannot produce trustworthy evidence."""
