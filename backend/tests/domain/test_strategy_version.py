import unittest
from datetime import date
from decimal import Decimal

from pydantic import ValidationError

from stratgauge.domain.models import Signal, StrategyVersion


class StrategyVersionTests(unittest.TestCase):
    def test_strategy_version_is_explicitly_versioned(self) -> None:
        strategy = StrategyVersion(
            strategy_version_id="technical-example-v1",
            strategy_id="technical-example",
            version=1,
            name="示例策略",
            feature_set_version="features-v1",
        )
        self.assertEqual(strategy.version, 1)
        self.assertEqual(strategy.lifecycle.value, "draft")

    def test_signal_rejects_reversed_entry_zone(self) -> None:
        with self.assertRaises(ValidationError):
            Signal(
                signal_id="signal-1",
                security_id="security-1",
                symbol="000001.SZ",
                as_of=date(2026, 1, 5),
                earliest_execution=date(2026, 1, 6),
                latest_execution=date(2026, 1, 7),
                strategy_version_id="strategy-1-v1",
                feature_set_version="features-v1",
                source_snapshot_id="snapshot-1",
                entry_lower=Decimal("10.20"),
                entry_upper=Decimal("10.10"),
            )


if __name__ == "__main__":
    unittest.main()
