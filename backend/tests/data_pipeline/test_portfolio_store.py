import unittest
from datetime import UTC, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore, PortfolioStoreError
from stratgauge.domain.models import Exchange, Security
from stratgauge.domain.portfolio import (
    ManualFill,
    ManualOrder,
    ManualOrderSide,
    PortfolioReview,
)


class PortfolioStoreTests(unittest.TestCase):
    def test_orders_fills_and_positions_are_derived_and_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            store = ManualPortfolioStore(directory)
            order = ManualOrder(
                order_id="order-buy-1",
                security=_security(),
                side=ManualOrderSide.BUY,
                ordered_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                quantity=Decimal("100"),
                note="manual test",
            )
            store.create_order(order)
            fill = ManualFill(
                fill_id="fill-buy-1",
                order_id=order.order_id,
                filled_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                quantity=Decimal("100"),
                price=Decimal("10"),
                commission=Decimal("5"),
            )
            self.assertEqual(store.record_fill(fill), fill)
            self.assertEqual(store.record_fill(fill), fill)
            position = store.positions()[0]
            self.assertEqual(position.quantity, Decimal("100"))
            self.assertEqual(position.invested_notional, Decimal("1005"))
            self.assertEqual(position.average_cost, Decimal("10.05"))

            review = PortfolioReview(
                review_id="review-1",
                symbol="000001.SZ",
                reviewed_at=datetime(2026, 1, 7, tzinfo=UTC),
                note="继续观察",
            )
            self.assertEqual(store.write_review(review), review)
            self.assertEqual(store.list_reviews(symbol="000001.SZ"), (review,))

    def test_sell_cannot_exceed_position_or_order_quantity(self) -> None:
        with TemporaryDirectory() as directory:
            store = ManualPortfolioStore(directory)
            store.create_order(
                ManualOrder(
                    order_id="order-buy",
                    security=_security(),
                    side=ManualOrderSide.BUY,
                    ordered_at=datetime(2026, 1, 5, tzinfo=UTC),
                    quantity=Decimal("10"),
                )
            )
            with self.assertRaisesRegex(PortfolioStoreError, "exceeds current position"):
                store.create_order(
                    ManualOrder(
                        order_id="order-sell",
                        security=_security(),
                        side=ManualOrderSide.SELL,
                        ordered_at=datetime(2026, 1, 6, tzinfo=UTC),
                        quantity=Decimal("1"),
                    )
                )
                store.record_fill(
                    ManualFill(
                        fill_id="fill-sell",
                        order_id="order-sell",
                        filled_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                        quantity=Decimal("1"),
                        price=Decimal("10"),
                    )
                )

            store.record_fill(
                ManualFill(
                    fill_id="fill-buy",
                    order_id="order-buy",
                    filled_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                    quantity=Decimal("10"),
                    price=Decimal("10"),
                )
            )
            with self.assertRaisesRegex(PortfolioStoreError, "exceed order quantity"):
                store.record_fill(
                    ManualFill(
                        fill_id="fill-buy-too-much",
                        order_id="order-buy",
                        filled_at=datetime(2026, 1, 6, 11, tzinfo=UTC),
                        quantity=Decimal("1"),
                        price=Decimal("10"),
                    )
                )


def _security() -> Security:
    return Security(
        security_id="security-000001.SZ",
        local_code="000001",
        symbol="000001.SZ",
        exchange=Exchange.SZ,
    )


if __name__ == "__main__":
    unittest.main()
