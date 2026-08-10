import unittest
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.data_pipeline.calendar_cache import CalendarCacheError, TradeCalendarCache
from stratgauge.data_sources.tushare_reference import (
    TushareReferenceClient,
    TushareReferenceStore,
)

NOW = datetime(2026, 1, 11, 8, tzinfo=UTC)


class CalendarCacheTests(unittest.TestCase):
    def test_cache_fetches_only_missing_ranges_and_is_idempotent(self) -> None:
        calls: list[tuple[str, str]] = []

        def query(endpoint: str, params: Mapping[str, str]) -> list[dict[str, str]]:
            self.assertEqual(endpoint, "trade_cal")
            calls.append((params["start_date"], params["end_date"]))
            sessions = {
                "20260101": {"cal_date": "20260102", "is_open": "1"},
                "20260102": {"cal_date": "20260105", "is_open": "1"},
                "20260106": {"cal_date": "20260106", "is_open": "1"},
                "20260107": {"cal_date": "20260107", "is_open": "1"},
                "20260108": {"cal_date": "20260108", "is_open": "1"},
                "20260109": {"cal_date": "20260109", "is_open": "1"},
            }
            start = int(params["start_date"])
            end = int(params["end_date"])
            return [row for key, row in sessions.items() if start <= int(key) <= end]

        with TemporaryDirectory() as directory:
            output = Path(directory) / "calendars" / "a-share-sessions.txt"
            cache = TradeCalendarCache(output)
            reference_store = TushareReferenceStore(directory)
            client = TushareReferenceClient(query=query)

            first = cache.ensure(
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 5),
                exchange="SSE",
                client=client,
                reference_store=reference_store,
                now=NOW,
            )
            covered = cache.ensure(
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 4),
                exchange="SSE",
                client=client,
                reference_store=reference_store,
                now=NOW,
            )
            extended = cache.ensure(
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 9),
                exchange="SSE",
                client=client,
                reference_store=reference_store,
                now=NOW,
            )

            self.assertEqual(first.sessions, (date(2026, 1, 2), date(2026, 1, 5)))
            self.assertFalse(first.skipped)
            self.assertTrue(covered.skipped)
            self.assertEqual(covered.fetched_ranges, ())
            self.assertEqual(extended.fetched_ranges, ((date(2026, 1, 6), date(2026, 1, 9)),))
            self.assertEqual(calls, [("20260101", "20260105"), ("20260106", "20260109")])
            self.assertEqual(
                extended.sessions,
                (
                    date(2026, 1, 2),
                    date(2026, 1, 5),
                    date(2026, 1, 6),
                    date(2026, 1, 7),
                    date(2026, 1, 8),
                    date(2026, 1, 9),
                ),
            )
            self.assertTrue(output.exists())
            self.assertTrue(cache.manifest_path.exists())

    def test_tampered_cache_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "sessions.txt"
            cache = TradeCalendarCache(output)
            reference_store = TushareReferenceStore(directory)
            client = TushareReferenceClient(
                query=lambda *_: [{"cal_date": "20260105", "is_open": "1"}]
            )
            cache.ensure(
                start_date=date(2026, 1, 5),
                end_date=date(2026, 1, 5),
                exchange="SSE",
                client=client,
                reference_store=reference_store,
                now=NOW,
            )
            output.write_text("2026-01-06\n", encoding="utf-8")

            with self.assertRaisesRegex(CalendarCacheError, "does not match"):
                cache.ensure(
                    start_date=date(2026, 1, 5),
                    end_date=date(2026, 1, 5),
                    exchange="SSE",
                    client=client,
                    reference_store=reference_store,
                    now=NOW,
                )


if __name__ == "__main__":
    unittest.main()
