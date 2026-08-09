import importlib.util
import unittest
from datetime import date

from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.baostock import BaoStockAdapter
from stratgauge.data_sources.base import AdapterError, DailyBarRequest
from stratgauge.data_sources.waizao import WaizaoAdapter
from stratgauge.domain.models import Exchange, Security


class OptionalImportTests(unittest.TestCase):
    def test_default_adapters_defer_optional_dependency_import(self) -> None:
        self.assertIsInstance(AKShareAdapter(), AKShareAdapter)
        self.assertIsInstance(BaoStockAdapter(), BaoStockAdapter)
        self.assertIsInstance(WaizaoAdapter(), WaizaoAdapter)

    def test_missing_provider_dependency_is_reported_at_use_time(self) -> None:
        request = DailyBarRequest(
            security=Security(
                security_id="security-1",
                local_code="000001",
                symbol="000001.SZ",
                exchange=Exchange.SZ,
            ),
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )
        adapters = tuple(
            adapter
            for adapter in (AKShareAdapter(), BaoStockAdapter())
            if importlib.util.find_spec(adapter.provider) is None
        )
        if not adapters:
            self.skipTest("optional provider dependencies are installed")
        for adapter in adapters:
            with self.subTest(provider=adapter.provider):
                with self.assertRaisesRegex(AdapterError, "not installed"):
                    adapter.fetch_daily_bars(request)


if __name__ == "__main__":
    unittest.main()
