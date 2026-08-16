"""Purpose-aware validation-only calibration for feature LightGBM outputs."""

from __future__ import annotations


class FeatureCalibrationError(ValueError):
    """Feature LightGBM calibration input or output is invalid."""
