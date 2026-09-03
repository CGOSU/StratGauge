import base64
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from stratgauge.api.app import create_app
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_sources.base import MarketAdapterResult, MarketDailyRequest, MarketDataAdapter
from stratgauge.domain.models import DailyBar


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

            paged_signals = client.get(
                "/v1/workbench/daily-signals/page",
                params={"page": 1, "page_size": 1, "as_of": "2026-01-05T18:00:00+00:00"},
            )
            self.assertEqual(paged_signals.status_code, 200)
            self.assertEqual(paged_signals.json()["total"], 1)
            self.assertFalse(paged_signals.json()["has_next"])
            self.assertEqual(paged_signals.json()["items"][0]["candidate"]["symbol"], "000001.SZ")

            watchlist = client.get(
                "/v1/workbench/watchlist",
                params={"as_of": "2026-01-05T18:00:00+00:00"},
            )
            self.assertEqual(watchlist.status_code, 200)
            self.assertEqual(len(watchlist.json()), 1)
            paged_watchlist = client.get(
                "/v1/workbench/watchlist/page",
                params={"page": 1, "page_size": 1, "as_of": "2026-01-05T18:00:00+00:00"},
            )
            self.assertEqual(paged_watchlist.status_code, 200)
            self.assertEqual(paged_watchlist.json()["total"], 1)
            self.assertEqual(len(paged_watchlist.json()["items"]), 1)
            observation = paged_watchlist.json()["items"][0]["observation_performance"]
            self.assertEqual(observation["status"], "unavailable")
            self.assertEqual(observation["started_at"], "2026-01-05T15:10:00+08:00")
            self.assertIsNone(observation["change_percent"])

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

    def test_manual_position_detail_returns_tracking_and_approved_market_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            client = TestClient(
                create_app(
                    data_root=directory,
                    selection_store=SelectionImportStore(directory),
                    market_root=f"{directory}/market",
                    portfolio_root=f"{directory}/portfolio",
                    market_adapter_factory=_unexpected_provider_call,
                )
            )
            confirmed = client.post(
                "/v1/manual-trades/confirm",
                json={
                    "order_id": "manual-buy",
                    "fill_id": "manual-fill",
                    "local_code": "000001",
                    "exchange": "SZ",
                    "side": "buy",
                    "ordered_at": "2026-01-05T15:00:00+00:00",
                    "filled_at": "2026-01-06T10:00:00+00:00",
                    "quantity": "100",
                    "price": "10",
                },
            )
            self.assertEqual(confirmed.status_code, 201)

            detail = client.get(
                "/v1/workbench/stocks/000001.SZ",
                params={"as_of": "2026-01-07T18:00:00+00:00"},
            )

            self.assertEqual(detail.status_code, 200)
            body = detail.json()
            self.assertEqual(body["source"], "manual_discovery")
            self.assertEqual(body["signals"], [])
            self.assertEqual(body["position"]["quantity"], "100")
            self.assertEqual(len(body["tracking_cases"]), 1)
            self.assertEqual(
                body["tracking_cases"][0]["tracking_policy_id"],
                "manual-position-tracking-v1",
            )
            self.assertEqual(
                {event["event_type"] for event in body["timeline"]},
                {"order", "fill"},
            )
            self.assertEqual(body["market"]["status"], "unavailable")

    def test_historically_recorded_position_detail_opens_after_explicit_enrollment(self) -> None:
        with TemporaryDirectory() as directory:
            client = TestClient(
                create_app(
                    data_root=directory,
                    selection_store=SelectionImportStore(directory),
                    market_root=f"{directory}/market",
                    portfolio_root=f"{directory}/portfolio",
                    market_adapter_factory=_unexpected_provider_call,
                )
            )
            self.assertEqual(
                client.post(
                    "/v1/manual-orders",
                    json={
                        "order_id": "historical-buy",
                        "local_code": "600000",
                        "exchange": "SH",
                        "side": "buy",
                        "ordered_at": "2026-01-05T15:00:00+00:00",
                        "quantity": "50",
                    },
                ).status_code,
                201,
            )
            self.assertEqual(
                client.post(
                    "/v1/manual-fills",
                    json={
                        "fill_id": "historical-fill",
                        "order_id": "historical-buy",
                        "filled_at": "2026-01-06T10:00:00+00:00",
                        "quantity": "50",
                        "price": "20",
                    },
                ).status_code,
                201,
            )
            enrolled = client.post(
                "/v1/manual-tracking-cases/enroll",
                json={
                    "tracking_case_id": "historical-case",
                    "local_code": "600000",
                    "exchange": "SH",
                },
            )
            self.assertEqual(enrolled.status_code, 201)

            detail = client.get("/v1/workbench/stocks/600000.SH")

            self.assertEqual(detail.status_code, 200)
            body = detail.json()
            self.assertEqual(body["source"], "manual_discovery")
            self.assertEqual(body["signals"], [])
            self.assertEqual(body["tracking_cases"][0]["tracking_case_id"], "historical-case")
            self.assertEqual(body["position"]["quantity"], "50")

    def test_stock_performance_endpoint_returns_percentages_only(self) -> None:
        with TemporaryDirectory() as directory:
            client = TestClient(
                create_app(
                    data_root=directory,
                    selection_store=SelectionImportStore(directory),
                    market_root=f"{directory}/market",
                    portfolio_root=f"{directory}/portfolio",
                    market_adapter_factory=_unexpected_provider_call,
                )
            )
            self.assertEqual(
                client.post(
                    "/v1/manual-trades/confirm",
                    json={
                        "order_id": "buy-1",
                        "fill_id": "fill-buy-1",
                        "local_code": "000001",
                        "exchange": "SZ",
                        "side": "buy",
                        "ordered_at": "2026-01-01T09:00:00+08:00",
                        "filled_at": "2026-01-01T10:00:00+08:00",
                        "quantity": "100",
                        "price": "10",
                        "commission": "1",
                    },
                ).status_code,
                201,
            )
            self.assertEqual(
                client.post(
                    "/v1/manual-trades/confirm",
                    json={
                        "order_id": "sell-1",
                        "fill_id": "fill-sell-1",
                        "local_code": "000001",
                        "exchange": "SZ",
                        "side": "sell",
                        "ordered_at": "2026-01-03T09:00:00+08:00",
                        "filled_at": "2026-01-03T10:00:00+08:00",
                        "quantity": "50",
                        "price": "11",
                        "commission": "1",
                        "stamp_duty": "0.5",
                        "sell_reason": "target_reached",
                    },
                ).status_code,
                201,
            )
            market_store = SnapshotStore(f"{directory}/market")
            for session_date, close in (
                (date(2026, 1, 1), Decimal("9.5")),
                (date(2026, 1, 2), Decimal("12")),
                (date(2026, 1, 3), Decimal("11")),
            ):
                _write_api_market_bar(market_store, session_date=session_date, close=close)

            response = client.get(
                "/v1/workbench/stocks/000001.SZ/performance",
                params={"as_of": "2026-01-03T18:00:00+00:00"},
            )

            self.assertEqual(response.status_code, 200)
            body = response.json()
            denominator = Decimal("1001")
            self.assertEqual(body["symbol"], "000001.SZ")
            self.assertEqual(body["status"], "available")
            self.assertEqual(
                Decimal(body["highest_return"]),
                Decimal("199") / denominator,
            )
            self.assertEqual(body["highest_return_date"], "2026-01-02")
            self.assertEqual(
                Decimal(body["maximum_loss_return"]),
                Decimal("-51") / denominator,
            )
            self.assertEqual(body["maximum_loss_date"], "2026-01-01")
            self.assertEqual(
                Decimal(body["latest_cumulative_return"]),
                Decimal("97.5") / denominator,
            )
            self.assertEqual(
                [Decimal(point["cumulative_return"]) for point in body["points"]],
                [
                    Decimal("-51") / denominator,
                    Decimal("199") / denominator,
                    Decimal("97.5") / denominator,
                ],
            )
            self.assertFalse(any("profit" in key for key in body))

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


def _write_api_market_bar(
    store: SnapshotStore,
    *,
    session_date: date,
    close: Decimal,
) -> None:
    shanghai = timezone(timedelta(hours=8), name="Asia/Shanghai")
    event_time = datetime.combine(session_date, datetime.min.time(), tzinfo=shanghai).replace(
        hour=15
    )
    result = MarketAdapterResult(
        provider="test",
        adapter_version="test-v1",
        request=MarketDailyRequest(
            session_date=session_date,
            universe_snapshot_id="test-universe",
            expected_security_ids=("portfolio-000001.SZ",),
        ),
        fetched_at=event_time + timedelta(hours=1),
        raw_records=(),
        bars=(
            DailyBar(
                security_id="portfolio-000001.SZ",
                symbol="000001.SZ",
                session_date=session_date,
                provider="test",
                provider_symbol="000001",
                open=close,
                high=close,
                low=close,
                close=close,
                previous_close=close,
                volume=Decimal("100"),
                amount=close * Decimal("100"),
                event_time=event_time,
                available_at=event_time + timedelta(hours=1),
                ingested_at=event_time + timedelta(hours=1),
                quality_state="approved",
            ),
        ),
    )
    manifest = store.write(result)
    store.update_status(manifest.snapshot_id, SnapshotStatus.APPROVED)


def _unexpected_provider_call(_: str) -> MarketDataAdapter:
    raise AssertionError("workbench read endpoints must not create a provider")


if __name__ == "__main__":
    unittest.main()
