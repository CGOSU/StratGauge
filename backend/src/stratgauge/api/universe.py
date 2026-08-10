"""Point-in-time stock search API contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.data_pipeline.universe import (
    SecurityOverride,
    UniverseOverrideStore,
    UniverseOverrideStoreError,
    UniverseSnapshotStore,
    UniverseSnapshotStoreError,
)
from stratgauge.domain.models import Security


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


class UniverseSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    as_of: datetime
    universe_snapshot_id: str
    universe_as_of: datetime
    results: tuple[Security, ...] = Field(max_length=100)


class UniverseSearchService:
    """Search only the latest universe known at the requested time."""

    def __init__(
        self,
        store: UniverseSnapshotStore,
        override_store: UniverseOverrideStore | None = None,
    ) -> None:
        self._store = store
        self._override_store = override_store

    def create_override(self, request: SecurityOverrideRequest) -> SecurityOverride:
        if self._override_store is None:
            raise UniverseSearchError("UNIVERSE_OVERRIDE_UNAVAILABLE", "股票覆写存储未配置。", 503)
        try:
            return self._override_store.write(SecurityOverride(**request.model_dump()))
        except (UniverseOverrideStoreError, ValueError) as exc:
            raise UniverseSearchError("UNIVERSE_OVERRIDE_REJECTED", str(exc), 409) from exc

    def search(self, *, query: str, as_of: datetime, limit: int = 50) -> UniverseSearchResponse:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise UniverseSearchError(
                "UNIVERSE_AS_OF_MUST_BE_AWARE",
                "as_of must include a timezone",
                422,
            )
        if limit < 1 or limit > 100:
            raise UniverseSearchError(
                "UNIVERSE_LIMIT_INVALID", "limit must be between 1 and 100", 422
            )
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
            results=results,
        )

    def _apply_override(self, security: Security, as_of: datetime) -> Security:
        if self._override_store is None:
            return security
        try:
            override = self._override_store.latest_for(security.symbol, as_of)
        except UniverseOverrideStoreError as exc:
            raise UniverseSearchError("UNIVERSE_OVERRIDE_READ_FAILED", str(exc), 409) from exc
        if override is None or override.name is None:
            return security
        return security.model_copy(update={"name": override.name})


def _matches(security: Security, query: str) -> bool:
    return (
        query in security.symbol
        or query in security.local_code
        or bool(security.name and query in security.name.upper())
    )
