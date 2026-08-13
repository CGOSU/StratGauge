import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.backtest.selection_ml_datasets import (
    build_buy_timing_dataset,
    build_sell_timing_dataset,
    read_buy_timing_dataset,
    serialize_buy_timing_dataset,
)
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.domain.models import Exchange, Security
from stratgauge.domain.portfolio import ManualFill, ManualOrder, ManualOrderSide
from stratgauge.domain.selections import (
    SelectionAnnotation,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
    TdxSelectionImport,
)

CONTENT = b"code,name,market\n000001,Ping,SZ\n"
EXPORTED_AT = datetime(2026, 1, 5, 15, 5, tzinfo=UTC)
IMPORTED_AT = datetime(2026, 1, 5, 15, 10, tzinfo=UTC)
SESSIONS = (
    date(2026, 1, 6),
    date(2026, 1, 7),
    date(2026, 1, 8),
    date(2026, 1, 9),
)


class SelectionMLDatasetTests(unittest.TestCase):
    def test_buy_timing_dataset_labels_known_purchase_and_pending_window(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            portfolio_store = ManualPortfolioStore(directory)
            imported = _imported(selection_store)
            candidate_id = imported.candidates[0].candidate_id
            selection_store.write_annotation(
                imported.batch.batch_id,
                SelectionAnnotation(
                    annotation_id="annotation-buy",
                    candidate_id=candidate_id,
                    reviewed_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
                    recorded_at=datetime(2026, 1, 5, 16, 1, tzinfo=UTC),
                    overall_score=4,
                    outlook=SelectionAnnotationOutlook.BULLISH,
                    decision=SelectionAnnotationDecision.PREPARE_BUY,
                ),
            )
            order = _order("order-buy", candidate_id, ManualOrderSide.BUY)
            portfolio_store.create_order(order)
            portfolio_store.record_fill(
                ManualFill(
                    fill_id="fill-buy",
                    order_id=order.order_id,
                    filled_at=datetime(2026, 1, 7, 10, tzinfo=UTC),
                    quantity=Decimal("100"),
                    price=Decimal("10"),
                )
            )

            rows = build_buy_timing_dataset(
                selection_store,
                portfolio_store,
                imported.batch.batch_id,
                trading_sessions=SESSIONS,
                evaluated_at=datetime(2026, 1, 8, 17, tzinfo=UTC),
            )

            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0].purchased_within_window)
            self.assertEqual(rows[0].fill_id, "fill-buy")
            self.assertEqual(
                len(read_buy_timing_dataset(serialize_buy_timing_dataset(rows).encode())),
                1,
            )

    def test_buy_timing_does_not_use_a_fill_after_evaluation_cutoff(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            portfolio_store = ManualPortfolioStore(directory)
            imported = _imported(selection_store)
            candidate_id = imported.candidates[0].candidate_id
            selection_store.write_annotation(
                imported.batch.batch_id,
                SelectionAnnotation(
                    annotation_id="annotation-pending",
                    candidate_id=candidate_id,
                    reviewed_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
                    recorded_at=datetime(2026, 1, 5, 16, 1, tzinfo=UTC),
                    overall_score=3,
                    outlook=SelectionAnnotationOutlook.NEUTRAL,
                    decision=SelectionAnnotationDecision.WATCH,
                ),
            )
            order = _order("order-future", candidate_id, ManualOrderSide.BUY)
            portfolio_store.create_order(order)
            portfolio_store.record_fill(
                ManualFill(
                    fill_id="fill-future",
                    order_id=order.order_id,
                    filled_at=datetime(2026, 1, 8, 10, tzinfo=UTC),
                    quantity=Decimal("100"),
                    price=Decimal("10"),
                )
            )

            rows = build_buy_timing_dataset(
                selection_store,
                portfolio_store,
                imported.batch.batch_id,
                trading_sessions=SESSIONS,
                evaluated_at=datetime(2026, 1, 7, 17, tzinfo=UTC),
            )

            self.assertEqual(rows[0].purchased_within_window, None)
            self.assertIsNone(rows[0].fill_id)

    def test_sell_timing_dataset_exposes_holding_sessions_and_future_sale_labels(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            portfolio_store = ManualPortfolioStore(directory)
            imported = _imported(selection_store)
            candidate_id = imported.candidates[0].candidate_id
            buy_order = _order("order-buy-sell", candidate_id, ManualOrderSide.BUY)
            portfolio_store.create_order(buy_order)
            portfolio_store.record_fill(
                ManualFill(
                    fill_id="fill-buy-sell",
                    order_id=buy_order.order_id,
                    filled_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                    quantity=Decimal("100"),
                    price=Decimal("10"),
                )
            )
            sell_order = _order("order-sell", candidate_id, ManualOrderSide.SELL)
            portfolio_store.create_order(sell_order)
            portfolio_store.record_fill(
                ManualFill(
                    fill_id="fill-sell",
                    order_id=sell_order.order_id,
                    filled_at=datetime(2026, 1, 8, 10, tzinfo=UTC),
                    quantity=Decimal("100"),
                    price=Decimal("11"),
                )
            )

            rows = build_sell_timing_dataset(
                selection_store,
                portfolio_store,
                imported.batch.batch_id,
                trading_sessions=SESSIONS,
                evaluated_at=datetime(2026, 1, 9, 17, tzinfo=UTC),
            )

            self.assertEqual(
                [row.exposure_session for row in rows], [date(2026, 1, 6), date(2026, 1, 7)]
            )
            self.assertFalse(rows[0].sold_within_window)
            self.assertTrue(rows[1].sold_within_window)
            self.assertEqual(rows[1].sell_fill_ids, ("fill-sell",))

    def test_sell_timing_rejects_unlinked_sell_as_ambiguous(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            portfolio_store = ManualPortfolioStore(directory)
            imported = _imported(selection_store)
            candidate_id = imported.candidates[0].candidate_id
            buy_order = _order("order-buy-ambiguous", candidate_id, ManualOrderSide.BUY)
            portfolio_store.create_order(buy_order)
            portfolio_store.record_fill(
                ManualFill(
                    fill_id="fill-buy-ambiguous",
                    order_id=buy_order.order_id,
                    filled_at=datetime(2026, 1, 6, 10, tzinfo=UTC),
                    quantity=Decimal("100"),
                    price=Decimal("10"),
                )
            )
            unlinked = _order("order-sell-ambiguous", None, ManualOrderSide.SELL)
            portfolio_store.create_order(unlinked)
            portfolio_store.record_fill(
                ManualFill(
                    fill_id="fill-sell-ambiguous",
                    order_id=unlinked.order_id,
                    filled_at=datetime(2026, 1, 8, 10, tzinfo=UTC),
                    quantity=Decimal("100"),
                    price=Decimal("11"),
                )
            )

            with self.assertRaisesRegex(ValueError, "ambiguous"):
                build_sell_timing_dataset(
                    selection_store,
                    portfolio_store,
                    imported.batch.batch_id,
                    trading_sessions=SESSIONS,
                    evaluated_at=datetime(2026, 1, 9, 17, tzinfo=UTC),
                )


def _imported(store: SelectionImportStore) -> TdxSelectionImport:
    imported = import_tdx_export(
        CONTENT,
        selection_date=EXPORTED_AT.date(),
        exported_at=EXPORTED_AT,
        imported_at=IMPORTED_AT,
        formula_name="fixture",
    )
    store.write(CONTENT, imported)
    return imported


def _order(order_id: str, candidate_id: str | None, side: ManualOrderSide) -> ManualOrder:
    return ManualOrder(
        order_id=order_id,
        security=Security(
            security_id="security-000001.SZ",
            local_code="000001",
            symbol="000001.SZ",
            exchange=Exchange.SZ,
        ),
        side=side,
        ordered_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
        quantity=Decimal("100"),
        selection_candidate_id=candidate_id,
    )


if __name__ == "__main__":
    unittest.main()
