"""Forward-adjust raw daily prices for research-only feature inputs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from stratgauge.domain.models import AdjustmentFactor, DailyBar


class PriceAdjustmentError(ValueError):
    """Raw bars and adjustment factors cannot be combined safely."""


def forward_adjust_daily_bars(
    bars: Sequence[DailyBar],
    factors: Sequence[AdjustmentFactor],
    *,
    as_of: datetime | None = None,
) -> tuple[DailyBar, ...]:
    """Scale historical prices to the latest factor without changing volume or amount."""

    if not bars:
        raise PriceAdjustmentError("price adjustment requires daily bars")
    if not factors:
        raise PriceAdjustmentError("price adjustment requires adjustment factors")
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise PriceAdjustmentError("as_of must include a timezone")
    ordered_bars = tuple(sorted(bars, key=lambda bar: bar.session_date))
    if len({bar.session_date for bar in ordered_bars}) != len(ordered_bars):
        raise PriceAdjustmentError("daily bars contain duplicate sessions")
    security_ids = {bar.security_id for bar in ordered_bars}
    symbols = {bar.symbol for bar in ordered_bars}
    if len(security_ids) != 1 or len(symbols) != 1:
        raise PriceAdjustmentError("price adjustment requires one security")
    security_id = ordered_bars[0].security_id
    relevant_factors = tuple(factor for factor in factors if factor.security_id == security_id)
    factor_by_date: dict[date, AdjustmentFactor] = {}
    for factor in relevant_factors:
        if factor.session_date in factor_by_date:
            raise PriceAdjustmentError("adjustment factors contain duplicate sessions")
        if as_of is not None and factor.available_at > as_of:
            raise PriceAdjustmentError("adjustment factor is unavailable at as_of")
        factor_by_date[factor.session_date] = factor
    missing = [bar.session_date for bar in ordered_bars if bar.session_date not in factor_by_date]
    if missing:
        raise PriceAdjustmentError(f"missing adjustment factor for {missing[0]}")
    latest_factor = factor_by_date[ordered_bars[-1].session_date].factor
    adjusted: list[DailyBar] = []
    for bar in ordered_bars:
        factor = factor_by_date[bar.session_date]
        multiplier = factor.factor / latest_factor
        available_at = max(bar.available_at, factor.available_at)
        adjusted.append(
            bar.model_copy(
                update={
                    "open": bar.open * multiplier,
                    "high": bar.high * multiplier,
                    "low": bar.low * multiplier,
                    "close": bar.close * multiplier,
                    "previous_close": (
                        None if bar.previous_close is None else bar.previous_close * multiplier
                    ),
                    "provider": "forward-adjusted",
                    "available_at": available_at,
                    "ingested_at": max(bar.ingested_at, available_at),
                    "quality_state": "derived-forward-adjusted",
                }
            )
        )
    return tuple(adjusted)
