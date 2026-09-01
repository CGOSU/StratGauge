import hashlib
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.backtest.manual_position_tracking_dataset import (
    MANUAL_POSITION_TRACKING_DATASET_SCHEMA_VERSION,
    ManualPositionTrackingEvidenceReport,
    build_manual_position_tracking_export,
    read_manual_position_tracking_dataset,
    read_manual_position_tracking_report,
    serialize_manual_position_tracking_dataset,
    serialize_manual_position_tracking_report,
    write_manual_position_tracking_dataset,
)
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_sources.base import MarketAdapterResult, MarketDailyRequest
from stratgauge.domain.models import DailyBar, Exchange, Security
from stratgauge.domain.portfolio import (
    ManualFill,
    ManualOrder,
    ManualOrderSide,
    ManualPositionTrackingCase,
    ManualPositionTrackingOrigin,
    PortfolioReview,
)

SECURITY = Security(
    security_id="security-000001.SZ",
    local_code="000001",
    symbol="000001.SZ",
    exchange=Exchange.SZ,
    name="Fixture",
)
STARTED_AT = datetime(2026, 1, 10, 12, tzinfo=UTC)
EVALUATED_AT = datetime(2026, 1, 12, 18, tzinfo=UTC)


class ManualPositionTrackingDatasetTests(unittest.TestCase):
    def test_only_post_start_case_facts_and_approved_market_are_exported(self) -> None:
        with TemporaryDirectory() as directory:
            portfolio = ManualPortfolioStore(directory)
            market = SnapshotStore(Path(directory) / "market")
            historical_order = _order("historical-order", datetime(2026, 1, 2, 9, tzinfo=UTC))
            historical_fill = _fill(
                "historical-fill", historical_order.order_id, datetime(2026, 1, 2, 10, tzinfo=UTC)
            )
            portfolio.record_order_and_fill(historical_order, historical_fill)
            portfolio.enroll_position(
                SECURITY,
                tracking_case_id="manual-case-1",
                tracking_started_at=STARTED_AT,
            )
            post_start_order = _order(
                "tracked-order",
                datetime(2026, 1, 11, 9, tzinfo=UTC),
                tracking_case_id="manual-case-1",
            )
            post_start_fill = _fill(
                "tracked-fill", post_start_order.order_id, datetime(2026, 1, 11, 10, tzinfo=UTC)
            )
            portfolio.record_order_and_fill(post_start_order, post_start_fill)
            portfolio.write_review(
                PortfolioReview(
                    review_id="review-1",
                    symbol=SECURITY.symbol,
                    reviewed_at=datetime(2026, 1, 11, 18, tzinfo=UTC),
                    note="继续观察",
                )
            )
            _write_market_snapshot(
                market,
                session_date=date(2026, 1, 11),
                available_at=datetime(2026, 1, 11, 17, tzinfo=UTC),
            )
            _write_market_snapshot(
                market,
                session_date=date(2026, 1, 5),
                available_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
            )

            exported = build_manual_position_tracking_export(
                portfolio,
                market,
                evaluated_at=EVALUATED_AT,
            )

            self.assertEqual(
                {row.source_id for row in exported.rows},
                {
                    "tracked-order",
                    "tracked-fill",
                    "review-1",
                    next(row.source_id for row in exported.rows if row.evidence_type == "market"),
                },
            )
            self.assertTrue(
                all(
                    row.schema_version == MANUAL_POSITION_TRACKING_DATASET_SCHEMA_VERSION
                    for row in exported.rows
                )
            )
            self.assertTrue(all(row.event_at >= STARTED_AT for row in exported.rows))
            self.assertNotIn("historical-order", {row.source_id for row in exported.rows})
            self.assertNotIn("historical-fill", {row.source_id for row in exported.rows})
            self.assertEqual(exported.report.cases[0].qualification_status, "eligible")
            self.assertEqual(exported.report.cases[0].source_order_ids, ("historical-order",))
            self.assertTrue(all(len(row.source_fact_sha256) == 64 for row in exported.rows))
            self.assertEqual(
                exported.report.dataset_sha256,
                hashlib.sha256(
                    serialize_manual_position_tracking_dataset(exported.rows).encode()
                ).hexdigest(),
            )
            reasons = {item.reason for item in exported.report.excluded_evidence}
            self.assertTrue(any("before tracking_started_at" in reason for reason in reasons))

    def test_missing_market_evidence_reports_not_eligible_without_fabricating_rows(self) -> None:
        with TemporaryDirectory() as directory:
            portfolio = ManualPortfolioStore(directory)
            historical_order = _order("historical-order", datetime(2026, 1, 2, 9, tzinfo=UTC))
            portfolio.record_order_and_fill(
                historical_order,
                _fill(
                    "historical-fill",
                    historical_order.order_id,
                    datetime(2026, 1, 2, 10, tzinfo=UTC),
                ),
            )
            portfolio.enroll_position(
                SECURITY,
                tracking_case_id="manual-case-2",
                tracking_started_at=STARTED_AT,
            )
            order = _order(
                "order-1", datetime(2026, 1, 11, 9, tzinfo=UTC), tracking_case_id="manual-case-2"
            )
            portfolio.record_order_and_fill(
                order,
                _fill("fill-1", order.order_id, datetime(2026, 1, 11, 10, tzinfo=UTC)),
            )

            exported = build_manual_position_tracking_export(
                portfolio,
                evaluated_at=EVALUATED_AT,
            )

            self.assertEqual({row.evidence_type for row in exported.rows}, {"order", "fill"})
            self.assertEqual({row.qualification_status for row in exported.rows}, {"not_eligible"})
            self.assertEqual(exported.report.ready, False)
            self.assertEqual(exported.report.cases[0].qualification_status, "not_eligible")
            self.assertTrue(
                any("market" in reason for reason in exported.report.cases[0].qualification_reasons)
            )

    def test_candidate_linked_source_facts_are_excluded_from_manual_case(self) -> None:
        with TemporaryDirectory() as directory:
            portfolio = ManualPortfolioStore(directory)
            portfolio.create_tracking_case(
                ManualPositionTrackingCase(
                    tracking_case_id="manual-case-4",
                    security=SECURITY,
                    tracking_started_at=STARTED_AT,
                    created_at=STARTED_AT,
                    origin=ManualPositionTrackingOrigin.HISTORICAL_ENROLLMENT,
                    source_order_ids=("candidate-order",),
                )
            )
            portfolio.create_order(
                ManualOrder(
                    order_id="candidate-order",
                    security=SECURITY,
                    side=ManualOrderSide.BUY,
                    ordered_at=datetime(2026, 1, 11, 9, tzinfo=UTC),
                    quantity=Decimal("100"),
                    selection_candidate_id="candidate-1",
                )
            )

            exported = build_manual_position_tracking_export(
                portfolio,
                evaluated_at=EVALUATED_AT,
            )

            self.assertEqual(exported.rows, ())
            self.assertEqual(
                {item.source_id for item in exported.report.excluded_evidence},
                {"candidate-order"},
            )
            self.assertEqual(exported.report.cases[0].qualification_status, "not_eligible")

    def test_dataset_and_report_round_trip_and_dataset_writer_is_immutable(self) -> None:
        with TemporaryDirectory() as directory:
            portfolio = ManualPortfolioStore(directory)
            order = _order("order-1", datetime(2026, 1, 11, 9, tzinfo=UTC))
            portfolio.record_order_and_fill(
                order, _fill("fill-1", order.order_id, datetime(2026, 1, 11, 10, tzinfo=UTC))
            )
            portfolio.enroll_position(
                SECURITY, tracking_case_id="manual-case-3", tracking_started_at=STARTED_AT
            )
            exported = build_manual_position_tracking_export(portfolio, evaluated_at=EVALUATED_AT)
            dataset_path = Path(directory) / "dataset.jsonl"
            write_manual_position_tracking_dataset(dataset_path, exported.rows)

            self.assertEqual(
                read_manual_position_tracking_dataset(dataset_path.read_bytes()), exported.rows
            )
            report = ManualPositionTrackingEvidenceReport.model_validate_json(
                serialize_manual_position_tracking_report(exported.report)
            )
            self.assertEqual(
                read_manual_position_tracking_report(
                    serialize_manual_position_tracking_report(report).encode()
                ),
                report,
            )
            with self.assertRaises(FileExistsError):
                write_manual_position_tracking_dataset(dataset_path, exported.rows)


def _order(
    order_id: str, ordered_at: datetime, *, tracking_case_id: str | None = None
) -> ManualOrder:
    return ManualOrder(
        order_id=order_id,
        security=SECURITY,
        side=ManualOrderSide.BUY,
        ordered_at=ordered_at,
        quantity=Decimal("100"),
        limit_price=Decimal("10"),
        tracking_case_id=tracking_case_id,
    )


def _fill(fill_id: str, order_id: str, filled_at: datetime) -> ManualFill:
    return ManualFill(
        fill_id=fill_id,
        order_id=order_id,
        filled_at=filled_at,
        quantity=Decimal("100"),
        price=Decimal("10"),
    )


def _write_market_snapshot(
    store: SnapshotStore,
    *,
    session_date: date,
    available_at: datetime,
) -> None:
    bar = DailyBar(
        security_id=SECURITY.security_id,
        symbol=SECURITY.symbol,
        session_date=session_date,
        provider="fixture",
        provider_symbol=SECURITY.symbol,
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10.5"),
        previous_close=Decimal("10"),
        volume=Decimal("1000"),
        amount=Decimal("10500"),
        event_time=datetime.combine(session_date, datetime.min.time().replace(hour=15), tzinfo=UTC),
        available_at=available_at,
        ingested_at=available_at,
    )
    manifest = store.write(
        MarketAdapterResult(
            provider="fixture",
            adapter_version="fixture-v1",
            request=MarketDailyRequest(
                session_date=session_date,
                universe_snapshot_id="universe-1",
                expected_security_ids=(SECURITY.security_id,),
            ),
            fetched_at=available_at,
            raw_records=(),
            bars=(bar,),
        )
    )
    store.update_status(manifest.snapshot_id, SnapshotStatus.APPROVED)


if __name__ == "__main__":
    unittest.main()
