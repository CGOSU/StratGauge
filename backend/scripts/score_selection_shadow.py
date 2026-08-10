"""Score feature snapshots with a registered model in an isolated shadow run."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.ml.calibration import CalibratedModel
from stratgauge.ml.registry import ModelRegistryStore
from stratgauge.ml.shadow import (
    ShadowScoreError,
    ShadowScoreStore,
    read_shadow_inputs,
    score_shadow_batch,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        content = arguments.input.read_bytes()
        calibration_model = (
            CalibratedModel.model_validate_json(arguments.calibration.read_bytes())
            if arguments.calibration is not None
            else None
        )
        report = score_shadow_batch(
            read_shadow_inputs(content),
            registry=ModelRegistryStore(arguments.root),
            score_store=ShadowScoreStore(arguments.root),
            model_version_id=arguments.model_version_id,
            scored_at=arguments.scored_at,
            source_input_sha256=hashlib.sha256(content).hexdigest(),
            calibration_model=calibration_model,
        )
        _write_report(arguments.output, report.model_dump_json(indent=2) + "\n")
    except (OSError, ShadowScoreError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(report.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--model-version-id", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--scored-at", type=_parse_aware_datetime, required=True)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if not arguments.input.is_file():
        parser.error(f"--input does not exist: {arguments.input}")
    if arguments.calibration is not None and not arguments.calibration.is_file():
        parser.error(f"--calibration does not exist: {arguments.calibration}")
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
        raise FileExistsError(f"shadow report already exists: {path}")
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
