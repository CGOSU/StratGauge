"""Immutable feature-set and feature-snapshot contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.domain.models import DomainModel

FeatureNullPolicy = Literal["reject_row"]
FeatureAvailabilityRule = Literal["all_inputs_available"]


class FeatureDefinition(DomainModel):
    """Metadata that makes one numeric feature auditable."""

    name: str
    description: str
    lookback_sessions: int = Field(gt=0)
    availability_rule: FeatureAvailabilityRule = "all_inputs_available"
    null_policy: FeatureNullPolicy = "reject_row"
    unit: str
    requires_benchmark: bool = False

    @field_validator("name", "description", "unit")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("feature metadata text must not be empty")
        return value.strip()


class FeatureSetVersion(DomainModel):
    """Content-addressed, immutable collection of feature definitions."""

    feature_set_version_id: str
    name: str
    definitions: tuple[FeatureDefinition, ...]
    definition_sha256: str

    @field_validator("feature_set_version_id", "name", "definition_sha256")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("feature-set value must not be empty")
        return value.strip()

    @model_validator(mode="after")
    def validate_definitions(self) -> FeatureSetVersion:
        names = tuple(definition.name for definition in self.definitions)
        if not names:
            raise ValueError("feature set must contain at least one definition")
        if len(set(names)) != len(names):
            raise ValueError("feature names must be unique")
        expected_hash = feature_definition_sha256(self.definitions)
        if self.definition_sha256 != expected_hash:
            raise ValueError("definition_sha256 does not match feature definitions")
        return self

    @property
    def names(self) -> frozenset[str]:
        return frozenset(definition.name for definition in self.definitions)


def feature_definition_sha256(definitions: Iterable[FeatureDefinition]) -> str:
    """Hash definitions canonically so equivalent versions share an identity."""

    payload = [definition.model_dump(mode="json") for definition in definitions]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class FeatureSnapshot(DomainModel):
    """One point-in-time feature row and its source evidence."""

    feature_snapshot_id: str
    feature_set_version_id: str
    security_id: str
    symbol: str
    selection_date: date
    as_of: datetime
    available_at: datetime
    input_session_start: date
    input_session_end: date
    source_snapshot_ids: tuple[str, ...]
    values: dict[str, Decimal]

    @field_validator(
        "feature_snapshot_id",
        "feature_set_version_id",
        "security_id",
        "symbol",
        "source_snapshot_ids",
    )
    @classmethod
    def validate_ids(cls, value: Any) -> Any:
        if isinstance(value, tuple):
            if not value or any(not item.strip() for item in value):
                raise ValueError("source snapshot identifiers must not be empty")
            return tuple(item.strip() for item in value)
        if not value.strip():
            raise ValueError("feature snapshot identifier must not be empty")
        return value.strip()

    @field_validator("as_of", "available_at")
    @classmethod
    def validate_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("feature timestamp must include a timezone")
        return value

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        if not value:
            raise ValueError("feature snapshot must contain values")
        if any(not item.is_finite() for item in value.values()):
            raise ValueError("feature values must be finite")
        return value

    @model_validator(mode="after")
    def validate_time_window(self) -> FeatureSnapshot:
        if self.input_session_end < self.input_session_start:
            raise ValueError("input_session_end must not precede input_session_start")
        if self.input_session_end >= self.selection_date:
            raise ValueError("feature input sessions must precede selection_date")
        if self.available_at > self.as_of:
            raise ValueError("feature inputs are not available at as_of")
        return self


DEFAULT_FEATURE_SET_NAME = "selection-technical-v1"


def default_feature_set() -> FeatureSetVersion:
    """Return the deterministic first technical feature contract."""

    definitions = (
        FeatureDefinition(
            name="close",
            description="最后一个可用交易日收盘价",
            lookback_sessions=1,
            unit="price",
        ),
        FeatureDefinition(
            name="return_1",
            description="最近一个交易日收益率",
            lookback_sessions=1,
            unit="ratio",
        ),
        FeatureDefinition(
            name="return_5",
            description="最近五个交易日收益率",
            lookback_sessions=5,
            unit="ratio",
        ),
        FeatureDefinition(
            name="return_20",
            description="最近二十个交易日收益率",
            lookback_sessions=20,
            unit="ratio",
        ),
        FeatureDefinition(
            name="volatility_5",
            description="最近五个交易日收益率总体波动率",
            lookback_sessions=5,
            unit="ratio",
        ),
        FeatureDefinition(
            name="volatility_20",
            description="最近二十个交易日收益率总体波动率",
            lookback_sessions=20,
            unit="ratio",
        ),
        FeatureDefinition(
            name="volume_ratio_20",
            description="最新成交量相对二十日平均成交量",
            lookback_sessions=20,
            unit="ratio",
        ),
        FeatureDefinition(
            name="amount_average_20",
            description="最近二十个交易日平均成交额",
            lookback_sessions=20,
            unit="currency",
        ),
        FeatureDefinition(
            name="ma_distance_5",
            description="收盘价相对五日均线距离",
            lookback_sessions=5,
            unit="ratio",
        ),
        FeatureDefinition(
            name="ma_distance_20",
            description="收盘价相对二十日均线距离",
            lookback_sessions=20,
            unit="ratio",
        ),
        FeatureDefinition(
            name="benchmark_return_20",
            description="基准标的二十日收益率",
            lookback_sessions=20,
            unit="ratio",
            requires_benchmark=True,
        ),
        FeatureDefinition(
            name="relative_strength_20",
            description="候选二十日收益率减去基准二十日收益率",
            lookback_sessions=20,
            unit="ratio",
            requires_benchmark=True,
        ),
    )
    digest = feature_definition_sha256(definitions)
    return FeatureSetVersion(
        feature_set_version_id=f"features-{digest[:20]}",
        name=DEFAULT_FEATURE_SET_NAME,
        definitions=definitions,
        definition_sha256=digest,
    )
