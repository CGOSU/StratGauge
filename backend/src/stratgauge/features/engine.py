"""Deterministic daily-bar feature computation with point-in-time guards."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal, localcontext

from stratgauge.domain.models import DailyBar
from stratgauge.features.contract import (
    FeatureDefinition,
    FeatureSetVersion,
    FeatureSnapshot,
    default_feature_set,
)


class FeatureComputationError(ValueError):
    """Feature inputs cannot satisfy the frozen contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def compute_daily_features(
    bars: Sequence[DailyBar],
    *,
    selection_date: date,
    as_of: datetime,
    source_snapshot_ids: Iterable[str],
    feature_set: FeatureSetVersion | None = None,
    benchmark_bars: Sequence[DailyBar] = (),
    benchmark_source_snapshot_ids: Iterable[str] = (),
) -> FeatureSnapshot:
    """Compute one immutable feature row without reading future or unavailable bars."""

    resolved_set = feature_set or default_feature_set()
    candidate_bars = _validated_bars(bars, selection_date, as_of, "candidate")
    benchmark = _validated_bars(benchmark_bars, selection_date, as_of, "benchmark")
    required_input_sessions = max(
        _required_input_sessions(definition) for definition in resolved_set.definitions
    )
    if len(candidate_bars) < required_input_sessions:
        raise FeatureComputationError(
            "insufficient_history",
            f"candidate history requires at least {required_input_sessions} sessions",
        )

    required_names = resolved_set.names
    if (
        any(definition.requires_benchmark for definition in resolved_set.definitions)
        and not benchmark
    ):
        raise FeatureComputationError(
            "missing_benchmark",
            "feature set requires benchmark history",
        )

    benchmark_by_date = {bar.session_date: bar for bar in benchmark}
    values = _compute_values(candidate_bars, required_names, benchmark_by_date)
    snapshot_sources = _unique_ids((*source_snapshot_ids, *benchmark_source_snapshot_ids))
    if not snapshot_sources:
        raise FeatureComputationError(
            "missing_lineage",
            "feature computation requires at least one source snapshot ID",
        )
    available_at = max(bar.available_at for bar in (*candidate_bars, *benchmark))
    snapshot_payload = {
        "as_of": as_of.isoformat(),
        "available_at": available_at.isoformat(),
        "feature_set_version_id": resolved_set.feature_set_version_id,
        "input_session_end": candidate_bars[-1].session_date.isoformat(),
        "input_session_start": candidate_bars[0].session_date.isoformat(),
        "security_id": candidate_bars[-1].security_id,
        "selection_date": selection_date.isoformat(),
        "source_snapshot_ids": snapshot_sources,
        "symbol": candidate_bars[-1].symbol,
        "values": {name: str(values[name]) for name in sorted(values)},
    }
    digest = hashlib.sha256(
        json.dumps(
            snapshot_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return FeatureSnapshot(
        feature_snapshot_id=f"feature-snapshot-{digest[:20]}",
        feature_set_version_id=resolved_set.feature_set_version_id,
        security_id=candidate_bars[-1].security_id,
        symbol=candidate_bars[-1].symbol,
        selection_date=selection_date,
        as_of=as_of,
        available_at=available_at,
        input_session_start=candidate_bars[0].session_date,
        input_session_end=candidate_bars[-1].session_date,
        source_snapshot_ids=snapshot_sources,
        values=values,
    )


def _validated_bars(
    bars: Sequence[DailyBar],
    selection_date: date,
    as_of: datetime,
    label: str,
) -> tuple[DailyBar, ...]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise FeatureComputationError("naive_as_of", "as_of must include a timezone")
    if not bars:
        if label == "benchmark":
            return ()
        raise FeatureComputationError("missing_history", f"{label} history is empty")
    ordered = tuple(sorted(bars, key=lambda bar: bar.session_date))
    keys = {(bar.security_id, bar.session_date) for bar in ordered}
    if len(keys) != len(ordered):
        raise FeatureComputationError(
            "duplicate_bar", f"{label} history contains duplicate sessions"
        )
    security_ids = {bar.security_id for bar in ordered}
    symbols = {bar.symbol for bar in ordered}
    if len(security_ids) != 1 or len(symbols) != 1:
        raise FeatureComputationError(
            "mixed_security", f"{label} history contains multiple securities"
        )
    future = [bar.session_date for bar in ordered if bar.session_date >= selection_date]
    if future:
        raise FeatureComputationError(
            "future_session",
            f"{label} history contains sessions on or after selection_date: {future[0]}",
        )
    unavailable = [bar.session_date for bar in ordered if bar.available_at > as_of]
    if unavailable:
        raise FeatureComputationError(
            "unavailable_at_as_of",
            f"{label} history is not available at as_of: {unavailable[0]}",
        )
    return ordered


def _compute_values(
    candidate: tuple[DailyBar, ...],
    names: frozenset[str],
    benchmark_by_date: dict[date, DailyBar],
) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    last_close = candidate[-1].close
    if "close" in names:
        values["close"] = last_close
    for window in (1, 5, 20):
        return_name = f"return_{window}"
        if return_name in names:
            values[return_name] = _return(candidate, window)
    for window in (5, 20):
        volatility_name = f"volatility_{window}"
        if volatility_name in names:
            values[volatility_name] = _volatility(candidate, window)
        distance_name = f"ma_distance_{window}"
        if distance_name in names:
            values[distance_name] = last_close / _average_close(candidate, window) - Decimal("1")
    if "volume_ratio_20" in names:
        average_volume = _average(candidate[-20:], "volume")
        if average_volume == 0:
            raise FeatureComputationError(
                "zero_volume_average", "twenty-day average volume is zero"
            )
        values["volume_ratio_20"] = candidate[-1].volume / average_volume
    if "amount_average_20" in names:
        values["amount_average_20"] = _average(candidate[-20:], "amount")

    benchmark_return: Decimal | None = None
    if "benchmark_return_20" in names or "relative_strength_20" in names:
        _require_aligned_sessions(candidate[-21:], benchmark_by_date)
        benchmark_window = tuple(benchmark_by_date[bar.session_date] for bar in candidate[-21:])
        benchmark_return = _return(benchmark_window, 20)
        if "benchmark_return_20" in names:
            values["benchmark_return_20"] = benchmark_return
    if "relative_strength_20" in names:
        values["relative_strength_20"] = _return(candidate, 20) - _require_value(benchmark_return)

    unknown = names.difference(values)
    if unknown:
        raise FeatureComputationError(
            "unsupported_feature",
            f"feature engine does not implement: {', '.join(sorted(unknown))}",
        )
    return values


def _required_input_sessions(definition: FeatureDefinition) -> int:
    if definition.name.startswith("return_") or definition.name.startswith("volatility_"):
        return definition.lookback_sessions + 1
    return definition.lookback_sessions


def _return(bars: Sequence[DailyBar], window: int) -> Decimal:
    if len(bars) < window + 1:
        raise FeatureComputationError(
            "insufficient_history",
            f"return_{window} requires {window + 1} sessions",
        )
    start = bars[-window - 1].close
    if start == 0:
        raise FeatureComputationError(
            "zero_reference_price", f"return_{window} reference close is zero"
        )
    return bars[-1].close / start - Decimal("1")


def _volatility(bars: Sequence[DailyBar], window: int) -> Decimal:
    if len(bars) < window + 1:
        raise FeatureComputationError(
            "insufficient_history",
            f"volatility_{window} requires {window + 1} sessions",
        )
    window_bars = bars[-window - 1 :]
    returns = tuple(
        window_bars[index + 1].close / window_bars[index].close - Decimal("1")
        for index in range(window)
    )
    mean = sum(returns, Decimal("0")) / Decimal(window)
    variance = sum((value - mean) ** 2 for value in returns) / Decimal(window)
    with localcontext() as context:
        context.prec = 28
        return variance.sqrt()


def _average_close(bars: Sequence[DailyBar], window: int) -> Decimal:
    if len(bars) < window:
        raise FeatureComputationError(
            "insufficient_history",
            f"ma_distance_{window} requires {window} sessions",
        )
    return _average(bars[-window:], "close")


def _average(bars: Sequence[DailyBar], field: str) -> Decimal:
    return sum((getattr(bar, field) for bar in bars), Decimal("0")) / Decimal(len(bars))


def _require_aligned_sessions(
    candidate: Sequence[DailyBar],
    benchmark_by_date: dict[date, DailyBar],
) -> None:
    missing = [bar.session_date for bar in candidate if bar.session_date not in benchmark_by_date]
    if missing:
        raise FeatureComputationError(
            "benchmark_session_mismatch",
            f"benchmark history misses candidate session: {missing[0]}",
        )


def _require_value(value: Decimal | None) -> Decimal:
    if value is None:
        raise FeatureComputationError("missing_benchmark", "benchmark return is unavailable")
    return value


def _unique_ids(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized:
            raise FeatureComputationError("invalid_lineage", "source snapshot ID must not be empty")
        if normalized not in result:
            result.append(normalized)
    return tuple(result)
