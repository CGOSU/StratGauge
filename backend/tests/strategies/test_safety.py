import unittest

from stratgauge.strategies.ast import StrategyExpressionError, parse_expression


class StrategySafetyTests(unittest.TestCase):
    def test_rejects_comprehensions_and_lambda(self) -> None:
        for source in ("[x for x in values]", "lambda: 1"):
            with self.subTest(source=source), self.assertRaises(StrategyExpressionError):
                parse_expression(source)

    def test_rejects_chained_comparisons(self) -> None:
        with self.assertRaises(StrategyExpressionError):
            parse_expression("low < close < high")


if __name__ == "__main__":
    unittest.main()
