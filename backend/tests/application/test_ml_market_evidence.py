import unittest
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from stratgauge.application.ml_market_evidence import (
    SelectionMLMarketEvidenceReport,
    SelectionMLMarketEvidenceRequest,
    prepare_selection_ml_market_evidence,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.data_pipeline.universe import UniverseSnapshotStore, build_universe_snapshot
from stratgauge.data_sources.base import (
    AdapterError,
    AdjustmentFactorRequest,
    DailyBarRequest,
    MarketDailyRequest,
)
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.data_sources.tushare_reference import (
    TushareReferenceClient,
    TushareReferenceStore,
)
from stratgauge.domain.models import Exchange, Security
from stratgauge.domain.selections import TdxSelectionImport

SELECTION_DATE = date(2026, 1, 12)
NOW = datetime(2026, 2, 1, 12, tzinfo=UTC)


class SelectionMLMarketEvidenceTests(unittest.TestCase):
    def test_prepares_all_evidence_and_reuses_approved_snapshots(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            request = fixture.request()

            first = fixture.run(request)

            self.assertTrue(first.ready)
            self.assertEqual(first.calendar.status, "completed")
            self.assertEqual(len(first.required_history_sessions), 2)
            self.assertEqual(len(first.required_maturity_sessions), 3)
            self.assertEqual(
                len(first.market_daily.results),
                len(first.required_sessions),
            )
            self.assertEqual(first.adjustment_factors.status, "completed")
            self.assertEqual(first.benchmark_index_daily.status, "completed")
            self.assertEqual(first.benchmark_index_daily.request_kind, "index_daily")
            self.assertEqual(fixture.market_calls, len(first.required_sessions))
            self.assertEqual(fixture.factor_calls, len(first.required_sessions))
            self.assertEqual(fixture.index_calls, 1)
            self.assertFalse(first.point_in_time.feature_visibility_guaranteed)

            second = fixture.run(request)

            self.assertTrue(second.ready)
            self.assertTrue(all(item.skipped for item in second.market_daily.results))
            assert second.adjustment_factors.report is not None
            self.assertTrue(all(item.skipped for item in second.adjustment_factors.report.results))
            self.assertTrue(second.benchmark_index_daily.skipped)
            self.assertEqual(fixture.market_calls, len(first.required_sessions))
            self.assertEqual(fixture.factor_calls, len(first.required_sessions))
            self.assertEqual(fixture.index_calls, 1)

    def test_universe_timepoint_blocks_market_stages_before_provider_fetch(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), universe_as_of=datetime(2026, 1, 9, 17, tzinfo=UTC))

            report = fixture.run(fixture.request())

            self.assertFalse(report.ready)
            self.assertTrue(report.universe_invalid_sessions)
            self.assertEqual(report.market_daily.status, "blocked")
            self.assertEqual(report.adjustment_factors.status, "blocked")
            self.assertEqual(report.benchmark_index_daily.status, "blocked")
            self.assertTrue(any("universe snapshot" in item for item in report.blockers))
            self.assertEqual(fixture.market_calls, 0)
            self.assertEqual(fixture.factor_calls, 0)
            self.assertEqual(fixture.index_calls, 0)

    def test_quality_rejection_and_provider_failure_are_preserved_in_stage_reports(self) -> None:
        for failure_kind in ("quality", "provider"):
            with self.subTest(failure_kind=failure_kind), TemporaryDirectory() as directory:
                fixture = _Fixture(Path(directory), market_failure_kind=failure_kind)

                report = fixture.run(fixture.request())

                self.assertFalse(report.ready)
                self.assertEqual(report.market_daily.status, "partial")
                bad_session = fixture.bad_session
                if failure_kind == "quality":
                    self.assertEqual(len(report.market_daily.rejected_snapshot_ids), 1)
                    assert report.adjustment_factors.report is not None
                    self.assertFalse(
                        report.adjustment_factors.report.results[
                            report.required_sessions.index(bad_session)
                        ].snapshot_id
                    )
                else:
                    self.assertEqual(len(report.market_daily.failures), 1)
                    self.assertEqual(report.market_daily.failures[0].session_date, bad_session)
                self.assertIn(bad_session, report.adjustment_factors.missing_daily_sessions)
                self.assertTrue(report.benchmark_index_daily.status == "completed")

    def test_calendar_provider_failure_blocks_all_following_stages(self) -> None:
        with TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), calendar_failure=True)

            report = fixture.run(fixture.request())

            self.assertFalse(report.ready)
            self.assertEqual(report.calendar.status, "failed")
            self.assertEqual(report.market_daily.status, "not_run")
            self.assertEqual(report.adjustment_factors.status, "not_run")
            self.assertEqual(report.benchmark_index_daily.status, "not_run")
            self.assertEqual(fixture.market_calls, 0)
            self.assertTrue(any("calendar preparation failed" in item for item in report.blockers))


class _Fixture:
    def __init__(
        self,
        root: Path,
        *,
        universe_as_of: datetime = datetime(2025, 12, 1, tzinfo=UTC),
        market_failure_kind: str | None = None,
        calendar_failure: bool = False,
    ) -> None:
        self.selection_root = root / "selection"
        self.universe_root = root / "universe"
        self.market_root = root / "market"
        self.reference_root = root / "references"
        self.calendar_path = root / "calendars" / "trade-cal-sse.txt"
        self.selection_store = SelectionImportStore(self.selection_root)
        self.market_store = SnapshotStore(self.market_root)
        self.universe_store = UniverseSnapshotStore(self.universe_root)
        self.imported = _write_selection(self.selection_store)
        security = Security(
            security_id="security-000001.SZ",
            local_code="000001",
            symbol="000001.SZ",
            exchange=Exchange.SZ,
        )
        universe = build_universe_snapshot(
            as_of=universe_as_of,
            source="fixture",
            source_snapshot_ids=("source-fixture",),
            securities=(security,),
        )
        self.universe_store.write(
            universe,
            SimpleNamespace(
                load_manifest=lambda _: SimpleNamespace(status=SnapshotStatus.APPROVED)
            ),
        )
        self.market_failure_kind = market_failure_kind
        self.bad_session = date(2026, 1, 13)
        self.calendar_failure = calendar_failure
        self.market_calls = 0
        self.factor_calls = 0
        self.index_calls = 0
        self.adapter = TushareAdapter(
            market_fetcher=self._fetch_market,
            adjustment_factor_fetcher=self._fetch_factors,
            fetcher=self._fetch_index,
            clock=lambda: NOW,
        )
        self.calendar_client = TushareReferenceClient(query=self._query_calendar)
        self.calendar_references = TushareReferenceStore(self.reference_root)

    def request(self) -> SelectionMLMarketEvidenceRequest:
        return SelectionMLMarketEvidenceRequest(
            batch_ids=(self.imported.batch.batch_id,),
            universe_snapshot_id=self.universe_store.load(
                next(
                    path.stem
                    for path in (self.universe_root / "universe" / "snapshots").glob("*.json")
                )
            ).universe_snapshot_id,
            lookback_sessions=2,
            maturity_horizon_sessions=3,
            minimum_security_coverage=Decimal("0.90"),
            now=NOW,
        )

    def run(self, request: SelectionMLMarketEvidenceRequest) -> SelectionMLMarketEvidenceReport:
        return prepare_selection_ml_market_evidence(
            self.selection_store,
            self.universe_store,
            self.market_store,
            request=request,
            calendar_path=self.calendar_path,
            calendar_reference_root=self.reference_root,
            calendar_client=self.calendar_client,
            calendar_reference_store=self.calendar_references,
            market_adapter=self.adapter,
            adjustment_factor_adapter=self.adapter,
            benchmark_adapter=self.adapter,
        )

    def _query_calendar(
        self,
        endpoint: str,
        params: Mapping[str, str],
    ) -> list[dict[str, str]]:
        if self.calendar_failure:
            raise AdapterError("fixture calendar unavailable")
        if endpoint != "trade_cal":
            raise AssertionError(f"unexpected calendar endpoint: {endpoint}")
        start = date.fromisoformat(params["start_date"])
        end = date.fromisoformat(params["end_date"])
        return [
            {"cal_date": current.strftime("%Y%m%d"), "is_open": "1"}
            for current in _sessions(start, end)
        ]

    def _fetch_market(self, request: MarketDailyRequest) -> list[dict[str, str]]:
        session_date = request.session_date
        self.market_calls += 1
        if session_date == self.bad_session and self.market_failure_kind == "provider":
            raise AdapterError("fixture market provider failed")
        if session_date == self.bad_session and self.market_failure_kind == "quality":
            return []
        return [_daily_row(session_date, "000001.SZ")]

    def _fetch_factors(self, request: AdjustmentFactorRequest) -> list[dict[str, str]]:
        session_date = request.session_date
        self.factor_calls += 1
        return [
            {
                "trade_date": session_date.strftime("%Y%m%d"),
                "ts_code": "000001.SZ",
                "adj_factor": "1",
            }
        ]

    def _fetch_index(self, request: DailyBarRequest) -> list[dict[str, str]]:
        self.index_calls += 1
        return [
            _daily_row(session, "000300.SH")
            for session in _sessions(request.start_date, request.end_date)
        ]


def _write_selection(store: SelectionImportStore) -> TdxSelectionImport:
    content = b"code,name,market\n000001,Fixture,SZ\n"
    exported_at = datetime(2026, 1, 12, 15, 5, tzinfo=UTC)
    imported = import_tdx_export(
        content,
        selection_date=SELECTION_DATE,
        exported_at=exported_at,
        imported_at=exported_at + timedelta(minutes=5),
        formula_name="fixture",
    )
    store.write(content, imported)
    return imported


def _sessions(start: date, end: date) -> tuple[date, ...]:
    return tuple(
        current
        for offset in range((end - start).days + 1)
        if (current := start + timedelta(days=offset)).weekday() < 5
    )


def _daily_row(session: date, symbol: str) -> dict[str, str]:
    return {
        "trade_date": session.strftime("%Y%m%d"),
        "ts_code": symbol,
        "pre_close": "10.00",
        "open": "10.10",
        "high": "10.50",
        "low": "9.90",
        "close": "10.20",
        "vol": "100",
        "amount": "20",
    }


if __name__ == "__main__":
    unittest.main()
