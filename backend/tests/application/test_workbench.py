import hashlib
import unittest
from datetime import UTC, date, datetime, timedelta
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
from stratgauge.data_sources.base import MarketAdapterResult, MarketDailyRequest
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

    def test_watchlist_observation_performance_uses_current_continuous_period(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            raw_content = b"code,name,market\n000001,Ping,SZ\n"
            imported = import_tdx_export(
                raw_content,
                selection_date=date(2026, 1, 5),
                exported_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                imported_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
                formula_name="趋势选股",
            )
            selection_store.write(raw_content, imported)
            candidate = imported.candidates[0]
            for index, decision in enumerate(
                ("discard", "watch", "prepare_buy", "discard", "watch")
            ):
                reviewed_at = datetime(2026, 1, 5 + index, 10, tzinfo=UTC)
                selection_store.write_annotation(
                    imported.batch.batch_id,
                    _annotation(candidate.candidate_id, decision, reviewed_at=reviewed_at),
                )
            market_store = SnapshotStore(f"{directory}/market")
            anchor_manifest = _write_approved_market_bar(
                market_store,
                symbol=candidate.symbol,
                local_code=candidate.local_code,
                session_date=date(2026, 1, 8),
                security_id=f"security-{candidate.symbol}",
                close=Decimal("20"),
                available_at=datetime(2026, 1, 8, 16, tzinfo=UTC),
            )
            _write_approved_market_bar(
                market_store,
                symbol=candidate.symbol,
                local_code=candidate.local_code,
                session_date=date(2026, 1, 10),
                security_id=f"security-{candidate.symbol}",
                close=Decimal("22"),
                available_at=datetime(2026, 1, 10, 16, tzinfo=UTC),
            )
            service = WorkbenchService(
                selection_store,
                ManualPortfolioStore(directory),
                market_store,
            )

            signal = service.list_watchlist(
                as_of=datetime(2026, 1, 11, 18, tzinfo=UTC),
            )[0]
            observation = signal.observation_performance
            self.assertIsNotNone(observation)
            assert observation is not None
            self.assertEqual(observation.status, MarketEvidenceStatus.AVAILABLE)
            self.assertEqual(observation.started_at, datetime(2026, 1, 9, 10, tzinfo=UTC))
            self.assertEqual(observation.start_snapshot_id, anchor_manifest.snapshot_id)
            self.assertEqual(observation.start_session_date, date(2026, 1, 8))
            self.assertEqual(observation.start_close, Decimal("20"))
            self.assertEqual(observation.change_percent, Decimal("0.1"))

    def test_watchlist_observation_performance_does_not_fill_late_anchor_with_zero(self) -> None:
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            raw_content = b"code,name,market\n000001,Ping,SZ\n"
            imported = import_tdx_export(
                raw_content,
                selection_date=date(2026, 1, 5),
                exported_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
                imported_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
                formula_name="趋势选股",
            )
            selection_store.write(raw_content, imported)
            candidate = imported.candidates[0]
            reviewed_at = datetime(2026, 1, 5, 15, 10, tzinfo=UTC)
            selection_store.write_annotation(
                imported.batch.batch_id,
                _annotation(candidate.candidate_id, "watch", reviewed_at=reviewed_at),
            )
            market_store = SnapshotStore(f"{directory}/market")
            _write_approved_market_bar(
                market_store,
                symbol=candidate.symbol,
                local_code=candidate.local_code,
                session_date=date(2026, 1, 5),
                security_id=f"security-{candidate.symbol}",
                close=Decimal("10"),
                available_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
            )
            service = WorkbenchService(
                selection_store,
                ManualPortfolioStore(directory),
                market_store,
            )

            observation = service.list_watchlist(
                as_of=datetime(2026, 1, 5, 18, tzinfo=UTC),
            )[0].observation_performance
            self.assertIsNotNone(observation)
            assert observation is not None
            self.assertEqual(observation.status, MarketEvidenceStatus.INCOMPLETE)
            self.assertIsNone(observation.start_close)
            self.assertIsNone(observation.change_percent)
            self.assertIn("尚不可用", observation.reason or "")

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
            self.assertEqual(set(load_bars.call_args.kwargs["symbols"]), {"600000.SH"})
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


def _write_approved_market_bar(
    store: SnapshotStore,
    *,
    security_id: str,
    symbol: str,
    local_code: str,
    session_date: date,
    close: Decimal,
    available_at: datetime,
) -> SnapshotManifest:
    event_time = datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        15,
        tzinfo=UTC,
    )
    result = MarketAdapterResult(
        provider="test",
        adapter_version="test-v1",
        request=MarketDailyRequest(
            session_date=session_date,
            universe_snapshot_id="test-universe",
            expected_security_ids=(security_id,),
        ),
        fetched_at=available_at,
        raw_records=(),
        bars=(
            DailyBar(
                security_id=security_id,
                symbol=symbol,
                session_date=session_date,
                provider="test",
                provider_symbol=local_code,
                open=close,
                high=close,
                low=close,
                close=close,
                previous_close=close - Decimal("1"),
                volume=Decimal("100"),
                amount=close * Decimal("100"),
                event_time=event_time,
                available_at=available_at,
                ingested_at=available_at,
                quality_state="approved",
            ),
        ),
    )
    manifest = store.write(result)
    return store.update_status(manifest.snapshot_id, SnapshotStatus.APPROVED)


def _annotation(
    candidate_id: str,
    decision: str,
    *,
    reviewed_at: datetime | None = None,
) -> SelectionAnnotation:
    from stratgauge.domain.selections import (
        SelectionAnnotationDecision,
        SelectionAnnotationOutlook,
    )

    actual_reviewed_at = reviewed_at or datetime(2026, 1, 5, 15, 10, tzinfo=UTC)
    return SelectionAnnotation(
        annotation_id="annotation-"
        + hashlib.sha256(
            f"{candidate_id}-{decision}-{actual_reviewed_at.isoformat()}".encode()
        ).hexdigest()[:20],
        candidate_id=candidate_id,
        reviewed_at=actual_reviewed_at,
        recorded_at=actual_reviewed_at + timedelta(minutes=1),
        overall_score=4,
        outlook=SelectionAnnotationOutlook.BULLISH,
        decision=SelectionAnnotationDecision(decision),
    )


if __name__ == "__main__":
    unittest.main()
