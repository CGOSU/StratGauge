import unittest
from datetime import UTC, datetime
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from stratgauge.api.app import create_app
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export


class PortfolioApiTests(unittest.TestCase):
    def test_records_manual_order_fill_review_and_position(self) -> None:
        with TemporaryDirectory() as directory:
            client = TestClient(create_app(portfolio_store=ManualPortfolioStore(directory)))
            order = client.post(
                "/v1/manual-orders",
                json={
                    "order_id": "order-1",
                    "local_code": "000001",
                    "exchange": "SZ",
                    "side": "buy",
                    "ordered_at": "2026-01-05T15:00:00+00:00",
                    "quantity": "100",
                },
            )
            self.assertEqual(order.status_code, 201)
            fill = client.post(
                "/v1/manual-fills",
                json={
                    "fill_id": "fill-1",
                    "order_id": "order-1",
                    "filled_at": "2026-01-06T10:00:00+00:00",
                    "quantity": "100",
                    "price": "10",
                    "commission": "5",
                },
            )
            self.assertEqual(fill.status_code, 201)
            positions = client.get("/v1/positions")
            self.assertEqual(positions.status_code, 200)
            self.assertEqual(positions.json()[0]["quantity"], "100")
            self.assertEqual(len(client.get("/v1/manual-orders").json()), 1)
            self.assertEqual(len(client.get("/v1/manual-fills").json()), 1)
            review = client.post(
                "/v1/portfolio-reviews",
                json={
                    "review_id": "review-1",
                    "symbol": "000001.SZ",
                    "reviewed_at": "2026-01-07T10:00:00+00:00",
                    "note": "继续观察",
                },
            )
            self.assertEqual(review.status_code, 201)
            self.assertEqual(len(client.get("/v1/portfolio-reviews").json()), 1)

    def test_unfilled_order_supports_split_fills_and_rejects_overfill(self) -> None:
        with TemporaryDirectory() as directory:
            client = TestClient(create_app(portfolio_store=ManualPortfolioStore(directory)))
            order = client.post(
                "/v1/manual-orders",
                json={
                    "order_id": "split-order",
                    "local_code": "000001",
                    "exchange": "SZ",
                    "side": "buy",
                    "ordered_at": "2026-01-05T09:00:00+00:00",
                    "quantity": "100",
                },
            )
            self.assertEqual(order.status_code, 201)
            self.assertEqual(client.get("/v1/positions").json(), [])

            first_fill = client.post(
                "/v1/manual-fills",
                json={
                    "fill_id": "split-fill-1",
                    "order_id": "split-order",
                    "filled_at": "2026-01-05T10:00:00+00:00",
                    "quantity": "40",
                    "price": "10",
                },
            )
            self.assertEqual(first_fill.status_code, 201)
            self.assertEqual(client.get("/v1/positions").json()[0]["quantity"], "40")

            second_fill = client.post(
                "/v1/manual-fills",
                json={
                    "fill_id": "split-fill-2",
                    "order_id": "split-order",
                    "filled_at": "2026-01-05T11:00:00+00:00",
                    "quantity": "60",
                    "price": "10.1",
                },
            )
            self.assertEqual(second_fill.status_code, 201)
            self.assertEqual(client.get("/v1/positions").json()[0]["quantity"], "100")

            overfill = client.post(
                "/v1/manual-fills",
                json={
                    "fill_id": "split-fill-over",
                    "order_id": "split-order",
                    "filled_at": "2026-01-05T12:00:00+00:00",
                    "quantity": "1",
                    "price": "10.2",
                },
            )
            self.assertEqual(overfill.status_code, 409)
            self.assertEqual(overfill.json()["error"]["code"], "PORTFOLIO_FILL_REJECTED")
            self.assertEqual(len(client.get("/v1/manual-fills").json()), 2)

            missing_sell_reason = client.post(
                "/v1/manual-orders",
                json={
                    "order_id": "sell-order-missing-reason",
                    "local_code": "000001",
                    "exchange": "SZ",
                    "side": "sell",
                    "ordered_at": "2026-01-05T13:00:00+00:00",
                    "quantity": "10",
                },
            )
            self.assertEqual(missing_sell_reason.status_code, 422)
            valid_sell = client.post(
                "/v1/manual-orders",
                json={
                    "order_id": "sell-order",
                    "local_code": "000001",
                    "exchange": "SZ",
                    "side": "sell",
                    "ordered_at": "2026-01-05T13:00:00+00:00",
                    "quantity": "10",
                    "sell_reason": "target_reached",
                },
            )
            self.assertEqual(valid_sell.status_code, 201)
            self.assertEqual(len(client.get("/v1/manual-orders").json()), 2)

    def test_candidate_trade_creates_linked_order_and_fill(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            content = b"code,name,market\n000001,Ping,SZ\n"
            imported = import_tdx_export(
                content,
                selection_date=datetime(2026, 1, 5, tzinfo=UTC).date(),
                exported_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                imported_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
                formula_name="fixture",
            )
            selection_store.write(content, imported)
            client = TestClient(
                create_app(
                    selection_store=selection_store,
                    portfolio_store=ManualPortfolioStore(directory),
                )
            )
            response = client.post(
                "/v1/selection-candidate-trades",
                json={
                    "order_id": "order-candidate",
                    "fill_id": "fill-candidate",
                    "selection_candidate_id": imported.candidates[0].candidate_id,
                    "local_code": "000001",
                    "exchange": "SZ",
                    "ordered_at": "2026-01-05T15:00:00+00:00",
                    "filled_at": "2026-01-06T10:00:00+00:00",
                    "quantity": "100",
                    "price": "10",
                    "note": "候选实际买入",
                },
            )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(
                response.json()[0]["selection_candidate_id"], imported.candidates[0].candidate_id
            )
            self.assertEqual(response.json()[1]["order_id"], "order-candidate")
            self.assertEqual(client.get("/v1/positions").json()[0]["quantity"], "100")

    def test_candidate_trade_requires_an_existing_matching_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            client = TestClient(
                create_app(
                    selection_store=SelectionImportStore(directory),
                    portfolio_store=ManualPortfolioStore(directory),
                )
            )
            response = client.post(
                "/v1/selection-candidate-trades",
                json={
                    "order_id": "order-missing-candidate",
                    "fill_id": "fill-missing-candidate",
                    "selection_candidate_id": "candidate-missing",
                    "local_code": "000001",
                    "exchange": "SZ",
                    "ordered_at": "2026-01-05T15:00:00+00:00",
                    "filled_at": "2026-01-06T10:00:00+00:00",
                    "quantity": "100",
                    "price": "10",
                },
            )
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "SELECTION_CANDIDATE_NOT_FOUND")

    def test_candidate_trade_is_rejected_without_leaving_a_partial_record(self) -> None:
        with TemporaryDirectory() as directory:
            client = TestClient(create_app(portfolio_store=ManualPortfolioStore(directory)))
            invalid = client.post(
                "/v1/selection-candidate-trades",
                json={
                    "order_id": "order-invalid",
                    "fill_id": "fill-invalid",
                    "selection_candidate_id": "candidate-1",
                    "local_code": "000001",
                    "exchange": "SZ",
                    "ordered_at": "2026-01-06T15:00:00+00:00",
                    "filled_at": "2026-01-05T10:00:00+00:00",
                    "quantity": "100",
                    "price": "10",
                },
            )
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(client.get("/v1/manual-orders").json(), [])
            self.assertEqual(client.get("/v1/manual-fills").json(), [])

    def test_confirmed_trade_supports_sell_reason_and_is_atomic(self) -> None:
        with TemporaryDirectory() as directory:
            client = TestClient(
                create_app(
                    data_root=directory,
                    portfolio_store=ManualPortfolioStore(directory),
                    market_root=f"{directory}/market",
                    portfolio_root=directory,
                )
            )
            buy = client.post(
                "/v1/manual-trades/confirm",
                json={
                    "order_id": "confirm-buy",
                    "fill_id": "confirm-buy-fill",
                    "local_code": "000001",
                    "exchange": "SZ",
                    "side": "buy",
                    "ordered_at": "2026-01-05T15:00:00+00:00",
                    "filled_at": "2026-01-06T10:00:00+00:00",
                    "quantity": "100",
                    "price": "10",
                },
            )
            self.assertEqual(buy.status_code, 201)
            sell = client.post(
                "/v1/manual-trades/confirm",
                json={
                    "order_id": "confirm-sell",
                    "fill_id": "confirm-sell-fill",
                    "local_code": "000001",
                    "exchange": "SZ",
                    "side": "sell",
                    "ordered_at": "2026-01-07T15:00:00+00:00",
                    "filled_at": "2026-01-07T10:00:00+00:00",
                    "quantity": "50",
                    "price": "11",
                    "sell_reason": "target_reached",
                    "note": "达到目标位",
                },
            )
            self.assertEqual(sell.status_code, 422)
            self.assertEqual(client.get("/v1/manual-orders").json().__len__(), 1)
            sell = client.post(
                "/v1/manual-trades/confirm",
                json={
                    "order_id": "confirm-sell",
                    "fill_id": "confirm-sell-fill",
                    "local_code": "000001",
                    "exchange": "SZ",
                    "side": "sell",
                    "ordered_at": "2026-01-07T09:00:00+00:00",
                    "filled_at": "2026-01-07T10:00:00+00:00",
                    "quantity": "50",
                    "price": "11",
                    "sell_reason": "target_reached",
                    "note": "达到目标位",
                },
            )
            self.assertEqual(sell.status_code, 201)
            self.assertEqual(sell.json()[0]["sell_reason"], "target_reached")
            self.assertEqual(client.get("/v1/positions").json()[0]["quantity"], "50")
            trades = client.get("/v1/workbench/trades")
            self.assertEqual(trades.status_code, 200)
            self.assertEqual(len(trades.json()), 2)
            positions = client.get(
                "/v1/workbench/positions",
                params={"as_of": "2026-01-08T00:00:00+00:00"},
            )
            self.assertEqual(positions.status_code, 200)
            self.assertIsNone(positions.json()[0]["unrealized_profit"])
            self.assertEqual(positions.json()[0]["market"]["status"], "unavailable")
            self.assertTrue(positions.json()[0]["unrealized_reason"])

    def test_confirmed_manual_buy_uses_the_builtin_tracking_policy(self) -> None:
        with TemporaryDirectory() as directory:
            portfolio_store = ManualPortfolioStore(directory)
            client = TestClient(
                create_app(
                    data_root=directory,
                    portfolio_store=portfolio_store,
                    market_root=f"{directory}/market",
                    portfolio_root=directory,
                )
            )
            payload = {
                "order_id": "tracked-buy",
                "fill_id": "tracked-fill",
                "local_code": "000001",
                "exchange": "SZ",
                "side": "buy",
                "ordered_at": "2026-01-05T15:00:00+00:00",
                "filled_at": "2026-01-06T10:00:00+00:00",
                "quantity": "100",
                "price": "10",
            }
            first = client.post("/v1/manual-trades/confirm", json=payload)
            repeated = client.post("/v1/manual-trades/confirm", json=payload)

            self.assertEqual(first.status_code, 201)
            self.assertEqual(repeated.status_code, 201)
            self.assertEqual(
                first.json()[0]["tracking_case_id"], repeated.json()[0]["tracking_case_id"]
            )
            cases = portfolio_store.list_tracking_cases()
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].tracking_policy_id, "manual-position-tracking-v1")
            self.assertEqual(cases[0].origin.value, "confirmed_trade")
            self.assertEqual(
                first.json()[0]["tracking_case_id"],
                first.json()[1]["tracking_case_id"],
            )
            self.assertEqual(
                client.get("/v1/manual-orders").json()[0]["tracking_case_id"],
                first.json()[0]["tracking_case_id"],
            )

    def test_historical_position_can_be_explicitly_enrolled_without_rewriting_trade_records(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            client = TestClient(
                create_app(
                    data_root=directory,
                    portfolio_store=ManualPortfolioStore(directory),
                    market_root=f"{directory}/market",
                    portfolio_root=directory,
                )
            )
            order = client.post(
                "/v1/manual-orders",
                json={
                    "order_id": "legacy-buy",
                    "local_code": "000001",
                    "exchange": "SZ",
                    "side": "buy",
                    "ordered_at": "2026-01-05T15:00:00+00:00",
                    "quantity": "100",
                },
            )
            self.assertEqual(order.status_code, 201)
            fill = client.post(
                "/v1/manual-fills",
                json={
                    "fill_id": "legacy-fill",
                    "order_id": "legacy-buy",
                    "filled_at": "2026-01-06T10:00:00+00:00",
                    "quantity": "100",
                    "price": "10",
                },
            )
            self.assertEqual(fill.status_code, 201)

            enrolled = client.post(
                "/v1/manual-tracking-cases/enroll",
                json={
                    "tracking_case_id": "legacy-case",
                    "local_code": "000001",
                    "exchange": "SZ",
                },
            )
            self.assertEqual(enrolled.status_code, 201)
            self.assertGreater(enrolled.json()["tracking_started_at"], "2026-01-06T10:00:00+00:00")
            self.assertEqual(enrolled.json()["origin"], "historical_enrollment")
            self.assertIsNone(client.get("/v1/manual-orders").json()[0]["tracking_case_id"])
            self.assertIsNone(client.get("/v1/manual-fills").json()[0]["tracking_case_id"])

            repeated = client.post(
                "/v1/manual-tracking-cases/enroll",
                json={
                    "tracking_case_id": "legacy-case",
                    "local_code": "000001",
                    "exchange": "SZ",
                },
            )
            self.assertEqual(repeated.status_code, 201)
            self.assertEqual(repeated.json(), enrolled.json())

    def test_fill_rejection_is_structured(self) -> None:
        with TemporaryDirectory() as directory:
            client = TestClient(create_app(portfolio_store=ManualPortfolioStore(directory)))
            response = client.post(
                "/v1/manual-fills",
                json={
                    "fill_id": "fill-missing",
                    "order_id": "missing-order",
                    "filled_at": datetime(2026, 1, 6, tzinfo=UTC).isoformat(),
                    "quantity": "1",
                    "price": "10",
                },
            )
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "PORTFOLIO_FILL_REJECTED")


if __name__ == "__main__":
    unittest.main()
