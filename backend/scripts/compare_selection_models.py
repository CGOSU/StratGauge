"""Create immutable comparison evidence for a manual model promotion."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.ml.comparison import (
    ComparisonPolicy,
    ModelComparisonError,
    ModelComparisonStore,
    compare_models,
)
from stratgauge.ml.registry import ModelRegistryStore


def main() -> int:
    arguments = _parse_arguments()
    try:
        policy = ComparisonPolicy.model_validate_json(arguments.policy.read_bytes())
        store = ModelComparisonStore(arguments.root)
        report = compare_models(
            registry=ModelRegistryStore(arguments.root),
            comparison_store=store,
            challenger_model_version_id=arguments.challenger_model_version_id,
            incumbent_model_version_id=arguments.incumbent_model_version_id,
            policy=policy,
            compared_at=arguments.compared_at,
            compared_by=arguments.compared_by,
        )
        _, report_sha256 = store.load_with_sha256(report.report_id)
    except (OSError, ModelComparisonError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    payload = report.model_dump(mode="json")
    payload["report_sha256"] = report_sha256
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--challenger-model-version-id", required=True)
    parser.add_argument("--incumbent-model-version-id")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--compared-at", type=_parse_aware_datetime, required=True)
    parser.add_argument("--compared-by", required=True)
    arguments = parser.parse_args()
    if not arguments.policy.is_file():
        parser.error(f"--policy does not exist: {arguments.policy}")
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
