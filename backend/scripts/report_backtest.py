"""Write a deterministic theory-only report from a BacktestResult JSON file."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from stratgauge.backtest.models import BacktestResult
from stratgauge.backtest.report import build_backtest_report


def main() -> int:
    arguments = _parse_arguments()
    result = BacktestResult.model_validate_json(arguments.input.read_text(encoding="utf-8"))
    market_states = _load_market_states(arguments.market_states)
    report = build_backtest_report(result, market_states=market_states)
    if arguments.output.exists():
        raise SystemExit(f"refusing to overwrite existing report: {arguments.output}")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(report.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    print(report.model_dump_json(indent=2))
    return 0


def _load_market_states(path: Path | None) -> dict[date, str]:
    if path is None:
        return {}
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("market state file must contain a JSON object")
    states: dict[date, str] = {}
    for session, state in payload.items():
        if not isinstance(session, str) or not isinstance(state, str):
            raise ValueError("market state file must map ISO dates to strings")
        states[date.fromisoformat(session)] = state
    return states


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--market-states",
        type=Path,
        help="JSON object mapping entry-session dates to explicit market-state labels",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
