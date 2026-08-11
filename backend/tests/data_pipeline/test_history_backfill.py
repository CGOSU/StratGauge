import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.data_pipeline.data_access import DataAccessDecision
from stratgauge.data_pipeline.history_backfill import (
    HistoryBackfillRequest,
    HistoryBackfillStatus,
    HistoryTargetRole,
    backfill_selection_history,
    build_history_backfill_targets,
    read_trading_sessions,
    write_history_backfill_report,
)
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.base import DailyBarRequest
from stratgauge.domain.models import Exchange, Security
from stratgauge.domain.selections import TdxSelectionImport

SELECTION_DATE = date(2026, 1, 6)
FETCHED_AT = datetime(2026, 1, 7, 8, tzinfo=UTC)
EXPORTED_AT = datetime(2026, 1, 6, 15, 5, tzinfo=UTC)
IMPORTED_AT = datetime(2026, 1, 6, 15, 10, tzinfo=UTC)
CONTENT = b"code,name,exchange\n000001,Sample,SZ\n"


class HistoryBackfillTests(unittest.TestCase):
    def test_backfill_uses_only_sessions_before_selection_date(self) -> None:
        selection = _selection()
        requests: list[tuple[date, date]] = []

        def fetch(request: DailyBarRequest) -> list[dict[str, str]]:
            requests.append((request.start_date, request.end_date))
            return [_bar("2026-01-02"), _bar("2026-01-05")]

        with TemporaryDirectory() as directory:
            market_store = SnapshotStore(directory)
            report = backfill_selection_history(
                HistoryBackfillRequest(
                    selection_date=SELECTION_DATE,
                    lookback_sessions=2,
                    expected_sessions=(date(2026, 1, 2), date(2026, 1, 5)),
                ),
                build_history_backfill_targets(selection),
                adapter=AKShareAdapter(fetcher=fetch, clock=lambda: FETCHED_AT),
                market_store=market_store,
            )

            self.assertTrue(report.ready)
            self.assertEqual(requests, [(date(2026, 1, 2), date(2026, 1, 5))])
            target = report.target_reports[0]
            self.assertEqual(target.status, HistoryBackfillStatus.READY)
            self.assertEqual(target.observed_session_count, 2)
            self.assertEqual(len(report.approved_snapshot_ids), 1)
            bars = market_store.load_bars(report.approved_snapshot_ids[0])
            self.assertEqual(len(bars), 2)
            self.assertTrue(all(bar.symbol == "000001.SZ" for bar in bars))

    def test_missing_session_is_reported_and_not_marked_ready(self) -> None:
        with TemporaryDirectory() as directory:
            report = backfill_selection_history(
                _request(),
                build_history_backfill_targets(_selection()),
                adapter=AKShareAdapter(
                    fetcher=lambda _: [_bar("2026-01-02")],
                    clock=lambda: FETCHED_AT,
                ),
                market_store=SnapshotStore(directory),
            )

        target = report.target_reports[0]
        self.assertFalse(report.ready)
        self.assertEqual(target.status, HistoryBackfillStatus.QUALITY_FAILED)
        self.assertEqual(target.missing_sessions, (date(2026, 1, 5),))
        self.assertEqual(
            report.missing_sessions["candidate:000001.SZ"],
            (date(2026, 1, 5),),
        )
        self.assertEqual(report.insufficient_history_security_ids, (target.security_id,))
        self.assertEqual(report.approved_snapshot_ids, ())
        self.assertEqual(len(report.rejected_snapshot_ids), 1)
        self.assertIn(target.security_id, report.quality_failure_security_ids)

    def test_offline_mode_reuses_exact_snapshot_without_calling_provider(self) -> None:
        calls = 0

        def fetch(request: DailyBarRequest) -> list[dict[str, str]]:
            nonlocal calls
            calls += 1
            return [_bar("2026-01-02"), _bar("2026-01-05")]

        def unexpected_fetch(_: DailyBarRequest) -> list[dict[str, str]]:
            raise AssertionError("offline backfill must not call provider")

        with TemporaryDirectory() as directory:
            market_store = SnapshotStore(directory)
            backfill_selection_history(
                _request(),
                build_history_backfill_targets(_selection()),
                adapter=AKShareAdapter(fetcher=fetch, clock=lambda: FETCHED_AT),
                market_store=market_store,
            )
            report = backfill_selection_history(
                _request(),
                build_history_backfill_targets(_selection()),
                adapter=AKShareAdapter(
                    fetcher=unexpected_fetch,
                    clock=lambda: FETCHED_AT,
                ),
                market_store=market_store,
                remote_allowed=False,
            )

        self.assertEqual(calls, 1)
        self.assertTrue(report.ready)
        self.assertFalse(report.remote_allowed)
        self.assertEqual(
            report.target_reports[0].access_plan.decision,
            DataAccessDecision.CACHE_HIT,
        )


    def test_empty_provider_response_is_reported_as_missing_security(self) -> None:
        with TemporaryDirectory() as directory:
            report = backfill_selection_history(
                _request(),
                build_history_backfill_targets(_selection()),
                adapter=AKShareAdapter(fetcher=lambda _: (), clock=lambda: FETCHED_AT),
                market_store=SnapshotStore(directory),
            )

        target = report.target_reports[0]
        self.assertEqual(target.status, HistoryBackfillStatus.MISSING_SECURITY)
        self.assertEqual(report.missing_security_ids, (target.security_id,))
        self.assertEqual(
            report.missing_sessions["candidate:000001.SZ"],
            _request().required_sessions,
        )
        self.assertFalse(report.approved_snapshot_ids)

    def test_benchmark_and_industry_targets_are_explicit(self) -> None:
        selection = _selection()
        targets = build_history_backfill_targets(
            selection,
            benchmark_securities=(
                Security(
                    security_id="history-000300.SH",
                    local_code="000300",
                    symbol="000300.SH",
                    exchange=Exchange.SH,
                ),
            ),
            industry_securities=(
                Security(
                    security_id="history-399006.SZ",
                    local_code="399006",
                    symbol="399006.SZ",
                    exchange=Exchange.SZ,
                ),
            ),
        )
        self.assertEqual(
            tuple(target.role for target in targets),
            (
                HistoryTargetRole.CANDIDATE,
                HistoryTargetRole.BENCHMARK,
                HistoryTargetRole.INDUSTRY,
            ),
        )

    def test_trading_session_file_is_explicit_and_non_overwriting_report(self) -> None:
        with TemporaryDirectory() as directory:
            sessions_path = Path(directory) / "sessions.txt"
            sessions_path.write_text(
                "# approved calendar\n2026-01-02\n2026-01-05\n",
                encoding="utf-8",
            )
            sessions = read_trading_sessions(sessions_path)
            self.assertEqual(sessions, (date(2026, 1, 2), date(2026, 1, 5)))

            report = backfill_selection_history(
                _request(),
                build_history_backfill_targets(_selection()),
                adapter=AKShareAdapter(
                    fetcher=lambda _: [_bar("2026-01-02"), _bar("2026-01-05")],
                    clock=lambda: FETCHED_AT,
                ),
                market_store=SnapshotStore(directory),
            )
            output = Path(directory) / "report.json"
            write_history_backfill_report(output, report)
            with self.assertRaises(FileExistsError):
                write_history_backfill_report(output, report)

    def test_request_rejects_selection_date_as_a_feature_session(self) -> None:
        with self.assertRaises(ValueError):
            HistoryBackfillRequest(
                selection_date=SELECTION_DATE,
                lookback_sessions=1,
                expected_sessions=(SELECTION_DATE,),
            )


def _selection() -> TdxSelectionImport:
    return import_tdx_export(
        CONTENT,
        selection_date=SELECTION_DATE,
        exported_at=EXPORTED_AT,
        imported_at=IMPORTED_AT,
        formula_name="公式 A",
    )


def _request() -> HistoryBackfillRequest:
    return HistoryBackfillRequest(
        selection_date=SELECTION_DATE,
        lookback_sessions=2,
        expected_sessions=(date(2026, 1, 2), date(2026, 1, 5)),
    )


def _bar(session: str) -> dict[str, str]:
    return {
        "日期": session,
        "开盘": "10.00",
        "最高": "10.50",
        "最低": "9.80",
        "收盘": "10.20",
        "成交量": "1000",
        "成交额": "10200",
    }


if __name__ == "__main__":
    unittest.main()
