"""Score purpose-aware feature snapshots through an explicitly enabled shadow model."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from stratgauge.application.ml import (
    FeatureShadowCalibrationMode,
    FeatureShadowError,
    ScoreFeatureShadowRequest,
    run_score_feature_shadow,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        workflow = run_score_feature_shadow(
            ScoreFeatureShadowRequest(
                root=arguments.root,
                registration_id=arguments.registration_id,
                input_path=arguments.input,
                source_input_sha256=arguments.source_input_sha256,
                scored_at=arguments.scored_at,
                calibration_mode=cast(
                    FeatureShadowCalibrationMode,
                    arguments.calibration_mode,
                ),
                output_path=arguments.output,
            )
        )
        report = workflow.report
    except (FeatureShadowError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(report.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--registration-id", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-input-sha256", required=True)
    parser.add_argument("--scored-at", type=_parse_aware_datetime, required=True)
    parser.add_argument(
        "--calibration-mode",
        choices=("none", "registered"),
        default="none",
    )
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if not arguments.input.is_file():
        parser.error(f"--input does not exist: {arguments.input}")
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
