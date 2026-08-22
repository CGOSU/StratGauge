import hashlib
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stratgauge.application.workbench import (
    MarketEvidenceStatus,
    WorkbenchService,
)
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotManifest, SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.domain.models import DailyBar
from stratgauge.domain.selections import (
    SelectionAnnotation,
    SelectionCandidateReviewState,
    SelectionPerformance,
    SelectionPerformancePoint,
    SelectionPerformanceStatus,
)


class WorkbenchApplicationTests(unittest.TestCase):
    def test_read_model_keeps_unreviewed_and_filters_watchlist(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            raw_content = b"code,name,market\n000001,Ping,SZ\n600000,PuFa,SH\n"
            imported = import_tdx_export(
                raw_content,
                selection_date=datetime(2026, 1, 5, tzinfo=UTC).date(),
                exported_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                imported_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
                formula_name="趋势选股",
            )
            selection_store.write(raw_content, imported)
            first, second = imported.candidates
            client = WorkbenchService(
                selection_store,
                ManualPortfolioStore(directory),
                SnapshotStore(f"{directory}/market"),
            )
            selection_store.write_annotation(
                imported.batch.batch_id,
                _annotation(first.candidate_id, "watch"),
            )
            signals = client.list_daily_signals(
                selection_date=imported.batch.selection_date,
                as_of=datetime(2026, 1, 5, 18, tzinfo=UTC),
            )
            self.assertEqual(len(signals), 2)
            signals_by_candidate = {signal.candidate.candidate_id: signal for signal in signals}
            self.assertEqual(
                signals_by_candidate[second.candidate_id].review_state,
                SelectionCandidateReviewState.UNREVIEWED,
            )
            self.assertEqual(
                signals_by_candidate[first.candidate_id].market.status,
                MarketEvidenceStatus.UNAVAILABLE,
            )
            self.assertEqual(
                client.list_watchlist(as_of=datetime(2026, 1, 5, 18, tzinfo=UTC))[
                    0
                ].candidate.candidate_id,
                first.candidate_id,
            )
            self.assertEqual(
                client.summary(as_of=datetime(2026, 1, 5, 18, tzinfo=UTC)).unreviewed_count,
                1,
            )
            self.assertEqual(second.symbol, "600000.SH")

    def test_summary_avoids_signal_and_market_hydration(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            raw_content = b"code,name,market\n000001,Ping,SZ\n600000,PuFa,SH\n"
            imported = import_tdx_export(
                raw_content,
                selection_date=date(2026, 1, 5),
                exported_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                imported_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
                formula_name="趋势选股",
            )
            selection_store.write(raw_content, imported)
            service = WorkbenchService(
                selection_store,
                ManualPortfolioStore(directory),
                SnapshotStore(f"{directory}/market"),
            )

            with (
                patch.object(service, "_build_signal", side_effect=AssertionError),
                patch.object(service, "_market_evidence_for_symbols", side_effect=AssertionError),
            ):
                summary = service.summary(as_of=datetime(2026, 1, 5, 18, tzinfo=UTC))

            self.assertEqual(summary.signal_count, 2)
            self.assertEqual(summary.unreviewed_count, 2)
            self.assertEqual(summary.excluded_count, 0)

    def test_paged_signals_hydrate_only_page_and_load_each_snapshot_once(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            raw_content = b"code,name,market\n000001,Ping,SZ\n600000,PuFa,SH\n"
            imported = import_tdx_export(
                raw_content,
                selection_date=date(2026, 1, 5),
                exported_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                imported_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
                formula_name="趋势选股",
            )
            selection_store.write(raw_content, imported)
            market_store = SnapshotStore(f"{directory}/market")
            service = WorkbenchService(
                selection_store,
                ManualPortfolioStore(directory),
                market_store,
            )
            manifest = SnapshotManifest(
                snapshot_id="snapshot-market-1",
                provider="test",
                adapter_version="test-1",
                start_date=date(2026, 1, 5),
                end_date=date(2026, 1, 5),
                row_count=1,
                content_sha256="a" * 64,
                raw_path="raw/snapshot-market-1.json",
                normalized_path="normalized/snapshot-market-1.parquet",
                status=SnapshotStatus.APPROVED,
                created_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
                request_kind="market_daily",
            )
            bar = DailyBar(
                security_id="security-600000.SH",
                symbol="600000.SH",
                session_date=date(2026, 1, 5),
                provider="test",
                provider_symbol="600000",
                open=Decimal("10"),
                high=Decimal("10.5"),
                low=Decimal("9.5"),
                close=Decimal("10.2"),
                previous_close=Decimal("10"),
                volume=Decimal("100"),
                amount=Decimal("1020"),
                event_time=datetime(2026, 1, 5, 15, tzinfo=UTC),
                available_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
                ingested_at=datetime(2026, 1, 5, 18, tzinfo=UTC),
            )

            with (
                patch.object(market_store, "list_manifests", return_value=(manifest,)),
                patch.object(market_store, "load_bars", return_value=(bar,)) as load_bars,
                patch.object(
                    selection_store,
                    "list_labels_for_candidates",
                    wraps=selection_store.list_labels_for_candidates,
                ) as list_labels,
                patch.object(
                    selection_store,
                    "list_outcomes_for_candidates",
                    wraps=selection_store.list_outcomes_for_candidates,
                ) as list_outcomes,
                patch.object(
                    selection_store,
                    "list_performances_for_candidates",
                    wraps=selection_store.list_performances_for_candidates,
                ) as list_performances,
            ):
                items, total = service.list_daily_signals_page(
                    page=1,
                    page_size=1,
                    as_of=datetime(2026, 1, 5, 18, tzinfo=UTC),
                )

            self.assertEqual(total, 2)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].candidate.symbol, "600000.SH")
            self.assertEqual(items[0].market.status, MarketEvidenceStatus.AVAILABLE)
            self.assertEqual(load_bars.call_count, 1)
            page_candidate_ids = {items[0].candidate.candidate_id}
            self.assertEqual(set(list_labels.call_args.args[0]), page_candidate_ids)
            self.assertEqual(set(list_outcomes.call_args.args[0]), page_candidate_ids)
            self.assertEqual(set(list_performances.call_args.args[0]), page_candidate_ids)

    def test_market_evidence_reads_only_approved_market_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            raw_content = b"code,name,market\n000001,Ping,SZ\n"
            imported = import_tdx_export(
                raw_content,
                selection_date=datetime(2026, 1, 5, tzinfo=UTC).date(),
                exported_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                imported_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
                formula_name="趋势选股",
            )
            selection_store.write(raw_content, imported)
            service = WorkbenchService(
                selection_store,
                ManualPortfolioStore(directory),
                SnapshotStore(f"{directory}/market"),
            )
            signal = service.list_daily_signals(as_of=datetime(2026, 1, 5, 18, tzinfo=UTC))[0]
            self.assertEqual(signal.market.status, MarketEvidenceStatus.UNAVAILABLE)

    def test_strategy_and_analytics_use_resolved_performance_only(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            raw_content = b"code,name,market\n000001,Ping,SZ\n"
            imported = import_tdx_export(
                raw_content,
                selection_date=datetime(2026, 1, 5, tzinfo=UTC).date(),
                exported_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                imported_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
                formula_name="趋势选股",
                formula_version="v1",
            )
            selection_store.write(raw_content, imported)
            selection_store.write_performance(
                SelectionPerformance(
                    candidate_id=imported.candidates[0].candidate_id,
                    batch_id=imported.batch.batch_id,
                    selection_date=imported.batch.selection_date,
                    status=SelectionPerformanceStatus.RESOLVED,
                    evaluated_at=datetime(2026, 1, 20, tzinfo=UTC),
                    performance_policy_id="performance-v1",
                    anchor_session=datetime(2026, 1, 6, tzinfo=UTC).date(),
                    anchor_price=Decimal("10"),
                    points=(
                        SelectionPerformancePoint(
                            horizon_sessions=1,
                            session_date=datetime(2026, 1, 7, tzinfo=UTC).date(),
                            close_price=Decimal("10.5"),
                            return_from_anchor=Decimal("0.05"),
                            maximum_rise=Decimal("0.06"),
                            maximum_drawdown=Decimal("-0.01"),
                        ),
                    ),
                    reason="完成观察窗口",
                )
            )
            service = WorkbenchService(
                selection_store,
                ManualPortfolioStore(directory),
                SnapshotStore(f"{directory}/market"),
            )
            strategies = service.list_strategies(as_of=datetime(2026, 1, 21, tzinfo=UTC))
            self.assertEqual(len(strategies), 1)
            all_signals = strategies[0].cohorts[0]
            self.assertEqual(all_signals.sample_count, 1)
            self.assertEqual(all_signals.resolved_count, 1)
            self.assertEqual(all_signals.horizons[0].average_return, Decimal("0.05"))
            analytics = service.analytics(as_of=datetime(2026, 1, 21, tzinfo=UTC))
            self.assertEqual(analytics.cohorts[2].sample_count, 0)
            self.assertIsNone(analytics.cohorts[2].horizons[0].average_return)

    def test_stock_detail_contains_source_preserving_timeline(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            raw_content = b"code,name,market\n000001,Ping,SZ\n"
            imported = import_tdx_export(
                raw_content,
                selection_date=datetime(2026, 1, 5, tzinfo=UTC).date(),
                exported_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                imported_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
                formula_name="趋势选股",
            )
            selection_store.write(raw_content, imported)
            candidate_id = imported.candidates[0].candidate_id
            selection_store.write_annotation(
                imported.batch.batch_id,
                _annotation(candidate_id, "prepare_buy"),
            )
            service = WorkbenchService(
                selection_store,
                ManualPortfolioStore(directory),
                SnapshotStore(f"{directory}/market"),
            )
            detail = service.stock_detail(
                "000001.SZ",
                as_of=datetime(2026, 1, 5, 18, tzinfo=UTC),
            )
            self.assertEqual(detail.symbol, "000001.SZ")
            self.assertEqual(
                {event.event_type for event in detail.timeline},
                {"signal", "annotation"},
            )
            self.assertTrue(all(event.source_id for event in detail.timeline))


def _annotation(candidate_id: str, decision: str) -> SelectionAnnotation:
    from stratgauge.domain.selections import (
        SelectionAnnotationDecision,
        SelectionAnnotationOutlook,
    )

    return SelectionAnnotation(
        annotation_id="annotation-"
        + hashlib.sha256(f"{candidate_id}-{decision}".encode()).hexdigest()[:20],
        candidate_id=candidate_id,
        reviewed_at=datetime(2026, 1, 5, 15, 10, tzinfo=UTC),
        recorded_at=datetime(2026, 1, 5, 15, 11, tzinfo=UTC),
        overall_score=4,
        outlook=SelectionAnnotationOutlook.BULLISH,
        decision=SelectionAnnotationDecision(decision),
    )


if __name__ == "__main__":
    unittest.main()
