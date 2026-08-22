"""Run purpose-aware, purged walk-forward validation for a registered feature model."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from stratgauge.application.ml import (
    FeatureWalkForwardUnavailableError,
    ValidateFeatureWalkForwardRequest,
    run_validate_feature_walk_forward,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        workflow = run_validate_feature_walk_forward(
            ValidateFeatureWalkForwardRequest(
                root=arguments.root,
                input_dir=arguments.input_dir,
                registration_id=arguments.registration_id,
                folds_path=arguments.folds,
                evaluated_at=arguments.evaluated_at,
            )
        )
        stored = workflow.report
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
