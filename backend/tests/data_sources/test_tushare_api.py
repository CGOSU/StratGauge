from __future__ import annotations

import json
import os
import unittest
from http.client import HTTPMessage
from unittest.mock import patch
from urllib.error import HTTPError

from stratgauge.data_sources.base import AdapterError, RetryableAdapterError
from stratgauge.data_sources.tushare_api import query_tushare


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class TushareApiTests(unittest.TestCase):
    def test_query_uses_https_endpoint_and_maps_rows(self) -> None:
        response = {
            "code": 0,
            "data": {
                "fields": ["trade_date", "ts_code"],
                "items": [["20240105", "000001.SZ"]],
            },
        }
        with patch.dict(os.environ, {"TUSHARE_TOKEN": "test-token"}, clear=False):
            with patch(
                "stratgauge.data_sources.tushare_api.urlopen",
                return_value=_Response(response),
            ) as opener:
                records = query_tushare(
                    "daily", {"ts_code": "000001.SZ", "fields": "trade_date,ts_code"}
                )

        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.tushare.pro/dataapi")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["token"], "test-token")
        self.assertEqual(payload["params"], {"ts_code": "000001.SZ"})
        self.assertEqual(records, ({"trade_date": "20240105", "ts_code": "000001.SZ"},))

    def test_http_gateway_failure_is_retryable(self) -> None:
        failure = HTTPError(
            "https://api.tushare.pro/dataapi", 502, "bad gateway", HTTPMessage(), None
        )
        with patch.dict(os.environ, {"TUSHARE_TOKEN": "test-token"}, clear=False):
            with patch("stratgauge.data_sources.tushare_api.urlopen", side_effect=failure):
                with self.assertRaises(RetryableAdapterError):
                    query_tushare("daily", {"ts_code": "000001.SZ"})

    def test_provider_error_preserves_code_and_retryability(self) -> None:
        response = {"code": 40203, "data": None, "msg": "request rate limit"}
        with patch.dict(os.environ, {"TUSHARE_TOKEN": "test-token"}, clear=False):
            with patch(
                "stratgauge.data_sources.tushare_api.urlopen",
                return_value=_Response(response),
            ):
                with self.assertRaisesRegex(RetryableAdapterError, "40203"):
                    query_tushare("trade_cal", {"exchange": "SSE"})

    def test_missing_token_is_rejected_before_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(AdapterError, "TUSHARE_TOKEN is not set"):
                query_tushare("daily", {"ts_code": "000001.SZ"})


if __name__ == "__main__":
    unittest.main()
