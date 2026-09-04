import unittest
from datetime import UTC, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore, PortfolioStoreError
from stratgauge.domain.models import Exchange, Security
from stratgauge.domain.portfolio import (
    MANUAL_POSITION_TRACKING_POLICY_ID,
    ManualFill,
    ManualOrder,
    ManualOrderSide,
    ManualPositionTrackingCase,
    ManualPositionTrackingOrigin,
    ManualSellReason,
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

    def test_confirmed_manual_buy_creates_an_idempotent_tracking_case(self) -> None:
        with TemporaryDirectory() as directory:
            store = ManualPortfolioStore(directory)
            order = ManualOrder(
                order_id="tracked-buy",
                security=_security(),
                side=ManualOrderSide.BUY,
                ordered_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                quantity=Decimal("100"),
            )
            fill = ManualFill(
                fill_id="tracked-fill",
                order_id=order.order_id,
                filled_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                quantity=Decimal("100"),
                price=Decimal("10"),
            )

            first = store.record_order_and_fill(
                order,
                fill,
                auto_track_manual_buy=True,
            )
            second = store.record_order_and_fill(
                order,
                fill,
                auto_track_manual_buy=True,
            )

            self.assertEqual(first, second)
            self.assertIsNotNone(first[0].tracking_case_id)
            self.assertEqual(first[0].tracking_case_id, first[1].tracking_case_id)
            cases = store.list_tracking_cases()
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].tracking_policy_id, MANUAL_POSITION_TRACKING_POLICY_ID)
            self.assertEqual(cases[0].origin, ManualPositionTrackingOrigin.CONFIRMED_TRADE)
            self.assertEqual(cases[0].source_order_ids, (order.order_id,))
            self.assertEqual(cases[0].source_fill_ids, (fill.fill_id,))

    def test_historical_enrollment_is_explicit_and_does_not_rewrite_legacy_records(self) -> None:
        with TemporaryDirectory() as directory:
            store = ManualPortfolioStore(directory)
            order = ManualOrder(
                order_id="legacy-buy",
                security=_security(),
                side=ManualOrderSide.BUY,
                ordered_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                quantity=Decimal("100"),
            )
            fill = ManualFill(
                fill_id="legacy-fill",
                order_id=order.order_id,
                filled_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                quantity=Decimal("100"),
                price=Decimal("10"),
            )
            store.record_order_and_fill(order, fill)
            started_at = datetime(2026, 2, 1, 10, tzinfo=UTC)

            case = store.enroll_position(
                _security(),
                tracking_case_id="legacy-case",
                tracking_started_at=started_at,
            )
            repeated = store.enroll_position(
                _security(),
                tracking_case_id="legacy-case",
                tracking_started_at=datetime(2026, 3, 1, tzinfo=UTC),
            )

            self.assertEqual(case, repeated)
            self.assertEqual(case.tracking_started_at, started_at)
            self.assertEqual(case.origin, ManualPositionTrackingOrigin.HISTORICAL_ENROLLMENT)
            self.assertEqual(case.source_order_ids, (order.order_id,))
            self.assertEqual(case.source_fill_ids, (fill.fill_id,))
            self.assertIsNone(store.list_orders()[0].tracking_case_id)
            self.assertIsNone(store.list_fills()[0].tracking_case_id)

    def test_tracking_case_limits_sells_to_its_remaining_quantity(self) -> None:
        with TemporaryDirectory() as directory:
            store = ManualPortfolioStore(directory)
            buy_order = ManualOrder(
                order_id="case-buy",
                security=_security(),
                side=ManualOrderSide.BUY,
                ordered_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                quantity=Decimal("100"),
            )
            buy_fill = ManualFill(
                fill_id="case-buy-fill",
                order_id=buy_order.order_id,
                filled_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                quantity=Decimal("100"),
                price=Decimal("10"),
            )
            store.record_order_and_fill(buy_order, buy_fill, auto_track_manual_buy=True)
            case_id = store.list_tracking_cases()[0].tracking_case_id
            sell_order = ManualOrder(
                order_id="case-sell",
                security=_security(),
                side=ManualOrderSide.SELL,
                ordered_at=datetime(2026, 1, 7, 9, tzinfo=UTC),
                quantity=Decimal("101"),
                tracking_case_id=case_id,
                sell_reason=ManualSellReason.OTHER,
            )
            sell_fill = ManualFill(
                fill_id="case-sell-fill",
                order_id=sell_order.order_id,
                tracking_case_id=case_id,
                filled_at=datetime(2026, 1, 7, 10, tzinfo=UTC),
                quantity=Decimal("101"),
                price=Decimal("11"),
            )

            with self.assertRaisesRegex(PortfolioStoreError, "tracking case position"):
                store.record_order_and_fill(sell_order, sell_fill)
            self.assertEqual(len(store.list_orders()), 1)
            self.assertEqual(len(store.list_fills()), 1)

    def test_sell_without_tracking_case_is_allowed_with_multiple_active_cases(self) -> None:
        with TemporaryDirectory() as directory:
            store = ManualPortfolioStore(directory)
            buys: list[tuple[ManualOrder, ManualFill]] = []
            for suffix in ("a", "b"):
                order = ManualOrder(
                    order_id=f"buy-{suffix}",
                    security=_security(),
                    side=ManualOrderSide.BUY,
                    ordered_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                    quantity=Decimal("100"),
                )
                fill = ManualFill(
                    fill_id=f"fill-{suffix}",
                    order_id=order.order_id,
                    filled_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                    quantity=Decimal("100"),
                    price=Decimal("10"),
                )
                store.record_order_and_fill(order, fill)
                buys.append((order, fill))

            for suffix, (order, fill) in zip(("a", "b"), buys, strict=True):
                store.create_tracking_case(
                    ManualPositionTrackingCase(
                        tracking_case_id=f"case-{suffix}",
                        security=_security(),
                        tracking_started_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                        created_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                        origin=ManualPositionTrackingOrigin.HISTORICAL_ENROLLMENT,
                        source_order_ids=(order.order_id,),
                        source_fill_ids=(fill.fill_id,),
                    )
                )

            sell_order = ManualOrder(
                order_id="untracked-sell",
                security=_security(),
                side=ManualOrderSide.SELL,
                ordered_at=datetime(2026, 1, 7, 9, tzinfo=UTC),
                quantity=Decimal("50"),
                sell_reason=ManualSellReason.OTHER,
            )
            sell_fill = ManualFill(
                fill_id="untracked-sell-fill",
                order_id=sell_order.order_id,
                filled_at=datetime(2026, 1, 7, 10, tzinfo=UTC),
                quantity=Decimal("50"),
                price=Decimal("11"),
            )

            result = store.record_order_and_fill(
                sell_order,
                sell_fill,
                auto_resolve_manual_sell=True,
            )

            self.assertIsNone(result[0].tracking_case_id)
            self.assertIsNone(result[1].tracking_case_id)
            self.assertEqual(store.positions()[0].quantity, Decimal("150"))

    def test_candidate_trade_writes_order_and_fill_atomically_and_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            store = ManualPortfolioStore(directory)
            order = ManualOrder(
                order_id="order-candidate",
                security=_security(),
                side=ManualOrderSide.BUY,
                ordered_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                quantity=Decimal("100"),
                selection_candidate_id="candidate-1",
            )
            fill = ManualFill(
                fill_id="fill-candidate",
                order_id=order.order_id,
                filled_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                quantity=Decimal("100"),
                price=Decimal("10"),
            )

            first = store.record_order_and_fill(order, fill)
            second = store.record_order_and_fill(order, fill)

            self.assertEqual(first, (order, fill))
            self.assertEqual(second, first)
            self.assertEqual(len(store.list_orders()), 1)
            self.assertEqual(len(store.list_fills()), 1)
            self.assertEqual(store.positions()[0].quantity, Decimal("100"))

    def test_candidate_trade_rejects_partial_existing_record(self) -> None:
        with TemporaryDirectory() as directory:
            store = ManualPortfolioStore(directory)
            order = ManualOrder(
                order_id="order-partial",
                security=_security(),
                side=ManualOrderSide.BUY,
                ordered_at=datetime(2026, 1, 5, tzinfo=UTC),
                quantity=Decimal("100"),
            )
            store.create_order(order)
            fill = ManualFill(
                fill_id="fill-partial",
                order_id=order.order_id,
                filled_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                quantity=Decimal("100"),
                price=Decimal("10"),
            )

            with self.assertRaisesRegex(PortfolioStoreError, "partial"):
                store.record_order_and_fill(order, fill)

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
