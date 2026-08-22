"""Build purpose-specific point-in-time feature snapshots from approved local evidence."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.application.ml import (
    BuildTrainingFeatureSnapshotsRequest,
    run_build_training_feature_snapshots,
)
from stratgauge.ml.training_datasets import (
    SelectionTrainingLabelRow,
    read_training_label_dataset,
)

_LABEL_SPLIT_FILES = ("train.jsonl", "validation.jsonl", "test.jsonl")


def main() -> int:
    arguments = _parse_arguments()
    try:
        labels = _read_labels(arguments.labels_dir)
        workflow = run_build_training_feature_snapshots(
            BuildTrainingFeatureSnapshotsRequest(
                labels=labels,
                selection_root=arguments.selection_root,
                market_root=arguments.market_root,
                feature_root=arguments.feature_root,
                report_path=arguments.output_dir / "report.json",
                market_snapshot_ids=tuple(arguments.market_snapshot_id),
                benchmark_snapshot_ids=tuple(arguments.benchmark_snapshot_id or ()),
                adjustment_factor_snapshot_ids=tuple(arguments.adjustment_factor_snapshot_id or ()),
                benchmark_adjustment_factor_snapshot_ids=tuple(
                    arguments.benchmark_adjustment_factor_snapshot_id or ()
                ),
                as_of_cutoff=arguments.as_of,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    report = workflow.report
    print(
        json.dumps(
            {
                "report_id": report.report_id,
                "report_path": str(workflow.report_path),
                "purpose": report.purpose.value,
                "feature_set_version_id": report.feature_set_version_id,
                "as_of_cutoff": arguments.as_of.isoformat(),
                "snapshot_count": report.snapshot_count,
                "feature_snapshot_ids": [
                    mapping.feature_snapshot_id for mapping in report.mappings
                ],
                "failure_count": report.failure_count,
                "failures": [failure.model_dump(mode="json") for failure in report.failures],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.failure_count == 0 else 1


def _read_labels(labels_dir: Path) -> tuple[SelectionTrainingLabelRow, ...]:
    rows: list[SelectionTrainingLabelRow] = []
    for filename in _LABEL_SPLIT_FILES:
        path = labels_dir / filename
        if not path.is_file():
            raise ValueError(f"prepared label split does not exist: {path}")
        rows.extend(read_training_label_dataset(path.read_bytes()))
    if not rows:
        raise ValueError("prepared label splits contain no labels")
    return tuple(rows)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels-dir", type=Path, required=True)
    parser.add_argument("--selection-root", type=Path, required=True)
    parser.add_argument("--feature-root", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--market-snapshot-id", action="append", required=True)
    parser.add_argument("--benchmark-snapshot-id", action="append")
    parser.add_argument("--adjustment-factor-snapshot-id", action="append")
    parser.add_argument("--benchmark-adjustment-factor-snapshot-id", action="append")
    parser.add_argument("--as-of", type=_parse_aware_datetime, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    if not arguments.labels_dir.is_dir():
        parser.error(f"--labels-dir does not exist: {arguments.labels_dir}")
    if not arguments.selection_root.is_dir():
        parser.error(f"--selection-root does not exist: {arguments.selection_root}")
    if not arguments.market_root.is_dir():
        parser.error(f"--market-root does not exist: {arguments.market_root}")
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
