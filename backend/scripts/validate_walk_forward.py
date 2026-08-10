"""Run explicit purged walk-forward validation for a feature JSONL dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

from stratgauge.backtest.selection_dataset import read_selection_dataset
from stratgauge.ml.selection_training import read_feature_dataset
from stratgauge.ml.walk_forward import WalkForwardFoldSpec, validate_walk_forward


def main() -> int:
    arguments = _parse_arguments()
    try:
        content = arguments.input.read_bytes()
        rows = read_feature_dataset(content)
        fold_payload = json.loads(arguments.folds.read_text(encoding="utf-8"))
        if not isinstance(fold_payload, list):
            raise ValueError("--folds must contain a JSON array")
        folds = tuple(WalkForwardFoldSpec.model_validate(item) for item in fold_payload)
        outcome_content = arguments.outcomes.read_bytes() if arguments.outcomes else None
        report = validate_walk_forward(
            rows,
            folds=folds,
            source_file_sha256=hashlib.sha256(content).hexdigest(),
            final_test_start=arguments.final_test_start,
            outcomes=(
                read_selection_dataset(outcome_content) if outcome_content is not None else ()
            ),
            outcome_source_file_sha256=(
                hashlib.sha256(outcome_content).hexdigest() if outcome_content is not None else None
            ),
        )
        _write_report(arguments.output, report.model_dump_json(indent=2) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(report.model_dump_json(indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--folds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--final-test-start", type=_parse_date)
    parser.add_argument(
        "--outcomes",
        type=Path,
        help="optional selection-dataset-v2 JSONL for execution and return metrics",
    )
    arguments = parser.parse_args()
    if not arguments.input.is_file():
        parser.error(f"--input does not exist: {arguments.input}")
    if not arguments.folds.is_file():
        parser.error(f"--folds does not exist: {arguments.folds}")
    if arguments.outcomes is not None and not arguments.outcomes.is_file():
        parser.error(f"--outcomes does not exist: {arguments.outcomes}")
    return arguments


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _write_report(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"walk-forward report already exists: {path}")
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
