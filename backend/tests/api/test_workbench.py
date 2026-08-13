import base64
import unittest
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from stratgauge.api.app import create_app
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_sources.base import MarketDataAdapter


class WorkbenchApiTests(unittest.TestCase):
    def test_daily_signals_watchlist_and_detail_are_read_only(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            client = TestClient(
                create_app(
                    data_root=directory,
                    selection_store=selection_store,
                    market_root=f"{directory}/market",
                    portfolio_root=f"{directory}/portfolio",
                    market_adapter_factory=_unexpected_provider_call,
                )
            )
            imported = client.post(
                "/v1/selection-batches",
                json={
                    "content_base64": base64.b64encode(
                        b"code,name,market\n000001,Ping,SZ\n"
                    ).decode(),
                    "selection_date": "2026-01-05",
                    "exported_at": "2026-01-05T15:05:00+08:00",
                    "formula_name": "趋势选股",
                },
            )
            self.assertEqual(imported.status_code, 201)
            batch = imported.json()
            candidate_id = batch["candidates"][0]["candidate"]["candidate_id"]
            batch_id = batch["batch"]["batch_id"]

            annotation = client.post(
                f"/v1/selection-batches/{batch_id}/annotations",
                json={
                    "candidate_id": candidate_id,
                    "reviewed_at": "2026-01-05T15:10:00+08:00",
                    "overall_score": 4,
                    "outlook": "bullish",
                    "decision": "watch",
                },
            )
            self.assertEqual(annotation.status_code, 200)

            signals = client.get(
                "/v1/workbench/daily-signals",
                params={"as_of": "2026-01-05T18:00:00+00:00"},
            )
            self.assertEqual(signals.status_code, 200)
            self.assertEqual(signals.json()[0]["review_state"], "watching")
            self.assertEqual(signals.json()[0]["market"]["status"], "unavailable")

            watchlist = client.get(
                "/v1/workbench/watchlist",
                params={"as_of": "2026-01-05T18:00:00+00:00"},
            )
            self.assertEqual(watchlist.status_code, 200)
            self.assertEqual(len(watchlist.json()), 1)

            summary = client.get(
                "/v1/workbench/summary",
                params={"as_of": "2026-01-05T18:00:00+00:00"},
            )
            self.assertEqual(summary.status_code, 200)
            self.assertEqual(summary.json()["watching_count"], 1)

            detail = client.get(
                "/v1/workbench/stocks/000001.SZ",
                params={"as_of": "2026-01-05T18:00:00+00:00"},
            )
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["timeline"][0]["event_type"], "signal")
            self.assertEqual(detail.json()["timeline"][1]["event_type"], "annotation")

    def test_invalid_symbol_and_missing_stock_are_structured_errors(self) -> None:
        with TemporaryDirectory() as directory:
            client = TestClient(
                create_app(
                    data_root=directory,
                    selection_store=SelectionImportStore(directory),
                    market_root=f"{directory}/market",
                    portfolio_root=f"{directory}/portfolio",
                )
            )
            invalid = client.get("/v1/workbench/stocks/not-a-symbol")
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(invalid.json()["error"]["code"], "WORKBENCH_SYMBOL_INVALID")
            missing = client.get("/v1/workbench/stocks/000001.SZ")
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(missing.json()["error"]["code"], "WORKBENCH_STOCK_NOT_FOUND")


def _unexpected_provider_call(_: str) -> MarketDataAdapter:
    raise AssertionError("workbench read endpoints must not create a provider")


if __name__ == "__main__":
    unittest.main()
