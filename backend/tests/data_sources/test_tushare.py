from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from datetime import UTC, date, datetime
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from stratgauge.data_pipeline.universe import UniverseSnapshotStore, build_universe_snapshot
from stratgauge.data_sources.base import AdapterError, DailyBarRequest, RetryableAdapterError
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.data_sources.tushare_api import TushareRetryPolicy, TushareTransport
from stratgauge.data_sources.tushare_reference import (
    TushareReferenceClient,
    TushareReferenceStore,
)
from stratgauge.domain.models import Exchange, Security


class TushareReferenceTests(unittest.TestCase):
    def test_stock_basic_and_calendar_are_explicitly_normalized(self) -> None:
        def query(endpoint: str, _: object) -> list[dict[str, str]]:
            if endpoint == "stock_basic":
                return [
                    {
                        "ts_code": "000001.SZ",
                        "symbol": "000001",
                        "name": "平安银行",
                        "fullname": "平安银行股份有限公司",
                        "area": "深圳",
                        "industry": "银行",
                        "curr_type": "CNY",
                        "is_hs": "H",
                        "act_name": "马明哲",
                        "market": "主板",
                        "exchange": "SZSE",
                        "list_status": "L",
                        "list_date": "19910403",
                        "delist_date": "",
                    },
                    {
                        "ts_code": "600000.SH",
                        "symbol": "600000",
                        "name": "浦发银行",
                        "fullname": "上海浦东发展银行股份有限公司",
                        "area": "上海",
                        "industry": "银行",
                        "curr_type": "CNY",
                        "is_hs": "S",
                        "act_name": "上海国际集团",
                        "market": "主板",
                        "exchange": "SSE",
                        "list_status": "L",
                        "list_date": "19991110",
                        "delist_date": "",
                    },
                    {
                        "ts_code": "300001.SZ",
                        "symbol": "300001",
                        "name": "特锐德",
                        "market": "创业板",
                        "exchange": "SZSE",
                        "list_status": "L",
                        "list_date": "20100601",
                        "delist_date": "",
                    },
                    {
                        "ts_code": "688001.SH",
                        "symbol": "688001",
                        "name": "华兴源创",
                        "market": "科创板",
                        "exchange": "SSE",
                        "list_status": "L",
                        "list_date": "20190722",
                        "delist_date": "",
                    },
                    {
                        "ts_code": "830001.BJ",
                        "symbol": "830001",
                        "name": "北京证券",
                        "market": "北交所",
                        "exchange": "BSE",
                        "list_status": "L",
                        "list_date": "20200101",
                        "delist_date": "",
                    },
                ]
            return [
                {"cal_date": "20260105", "is_open": "1"},
                {"cal_date": "20260104", "is_open": "0"},
            ]

        client = TushareReferenceClient(query=query)
        rows = client.stock_basic()
        securities = client.securities(rows)
        master_records = client.security_master_records(rows)
        self.assertEqual(tuple(row["ts_code"] for row in rows), ("000001.SZ", "600000.SH"))
        self.assertEqual(tuple(item.symbol for item in securities), ("000001.SZ", "600000.SH"))
        self.assertEqual(securities[0].exchange, Exchange.SZ)
        self.assertEqual(securities[0].listed_at, date(1991, 4, 3))
        self.assertEqual(master_records[0].full_name, "平安银行股份有限公司")
        self.assertEqual(master_records[0].industry, "银行")
        self.assertEqual(master_records[0].actual_controller, "马明哲")
        self.assertEqual(
            client.trade_calendar(start_date=date(2026, 1, 1), end_date=date(2026, 1, 5)),
            (date(2026, 1, 5),),
        )

    def test_reference_snapshot_can_authorize_universe_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            created_at = datetime(2026, 1, 6, 8, tzinfo=UTC)
            reference_store = TushareReferenceStore(directory)
            reference = reference_store.write(
                endpoint="stock_basic",
                request={"list_status": "L"},
                records=({"ts_code": "000001.SZ"},),
                created_at=created_at,
            )
            client = TushareReferenceClient(
                query=lambda *_: [
                    {
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                        "market": "主板",
                        "exchange": "SZSE",
                        "list_status": "L",
                    }
                ]
            )
            snapshot = build_universe_snapshot(
                as_of=created_at,
                source="tushare:stock_basic",
                source_snapshot_ids=(reference.snapshot_id,),
                securities=client.securities(client.stock_basic()),
            )
            stored = UniverseSnapshotStore(directory).write(snapshot, reference_store)
            self.assertEqual(stored, snapshot)


class FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")
        self.closed = False

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, responses: list[FakeResponse | BaseException]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.close_count = 0
        self.tunnel: tuple[str, int, dict[str, str]] | None = None

    def request(
        self,
        method: str,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> None:
        self.requests.append((method, url, body, dict(headers)))
        if self.responses and isinstance(self.responses[0], BaseException):
            failure = self.responses.pop(0)
            raise cast(BaseException, failure)

    def getresponse(self) -> FakeResponse:
        if not self.responses:
            raise AssertionError("fake response was not queued")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def set_tunnel(
        self,
        host: str,
        port: int,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.tunnel = (host, port, dict(headers or {}))

    def close(self) -> None:
        self.close_count += 1


class _RecordingTransport:
    def __init__(self) -> None:
        self.closed = 0
        self.queries: list[tuple[str, Mapping[str, str]]] = []

    def query(self, endpoint: str, params: Mapping[str, str]) -> tuple[dict[str, Any], ...]:
        self.queries.append((endpoint, params))
        return (
            {
                "trade_date": "20260105",
                "ts_code": "000001.SZ",
                "pre_close": "10.00",
                "open": "10.10",
                "high": "10.50",
                "low": "9.90",
                "close": "10.20",
                "vol": "100",
                "amount": "20",
            },
        )

    def close(self) -> None:
        self.closed += 1


class TushareTransportTests(unittest.TestCase):
    @staticmethod
    def success_payload() -> dict[str, object]:
        return {
            "code": 0,
            "data": {"fields": ["trade_date", "ts_code"], "items": [["20260105", "000001.SZ"]]},
        }

    def test_reuses_one_connection_and_closes_it(self) -> None:
        connection = FakeConnection([FakeResponse(200, self.success_payload())] * 2)
        created: list[tuple[str, int, float]] = []

        def factory(host: str, port: int, timeout: float) -> FakeConnection:
            created.append((host, port, timeout))
            return connection

        transport = TushareTransport(
            token="secret-token",
            default_endpoint_interval_seconds=0,
            retry_policy=TushareRetryPolicy(max_attempts=1),
            connection_factory=factory,
        )

        self.assertEqual(
            transport.query("daily", {"trade_date": "20260105"})[0]["ts_code"],
            "000001.SZ",
        )
        self.assertEqual(
            transport.query("adj_factor", {"trade_date": "20260105"})[0]["ts_code"],
            "000001.SZ",
        )
        transport.close()
        transport.close()

        self.assertEqual(len(created), 1)
        self.assertEqual(len(connection.requests), 2)
        self.assertEqual(connection.close_count, 1)
        payload = json.loads(connection.requests[0][2].decode("utf-8"))
        self.assertEqual(payload["token"], "secret-token")

    def test_timeout_retries_with_bounded_backoff(self) -> None:
        connections = [
            FakeConnection([TimeoutError("socket timeout")]),
            FakeConnection([FakeResponse(200, self.success_payload())]),
        ]
        created = 0
        sleeps: list[float] = []

        def factory(_: str, __: int, ___: float) -> FakeConnection:
            nonlocal created
            connection = connections[created]
            created += 1
            return connection

        transport = TushareTransport(
            token="test-token",
            default_endpoint_interval_seconds=0,
            retry_policy=TushareRetryPolicy(
                max_attempts=2,
                initial_backoff_seconds=0.25,
                max_backoff_seconds=0.25,
            ),
            connection_factory=factory,
            sleep=sleeps.append,
        )

        self.assertEqual(len(transport.query("daily", {})), 1)
        self.assertEqual(created, 2)
        self.assertEqual(sleeps, [0.25])

    def test_rate_limit_retries_but_non_retryable_http_error_does_not(self) -> None:
        rate_limited = FakeConnection([FakeResponse(429, {"code": 429, "msg": "rate limit"})])
        recovered = FakeConnection([FakeResponse(200, self.success_payload())])
        connections = iter((rate_limited, recovered))
        sleeps: list[float] = []
        transport = TushareTransport(
            token="test-token",
            default_endpoint_interval_seconds=0,
            retry_policy=TushareRetryPolicy(max_attempts=2, initial_backoff_seconds=0.1),
            connection_factory=lambda *_: next(connections),
            sleep=sleeps.append,
        )
        self.assertEqual(len(transport.query("daily", {})), 1)
        self.assertEqual(sleeps, [0.1])

        bad_request = FakeConnection([FakeResponse(400, {"code": 400, "msg": "bad request"})])
        bad_transport = TushareTransport(
            token="test-token",
            default_endpoint_interval_seconds=0,
            retry_policy=TushareRetryPolicy(max_attempts=3),
            connection_factory=lambda *_: bad_request,
            sleep=sleeps.append,
        )
        with self.assertRaises(AdapterError):
            bad_transport.query("daily", {})
        self.assertEqual(len(sleeps), 1)

    def test_endpoint_throttle_is_explicit_and_provider_token_is_redacted(self) -> None:
        connection = FakeConnection(
            [
                FakeResponse(
                    200,
                    {"code": 40203, "msg": "rate limit token=secret-token", "data": None},
                )
            ]
        )
        sleeps: list[float] = []
        transport = TushareTransport(
            token="secret-token",
            default_endpoint_interval_seconds=0.5,
            retry_policy=TushareRetryPolicy(max_attempts=1),
            connection_factory=lambda *_: connection,
            sleep=sleeps.append,
            clock=lambda: 0.0,
        )
        with self.assertRaises(RetryableAdapterError) as raised:
            transport.query("daily", {})
        self.assertNotIn("secret-token", str(raised.exception))

        second = FakeConnection(
            [FakeResponse(200, self.success_payload()), FakeResponse(200, self.success_payload())]
        )
        second_transport = TushareTransport(
            token="test-token",
            default_endpoint_interval_seconds=0.5,
            retry_policy=TushareRetryPolicy(max_attempts=1),
            connection_factory=lambda *_: second,
            sleep=sleeps.append,
            clock=lambda: 0.0,
        )
        second_transport.query("daily", {})
        second_transport.query("daily", {})
        self.assertEqual(sleeps[-1], 0.5)

    def test_adapter_accepts_fake_transport_and_closes_it(self) -> None:
        transport = _RecordingTransport()
        adapter = TushareAdapter(
            transport=transport,
            clock=lambda: datetime(2026, 1, 6, 8, tzinfo=UTC),
        )
        security = Security(
            security_id="security-1",
            local_code="000001",
            symbol="000001.SZ",
            exchange=Exchange.SZ,
        )
        result = adapter.fetch_daily_bars(
            DailyBarRequest(
                security=security,
                start_date=date(2026, 1, 5),
                end_date=date(2026, 1, 5),
            )
        )
        adapter.close()

        self.assertEqual(result.bars[0].symbol, "000001.SZ")
        self.assertEqual(transport.queries[0][0], "daily")
        self.assertEqual(transport.closed, 1)

    def test_proxy_selection_uses_environment_and_closes_transport_context(self) -> None:
        connection = FakeConnection([FakeResponse(200, self.success_payload())])
        with (
            patch(
                "stratgauge.data_sources.tushare_api.HTTPSConnection",
                return_value=connection,
            ),
            patch(
                "stratgauge.data_sources.tushare_api.getproxies",
                return_value={"https": "http://proxy.example:8080"},
            ),
            patch("stratgauge.data_sources.tushare_api.proxy_bypass", return_value=False),
        ):
            with TushareTransport(
                token="test-token",
                default_endpoint_interval_seconds=0,
                retry_policy=TushareRetryPolicy(max_attempts=1),
            ) as transport:
                transport.query("daily", {})

        self.assertEqual(connection.tunnel, ("api.tushare.pro", 443, {}))
        self.assertEqual(connection.close_count, 1)


if __name__ == "__main__":
    unittest.main()
