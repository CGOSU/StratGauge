"""Run queued main-board market synchronization jobs once."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stratgauge.application.job_runner import JobStatus, LocalJobStore
from stratgauge.application.market_sync_jobs import run_main_board_market_sync_jobs
from stratgauge.runtime import RuntimeConfig


def main() -> int:
    arguments = _parse_arguments()
    runtime = RuntimeConfig.from_environment(
        arguments.data_root,
        market_root=arguments.market_root,
        universe_root=arguments.universe_root,
    )
    jobs = run_main_board_market_sync_jobs(
        LocalJobStore(runtime.data_root),
        limit=arguments.limit,
    )
    print(
        json.dumps(
            {
                "jobs": [job.model_dump(mode="json") for job in jobs],
                "roots": runtime.describe(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if any(job.status is JobStatus.FAILED for job in jobs) else 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        "--root",
        dest="data_root",
        type=Path,
        default=None,
        help="本地数据根目录，默认读取 STRATGAUGE_DATA_ROOT 或 data",
    )
    parser.add_argument("--market-root", type=Path, default=None)
    parser.add_argument("--universe-root", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=1)
    arguments = parser.parse_args()
    if arguments.limit <= 0:
        parser.error("--limit must be positive")
    return arguments


if __name__ == "__main__":
    raise SystemExit(main())
