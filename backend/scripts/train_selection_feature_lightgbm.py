"""Train an optional, purpose-isolated LightGBM feature model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stratgauge.application.ml import (
    FeatureLightGBMUnavailableError,
    TrainFeatureLightGBMRequest,
    run_train_feature_lightgbm,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        workflow = run_train_feature_lightgbm(
            TrainFeatureLightGBMRequest(
                input_dir=arguments.input_dir,
                output_dir=arguments.output_dir,
                random_seed=arguments.random_seed,
                minimum_labeled_samples=arguments.minimum_labeled_samples,
            )
        )
        result = workflow.result
    except (FeatureLightGBMUnavailableError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(
        json.dumps(
            {
                "model_version_id": result.model.model_version_id,
                "model_type": result.model.model_type,
                "purpose": result.model.purpose,
                "feature_set_version_id": result.model.feature_set_version_id,
                "output_dir": str(arguments.output_dir),
                "row_counts": result.model.row_counts,
                "purged_rows_excluded": True,
                "calibrated": False,
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
    parser.add_argument("--random-seed", type=int, default=0)
    parser.add_argument("--minimum-labeled-samples", type=_positive_int, default=100)
    arguments = parser.parse_args()
    if not arguments.input_dir.is_dir():
        parser.error(f"--input-dir does not exist: {arguments.input_dir}")
    return arguments


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
