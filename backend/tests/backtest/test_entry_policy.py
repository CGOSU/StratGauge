import unittest
from datetime import date

from stratgauge.backtest.entry_policy import EntryWindowError, build_entry_window
from stratgauge.backtest.models import EntryExecutionPolicy


class EntryPolicyTests(unittest.TestCase):
    def test_window_uses_next_three_distinct_trading_sessions(self) -> None:
        first, last = build_entry_window(
            as_of=date(2026, 1, 5),
            trading_sessions=[
                date(2026, 1, 8),
                date(2026, 1, 6),
                date(2026, 1, 7),
                date(2026, 1, 7),
                date(2026, 1, 9),
            ],
            policy=EntryExecutionPolicy(),
        )
        self.assertEqual((first, last), (date(2026, 1, 6), date(2026, 1, 8)))

    def test_window_rejects_an_incomplete_calendar(self) -> None:
        with self.assertRaisesRegex(EntryWindowError, "requires 3 sessions"):
            build_entry_window(
                as_of=date(2026, 1, 5),
                trading_sessions=[date(2026, 1, 6), date(2026, 1, 8)],
                policy=EntryExecutionPolicy(),
            )


if __name__ == "__main__":
    unittest.main()
