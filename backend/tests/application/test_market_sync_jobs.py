from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory
from typing import Any

from stratgauge.application.job_runner import JobStatus, LocalJobError, LocalJobStore
from stratgauge.application.market_sync_jobs import (
    MainBoardMarketSyncJobRunner,
    MarketSyncJobRequest,
    enqueue_main_board_market_sync,
)
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.universe import (
    UniverseSnapshotStore,
    build_universe_snapshot,
)
from stratgauge.data_sources.base import DailyBarRequest, RetryableAdapterError
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.domain.models import Exchange, Security

NOW = datetime(2026, 1, 6, 8, tzinfo=UTC)


def _security(security_id: str, local_code: str, exchange: Exchange) -> Security:
    return Security(
        security_id=security_id,
        local_code=local_code,
        symbol=f"{local_code}.{exchange.value}",
        exchange=exchange,
    )


def _daily_row(symbol: str, session_date: date) -> dict[str, str]:
    return {
        "trade_date": session_date.strftime("%Y%m%d"),
        "ts_code": symbol,
        "pre_close": "10.00",
        "open": "10.10",
        "high": "10.50",
        "low": "9.90",
        "close": "10.20",
        "vol": "100",
        "amount": "20",
    }


def _factor_row(symbol: str, session_date: date) -> dict[str, str]:
    return {
        "trade_date": session_date.strftime("%Y%m%d"),
        "ts_code": symbol,
        "adj_factor": "1.5",
    }


class MainBoardMarketSyncJobTests(unittest.TestCase):
    def test_enqueue_is_idempotent_and_successful_job_is_durable(self) -> None:
        with TemporaryDirectory() as directory:
            job_store = LocalJobStore(directory)
            request = MarketSyncJobRequest(
                session_date=date(2026, 1, 5),
                minimum_coverage=Decimal("0.90"),
                lookback_days=1,
            )
            first = enqueue_main_board_market_sync(
                job_store,
                request,
                market_root=f"{directory}/market",
                universe_root=f"{directory}/universe",
                now=NOW,
            )
            duplicate = enqueue_main_board_market_sync(
                job_store,
                request,
                market_root=f"{directory}/market",
                universe_root=f"{directory}/universe",
                now=NOW + timedelta(minutes=1),
            )
            self.assertTrue(first.created)
            self.assertFalse(duplicate.created)
            self.assertEqual(first.job.job_id, duplicate.job.job_id)
            self.assertEqual(len(job_store.list()), 1)
            self.assertEqual(first.job.payload["request"]["session_date"], "2026-01-05")

            calls: list[str] = []

            def sync(
                request: MarketSyncJobRequest,
                *,
                market_root: str,
                universe_root: str,
                adapter: TushareAdapter,
            ) -> dict[str, Any]:
                del market_root, universe_root, adapter
                calls.append(request.session_date.isoformat())
                return {"research_ready": True, "sessions": []}

            runner = MainBoardMarketSyncJobRunner(
                job_store,
                sync_function=sync,
                adapter_factory=lambda _: TushareAdapter(fetcher=lambda _: []),
                clock=lambda: NOW,
            )
            finished = runner.run_pending()
            self.assertEqual(len(finished), 1)
            self.assertEqual(finished[0].status, JobStatus.COMPLETED)
            self.assertEqual(finished[0].attempt_count, 1)
            result = finished[0].result
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["research_ready"], True)
            self.assertEqual(calls, ["2026-01-05"])
            self.assertEqual(runner.run_pending(), ())
            self.assertEqual(len(calls), 1)

    def test_partial_response_is_terminal_and_keeps_result_reference(self) -> None:
        with TemporaryDirectory() as directory:
            job_store = LocalJobStore(directory)
            enqueue_main_board_market_sync(
                job_store,
                MarketSyncJobRequest(session_date=date(2026, 1, 5), lookback_days=1),
                market_root=f"{directory}/market",
                universe_root=f"{directory}/universe",
                max_retries=2,
                now=NOW,
            )

            def partial_sync(
                request: MarketSyncJobRequest,
                *,
                market_root: str,
                universe_root: str,
                adapter: TushareAdapter,
            ) -> dict[str, Any]:
                del request, market_root, universe_root, adapter
                return {
                    "research_ready": False,
                    "sessions": [{"market_status": "rejected"}],
                }

            finished = MainBoardMarketSyncJobRunner(
                job_store,
                sync_function=partial_sync,
                adapter_factory=lambda _: TushareAdapter(fetcher=lambda _: []),
                clock=lambda: NOW,
            ).run_pending()
            self.assertEqual(finished[0].status, JobStatus.PARTIAL)
            result = finished[0].result
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["response"]["sessions"][0]["market_status"], "rejected")
            self.assertEqual(finished[0].attempt_count, 1)

    def test_retryable_failure_recovers_after_runner_restart(self) -> None:
        with TemporaryDirectory() as directory:
            job_store = LocalJobStore(directory)
            request = MarketSyncJobRequest(session_date=date(2026, 1, 5), lookback_days=1)
            queued = enqueue_main_board_market_sync(
                job_store,
                request,
                market_root=f"{directory}/market",
                universe_root=f"{directory}/universe",
                max_retries=2,
                timeout_seconds=5,
                now=NOW,
            )
            calls = 0

            def flaky_sync(
                request: MarketSyncJobRequest,
                *,
                market_root: str,
                universe_root: str,
                adapter: TushareAdapter,
            ) -> dict[str, Any]:
                del request, market_root, universe_root, adapter
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RetryableAdapterError("provider timeout token=must-not-persist")
                return {"research_ready": True}

            current = [NOW]
            first_runner = MainBoardMarketSyncJobRunner(
                job_store,
                sync_function=flaky_sync,
                adapter_factory=lambda _: TushareAdapter(fetcher=lambda _: []),
                clock=lambda: current[0],
            )
            first = first_runner.run_pending()
            self.assertEqual(first[0].status, JobStatus.RETRYING)
            self.assertNotIn("must-not-persist", job_store.load(queued.job.job_id).error or "")

            current[0] = NOW + timedelta(seconds=6)
            abandoned = job_store.start(queued.job.job_id, now=current[0])
            self.assertEqual(abandoned.status, JobStatus.RUNNING)
            current[0] = NOW + timedelta(seconds=12)
            restarted = MainBoardMarketSyncJobRunner(
                job_store,
                sync_function=flaky_sync,
                adapter_factory=lambda _: TushareAdapter(fetcher=lambda _: []),
                clock=lambda: current[0],
            )
            recovered = restarted.run_pending()
            self.assertEqual(recovered[0].status, JobStatus.COMPLETED)
            self.assertEqual(recovered[0].attempt_count, 3)
            self.assertEqual(calls, 2)

    def test_only_queued_jobs_can_be_cancelled(self) -> None:
        with TemporaryDirectory() as directory:
            job_store = LocalJobStore(directory)
            request = MarketSyncJobRequest(session_date=date(2026, 1, 5), lookback_days=1)
            queued = enqueue_main_board_market_sync(
                job_store,
                request,
                market_root=f"{directory}/market",
                universe_root=f"{directory}/universe",
                now=NOW,
            )
            cancelled = job_store.cancel(queued.job.job_id, now=NOW)
            self.assertEqual(cancelled.status, JobStatus.CANCELLED)
            self.assertEqual(
                MainBoardMarketSyncJobRunner(job_store).run_pending(),
                (),
            )
            with self.assertRaises(LocalJobError):
                job_store.cancel(queued.job.job_id, now=NOW)

    def test_runner_reuses_existing_sync_service_with_offline_fake_adapter(self) -> None:
        securities = (
            _security("security-1", "000001", Exchange.SZ),
            _security("security-2", "600000", Exchange.SH),
        )
        adapter = TushareAdapter(
            fetcher=lambda request: [_daily_row(request.security.symbol, request.start_date)],
            market_fetcher=lambda request: [
                _daily_row(security.symbol, request.session_date) for security in securities
            ],
            adjustment_factor_fetcher=lambda request: [
                _factor_row(security.symbol, request.session_date) for security in securities
            ],
            clock=lambda: NOW,
        )
        with TemporaryDirectory() as directory:
            market_root = f"{directory}/market"
            universe_root = f"{directory}/universe"
            market_store = SnapshotStore(market_root)
            seed = market_store.write(
                adapter.fetch_daily_bars(
                    DailyBarRequest(
                        security=securities[0],
                        start_date=date(2026, 1, 5),
                        end_date=date(2026, 1, 5),
                    )
                )
            )
            market_store.update_status(seed.snapshot_id, SnapshotStatus.APPROVED)
            UniverseSnapshotStore(universe_root).write(
                build_universe_snapshot(
                    as_of=datetime(2026, 1, 4, 17, tzinfo=UTC),
                    source="fixture-main-board",
                    source_snapshot_ids=(seed.snapshot_id,),
                    securities=securities,
                ),
                market_store,
            )
            job_store = LocalJobStore(f"{directory}/jobs")
            enqueue_main_board_market_sync(
                job_store,
                MarketSyncJobRequest(
                    session_date=date(2026, 1, 5),
                    minimum_coverage=Decimal("0.90"),
                    lookback_days=1,
                ),
                market_root=market_root,
                universe_root=universe_root,
                max_retries=0,
                now=NOW,
            )

            finished = MainBoardMarketSyncJobRunner(
                job_store,
                adapter_factory=lambda _: adapter,
                clock=lambda: NOW,
            ).run_pending()

            self.assertEqual(finished[0].status, JobStatus.COMPLETED)
            result = finished[0].result
            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result["research_ready"])
            response = result["response"]
            self.assertEqual(response["sessions"][0]["market_status"], "approved")
            self.assertEqual(response["sessions"][0]["adjustment_factor_status"], "approved")


if __name__ == "__main__":
    unittest.main()
