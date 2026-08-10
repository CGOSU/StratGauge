import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from stratgauge.data_sources.base import MarketDailyRequest
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.domain.models import Exchange, Security

FETCHED_AT = datetime(2026, 1, 6, 8, tzinfo=UTC)


def security(security_id: str, local_code: str, exchange: Exchange) -> Security:
    return Security(
        security_id=security_id,
        local_code=local_code,
        symbol=f"{local_code}.{exchange.value}",
        exchange=exchange,
    )


class TushareMarketTests(unittest.TestCase):
    def test_market_daily_filters_to_the_approved_universe_and_converts_units(self) -> None:
        sz = security("security-1", "000001", Exchange.SZ)
        sh = security("security-2", "600000", Exchange.SH)
        adapter = TushareAdapter(
            market_fetcher=lambda _: [
                {
                    "trade_date": "20260105",
                    "ts_code": "600000.SH",
                    "pre_close": "10.00",
                    "open": "10.10",
                    "high": "10.50",
                    "low": "9.90",
                    "close": "10.20",
                    "vol": "200",
                    "amount": "30",
                },
                {
                    "trade_date": "20260105",
                    "ts_code": "000001.SZ",
                    "pre_close": "10.00",
                    "open": "10.10",
                    "high": "10.50",
                    "low": "9.90",
                    "close": "10.20",
                    "vol": "100",
                    "amount": "20",
                },
                {
                    "trade_date": "20260105",
                    "ts_code": "300001.SZ",
                    "pre_close": "10.00",
                    "open": "10.10",
                    "high": "10.50",
                    "low": "9.90",
                    "close": "10.20",
                    "vol": "100",
                    "amount": "20",
                },
            ],
            clock=lambda: FETCHED_AT,
        )
        request = MarketDailyRequest(
            session_date=date(2026, 1, 5),
            universe_snapshot_id="universe-1",
            expected_security_ids=(sz.security_id, sh.security_id),
        )

        result = adapter.fetch_market_daily(request, (sz, sh))

        self.assertEqual(tuple(bar.symbol for bar in result.bars), ("000001.SZ", "600000.SH"))
        self.assertEqual(len(result.raw_records), 2)
        self.assertEqual(result.bars[0].volume, Decimal("10000"))
        self.assertEqual(result.bars[0].amount, Decimal("20000"))
        self.assertEqual(result.bars[1].volume, Decimal("20000"))
        self.assertEqual(result.bars[1].amount, Decimal("30000"))


if __name__ == "__main__":
    unittest.main()
