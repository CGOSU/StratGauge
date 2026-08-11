import unittest
from datetime import UTC, date, datetime
from tempfile import TemporaryDirectory

from stratgauge.data_sources.calendar import (
    AKShareCalendarSource,
    BaoStockCalendarSource,
    CalendarReferenceStore,
)


class CalendarSourceTests(unittest.TestCase):
    def test_baostock_parser_keeps_only_trading_days(self) -> None:
        sessions = BaoStockCalendarSource.parse_sessions(
            (
                {"calendar_date": "2026-01-01", "is_trading_day": "0"},
                {"calendar_date": "2026-01-02", "is_trading_day": "1"},
            )
        )

        self.assertEqual(sessions, (date(2026, 1, 2),))

    def test_akshare_parser_normalizes_iso_dates(self) -> None:
        sessions = AKShareCalendarSource.parse_sessions(
            ({"trade_date": "2026-01-02"}, {"trade_date": "2026-01-05"})
        )

        self.assertEqual(sessions, (date(2026, 1, 2), date(2026, 1, 5)))

    def test_calendar_reference_store_is_content_addressed(self) -> None:
        with TemporaryDirectory() as directory:
            store = CalendarReferenceStore(directory)
            first = store.write(
                provider="baostock",
                endpoint="query_trade_dates",
                request={"start_date": "20260101", "end_date": "20260105"},
                records=({"calendar_date": "2026-01-02", "is_trading_day": "1"},),
                created_at=datetime(2026, 1, 6, tzinfo=UTC),
            )

            self.assertEqual(store.load(first.snapshot_id), first)


if __name__ == "__main__":
    unittest.main()
