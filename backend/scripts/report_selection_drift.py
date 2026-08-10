"""Create an immutable feature-distribution drift report from two JSONL files."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from stratgauge.ml.drift import DriftError, DriftPolicy, FeatureDriftStore, build_drift_report


def main() -> int:
    arguments = _parse_arguments()
    try:
        baseline_content = arguments.baseline.read_bytes()
        current_content = arguments.current.read_bytes()
        baseline_rows = _read_jsonl(baseline_content)
        current_rows = _read_jsonl(current_content)
        report = build_drift_report(
            baseline_rows=baseline_rows,
            current_rows=current_rows,
            baseline_source_sha256=_sha256(baseline_content),
            current_source_sha256=_sha256(current_content),
            policy=DriftPolicy(
                drift_policy_id=arguments.policy_id,
                feature_names=tuple(arguments.feature),
                minimum_samples=arguments.minimum_samples,
                bin_count=arguments.bin_count,
                psi_threshold=Decimal(arguments.psi_threshold),
                mean_delta_threshold=Decimal(arguments.mean_delta_threshold),
            ),
            generated_at=datetime.now(UTC),
        )
        stored = FeatureDriftStore(arguments.root).write(report)
        _write_once(arguments.output, stored.model_dump_json(indent=2) + "\n")
    except (DriftError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(stored.model_dump_json(indent=2))
    return 0 if stored.gate_passed else 1


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--feature", action="append", required=True)
    parser.add_argument("--policy-id", required=True)
    parser.add_argument("--minimum-samples", type=_positive_int, default=100)
    parser.add_argument("--bin-count", type=_positive_int, default=10)
    parser.add_argument("--psi-threshold", default="0.2")
    parser.add_argument("--mean-delta-threshold", default="0")
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _read_jsonl(content: bytes) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(content.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, Mapping):
            raise ValueError(f"JSONL row {line_number} must be an object")
        rows.append(payload)
    return tuple(rows)


def _write_once(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise DriftError("drift output identity already has different content")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise DriftError("failed to write drift output") from exc


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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
