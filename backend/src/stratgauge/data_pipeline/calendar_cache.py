"""Incrementally cached explicit trading-session calendars."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from stratgauge.data_sources.calendar import CalendarReferenceStore, CalendarSource
from stratgauge.data_sources.tushare_reference import (
    TushareReferenceClient,
    TushareReferenceStore,
    parse_trade_calendar_rows,
)


class CalendarCacheError(RuntimeError):
    """A trading-calendar cache cannot be read or safely extended."""


class CalendarCacheManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cache_id: str
    exchange: str
    start_date: date
    end_date: date
    sessions: tuple[date, ...]
    source_snapshot_ids: tuple[str, ...]
    source_providers: tuple[str, ...] = ()
    content_sha256: str
    output_path: str
    updated_at: datetime

    @field_validator("cache_id", "exchange", "content_sha256", "output_path")
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("calendar cache text must not be empty")
        return normalized

    @field_validator("source_snapshot_ids")
    @classmethod
    def validate_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("calendar cache requires source snapshot IDs")
        return tuple(dict.fromkeys(item.strip() for item in value))

    @field_validator("source_providers")
    @classmethod
    def validate_providers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("calendar cache source providers must not be empty")
        return tuple(dict.fromkeys(item.strip() for item in value))

    @field_validator("updated_at")
    @classmethod
    def validate_updated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("calendar cache updated_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> CalendarCacheManifest:
        if self.end_date < self.start_date:
            raise ValueError("calendar cache end_date must not precede start_date")
        if tuple(sorted(self.sessions)) != self.sessions:
            raise ValueError("calendar cache sessions must be sorted")
        if len(set(self.sessions)) != len(self.sessions):
            raise ValueError("calendar cache sessions must be unique")
        if any(not self.start_date <= session <= self.end_date for session in self.sessions):
            raise ValueError("calendar cache session is outside its covered range")
        return self


class CalendarCacheResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: CalendarCacheManifest
    sessions: tuple[date, ...]
    fetched_ranges: tuple[tuple[date, date], ...]
    skipped: bool


class TradeCalendarCache:
    """Maintain one atomic text calendar plus a coverage manifest."""

    def __init__(self, output_path: str | Path) -> None:
        self.output_path = Path(output_path)
        self.manifest_path = self.output_path.with_name(f"{self.output_path.stem}.manifest.json")

    def ensure(
        self,
        *,
        start_date: date,
        end_date: date,
        exchange: str,
        client: TushareReferenceClient,
        reference_store: TushareReferenceStore,
        now: datetime,
    ) -> CalendarCacheResult:
        if end_date < start_date:
            raise CalendarCacheError("calendar cache end_date must not precede start_date")
        existing = self._load_existing()
        if existing is not None and existing.exchange != exchange:
            raise CalendarCacheError("calendar cache exchange does not match the request")

        ranges = _missing_ranges(existing, start_date, end_date)
        fetched_sessions: set[date] = set()
        source_snapshot_ids = set(existing.source_snapshot_ids if existing else ())
        source_providers = set(existing.source_providers if existing else ())
        for range_start, range_end in ranges:
            records = client.trade_calendar_records(
                start_date=range_start,
                end_date=range_end,
                exchange=exchange,
            )
            sessions = parse_trade_calendar_rows(records)
            if any(not range_start <= session <= range_end for session in sessions):
                raise CalendarCacheError("Tushare returned a session outside the requested range")
            source_snapshot = reference_store.write(
                endpoint="trade_cal",
                request={
                    "exchange": exchange,
                    "start_date": range_start.strftime("%Y%m%d"),
                    "end_date": range_end.strftime("%Y%m%d"),
                    "is_open": "1",
                },
                records=records,
                created_at=now,
            )
            fetched_sessions.update(sessions)
            source_snapshot_ids.add(source_snapshot.snapshot_id)
            source_providers.add(client.provider)

        covered_start = min(
            (existing.start_date if existing else start_date),
            start_date,
        )
        covered_end = max(existing.end_date if existing else end_date, end_date)
        all_sessions = set(existing.sessions if existing else ()) | fetched_sessions
        if not ranges and existing is not None:
            return CalendarCacheResult(
                manifest=existing,
                sessions=existing.sessions,
                fetched_ranges=(),
                skipped=True,
            )
        manifest = self._write(
            exchange=exchange,
            start_date=covered_start,
            end_date=covered_end,
            sessions=tuple(sorted(all_sessions)),
            source_snapshot_ids=tuple(sorted(source_snapshot_ids)),
            source_providers=tuple(sorted(source_providers)),
            now=now,
        )
        return CalendarCacheResult(
            manifest=manifest,
            sessions=manifest.sessions,
            fetched_ranges=ranges,
            skipped=False,
        )

    def ensure_cross_validated(
        self,
        *,
        start_date: date,
        end_date: date,
        exchange: str,
        primary: CalendarSource,
        secondary: CalendarSource,
        reference_store: CalendarReferenceStore,
        now: datetime,
    ) -> CalendarCacheResult:
        """Extend the cache only when two independent sources agree."""

        if end_date < start_date:
            raise CalendarCacheError("calendar cache end_date must not precede start_date")
        if primary.provider == secondary.provider:
            raise CalendarCacheError("calendar cache sources must be independent")
        existing = self._load_existing()
        if existing is not None and existing.exchange != exchange:
            raise CalendarCacheError("calendar cache exchange does not match the request")

        ranges = _missing_ranges(existing, start_date, end_date)
        fetched_sessions: set[date] = set()
        source_snapshot_ids = set(existing.source_snapshot_ids if existing else ())
        source_providers = set(existing.source_providers if existing else ())
        for range_start, range_end in ranges:
            request = {
                "exchange": exchange,
                "start_date": range_start.isoformat(),
                "end_date": range_end.isoformat(),
                "is_open": "1",
            }
            primary_records = primary.fetch_records(
                start_date=range_start,
                end_date=range_end,
                exchange=exchange,
            )
            secondary_records = secondary.fetch_records(
                start_date=range_start,
                end_date=range_end,
                exchange=exchange,
            )
            primary_snapshot = reference_store.write(
                provider=primary.provider,
                endpoint=primary.endpoint,
                request=request,
                records=primary_records,
                created_at=now,
            )
            secondary_snapshot = reference_store.write(
                provider=secondary.provider,
                endpoint=secondary.endpoint,
                request=request,
                records=secondary_records,
                created_at=now,
            )
            primary_sessions = primary.parse_sessions(primary_records)
            secondary_sessions = secondary.parse_sessions(secondary_records)
            if primary_sessions != secondary_sessions:
                only_primary = tuple(sorted(set(primary_sessions) - set(secondary_sessions)))
                only_secondary = tuple(sorted(set(secondary_sessions) - set(primary_sessions)))
                raise CalendarCacheError(
                    "backup calendar sources disagree for "
                    f"{range_start.isoformat()}..{range_end.isoformat()}; "
                    f"primary_only={only_primary}, secondary_only={only_secondary}; "
                    f"snapshots={primary_snapshot.snapshot_id},{secondary_snapshot.snapshot_id}"
                )
            fetched_sessions.update(primary_sessions)
            source_snapshot_ids.update(
                (primary_snapshot.snapshot_id, secondary_snapshot.snapshot_id)
            )
            source_providers.update((primary.provider, secondary.provider))

        if not ranges and existing is not None:
            return CalendarCacheResult(
                manifest=existing,
                sessions=existing.sessions,
                fetched_ranges=(),
                skipped=True,
            )
        covered_start = min(
            (existing.start_date if existing else start_date),
            start_date,
        )
        covered_end = max(existing.end_date if existing else end_date, end_date)
        all_sessions = set(existing.sessions if existing else ()) | fetched_sessions
        manifest = self._write(
            exchange=exchange,
            start_date=covered_start,
            end_date=covered_end,
            sessions=tuple(sorted(all_sessions)),
            source_snapshot_ids=tuple(sorted(source_snapshot_ids)),
            source_providers=tuple(sorted(source_providers)),
            now=now,
        )
        return CalendarCacheResult(
            manifest=manifest,
            sessions=manifest.sessions,
            fetched_ranges=ranges,
            skipped=False,
        )

    def _load_existing(self) -> CalendarCacheManifest | None:
        if not self.manifest_path.exists():
            return None
        if not self.output_path.exists():
            raise CalendarCacheError("calendar cache manifest exists without its output file")
        try:
            manifest = CalendarCacheManifest.model_validate_json(
                self.manifest_path.read_text(encoding="utf-8")
            )
            sessions = _read_sessions(self.output_path)
        except (OSError, ValueError) as exc:
            raise CalendarCacheError("calendar cache is invalid") from exc
        if sessions != manifest.sessions:
            raise CalendarCacheError("calendar cache output does not match its manifest")
        if (
            _content_sha256(
                exchange=manifest.exchange,
                start_date=manifest.start_date,
                end_date=manifest.end_date,
                sessions=manifest.sessions,
                source_snapshot_ids=manifest.source_snapshot_ids,
                source_providers=manifest.source_providers,
            )
            != manifest.content_sha256
        ):
            raise CalendarCacheError("calendar cache manifest hash does not match its content")
        return manifest

    def _write(
        self,
        *,
        exchange: str,
        start_date: date,
        end_date: date,
        sessions: tuple[date, ...],
        source_snapshot_ids: tuple[str, ...],
        now: datetime,
        source_providers: tuple[str, ...] = (),
    ) -> CalendarCacheManifest:
        if now.tzinfo is None or now.utcoffset() is None:
            raise CalendarCacheError("calendar cache timestamp must include a timezone")
        digest = _content_sha256(
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
            sessions=sessions,
            source_snapshot_ids=source_snapshot_ids,
            source_providers=source_providers,
        )
        manifest = CalendarCacheManifest(
            cache_id=f"calendar-{digest[:20]}",
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
            sessions=sessions,
            source_snapshot_ids=source_snapshot_ids,
            source_providers=source_providers,
            content_sha256=digest,
            output_path=str(self.output_path),
            updated_at=now.astimezone(UTC),
        )
        output = "".join(f"{session.isoformat()}\n" for session in sessions)
        _atomic_write(self.output_path, output)
        _atomic_write(self.manifest_path, manifest.model_dump_json(indent=2) + "\n")
        return manifest


def _missing_ranges(
    existing: CalendarCacheManifest | None,
    start_date: date,
    end_date: date,
) -> tuple[tuple[date, date], ...]:
    if existing is None:
        return ((start_date, end_date),)
    ranges: list[tuple[date, date]] = []
    if start_date < existing.start_date:
        left_end = min(end_date, existing.start_date - timedelta(days=1))
        ranges.append((start_date, left_end))
    if end_date > existing.end_date:
        right_start = max(start_date, existing.end_date + timedelta(days=1))
        ranges.append((right_start, end_date))
    return tuple(ranges)


def _content_sha256(
    *,
    exchange: str,
    start_date: date,
    end_date: date,
    sessions: tuple[date, ...],
    source_snapshot_ids: tuple[str, ...],
    source_providers: tuple[str, ...] = (),
) -> str:
    payload = {
        "end_date": end_date.isoformat(),
        "exchange": exchange,
        "sessions": [session.isoformat() for session in sessions],
        "source_snapshot_ids": list(source_snapshot_ids),
        "start_date": start_date.isoformat(),
    }
    if source_providers:
        payload["source_providers"] = list(source_providers)
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _read_sessions(path: Path) -> tuple[date, ...]:
    sessions: list[date] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw_line.split("#", 1)[0].strip()
        if not value:
            continue
        try:
            sessions.append(date.fromisoformat(value))
        except ValueError as exc:
            raise ValueError(f"invalid calendar session at line {line_number}") from exc
    result = tuple(sessions)
    if len(set(result)) != len(result) or tuple(sorted(result)) != result:
        raise ValueError("calendar cache sessions must be unique and sorted")
    return result


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise CalendarCacheError(f"failed to write calendar cache: {path}") from exc
