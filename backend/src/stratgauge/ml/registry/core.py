"""Immutable local registration and manual lifecycle for selection models."""

from __future__ import annotations


class ModelRegistryError(RuntimeError):
    """A model registration or lifecycle event cannot be safely persisted."""
