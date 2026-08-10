"""Audit a selection JSONL dataset before machine-learning training."""

from __future__ import annotations

import argparse
from pathlib import Path

from stratgauge.backtest.selection_ml_audit import audit_selection_dataset_file


def main() -> int:
    arguments = _parse_arguments()
    report = audit_selection_dataset_file(
        arguments.input,
        strategy_parameters_frozen=arguments.strategy_parameters_frozen,
        minimum_completed_samples=arguments.min_completed_samples,
        tracking_policy_id=arguments.tracking_policy_id,
    )
    output = report.model_dump_json(indent=2)
    print(output)
    if arguments.output:
        if arguments.output.exists():
            raise SystemExit(f"report already exists: {arguments.output}")
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(f"{output}\n", encoding="utf-8", newline="\n")
    return 0 if report.eligible else 2


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--strategy-parameters-frozen",
        action="store_true",
        help="declare that the strategy and outcome policy are frozen",
    )
    parser.add_argument("--min-completed-samples", type=_positive_int, default=100)
    parser.add_argument("--tracking-policy-id")
    arguments = parser.parse_args()
    if not arguments.input.is_file():
        parser.error(f"--input does not exist: {arguments.input}")
    return arguments


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
