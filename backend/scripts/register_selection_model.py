"""Register an immutable offline selection model and its evidence files."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.ml.lightgbm_training import LightGBMModel
from stratgauge.ml.registry import ModelRegistryError, ModelRegistryStore
from stratgauge.ml.selection_training import BaselineModel


def main() -> int:
    arguments = _parse_arguments()
    try:
        model_content = arguments.model.read_bytes()
        model = _parse_model(model_content)
        runtime_artifact = (
            arguments.runtime_artifact.read_bytes()
            if arguments.runtime_artifact is not None
            else None
        )
        registered = ModelRegistryStore(arguments.root).register(
            model,
            model_artifact=model_content,
            runtime_artifact=runtime_artifact,
            evaluation_report=arguments.evaluation.read_bytes(),
            training_manifest=arguments.manifest.read_bytes(),
            registered_at=arguments.registered_at,
            registered_by=arguments.registered_by,
        )
    except (OSError, ModelRegistryError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(registered.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--runtime-artifact",
        type=Path,
        help="LightGBM model.txt; required when --model is LightGBM metadata",
    )
    parser.add_argument("--registered-at", type=_parse_aware_datetime, required=True)
    parser.add_argument("--registered-by", required=True)
    arguments = parser.parse_args()
    for path, name in (
        (arguments.model, "--model"),
        (arguments.evaluation, "--evaluation"),
        (arguments.manifest, "--manifest"),
    ):
        if not path.is_file():
            parser.error(f"{name} does not exist: {path}")
    if arguments.runtime_artifact is not None and not arguments.runtime_artifact.is_file():
        parser.error(f"--runtime-artifact does not exist: {arguments.runtime_artifact}")
    return arguments


def _parse_model(content: bytes) -> BaselineModel | LightGBMModel:
    try:
        return BaselineModel.model_validate_json(content)
    except ValueError:
        try:
            return LightGBMModel.model_validate_json(content)
        except ValueError as exc:
            raise ValueError("model is neither a baseline nor LightGBM metadata artifact") from exc


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
