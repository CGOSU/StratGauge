"""Join a prepared label split to explicitly supplied feature snapshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stratgauge.application.ml import (
    JoinTrainingFeaturesRequest,
    run_join_training_features,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        workflow = run_join_training_features(
            JoinTrainingFeaturesRequest(
                labels_dir=arguments.labels_dir,
                feature_root=arguments.feature_root,
                feature_set_version_id=arguments.feature_set_version_id,
                feature_snapshot_ids=tuple(arguments.feature_snapshot_id),
                output_dir=arguments.output_dir,
            )
        )
        result = workflow.split
    except (FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(
        json.dumps(
            {
                "purpose": result.report.purpose,
                "feature_set_version_id": result.report.feature_set_version_id,
                "output_dir": str(arguments.output_dir),
                "joined_row_count": result.report.joined_row_count,
                "purged_row_count": result.report.purged_row_count,
                "failure_count": result.report.failure_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.report.failure_count == 0 else 1


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels-dir", type=Path, required=True)
    parser.add_argument("--feature-root", type=Path, required=True)
    parser.add_argument("--feature-set-version-id", required=True)
    parser.add_argument("--feature-snapshot-id", action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    if not arguments.labels_dir.is_dir():
        parser.error(f"--labels-dir does not exist: {arguments.labels_dir}")
    if not arguments.feature_root.is_dir():
        parser.error(f"--feature-root does not exist: {arguments.feature_root}")
    return arguments


if __name__ == "__main__":
    raise SystemExit(main())
