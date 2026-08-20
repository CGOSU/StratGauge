import json
import runpy
import sys
import unittest
from collections.abc import Callable, Mapping
from contextlib import redirect_stdout
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from stratgauge.data_pipeline.ingest import ingest
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_sources.akshare import AKShareAdapter
from stratgauge.data_sources.base import DailyBarRequest, IndexDailyRequest
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.domain.models import Exchange, Security

FETCHED_AT = datetime(2026, 1, 6, 8, tzinfo=UTC)


class _OfflineTransport:
    def query(self, _: str, __: Mapping[str, str]) -> tuple[dict[str, Any], ...]:
        raise AssertionError("transport query should not run in the CLI substitute")

    def close(self) -> None:
        pass


def request() -> DailyBarRequest:
    return DailyBarRequest(
        security=Security(
            security_id="security-1",
            local_code="000001",
            symbol="000001.SZ",
            exchange=Exchange.SZ,
        ),
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 5),
    )


def index_request() -> IndexDailyRequest:
    return IndexDailyRequest(
        security=Security(
            security_id="index-000300.SH",
            local_code="000300",
            symbol="000300.SH",
            exchange=Exchange.SH,
        ),
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 5),
    )


def adapter_for(session: str = "2026-01-05") -> AKShareAdapter:
    return AKShareAdapter(
        fetcher=lambda _: [
            {
                "日期": session,
                "开盘": "10.00",
                "最高": "10.50",
                "最低": "9.80",
                "收盘": "10.20",
                "成交量": "1000",
                "成交额": "10200",
            }
        ],
        clock=lambda: FETCHED_AT,
    )


class IngestTests(unittest.TestCase):
    def test_quality_gate_approves_complete_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            result = ingest(
                adapter_for(),
                request(),
                SnapshotStore(directory),
                expected_sessions=[date(2026, 1, 5)],
                expected_security_ids=["security-1"],
            )
            self.assertTrue(result.quality.approved)
            self.assertEqual(result.manifest.status, SnapshotStatus.APPROVED)
            self.assertIsNotNone(result.manifest.quality_run_id)

    def test_quality_gate_rejects_missing_session(self) -> None:
        with TemporaryDirectory() as directory:
            result = ingest(
                adapter_for(),
                request(),
                SnapshotStore(directory),
                expected_sessions=[date(2026, 1, 5), date(2026, 1, 6)],
            )
            self.assertFalse(result.quality.approved)
            self.assertEqual(result.manifest.status, SnapshotStatus.REJECTED)

    def test_index_daily_reuses_immutable_ingest_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            first = ingest(
                adapter_for(),
                index_request(),
                store,
                expected_sessions=[date(2026, 1, 5)],
                expected_security_ids=["index-000300.SH"],
            )
            raw_before = (store.root / first.manifest.raw_path).read_bytes()

            second = ingest(
                AKShareAdapter(
                    fetcher=lambda _: (_ for _ in ()).throw(AssertionError("remote fetch")),
                    clock=lambda: FETCHED_AT,
                ),
                index_request(),
                store,
                expected_sessions=[date(2026, 1, 5)],
                expected_security_ids=["index-000300.SH"],
            )

            self.assertTrue(first.quality.approved)
            self.assertEqual(first.manifest.request_kind, "index_daily")
            self.assertEqual(second.manifest.snapshot_id, first.manifest.snapshot_id)
            self.assertEqual(second.access_plan.decision.value, "cache_hit")
            self.assertEqual((store.root / first.manifest.raw_path).read_bytes(), raw_before)

    def test_index_daily_cli_writes_approved_snapshot_with_offline_substitute(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            calendar_file = root / "calendar.txt"
            calendar_file.write_text("2026-01-05\n", encoding="utf-8")
            adapter = TushareAdapter(
                fetcher=lambda _: [
                    {
                        "trade_date": "20260105",
                        "ts_code": "000300.SH",
                        "pre_close": "100",
                        "open": "101",
                        "high": "102",
                        "low": "99",
                        "close": "101.5",
                        "vol": "100",
                        "amount": "200",
                    }
                ],
                clock=lambda: FETCHED_AT,
                transport=_OfflineTransport(),
            )
            script_globals = runpy.run_path(
                str(Path(__file__).parents[2] / "scripts" / "sync_tushare_index_daily.py"),
                run_name="sync_tushare_index_daily",
            )
            script_globals["main"].__globals__["TushareAdapter"] = lambda: adapter
            main = cast(Callable[[], int], script_globals["main"])
            output = StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "sync_tushare_index_daily.py",
                        "--session-date",
                        "2026-01-05",
                        "--calendar-file",
                        str(calendar_file),
                        "--market-root",
                        str(root / "market"),
                    ],
                ),
                redirect_stdout(output),
            ):
                exit_code = main()

            payload = json.loads(output.getvalue())
            manifest = SnapshotStore(root / "market").list_manifests()[0]
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["request_kind"], "index_daily")
            self.assertEqual(payload["symbol"], "000300.SH")
            self.assertEqual(manifest.request_kind, "index_daily")
            self.assertEqual(manifest.status.value, "approved")


if __name__ == "__main__":
    unittest.main()
