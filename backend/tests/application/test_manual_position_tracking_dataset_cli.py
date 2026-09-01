import json
import runpy
import sys
import unittest
from collections.abc import Callable
from contextlib import redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import patch

from stratgauge.backtest.manual_position_tracking_dataset import (
    read_manual_position_tracking_report,
)
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore
from stratgauge.domain.models import Exchange, Security
from stratgauge.domain.portfolio import ManualFill, ManualOrder, ManualOrderSide

SECURITY = Security(
    security_id="security-000001.SZ",
    local_code="000001",
    symbol="000001.SZ",
    exchange=Exchange.SZ,
)


class ManualPositionTrackingDatasetCliTests(unittest.TestCase):
    def test_cli_writes_isolated_dataset_and_report_once(self) -> None:
        with (
            TemporaryDirectory() as portfolio_directory,
            TemporaryDirectory() as market_directory,
            TemporaryDirectory() as output_directory,
        ):
            portfolio = ManualPortfolioStore(portfolio_directory)
            historical_order = ManualOrder(
                order_id="historical-order",
                security=SECURITY,
                side=ManualOrderSide.BUY,
                ordered_at=datetime(2026, 1, 2, 9, tzinfo=UTC),
                quantity=Decimal("100"),
                limit_price=Decimal("10"),
            )
            portfolio.record_order_and_fill(
                historical_order,
                ManualFill(
                    fill_id="historical-fill",
                    order_id=historical_order.order_id,
                    filled_at=datetime(2026, 1, 2, 10, tzinfo=UTC),
                    quantity=Decimal("100"),
                    price=Decimal("10"),
                ),
            )
            portfolio.enroll_position(
                SECURITY,
                tracking_case_id="manual-case-cli",
                tracking_started_at=datetime(2026, 1, 10, 12, tzinfo=UTC),
            )
            output_root = Path(output_directory) / "evidence"
            exit_code, payload = _run_cli(
                [
                    "--portfolio-root",
                    portfolio_directory,
                    "--market-root",
                    market_directory,
                    "--evaluated-at",
                    "2026-01-12T18:00:00+00:00",
                    "--output-dir",
                    str(output_root),
                ]
            )

            dataset_path = output_root / "manual-position-tracking-dataset.jsonl"
            report_path = output_root / "manual-position-tracking-report.json"
            self.assertEqual(exit_code, 1)
            self.assertEqual(payload["case_count"], 1)
            self.assertFalse(payload["ready"])
            self.assertTrue(dataset_path.exists())
            self.assertTrue(report_path.exists())
            self.assertEqual(
                read_manual_position_tracking_report(report_path.read_bytes()).case_count,
                1,
            )
            first_dataset = dataset_path.read_bytes()
            second_exit_code, second_payload = _run_cli(
                [
                    "--portfolio-root",
                    portfolio_directory,
                    "--market-root",
                    market_directory,
                    "--evaluated-at",
                    "2026-01-12T18:00:00+00:00",
                    "--output-dir",
                    str(output_root),
                ]
            )
            self.assertEqual(second_exit_code, 2)
            self.assertIn("already exists", str(second_payload["error"]))
            self.assertEqual(dataset_path.read_bytes(), first_dataset)


def _load_main() -> Callable[[], int]:
    script = Path(__file__).parents[2] / "scripts" / "export_manual_position_tracking_dataset.py"
    globals_ = runpy.run_path(str(script), run_name="export_manual_position_tracking_dataset")
    return cast(Callable[[], int], globals_["main"])


def _run_cli(arguments: list[str]) -> tuple[int, dict[str, object]]:
    output = StringIO()
    main = _load_main()
    with (
        patch.object(sys, "argv", ["export_manual_position_tracking_dataset.py", *arguments]),
        redirect_stdout(output),
    ):
        exit_code = main()
    return exit_code, cast(dict[str, object], json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()
