"""Export isolated manual-position tracking evidence and its coverage report."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from stratgauge.backtest.manual_position_tracking_dataset import (
    build_manual_position_tracking_export,
    serialize_manual_position_tracking_dataset,
    serialize_manual_position_tracking_report,
)
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore, PortfolioStoreError
from stratgauge.data_pipeline.store import SnapshotStore, SnapshotStoreError


def main() -> int:
    arguments = _parse_arguments()
    dataset_path, report_path = _output_paths(arguments)
    existing = tuple(path for path in (dataset_path, report_path) if path.exists())
    if existing:
        print(
            json.dumps(
                {
                    "error": "evidence output already exists",
                    "paths": [str(path) for path in existing],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    try:
        export = build_manual_position_tracking_export(
            ManualPortfolioStore(arguments.portfolio_root),
            SnapshotStore(arguments.market_root),
            evaluated_at=arguments.evaluated_at,
        )
        _write_bundle(
            {
                dataset_path: serialize_manual_position_tracking_dataset(export.rows),
                report_path: serialize_manual_position_tracking_report(export.report),
            }
        )
    except (FileExistsError, OSError, PortfolioStoreError, SnapshotStoreError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    report = export.report
    print(
        json.dumps(
            {
                "evaluated_at": report.evaluated_at.isoformat(),
                "dataset": str(dataset_path),
                "report": str(report_path),
                "case_count": report.case_count,
                "evidence_row_count": report.evidence_row_count,
                "eligible_case_count": report.eligible_case_count,
                "not_eligible_case_count": report.not_eligible_case_count,
                "coverage_status": report.coverage_status,
                "ready": report.ready,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.ready else 1


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    output_group = parser.add_mutually_exclusive_group(required=True)
    output_group.add_argument("--output-dir", type=Path)
    output_group.add_argument("--output", dest="output_path", type=Path)
    output_group.add_argument("--dataset-output", dest="dataset_output", type=Path)
    parser.add_argument("--portfolio-root", type=Path, default=Path("data"))
    parser.add_argument("--market-root", type=Path, default=Path("data/market"))
    parser.add_argument("--evaluated-at", type=_parse_aware_datetime, required=True)
    parser.add_argument(
        "--report",
        "--report-path",
        "--report-output",
        dest="report_path",
        type=Path,
    )
    return parser.parse_args()


def _output_paths(arguments: argparse.Namespace) -> tuple[Path, Path]:
    if arguments.output_dir is not None:
        dataset_path = arguments.output_dir / "manual-position-tracking-dataset.jsonl"
        default_report = arguments.output_dir / "manual-position-tracking-report.json"
    elif arguments.dataset_output is not None:
        dataset_path = arguments.dataset_output
        default_report = dataset_path.with_name("manual-position-tracking-report.json")
    else:
        assert arguments.output_path is not None
        if arguments.output_path.suffix.lower() == ".json":
            dataset_path = arguments.output_path.with_name("manual-position-tracking-dataset.jsonl")
            default_report = arguments.output_path
        elif arguments.output_path.suffix.lower() == ".jsonl":
            dataset_path = arguments.output_path
            default_report = dataset_path.with_name("manual-position-tracking-report.json")
        else:
            dataset_path = arguments.output_path / "manual-position-tracking-dataset.jsonl"
            default_report = arguments.output_path / "manual-position-tracking-report.json"
    report_path = arguments.report_path or default_report
    return dataset_path, report_path


def _write_bundle(contents: dict[Path, str]) -> None:
    created: list[Path] = []
    try:
        for destination, content in contents.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8", newline="\n") as handle:
                created.append(destination)
                handle.write(content)
    except Exception:
        for destination in reversed(created):
            destination.unlink(missing_ok=True)
        raise


def _parse_aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("datetime must use ISO format") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone offset")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
