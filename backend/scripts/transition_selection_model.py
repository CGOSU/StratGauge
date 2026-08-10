"""Apply one manual selection-model lifecycle transition or rollback."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.domain.models import ModelLifecycle
from stratgauge.ml.registry import ModelRegistryError, ModelRegistryStore


def main() -> int:
    arguments = _parse_arguments()
    try:
        store = ModelRegistryStore(arguments.root)
        if arguments.rollback_to is not None:
            if arguments.evaluation_sha256 is None:
                raise ModelRegistryError("rollback requires --evaluation-sha256")
            model = store.rollback(
                arguments.rollback_to,
                changed_at=arguments.changed_at,
                reason=arguments.reason,
                operator=arguments.operator,
                evaluation_sha256=arguments.evaluation_sha256,
            )
        elif arguments.to_lifecycle is ModelLifecycle.ACTIVE:
            if arguments.evaluation_sha256 is None:
                raise ModelRegistryError("activation requires --evaluation-sha256")
            if arguments.comparison_report_id is None:
                raise ModelRegistryError("activation requires --comparison-report-id")
            if arguments.comparison_report_sha256 is None:
                raise ModelRegistryError("activation requires --comparison-report-sha256")
            model = store.promote(
                arguments.model_version_id,
                changed_at=arguments.changed_at,
                reason=arguments.reason,
                operator=arguments.operator,
                evaluation_sha256=arguments.evaluation_sha256,
                comparison_report_id=arguments.comparison_report_id,
                comparison_report_sha256=arguments.comparison_report_sha256,
            )
        else:
            model = store.transition(
                arguments.model_version_id,
                arguments.to_lifecycle,
                changed_at=arguments.changed_at,
                reason=arguments.reason,
                operator=arguments.operator,
            )
    except (OSError, ModelRegistryError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(model.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--model-version-id")
    group.add_argument("--rollback-to")
    parser.add_argument("--to", dest="to_lifecycle", type=ModelLifecycle)
    parser.add_argument("--changed-at", type=_parse_aware_datetime, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--evaluation-sha256")
    parser.add_argument("--comparison-report-id")
    parser.add_argument("--comparison-report-sha256")
    arguments = parser.parse_args()
    if arguments.rollback_to is None and arguments.to_lifecycle is None:
        parser.error("--to is required unless --rollback-to is used")
    if arguments.rollback_to is not None and arguments.to_lifecycle is not None:
        parser.error("--to cannot be used with --rollback-to")
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
