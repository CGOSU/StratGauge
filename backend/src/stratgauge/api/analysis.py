"""Stock-analysis application service and response contract."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.data_pipeline.store import SnapshotStatus
from stratgauge.domain.models import ActionState, Exchange, canonical_symbol


class AnalysisError(Exception):
    """Expected API-facing analysis failure."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class AnalysisRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    as_of: date
    snapshot_status: SnapshotStatus
    data_freshness: datetime
    action_state: ActionState
    source_snapshot_id: str
    feature_set_version: str
    strategy_version_id: str
    model_version_id: str | None = None
    probability: Decimal | None = Field(default=None, ge=0, le=1)
    failed_conditions: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @field_validator("data_freshness")
    @classmethod
    def require_aware_freshness(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data_freshness must include a timezone")
        return value

    @field_validator("probability", mode="before")
    @classmethod
    def normalize_probability(cls, value: Decimal | str | int | float | None) -> Decimal | None:
        return None if value is None else Decimal(str(value))


class AnalysisRepository(Protocol):
    def get(self, symbol: str, as_of: date) -> AnalysisRecord | None: ...


class InMemoryAnalysisRepository:
    """Fixture-backed repository used until the application query layer is added."""

    def __init__(self, records: Mapping[tuple[str, date], AnalysisRecord] | None = None) -> None:
        self._records = dict(records or {})

    def get(self, symbol: str, as_of: date) -> AnalysisRecord | None:
        return self._records.get((symbol, as_of))


class AnalysisService:
    def __init__(self, repository: AnalysisRepository) -> None:
        self._repository = repository

    def get(self, symbol: str, as_of: date) -> AnalysisRecord:
        canonical = _canonicalize_path_symbol(symbol)
        record = self._repository.get(canonical, as_of)
        if record is None:
            raise AnalysisError(
                "ANALYSIS_NOT_FOUND",
                "指定日期没有可用的股票分析。",
                404,
            )
        if record.snapshot_status != SnapshotStatus.APPROVED:
            raise AnalysisError(
                "DATA_SNAPSHOT_NOT_APPROVED",
                "请求的分析无法提供，因为市场快照未通过必要的数据质量检查。",
                409,
            )
        return record


def _canonicalize_path_symbol(symbol: str) -> str:
    parts = symbol.strip().upper().split(".")
    if len(parts) != 2:
        raise AnalysisError("INVALID_SYMBOL", "股票代码必须使用如 000001.SZ 的格式。", 400)
    try:
        return canonical_symbol(parts[0], Exchange(parts[1]))
    except ValueError as exc:
        raise AnalysisError("INVALID_SYMBOL", "股票代码或交易所无效。", 400) from exc
