"""Score purpose-aware feature snapshots through an explicitly enabled shadow model."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.ml.training_feature_registry import FeatureModelRegistryStore
from stratgauge.ml.training_feature_shadow import (
    FeatureShadowError,
    FeatureShadowScoreStore,
    feature_shadow_input_sha256,
    read_feature_shadow_inputs,
    score_feature_shadow_batch,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        content = arguments.input.read_bytes()
        inputs = read_feature_shadow_inputs(content)
        if feature_shadow_input_sha256(inputs) != arguments.source_input_sha256:
            raise FeatureShadowError(
                "feature shadow input hash does not match --source-input-sha256"
            )
        report = score_feature_shadow_batch(
            inputs,
            registry=FeatureModelRegistryStore(arguments.root),
            score_store=FeatureShadowScoreStore(arguments.root),
            registration_id=arguments.registration_id,
            scored_at=arguments.scored_at,
            calibration_mode=arguments.calibration_mode,
        )
        _write_report(arguments.output, report.model_dump_json(indent=2) + "\n")
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


def _write_report(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"feature shadow report already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
