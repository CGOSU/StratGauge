import unittest
from datetime import UTC, datetime
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from stratgauge.api.app import create_app
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore


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
