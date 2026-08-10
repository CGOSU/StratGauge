"""Tushare Pro reference-data client and immutable response snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from stratgauge.data_pipeline.store import SnapshotStatus
from stratgauge.data_sources.base import AdapterError
from stratgauge.data_sources.tushare_api import query_tushare
from stratgauge.domain.models import Exchange, Security, canonical_symbol

ReferenceQuery = Callable[[str, Mapping[str, str]], Iterable[Mapping[str, Any]]]

TUSHARE_MAIN_BOARD_MARKET = "主板"
TUSHARE_MAIN_BOARD_EXCHANGES = frozenset({"SSE", "SZSE", "SH", "SZ"})


class TushareReferenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    provider: str = "tushare"
    endpoint: str
    request: dict[str, str]
    content_sha256: str
    raw_path: str
    status: SnapshotStatus = SnapshotStatus.APPROVED
    created_at: datetime


class TushareReferenceStoreError(RuntimeError):
    """A Tushare reference response cannot be safely persisted."""


class TushareReferenceStore:
    """Persist stock-list and calendar responses without overwriting evidence."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.raw_dir = self.root / "tushare" / "reference" / "raw"
        self.manifest_dir = self.root / "tushare" / "reference" / "manifests"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        *,
        endpoint: str,
        request: Mapping[str, str],
        records: Iterable[Mapping[str, Any]],
        created_at: datetime,
    ) -> TushareReferenceSnapshot:
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise TushareReferenceStoreError("created_at must include a timezone")
        ordered_records = tuple(dict(record) for record in records)
        payload = {
            "endpoint": endpoint,
            "records": ordered_records,
            "request": dict(sorted(request.items())),
        }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        snapshot = TushareReferenceSnapshot(
            snapshot_id=f"tushare-reference-{digest[:20]}",
            endpoint=endpoint,
            request=dict(sorted(request.items())),
            content_sha256=digest,
            raw_path=f"tushare/reference/raw/{digest}.json",
            created_at=created_at,
        )
        manifest_path = self.manifest_dir / f"{snapshot.snapshot_id}.json"
        if manifest_path.exists():
            existing = self.load(snapshot.snapshot_id)
            if existing != snapshot:
                raise TushareReferenceStoreError(
                    "Tushare reference snapshot identity already has different content"
                )
            return existing
        _atomic_write_text(self.root / snapshot.raw_path, content)
        _atomic_write_text(manifest_path, snapshot.model_dump_json(indent=2))
        return snapshot

    def load_manifest(self, snapshot_id: str) -> TushareReferenceSnapshot:
        return self.load(snapshot_id)

    def load(self, snapshot_id: str) -> TushareReferenceSnapshot:
        path = self.manifest_dir / f"{snapshot_id}.json"
        if not path.exists():
            raise TushareReferenceStoreError(
                f"Tushare reference snapshot does not exist: {snapshot_id}"
            )
        try:
            return TushareReferenceSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise TushareReferenceStoreError(
                f"Tushare reference snapshot is invalid: {snapshot_id}"
            ) from exc


class TushareReferenceClient:
    """Fetch Tushare reference rows through a narrow provider boundary."""

    def __init__(self, query: ReferenceQuery | None = None) -> None:
        self._query = query or self._query_from_tushare

    def stock_basic(self, *, list_status: str = "L") -> tuple[Mapping[str, Any], ...]:
        rows = self._query(
            "stock_basic",
            {
                "exchange": "",
                "list_status": list_status,
                "market": TUSHARE_MAIN_BOARD_MARKET,
                "fields": ("ts_code,symbol,name,market,exchange,list_status,list_date,delist_date"),
            },
        )
        return tuple(row for row in rows if _is_main_board_row(row))

    def trade_calendar_records(
        self,
        *,
        start_date: date,
        end_date: date,
        exchange: str = "SSE",
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            self._query(
                "trade_cal",
                {
                    "exchange": exchange,
                    "start_date": start_date.strftime("%Y%m%d"),
                    "end_date": end_date.strftime("%Y%m%d"),
                    "is_open": "1",
                },
            )
        )

    def trade_calendar(
        self,
        *,
        start_date: date,
        end_date: date,
        exchange: str = "SSE",
    ) -> tuple[date, ...]:
        return parse_trade_calendar_rows(
            self.trade_calendar_records(
                start_date=start_date,
                end_date=end_date,
                exchange=exchange,
            )
        )

    @staticmethod
    def securities(rows: Iterable[Mapping[str, Any]]) -> tuple[Security, ...]:
        securities: list[Security] = []
        for row in rows:
            ts_code = str(row.get("ts_code", "")).strip().upper()
            if ts_code.count(".") != 1:
                raise AdapterError(f"Tushare stock_basic returned invalid ts_code: {ts_code!r}")
            local_code, exchange_code = ts_code.split(".")
            exchange = {
                "SSE": Exchange.SH,
                "SH": Exchange.SH,
                "SZSE": Exchange.SZ,
                "SZ": Exchange.SZ,
                "BSE": Exchange.BJ,
                "BJ": Exchange.BJ,
            }.get(exchange_code)
            if exchange is None:
                raise AdapterError(f"Tushare returned unsupported exchange: {exchange_code!r}")
            symbol = canonical_symbol(local_code, exchange)
            delisted_at = _parse_optional_date(row.get("delist_date"))
            securities.append(
                Security(
                    security_id=f"tushare-{symbol}",
                    local_code=local_code,
                    symbol=symbol,
                    exchange=exchange,
                    name=str(row.get("name", "")).strip() or None,
                    listed=str(row.get("list_status", "L")).strip() == "L",
                    delisted_at=delisted_at,
                )
            )
        by_symbol = {security.symbol: security for security in securities}
        if len(by_symbol) != len(securities):
            raise AdapterError("Tushare stock_basic returned duplicate symbols")
        return tuple(sorted(by_symbol.values(), key=lambda security: security.symbol))

    @staticmethod
    def _query_from_tushare(
        endpoint: str, params: Mapping[str, str]
    ) -> Iterable[Mapping[str, Any]]:
        return query_tushare(endpoint, params)


def parse_trade_calendar_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[date, ...]:
    sessions: list[date] = []
    for row in rows:
        if str(row.get("is_open", "1")).strip() not in {"1", "True", "true"}:
            continue
        value = row.get("cal_date")
        if value in (None, ""):
            raise AdapterError("Tushare trade_cal row is missing cal_date")
        parsed = _parse_optional_date(value)
        if parsed is None:
            raise AdapterError(f"Tushare returned invalid calendar date: {value!r}")
        sessions.append(parsed)
    return tuple(sorted(set(sessions)))


def _is_main_board_row(row: Mapping[str, Any]) -> bool:
    market = str(row.get("market", "")).strip()
    exchange = str(row.get("exchange", "")).strip().upper()
    return market == TUSHARE_MAIN_BOARD_MARKET and exchange in TUSHARE_MAIN_BOARD_EXCHANGES


def _parse_optional_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    text = str(value)
    try:
        if len(text) == 8 and text.isdigit():
            return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise AdapterError(f"Tushare returned invalid date: {value!r}") from exc


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise TushareReferenceStoreError(f"failed to write Tushare reference file: {path}") from exc
