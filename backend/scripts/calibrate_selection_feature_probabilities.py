"""Fit purpose-aware isotonic calibration for a feature LightGBM output."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from stratgauge.ml.training_feature_lightgbm_calibration import (
    FeatureCalibrationError,
    calibrate_feature_lightgbm,
    read_feature_lightgbm_output,
    write_feature_calibration_result,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        training = read_feature_lightgbm_output(arguments.input_dir)
        result = calibrate_feature_lightgbm(
            training.model,
            training.predictions,
            source_prediction_sha256=training.predictions_sha256,
            source_model_sha256=training.model_sha256,
            source_manifest_sha256=training.manifest_sha256,
            calibrated_at=arguments.calibrated_at,
            minimum_validation_samples=arguments.minimum_validation_samples,
        )
        write_feature_calibration_result(arguments.output_dir, result)
    except (FeatureCalibrationError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(
        json.dumps(
            {
                "calibration_id": result.model.calibration_id,
                "output_dir": str(arguments.output_dir),
                "purpose": result.model.purpose.value,
                "calibration_row_count": result.model.calibration_row_count,
                "test_row_count": result.model.test_row_count,
                "calibrated": True,
                "used_for_decision": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--calibrated-at", type=_parse_datetime, required=True)
    parser.add_argument("--minimum-validation-samples", type=_positive_int, default=100)
    arguments = parser.parse_args()
    if not arguments.input_dir.is_dir():
        parser.error(f"--input-dir does not exist: {arguments.input_dir}")
    return arguments


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must use ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be positive") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
