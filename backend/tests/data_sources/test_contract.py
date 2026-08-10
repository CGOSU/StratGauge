import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import ValidationError

from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.baostock import BaoStockAdapter
from stratgauge.data_sources.base import AdapterError, DailyBarRequest
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.data_sources.waizao import WaizaoAdapter
from stratgauge.domain.models import Exchange, Security

FETCHED_AT = datetime(2026, 1, 6, 8, tzinfo=UTC)


def request() -> DailyBarRequest:
    return DailyBarRequest(
        security=Security(
            security_id="security-1",
            local_code="000001",
            symbol="000001.SZ",
            exchange=Exchange.SZ,
        ),
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 5),
    )


def rows() -> list[dict[str, str]]:
    return [
        {
            "日期": "2026-01-05",
            "开盘": "10.00",
            "最高": "10.50",
            "最低": "9.80",
            "收盘": "10.20",
            "成交量": "1000",
            "成交额": "10200",
        }
    ]


class AdapterContractTests(unittest.TestCase):
    def test_akshare_adapter_normalizes_provider_columns(self) -> None:
        adapter = AKShareAdapter(fetcher=lambda _: rows(), clock=lambda: FETCHED_AT)
        result = adapter.fetch_daily_bars(request())
        self.assertEqual(result.provider, "akshare")
        self.assertEqual(result.bars[0].close, Decimal("10.20"))
        self.assertEqual(result.bars[0].symbol, "000001.SZ")

    def test_waizao_adapter_normalizes_api_columns(self) -> None:
        raw = [
            {
                "tdate": "2026-01-05",
                "code": "000001",
                "open": 10.00,
                "high": 10.50,
                "low": 9.80,
                "close": 10.20,
                "cjl": 1000,
                "cje": 10200,
                "fq": 0,
                "ktype": 101,
            }
        ]
        adapter = WaizaoAdapter(fetcher=lambda _: raw, clock=lambda: FETCHED_AT)
        result = adapter.fetch_daily_bars(request())
        self.assertEqual(result.provider, "waizao")
        self.assertEqual(result.raw_records, tuple(raw))
        self.assertEqual(result.bars[0].volume, Decimal("100000"))
        self.assertEqual(result.bars[0].amount, Decimal("10200"))

    def test_tushare_adapter_converts_lots_and_thousand_currency(self) -> None:
        raw = [
            {
                "trade_date": "20260105",
                "ts_code": "000001.SZ",
                "open": "10.00",
                "high": "10.50",
                "low": "9.80",
                "close": "10.20",
                "pre_close": "10.00",
                "vol": "1000",
                "amount": "10200",
            }
        ]
        adapter = TushareAdapter(fetcher=lambda _: raw, clock=lambda: FETCHED_AT)
        result = adapter.fetch_daily_bars(request())
        self.assertEqual(result.provider, "tushare")
        self.assertEqual(result.raw_records, tuple(raw))
        self.assertEqual(result.bars[0].volume, Decimal("100000"))
        self.assertEqual(result.bars[0].amount, Decimal("10200000"))
        self.assertEqual(result.bars[0].previous_close, Decimal("10.00"))

    def test_baostock_adapter_obeys_same_contract(self) -> None:
        adapter = BaoStockAdapter(
            fetcher=lambda _: [
                {
                    "date": "2026-01-05",
                    "code": "sz.000001",
                    "open": "10.00",
                    "high": "10.50",
                    "low": "9.80",
                    "close": "10.20",
                    "preclose": "10.00",
                    "volume": "1000",
                    "amount": "10200",
                    "tradestatus": "1",
                }
            ],
            clock=lambda: FETCHED_AT,
        )
        result = adapter.fetch_daily_bars(request())
        self.assertEqual(result.bars[0].previous_close, Decimal("10.00"))
        self.assertTrue(result.bars[0].tradable)

    def test_adapter_rejects_rows_outside_request(self) -> None:
        adapter = AKShareAdapter(
            fetcher=lambda _: [{**rows()[0], "日期": "2026-01-06"}],
            clock=lambda: FETCHED_AT,
        )
        with self.assertRaises(AdapterError):
            adapter.fetch_daily_bars(request())

    def test_request_rejects_reversed_date_range(self) -> None:
        with self.assertRaises(ValidationError):
            DailyBarRequest(
                security=request().security,
                start_date=date(2026, 1, 6),
                end_date=date(2026, 1, 5),
            )


if __name__ == "__main__":
    unittest.main()
