"""Run purpose-aware, purged walk-forward validation for a registered feature model."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from stratgauge.ml.training_feature_baseline import read_training_feature_split
from stratgauge.ml.training_feature_registry import FeatureModelRegistryStore
from stratgauge.ml.training_feature_walk_forward import (
    FeatureWalkForwardFoldSpec,
    FeatureWalkForwardReportStore,
    FeatureWalkForwardUnavailableError,
    validate_feature_walk_forward,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        split_input = read_training_feature_split(arguments.input_dir)
        registry = FeatureModelRegistryStore(arguments.root)
        registration, training_output, calibration_output = registry.load_outputs(
            arguments.registration_id
        )
        fold_payload = json.loads(arguments.folds.read_text(encoding="utf-8"))
        if not isinstance(fold_payload, list):
            raise ValueError("--folds must contain a JSON array")
        folds = tuple(FeatureWalkForwardFoldSpec.model_validate(item) for item in fold_payload)
        report = validate_feature_walk_forward(
            split_input,
            registration=registration,
            training_output=training_output,
            calibration_output=calibration_output,
            folds=folds,
            evaluated_at=arguments.evaluated_at,
        )
        stored = FeatureWalkForwardReportStore(arguments.root).write(report)
    except (
        FeatureWalkForwardUnavailableError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(stored.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--registration-id", required=True)
    parser.add_argument("--folds", type=Path, required=True)
    parser.add_argument("--evaluated-at", type=_parse_datetime, required=True)
    arguments = parser.parse_args()
    if not arguments.input_dir.is_dir():
        parser.error(f"--input-dir does not exist: {arguments.input_dir}")
    if not arguments.folds.is_file():
        parser.error(f"--folds does not exist: {arguments.folds}")
    return arguments


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("datetime must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
