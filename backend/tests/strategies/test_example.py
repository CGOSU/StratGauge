import unittest
from datetime import date
from decimal import Decimal

from stratgauge.strategies.example import ExampleStrategy


class ExampleStrategyTests(unittest.TestCase):
    def test_generates_next_session_entry_zone(self) -> None:
        signal = ExampleStrategy().generate_signal(
            signal_id="signal-1",
            security_id="security-1",
            symbol="000001.SZ",
            as_of=date(2026, 1, 5),
            execution_session=date(2026, 1, 6),
            source_snapshot_id="snapshot-1",
            features={
                "close": Decimal("10"),
                "ma20": Decimal("9"),
                "ma5": Decimal("10"),
                "ma10": Decimal("9.5"),
                "volume": Decimal("200"),
                "volume_ma20": Decimal("100"),
                "support_price": Decimal("9.8"),
                "resistance_price": Decimal("10.05"),
            },
        )
        self.assertIsNone(signal.rejection_reason)
        self.assertEqual(signal.earliest_execution, date(2026, 1, 6))
        self.assertEqual(signal.entry_upper, Decimal("10.05"))

    def test_rejects_ineligible_candidate_without_price_zone(self) -> None:
        signal = ExampleStrategy().generate_signal(
            signal_id="signal-1",
            security_id="security-1",
            symbol="000001.SZ",
            as_of=date(2026, 1, 5),
            execution_session=date(2026, 1, 6),
            source_snapshot_id="snapshot-1",
            features={
                "close": Decimal("8"),
                "ma20": Decimal("9"),
                "ma5": Decimal("8"),
                "ma10": Decimal("9.5"),
                "volume": Decimal("200"),
                "volume_ma20": Decimal("100"),
            },
        )
        self.assertIsNotNone(signal.rejection_reason)
        self.assertIsNone(signal.entry_lower)


if __name__ == "__main__":
    unittest.main()
