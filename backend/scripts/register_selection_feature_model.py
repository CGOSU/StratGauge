"""Register verified purpose-aware feature LightGBM evidence as a challenger."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.application.ml import (
    FeatureModelRegistryError,
    RegisterFeatureModelRequest,
    run_register_feature_model,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        workflow = run_register_feature_model(
            RegisterFeatureModelRequest(
                root=arguments.root,
                training_dir=arguments.training_dir,
                calibration_dir=arguments.calibration_dir,
                registered_at=arguments.registered_at,
                registered_by=arguments.registered_by,
            )
        )
        registration = workflow.registration
    except (FeatureModelRegistryError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(registration.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--training-dir", type=Path, required=True)
    parser.add_argument("--calibration-dir", type=Path)
    parser.add_argument("--registered-at", type=_parse_aware_datetime, required=True)
    parser.add_argument("--registered-by", required=True)
    arguments = parser.parse_args()
    if not arguments.training_dir.is_dir():
        parser.error(f"--training-dir does not exist: {arguments.training_dir}")
    if arguments.calibration_dir is not None and not arguments.calibration_dir.is_dir():
        parser.error(f"--calibration-dir does not exist: {arguments.calibration_dir}")
    return arguments


def _parse_aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("datetime must use ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone offset")
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
