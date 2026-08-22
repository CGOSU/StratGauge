"""Create immutable purpose-aware comparison evidence for manual review."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.application.ml import (
    CompareFeatureModelsRequest,
    FeatureComparisonError,
    run_compare_feature_models,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        workflow = run_compare_feature_models(
            CompareFeatureModelsRequest(
                root=arguments.root,
                policy_path=arguments.policy,
                challenger_registration_id=arguments.challenger_registration_id,
                challenger_report_id=arguments.challenger_report_id,
                reference_registration_id=arguments.reference_registration_id,
                reference_report_id=arguments.reference_report_id,
                compared_at=arguments.compared_at,
                compared_by=arguments.compared_by,
            )
        )
        report = workflow.report
        report_sha256 = workflow.report_sha256
    except (FeatureComparisonError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    payload = report.model_dump(mode="json")
    payload["report_sha256"] = report_sha256
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--challenger-registration-id", required=True)
    parser.add_argument("--challenger-report-id", required=True)
    parser.add_argument("--reference-registration-id")
    parser.add_argument("--reference-report-id")
    parser.add_argument("--compared-at", type=_parse_aware_datetime, required=True)
    parser.add_argument("--compared-by", required=True)
    arguments = parser.parse_args()
    if not arguments.policy.is_file():
        parser.error(f"--policy does not exist: {arguments.policy}")
    if (arguments.reference_registration_id is None) != (arguments.reference_report_id is None):
        parser.error(
            "--reference-registration-id and --reference-report-id must be provided together"
        )
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
