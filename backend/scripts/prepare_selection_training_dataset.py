"""Prepare one raw selection dataset as a leakage-safe training-label split."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    audit_training_label_split,
    prepare_training_label_dataset,
    split_training_label_dataset,
    write_training_label_split,
)


def main() -> int:
    arguments = _parse_arguments()
    try:
        prepared = prepare_training_label_dataset(
            arguments.input.read_bytes(),
            purpose=arguments.purpose,
            tracking_policy_id=arguments.tracking_policy_id,
        )
        split = split_training_label_dataset(
            prepared,
            train_end=arguments.train_end,
            validation_end=arguments.validation_end,
            label_available_through=arguments.label_available_through,
        )
        report = audit_training_label_split(
            split,
            minimum_labeled_samples=arguments.minimum_labeled_samples,
        )
        manifest = write_training_label_split(arguments.output_dir, split, report)
    except (FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(
        json.dumps(
            {
                "purpose": prepared.purpose,
                "label_contract_id": prepared.label_contract_id,
                "output_dir": str(arguments.output_dir),
                "row_counts": manifest.row_counts,
                "eligible": report.eligible,
                "blockers": report.blockers,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.eligible else 2


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--purpose",
        choices=tuple(purpose.value for purpose in TrainingDatasetPurpose),
        required=True,
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--train-end", type=_parse_date, required=True)
    parser.add_argument("--validation-end", type=_parse_date, required=True)
    parser.add_argument("--label-available-through", type=_parse_aware_datetime, required=True)
    parser.add_argument("--minimum-labeled-samples", type=_positive_int, default=100)
    parser.add_argument(
        "--tracking-policy-id",
        help="required when a candidate-success source contains multiple policies",
    )
    arguments = parser.parse_args()
    if not arguments.input.is_file():
        parser.error(f"--input does not exist: {arguments.input}")
    if arguments.train_end >= arguments.validation_end:
        parser.error("--train-end must precede --validation-end")
    if arguments.label_available_through.date() < arguments.validation_end:
        parser.error("--label-available-through cannot precede --validation-end")
    return arguments


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _parse_aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("datetime must use ISO-8601 format") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone offset")
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
