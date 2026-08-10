import unittest
from datetime import UTC, date, datetime
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from stratgauge.api.app import create_app
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.universe import UniverseSnapshotStore, build_universe_snapshot
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.base import DailyBarRequest
from stratgauge.domain.models import Exchange, Security


class UniverseApiTests(unittest.TestCase):
    def test_search_uses_latest_snapshot_known_at_as_of(self) -> None:
        with TemporaryDirectory() as directory:
            market_store = _approved_market_store(f"{directory}/market")
            source_snapshot_id = market_store.list_manifests()[0].snapshot_id
            universe_store = UniverseSnapshotStore(f"{directory}/universe")
            snapshot = build_universe_snapshot(
                as_of=datetime(2026, 1, 5, 16, tzinfo=UTC),
                source="fixture-universe",
                source_snapshot_ids=(source_snapshot_id,),
                securities=(
                    _security("000001", Exchange.SZ, "平安银行"),
                    _security("600000", Exchange.SH, "浦发银行"),
                ),
            )
            universe_store.write(snapshot, market_store)
            client = TestClient(
                create_app(
                    selection_store=SelectionImportStore(f"{directory}/selection"),
                    portfolio_store=ManualPortfolioStore(f"{directory}/portfolio"),
                    universe_store=universe_store,
                )
            )

            response = client.get(
                "/v1/universe/securities",
                params={"as_of": "2026-01-06T09:00:00+00:00", "query": "平安"},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["universe_snapshot_id"], snapshot.universe_snapshot_id)
            self.assertEqual([item["symbol"] for item in payload["results"]], ["000001.SZ"])

            future_response = client.get(
                "/v1/universe/securities",
                params={"as_of": "2026-01-04T09:00:00+00:00"},
            )
            self.assertEqual(future_response.status_code, 404)
            self.assertEqual(
                future_response.json()["error"]["code"],
                "UNIVERSE_SNAPSHOT_NOT_FOUND",
            )

    def test_limit_is_explicitly_bounded(self) -> None:
        with TemporaryDirectory() as directory:
            client = TestClient(
                create_app(
                    selection_store=SelectionImportStore(f"{directory}/selection"),
                    portfolio_store=ManualPortfolioStore(f"{directory}/portfolio"),
                    universe_store=UniverseSnapshotStore(f"{directory}/universe"),
                )
            )
            response = client.get(
                "/v1/universe/securities",
                params={"as_of": "2026-01-06T09:00:00+00:00", "limit": 0},
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["error"]["code"], "UNIVERSE_LIMIT_INVALID")


def _approved_market_store(root: str) -> SnapshotStore:
    store = SnapshotStore(root)
    adapter = AKShareAdapter(
        fetcher=lambda _: [
            {
                "日期": "2026-01-05",
                "开盘": "10",
                "最高": "10",
                "最低": "10",
                "收盘": "10",
                "成交量": "1",
                "成交额": "10",
            }
        ],
        clock=lambda: datetime(2026, 1, 6, 8, tzinfo=UTC),
    )
    manifest = store.write(
        adapter.fetch_daily_bars(
            DailyBarRequest(
                security=_security("000001", Exchange.SZ),
                start_date=date(2026, 1, 5),
                end_date=date(2026, 1, 5),
            )
        )
    )
    store.update_status(manifest.snapshot_id, SnapshotStatus.APPROVED)
    return store


def _security(local_code: str, exchange: Exchange, name: str | None = None) -> Security:
    symbol = f"{local_code}.{exchange.value}"
    return Security(
        security_id=f"security-{symbol}",
        local_code=local_code,
        symbol=symbol,
        exchange=exchange,
        name=name,
    )


if __name__ == "__main__":
    unittest.main()
