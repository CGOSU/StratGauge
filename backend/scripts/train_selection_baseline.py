"""Train a deterministic constant-probability selection baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

from stratgauge.ml.selection_training import (
    read_feature_dataset,
    train_constant_baseline,
    write_baseline_result,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        content = arguments.input.read_bytes()
        rows = read_feature_dataset(content)
        result = train_constant_baseline(
            rows,
            train_end=arguments.train_end,
            validation_end=arguments.validation_end,
            source_file_sha256=hashlib.sha256(content).hexdigest(),
            random_seed=arguments.random_seed,
            expected_feature_set_version_id=arguments.feature_set_version_id,
            expected_tracking_policy_id=arguments.tracking_policy_id,
            label_field=arguments.label_field,
        )
        write_baseline_result(arguments.output_dir, result)
    except (FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(
        json.dumps(
            {
                "model_version_id": result.model.model_version_id,
                "output_dir": str(arguments.output_dir),
                "row_counts": result.row_counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--train-end", type=_parse_date, required=True)
    parser.add_argument("--validation-end", type=_parse_date, required=True)
    parser.add_argument("--random-seed", type=int, default=0)
    parser.add_argument("--feature-set-version-id", required=True)
    parser.add_argument("--tracking-policy-id", required=True)
    parser.add_argument("--label-field", choices=("label",), default="label")
    arguments = parser.parse_args()
    if not arguments.input.is_file():
        parser.error(f"--input does not exist: {arguments.input}")
    if arguments.train_end >= arguments.validation_end:
        parser.error("--train-end must precede --validation-end")
    return arguments


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


if __name__ == "__main__":
    raise SystemExit(main())
