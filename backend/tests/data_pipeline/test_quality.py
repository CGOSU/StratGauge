import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from stratgauge.data_pipeline.quality import (
    check_adjustment_factors,
    check_bars,
    compare_bars,
)
from stratgauge.domain.models import AdjustmentFactor, DailyBar


def factor(**changes: object) -> AdjustmentFactor:
    values: dict[str, object] = {
        "security_id": "security-1",
        "symbol": "000001.SZ",
        "session_date": date(2026, 1, 5),
        "provider": "fixture",
        "provider_symbol": "000001.SZ",
        "factor": Decimal("1.0"),
        "event_time": datetime(2026, 1, 5, 7, tzinfo=UTC),
        "available_at": datetime(2026, 1, 5, 8, tzinfo=UTC),
        "ingested_at": datetime(2026, 1, 5, 9, tzinfo=UTC),
    }
    values.update(changes)
    return AdjustmentFactor.model_validate(values)


def bar(**changes: object) -> DailyBar:
    values: dict[str, object] = {
        "security_id": "security-1",
        "symbol": "000001.SZ",
        "session_date": date(2026, 1, 5),
        "provider": "fixture",
        "provider_symbol": "000001",
        "open": Decimal("10.00"),
        "high": Decimal("10.50"),
        "low": Decimal("9.80"),
        "close": Decimal("10.20"),
        "volume": Decimal("1000"),
        "amount": Decimal("10200"),
        "event_time": datetime(2026, 1, 5, 7, tzinfo=UTC),
        "available_at": datetime(2026, 1, 5, 8, tzinfo=UTC),
        "ingested_at": datetime(2026, 1, 5, 9, tzinfo=UTC),
    }
    values.update(changes)
    return DailyBar.model_validate(values)


class QualityTests(unittest.TestCase):
    def test_adjustment_factor_coverage_uses_same_quality_gate(self) -> None:
        report = check_adjustment_factors(
            [factor()],
            snapshot_id="factor-snapshot-1",
            expected_sessions=(date(2026, 1, 5),),
            expected_security_ids=("security-1", "security-2"),
            missing_security_severity="warning",
            minimum_security_coverage=Decimal("0.9"),
        )

        self.assertFalse(report.approved)
        self.assertEqual(report.errors[0].code, "LOW_SECURITY_COVERAGE")

    def test_missing_session_blocks_approval(self) -> None:
        report = check_bars(
            [bar()],
            snapshot_id="snapshot-1",
            expected_sessions=[date(2026, 1, 5), date(2026, 1, 6)],
        )
        self.assertFalse(report.approved)
        self.assertEqual(report.errors[0].code, "MISSING_SESSION")

    def test_missing_security_can_be_a_warning_with_a_coverage_gate(self) -> None:
        report = check_bars(
            [bar()],
            snapshot_id="snapshot-1",
            expected_security_ids=("security-1", "security-2"),
            missing_security_severity="warning",
            minimum_security_coverage=Decimal("0.5"),
        )

        self.assertTrue(report.approved)
        self.assertEqual(report.warnings[0].code, "MISSING_SECURITY")

    def test_low_security_coverage_blocks_approval(self) -> None:
        report = check_bars(
            [bar()],
            snapshot_id="snapshot-1",
            expected_security_ids=("security-1", "security-2"),
            missing_security_severity="warning",
            minimum_security_coverage=Decimal("0.9"),
        )

        self.assertFalse(report.approved)
        self.assertEqual(report.errors[0].code, "LOW_SECURITY_COVERAGE")

    def test_duplicate_bar_blocks_approval(self) -> None:
        report = check_bars([bar(), bar()], snapshot_id="snapshot-1")
        self.assertFalse(report.approved)
        self.assertEqual(report.errors[0].code, "DUPLICATE_BAR")

    def test_cross_source_disagreement_is_not_silently_resolved(self) -> None:
        report = compare_bars(
            [bar(provider="akshare")],
            [bar(provider="baostock", close=Decimal("10.50"))],
            snapshot_id="comparison-1",
        )
        self.assertFalse(report.approved)
        self.assertEqual(report.errors[0].code, "CROSS_SOURCE_DISAGREEMENT")
        self.assertEqual(report.errors[0].details["primary"], "10.20")

    def test_cross_source_values_within_tolerance_pass(self) -> None:
        report = compare_bars(
            [bar(provider="akshare")],
            [bar(provider="baostock", close=Decimal("10.205"))],
            snapshot_id="comparison-1",
        )
        self.assertTrue(report.approved)

    def test_cross_source_amount_rounding_uses_relative_tolerance(self) -> None:
        report = compare_bars(
            [bar(provider="waizao", amount=Decimal("10200"))],
            [bar(provider="baostock", amount=Decimal("10200.05"))],
            snapshot_id="comparison-1",
        )
        self.assertTrue(report.approved)

    def test_cross_source_amount_difference_beyond_relative_tolerance_fails(self) -> None:
        report = compare_bars(
            [bar(provider="waizao", amount=Decimal("10200"))],
            [bar(provider="baostock", amount=Decimal("10202"))],
            snapshot_id="comparison-1",
        )
        self.assertFalse(report.approved)


if __name__ == "__main__":
    unittest.main()
