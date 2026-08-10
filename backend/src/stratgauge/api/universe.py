"""Point-in-time stock search API contracts."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.data_pipeline.universe import (
    SecurityMasterStore,
    SecurityMasterStoreError,
    SecurityOverride,
    UniverseOverrideStore,
    UniverseOverrideStoreError,
    UniverseSnapshotStore,
    UniverseSnapshotStoreError,
)
from stratgauge.domain.models import Security, SecurityMasterRecord


class UniverseSearchError(Exception):
    """An explicit point-in-time universe search cannot be served."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class SecurityOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    override_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    effective_at: datetime
    name: str | None = None
    note: str = Field(min_length=1)
    operator: str = Field(min_length=1)

    @field_validator("effective_at")
    @classmethod
    def require_aware_effective_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective_at must include a timezone")
        return value


class SecurityMasterSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    effective_date: date
    known_at: datetime
    master_snapshot_id: str
    observed_at: datetime
    results: tuple[SecurityMasterRecord, ...] = Field(max_length=100)


class UniverseSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    as_of: datetime
    universe_snapshot_id: str
    universe_as_of: datetime
    membership_date: date | None = None
    results: tuple[Security, ...] = Field(max_length=100)


class UniverseSearchService:
    """Search only the latest universe known at the requested time."""

    def __init__(
        self,
        store: UniverseSnapshotStore,
        override_store: UniverseOverrideStore | None = None,
        master_store: SecurityMasterStore | None = None,
    ) -> None:
        self._store = store
        self._override_store = override_store
        self._master_store = master_store

    def create_override(self, request: SecurityOverrideRequest) -> SecurityOverride:
        if self._override_store is None:
            raise UniverseSearchError("UNIVERSE_OVERRIDE_UNAVAILABLE", "股票覆写存储未配置。", 503)
        try:
            return self._override_store.write(SecurityOverride(**request.model_dump()))
        except (UniverseOverrideStoreError, ValueError) as exc:
            raise UniverseSearchError("UNIVERSE_OVERRIDE_REJECTED", str(exc), 409) from exc

    def search_master(
        self,
        *,
        query: str,
        effective_date: date,
        known_at: datetime,
        limit: int = 50,
    ) -> SecurityMasterSearchResponse:
        _require_aware(known_at, "known_at")
        if effective_date > known_at.date():
            raise UniverseSearchError(
                "SECURITY_MASTER_EFFECTIVE_DATE_IN_FUTURE",
                "effective_date must not be after known_at",
                422,
            )
        _require_limit(limit)
        if self._master_store is None:
            raise UniverseSearchError("SECURITY_MASTER_UNAVAILABLE", "证券主数据未配置。", 503)
        normalized_query = query.strip().upper()
        try:
            snapshot = self._master_store.latest_at(known_at)
        except SecurityMasterStoreError as exc:
            raise UniverseSearchError("SECURITY_MASTER_READ_FAILED", str(exc), 409) from exc
        if snapshot is None:
            raise UniverseSearchError(
                "SECURITY_MASTER_SNAPSHOT_NOT_FOUND",
                "no security master snapshot is known at the requested time",
                404,
            )
        results: list[SecurityMasterRecord] = []
        for record in snapshot.records:
            if not record.is_effective_on(effective_date):
                continue
            visible_record = self._apply_master_override(record, known_at)
            if normalized_query and not _matches_master(visible_record, normalized_query):
                continue
            results.append(visible_record)
            if len(results) == limit:
                break
        return SecurityMasterSearchResponse(
            query=query.strip(),
            effective_date=effective_date,
            known_at=known_at,
            master_snapshot_id=snapshot.snapshot_id,
            observed_at=snapshot.observed_at,
            results=tuple(results),
        )

    def search(self, *, query: str, as_of: datetime, limit: int = 50) -> UniverseSearchResponse:
        _require_aware(as_of, "as_of")
        _require_limit(limit)
        normalized_query = query.strip().upper()
        try:
            snapshot = self._store.latest_at(as_of)
        except UniverseSnapshotStoreError as exc:
            raise UniverseSearchError("UNIVERSE_READ_FAILED", str(exc), 409) from exc
        if snapshot is None:
            raise UniverseSearchError(
                "UNIVERSE_SNAPSHOT_NOT_FOUND",
                "no approved universe snapshot is known at the requested time",
                404,
            )
        results = tuple(
            self._apply_override(security, as_of)
            for security in snapshot.securities
            if not normalized_query or _matches(security, normalized_query)
        )[:limit]
        return UniverseSearchResponse(
            query=query.strip(),
            as_of=as_of,
            universe_snapshot_id=snapshot.universe_snapshot_id,
            universe_as_of=snapshot.as_of,
            membership_date=snapshot.membership_date,
            results=results,
        )

    def _apply_master_override(
        self,
        record: SecurityMasterRecord,
        as_of: datetime,
    ) -> SecurityMasterRecord:
        override = self._latest_override(record.symbol, as_of)
        if override is None or override.name is None:
            return record
        return record.model_copy(update={"name": override.name})

    def _apply_override(self, security: Security, as_of: datetime) -> Security:
        if self._override_store is None:
            return security
        override = self._latest_override(security.symbol, as_of)
        if override is None or override.name is None:
            return security
        return security.model_copy(update={"name": override.name})

    def _latest_override(self, symbol: str, as_of: datetime) -> SecurityOverride | None:
        if self._override_store is None:
            return None
        try:
            return self._override_store.latest_for(symbol, as_of)
        except UniverseOverrideStoreError as exc:
            raise UniverseSearchError("UNIVERSE_OVERRIDE_READ_FAILED", str(exc), 409) from exc


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise UniverseSearchError(
            f"UNIVERSE_{field_name.upper()}_MUST_BE_AWARE",
            f"{field_name} must include a timezone",
            422,
        )


def _require_limit(limit: int) -> None:
    if limit < 1 or limit > 100:
        raise UniverseSearchError("UNIVERSE_LIMIT_INVALID", "limit must be between 1 and 100", 422)


def _matches_master(record: SecurityMasterRecord, query: str) -> bool:
    return any(
        query in value.upper()
        for value in (
            record.symbol,
            record.local_code,
            record.name,
            record.full_name,
            record.english_name,
            record.pinyin,
            record.area,
            record.industry,
            record.actual_controller,
        )
        if value
    )


def _matches(security: Security, query: str) -> bool:
    return (
        query in security.symbol
        or query in security.local_code
        or bool(security.name and query in security.name.upper())
    )
