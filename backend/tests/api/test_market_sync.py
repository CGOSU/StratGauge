import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from stratgauge.api.app import create_app
from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.universe import UniverseSnapshotStore, build_universe_snapshot
from stratgauge.data_sources.base import DailyBarRequest
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.domain.models import Exchange, Security

FETCHED_AT = datetime(2026, 1, 6, 8, tzinfo=UTC)


def security(security_id: str, local_code: str, exchange: Exchange) -> Security:
    return Security(
        security_id=security_id,
        local_code=local_code,
        symbol=f"{local_code}.{exchange.value}",
        exchange=exchange,
    )


def daily_row(symbol: str, session_date: date = date(2026, 1, 5)) -> dict[str, str]:
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


def factor_row(symbol: str, session_date: date = date(2026, 1, 5)) -> dict[str, str]:
    return {
        "trade_date": session_date.strftime("%Y%m%d"),
        "ts_code": symbol,
        "adj_factor": "1.5",
    }


class MainBoardMarketSyncApiTests(unittest.TestCase):
    def test_daily_quality_rejection_returns_actionable_diagnostics(self) -> None:
        securities = (
            security("security-1", "000001", Exchange.SZ),
            security("security-2", "600000", Exchange.SH),
        )
        adapter = TushareAdapter(
            fetcher=lambda request: [daily_row(request.security.symbol, request.start_date)],
            market_fetcher=lambda _: [daily_row("000001.SZ")],
            adjustment_factor_fetcher=lambda _: [],
            clock=lambda: FETCHED_AT,
        )
        with TemporaryDirectory() as directory:
            client = self._client(directory, adapter, securities)
            response = client.post(
                "/v1/market-sync/main-board",
                json={
                    "session_date": "2026-01-05",
                    "minimum_coverage": "0.90",
                    "lookback_days": 1,
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            session = payload["sessions"][0]
            diagnostic = session["market_diagnostic"]
            self.assertEqual(session["market_status"], "rejected")
            self.assertEqual(diagnostic["phase"], "market_daily")
            self.assertEqual(diagnostic["phase_label"], "日线行情")
            self.assertEqual(diagnostic["status"], "rejected")
            self.assertEqual(diagnostic["expected_securities"], 2)
            self.assertEqual(diagnostic["actual_securities"], 1)
            self.assertEqual(Decimal(str(diagnostic["coverage"])), Decimal("0.5"))
            self.assertTrue(diagnostic["quality_run_id"].startswith("quality-"))
            issue = next(
                item for item in diagnostic["issues"] if item["code"] == "LOW_SECURITY_COVERAGE"
            )
            self.assertEqual(issue["label"], "证券覆盖率不足")
            self.assertIn("不会进入研究", diagnostic["summary"])
            self.assertFalse(payload["research_ready"])

    def test_factor_quality_rejection_returns_factor_diagnostics(self) -> None:
        securities = (
            security("security-1", "000001", Exchange.SZ),
            security("security-2", "600000", Exchange.SH),
        )
        adapter = TushareAdapter(
            fetcher=lambda request: [daily_row(request.security.symbol, request.start_date)],
            market_fetcher=lambda _: [daily_row("000001.SZ"), daily_row("600000.SH")],
            adjustment_factor_fetcher=lambda _: [factor_row("000001.SZ")],
            clock=lambda: FETCHED_AT,
        )
        with TemporaryDirectory() as directory:
            client = self._client(directory, adapter, securities)
            response = client.post(
                "/v1/market-sync/main-board",
                json={
                    "session_date": "2026-01-05",
                    "minimum_coverage": "0.90",
                    "lookback_days": 1,
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            session = payload["sessions"][0]
            diagnostic = session["adjustment_factor_diagnostic"]
            self.assertEqual(session["market_status"], "approved")
            self.assertEqual(session["adjustment_factor_status"], "rejected")
            self.assertEqual(diagnostic["phase_label"], "复权因子")
            self.assertEqual(diagnostic["expected_securities"], 2)
            self.assertEqual(diagnostic["actual_securities"], 1)
            self.assertEqual(Decimal(str(diagnostic["coverage"])), Decimal("0.5"))
            self.assertTrue(diagnostic["quality_run_id"].startswith("quality-"))
            self.assertIn(
                "LOW_SECURITY_COVERAGE",
                {item["code"] for item in diagnostic["issues"]},
            )
            self.assertFalse(payload["research_ready"])

    def test_provider_failure_returns_structured_chinese_error_without_details(self) -> None:
        def fail_market_fetch(_: object) -> list[dict[str, str]]:
            raise ConnectionError("provider token=secret-token")

        securities = (security("security-1", "000001", Exchange.SZ),)
        adapter = TushareAdapter(
            fetcher=lambda request: [daily_row(request.security.symbol, request.start_date)],
            market_fetcher=fail_market_fetch,
            clock=lambda: FETCHED_AT,
        )
        with TemporaryDirectory() as directory:
            client = self._client(directory, adapter, securities)
            response = client.post(
                "/v1/market-sync/main-board",
                json={
                    "session_date": "2026-01-05",
                    "lookback_days": 1,
                },
            )

            self.assertEqual(response.status_code, 503)
            error = response.json()["error"]
            self.assertEqual(error["code"], "MARKET_SYNC_PROVIDER_UNAVAILABLE")
            self.assertIn("暂时不可用", error["message"])
            self.assertIn("suggestion", error["details"])
            self.assertIn("重试", error["details"]["suggestion"])
            self.assertNotIn("secret-token", response.text)

    def test_task_api_is_idempotent_filters_and_preserves_queued_cancellation(self) -> None:
        securities = (security("security-1", "000001", Exchange.SZ),)
        adapter = TushareAdapter(
            fetcher=lambda request: [daily_row(request.security.symbol, request.start_date)],
            market_fetcher=lambda _: [daily_row("000001.SZ")],
            adjustment_factor_fetcher=lambda _: [factor_row("000001.SZ")],
            clock=lambda: FETCHED_AT,
        )
        with TemporaryDirectory() as directory:
            client = self._client(directory, adapter, securities)
            body = {
                "session_date": "2026-01-05",
                "minimum_coverage": "0.90",
                "lookback_days": 1,
                "max_retries": 2,
                "timeout_seconds": 60,
            }
            first = client.post("/v1/market-sync/tasks", json=body)
            duplicate = client.post("/v1/market-sync/tasks", json=body)
            self.assertEqual(first.status_code, 202)
            self.assertEqual(duplicate.status_code, 202)
            self.assertEqual(first.json()["job_id"], duplicate.json()["job_id"])
            self.assertNotIn(directory, first.text)

            page = client.get(
                "/v1/market-sync/tasks",
                params={"status": "queued", "page": 1, "page_size": 1},
            )
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.json()["total"], 1)
            self.assertEqual(page.json()["items"][0]["status"], "queued")
            self.assertFalse(page.json()["has_next"])

            cancelled = client.post(f"/v1/market-sync/tasks/{first.json()['job_id']}/cancel")
            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(cancelled.json()["status"], "cancelled")
            rejected = client.post(f"/v1/market-sync/tasks/{first.json()['job_id']}/cancel")
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(
                rejected.json()["error"]["code"],
                "MARKET_SYNC_TASK_NOT_CANCELLABLE",
            )
            fetched = client.get(f"/v1/market-sync/tasks/{first.json()['job_id']}")
            self.assertEqual(fetched.status_code, 200)
            self.assertEqual(fetched.json()["status"], "cancelled")

            restarted = TestClient(create_app(data_root=directory))
            persisted = restarted.get(f"/v1/market-sync/tasks/{first.json()['job_id']}")
            self.assertEqual(persisted.status_code, 200)
            self.assertEqual(persisted.json()["status"], "cancelled")

    def test_task_runner_api_executes_one_explicit_job_and_returns_result(self) -> None:
        securities = (security("security-1", "000001", Exchange.SZ),)
        adapter = TushareAdapter(
            fetcher=lambda request: [daily_row(request.security.symbol, request.start_date)],
            market_fetcher=lambda _: [daily_row("000001.SZ")],
            adjustment_factor_fetcher=lambda _: [factor_row("000001.SZ")],
            clock=lambda: FETCHED_AT,
        )
        with TemporaryDirectory() as directory:
            client = self._client(directory, adapter, securities)
            queued = client.post(
                "/v1/market-sync/tasks",
                json={"session_date": "2026-01-05", "lookback_days": 1},
            )
            self.assertEqual(queued.status_code, 202)
            job_id = queued.json()["job_id"]

            run = client.post("/v1/market-sync/tasks/run", json={"limit": 1})
            self.assertEqual(run.status_code, 200)
            self.assertEqual(len(run.json()["jobs"]), 1)
            job = run.json()["jobs"][0]
            self.assertEqual(job["job_id"], job_id)
            self.assertEqual(job["status"], "completed")
            self.assertTrue(job["result"]["research_ready"])
            self.assertEqual(job["result"]["sessions"][0]["market_status"], "approved")
            self.assertEqual(
                client.get(f"/v1/market-sync/tasks/{job_id}").json()["status"],
                "completed",
            )

    def _client(
        self,
        directory: str,
        adapter: TushareAdapter,
        securities: tuple[Security, ...],
    ) -> TestClient:
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
        universe_store = UniverseSnapshotStore(universe_root)
        universe_store.write(
            build_universe_snapshot(
                as_of=datetime(2026, 1, 4, 17, tzinfo=UTC),
                source="fixture-main-board",
                source_snapshot_ids=(seed.snapshot_id,),
                securities=securities,
            ),
            market_store,
        )
        return TestClient(
            create_app(
                data_root=directory,
                selection_store=SelectionImportStore(f"{directory}/selection"),
                portfolio_store=ManualPortfolioStore(f"{directory}/portfolio"),
                market_root=market_root,
                portfolio_root=f"{directory}/portfolio",
                universe_root=universe_root,
                universe_store=universe_store,
                market_adapter_factory=lambda _: adapter,
            )
        )


if __name__ == "__main__":
    unittest.main()
