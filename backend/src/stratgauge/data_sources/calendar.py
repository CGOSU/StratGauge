"""Backup trading-calendar sources and immutable reference evidence."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, NoReturn, Protocol

from pydantic import BaseModel, ConfigDict, field_validator

from stratgauge.data_sources.base import (
    AdapterError,
    RetryableAdapterError,
    is_retryable_provider_message,
)


class CalendarSource(Protocol):
    """Provider boundary for an explicit A-share trading-calendar range."""

    provider: str
    endpoint: str

    def fetch_records(
        self,
        *,
        start_date: date,
        end_date: date,
        exchange: str,
    ) -> tuple[Mapping[str, str], ...]: ...

    def parse_sessions(self, records: Iterable[Mapping[str, str]]) -> tuple[date, ...]: ...


class CalendarReferenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    provider: str
    endpoint: str
    request: dict[str, str]
    content_sha256: str
    raw_path: str
    created_at: datetime

    @field_validator("snapshot_id", "provider", "endpoint", "content_sha256", "raw_path")
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("calendar reference text must not be empty")
        return normalized

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("calendar reference created_at must include a timezone")
        return value


class CalendarReferenceStoreError(RuntimeError):
    """A backup calendar reference snapshot cannot be safely persisted."""


class CalendarReferenceStore:
    """Persist raw backup-calendar responses without overwriting evidence."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.raw_dir = self.root / "calendar" / "reference" / "raw"
        self.manifest_dir = self.root / "calendar" / "reference" / "manifests"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        *,
        provider: str,
        endpoint: str,
        request: Mapping[str, str],
        records: Iterable[Mapping[str, str]],
        created_at: datetime,
    ) -> CalendarReferenceSnapshot:
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise CalendarReferenceStoreError("created_at must include a timezone")
        ordered_records = tuple(dict(record) for record in records)
        payload = {
            "endpoint": endpoint,
            "provider": provider,
            "records": ordered_records,
            "request": dict(sorted(request.items())),
        }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        snapshot = CalendarReferenceSnapshot(
            snapshot_id=f"calendar-reference-{provider}-{digest[:20]}",
            provider=provider,
            endpoint=endpoint,
            request=dict(sorted(request.items())),
            content_sha256=digest,
            raw_path=f"calendar/reference/raw/{provider}/{digest}.json",
            created_at=created_at,
        )
        manifest_path = self.manifest_dir / f"{snapshot.snapshot_id}.json"
        if manifest_path.exists():
            existing = self.load(snapshot.snapshot_id)
            if existing != snapshot:
                raise CalendarReferenceStoreError(
                    "calendar reference snapshot identity already has different content"
                )
            return existing
        _atomic_write_text(self.root / snapshot.raw_path, content)
        _atomic_write_text(manifest_path, snapshot.model_dump_json(indent=2))
        return snapshot

    def load(self, snapshot_id: str) -> CalendarReferenceSnapshot:
        path = self.manifest_dir / f"{snapshot_id}.json"
        if not path.exists():
            raise CalendarReferenceStoreError(
                f"calendar reference snapshot does not exist: {snapshot_id}"
            )
        try:
            return CalendarReferenceSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CalendarReferenceStoreError(
                f"calendar reference snapshot is invalid: {snapshot_id}"
            ) from exc


class BaoStockCalendarSource:
    """Fetch the BaoStock A-share trading calendar."""

    provider = "baostock"
    endpoint = "query_trade_dates"

    def fetch_records(
        self,
        *,
        start_date: date,
        end_date: date,
        exchange: str,
    ) -> tuple[Mapping[str, str], ...]:
        _validate_a_share_exchange(exchange)
        try:
            bs = importlib.import_module("baostock")
        except ImportError as exc:
            raise AdapterError(
                "BaoStock is not installed; install the optional data-source dependency"
            ) from exc

        with contextlib.redirect_stdout(io.StringIO()):
            login = bs.login()
        if str(getattr(login, "error_code", "0")) != "0":
            message = str(getattr(login, "error_msg", login))
            _raise_provider_error("BaoStock login failed", message)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                result = bs.query_trade_dates(
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat(),
                )
            if str(getattr(result, "error_code", "0")) != "0":
                message = str(getattr(result, "error_msg", result))
                _raise_provider_error("BaoStock calendar query failed", message)
            rows: list[Mapping[str, str]] = []
            while result.next():
                fields = tuple(str(field) for field in result.fields)
                values = tuple(str(value) for value in result.get_row_data())
                rows.append(dict(zip(fields, values, strict=True)))
            return tuple(rows)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                bs.logout()

    @staticmethod
    def parse_sessions(records: Iterable[Mapping[str, str]]) -> tuple[date, ...]:
        sessions: list[date] = []
        for record in records:
            if record.get("is_trading_day") != "1":
                continue
            value = record.get("calendar_date", "")
            sessions.append(_parse_date(value, "BaoStock calendar_date"))
        return _sorted_unique_sessions(sessions)


class AKShareCalendarSource:
    """Fetch the Sina-backed AKShare A-share trading calendar."""

    provider = "akshare"
    endpoint = "tool_trade_date_hist_sina"

    def fetch_records(
        self,
        *,
        start_date: date,
        end_date: date,
        exchange: str,
    ) -> tuple[Mapping[str, str], ...]:
        _validate_a_share_exchange(exchange)
        try:
            ak = importlib.import_module("akshare")
            frame = ak.tool_trade_date_hist_sina()
            raw_rows = frame.to_dict(orient="records")
        except ImportError as exc:
            raise AdapterError(
                "AKShare is not installed; install the optional data-source dependency"
            ) from exc
        except Exception as exc:
            _raise_provider_error("AKShare calendar query failed", str(exc))
            raise AssertionError("unreachable") from exc

        rows: list[Mapping[str, str]] = []
        for raw_row in raw_rows:
            value = _parse_date_value(raw_row.get("trade_date"), "AKShare trade_date")
            if start_date <= value <= end_date:
                rows.append({"trade_date": value.isoformat()})
        return tuple(rows)

    @staticmethod
    def parse_sessions(records: Iterable[Mapping[str, str]]) -> tuple[date, ...]:
        return _sorted_unique_sessions(
            _parse_date(record.get("trade_date", ""), "AKShare trade_date") for record in records
        )


def _validate_a_share_exchange(exchange: str) -> None:
    if exchange.upper() not in {"SSE", "SZSE", "SH", "SZ"}:
        raise AdapterError(f"backup calendar source does not support exchange: {exchange}")


def _raise_provider_error(prefix: str, message: str) -> NoReturn:
    error_type = RetryableAdapterError if is_retryable_provider_message(message) else AdapterError
    raise error_type(f"{prefix}: {message}")


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AdapterError(f"invalid {field_name}: {value!r}") from exc


def _parse_date_value(value: Any, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _parse_date(str(value), field_name)


def _sorted_unique_sessions(values: Iterable[date]) -> tuple[date, ...]:
    return tuple(sorted(set(values)))


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise CalendarReferenceStoreError(f"failed to write calendar reference: {path}") from exc
