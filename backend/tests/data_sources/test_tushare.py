import unittest
from datetime import UTC, date, datetime
from tempfile import TemporaryDirectory

from stratgauge.data_pipeline.universe import UniverseSnapshotStore, build_universe_snapshot
from stratgauge.data_sources.tushare_reference import (
    TushareReferenceClient,
    TushareReferenceStore,
)
from stratgauge.domain.models import Exchange


class TushareReferenceTests(unittest.TestCase):
    def test_stock_basic_and_calendar_are_explicitly_normalized(self) -> None:
        def query(endpoint: str, _: object) -> list[dict[str, str]]:
            if endpoint == "stock_basic":
                return [
                    {
                        "ts_code": "000001.SZ",
                        "symbol": "000001",
                        "name": "平安银行",
                        "market": "主板",
                        "exchange": "SZSE",
                        "list_status": "L",
                        "list_date": "19910403",
                        "delist_date": "",
                    },
                    {
                        "ts_code": "600000.SH",
                        "symbol": "600000",
                        "name": "浦发银行",
                        "market": "主板",
                        "exchange": "SSE",
                        "list_status": "L",
                        "list_date": "19991110",
                        "delist_date": "",
                    },
                    {
                        "ts_code": "300001.SZ",
                        "symbol": "300001",
                        "name": "特锐德",
                        "market": "创业板",
                        "exchange": "SZSE",
                        "list_status": "L",
                        "list_date": "20100601",
                        "delist_date": "",
                    },
                    {
                        "ts_code": "688001.SH",
                        "symbol": "688001",
                        "name": "华兴源创",
                        "market": "科创板",
                        "exchange": "SSE",
                        "list_status": "L",
                        "list_date": "20190722",
                        "delist_date": "",
                    },
                    {
                        "ts_code": "830001.BJ",
                        "symbol": "830001",
                        "name": "北京证券",
                        "market": "北交所",
                        "exchange": "BSE",
                        "list_status": "L",
                        "list_date": "20200101",
                        "delist_date": "",
                    },
                ]
            return [
                {"cal_date": "20260105", "is_open": "1"},
                {"cal_date": "20260104", "is_open": "0"},
            ]

        client = TushareReferenceClient(query=query)
        rows = client.stock_basic()
        securities = client.securities(rows)
        self.assertEqual(tuple(row["ts_code"] for row in rows), ("000001.SZ", "600000.SH"))
        self.assertEqual(tuple(item.symbol for item in securities), ("000001.SZ", "600000.SH"))
        self.assertEqual(securities[0].exchange, Exchange.SZ)
        self.assertEqual(
            client.trade_calendar(start_date=date(2026, 1, 1), end_date=date(2026, 1, 5)),
            (date(2026, 1, 5),),
        )

    def test_reference_snapshot_can_authorize_universe_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            created_at = datetime(2026, 1, 6, 8, tzinfo=UTC)
            reference_store = TushareReferenceStore(directory)
            reference = reference_store.write(
                endpoint="stock_basic",
                request={"list_status": "L"},
                records=({"ts_code": "000001.SZ"},),
                created_at=created_at,
            )
            client = TushareReferenceClient(
                query=lambda *_: [
                    {
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                        "market": "主板",
                        "exchange": "SZSE",
                        "list_status": "L",
                    }
                ]
            )
            snapshot = build_universe_snapshot(
                as_of=created_at,
                source="tushare:stock_basic",
                source_snapshot_ids=(reference.snapshot_id,),
                securities=client.securities(client.stock_basic()),
            )
            stored = UniverseSnapshotStore(directory).write(snapshot, reference_store)
            self.assertEqual(stored, snapshot)


if __name__ == "__main__":
    unittest.main()
