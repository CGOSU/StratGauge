import unittest
from decimal import Decimal

from stratgauge.strategies.ast import StrategyExpressionError, evaluate_expression


class StrategyAstTests(unittest.TestCase):
    def test_evaluates_allow_listed_arithmetic_and_boolean_expression(self) -> None:
        result = evaluate_expression(
            "close > ma20 and volume / volume_ma20 > 1.5",
            {
                "close": Decimal("10"),
                "ma20": Decimal("9"),
                "volume": Decimal("200"),
                "volume_ma20": Decimal("100"),
            },
        )
        self.assertTrue(result)

    def test_supports_only_explicit_functions(self) -> None:
        result = evaluate_expression(
            "min(close * 1.01, resistance)",
            {"close": Decimal("10"), "resistance": Decimal("10.05")},
        )
        self.assertEqual(result, Decimal("10.05"))

    def test_rejects_attribute_access_and_unknown_functions(self) -> None:
        with self.assertRaises(StrategyExpressionError):
            evaluate_expression("__import__('os').system('echo bad')", {})
        with self.assertRaises(StrategyExpressionError):
            evaluate_expression("round(close)", {"close": Decimal("10")})

    def test_rejects_missing_features_and_division_by_zero(self) -> None:
        with self.assertRaises(StrategyExpressionError):
            evaluate_expression("close > ma20", {"close": Decimal("10")})
        with self.assertRaises(StrategyExpressionError):
            evaluate_expression("close / volume", {"close": Decimal("10"), "volume": Decimal("0")})


if __name__ == "__main__":
    unittest.main()
