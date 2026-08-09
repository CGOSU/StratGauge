"""One deliberately small example strategy for the foundation milestone."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from stratgauge.domain.models import Signal, StrategyVersion
from stratgauge.strategies.ast import Features, StrategyExpressionError, evaluate_expression


class ExampleStrategy:
    """A versioned, rules-only example; not the user's final trading strategy."""

    version = StrategyVersion(
        strategy_version_id="technical-example-v1",
        strategy_id="technical-example",
        version=1,
        name="技术示例策略",
        feature_set_version="features-v1",
    )

    _eligibility_expression = "close > ma20 and ma5 > ma10 and volume / volume_ma20 > 1.5"

    def generate_signal(
        self,
        *,
        signal_id: str,
        security_id: str,
        symbol: str,
        as_of: date,
        execution_session: date,
        source_snapshot_id: str,
        features: Features,
    ) -> Signal:
        try:
            eligible = evaluate_expression(self._eligibility_expression, features)
            if not isinstance(eligible, bool):
                raise StrategyExpressionError("eligibility expression must return boolean")
        except StrategyExpressionError as exc:
            return self._rejected_signal(
                signal_id=signal_id,
                security_id=security_id,
                symbol=symbol,
                as_of=as_of,
                execution_session=execution_session,
                source_snapshot_id=source_snapshot_id,
                reason=str(exc),
            )

        if not eligible:
            return self._rejected_signal(
                signal_id=signal_id,
                security_id=security_id,
                symbol=symbol,
                as_of=as_of,
                execution_session=execution_session,
                source_snapshot_id=source_snapshot_id,
                reason="策略条件未全部满足",
            )

        lower = _required_feature(features, "support_price")
        resistance = _required_feature(features, "resistance_price")
        close = _required_feature(features, "close")
        upper = min(close * Decimal("1.01"), resistance)
        if lower < 0 or upper < lower:
            return self._rejected_signal(
                signal_id=signal_id,
                security_id=security_id,
                symbol=symbol,
                as_of=as_of,
                execution_session=execution_session,
                source_snapshot_id=source_snapshot_id,
                reason="价格区间无效",
            )
        return Signal(
            signal_id=signal_id,
            security_id=security_id,
            symbol=symbol,
            as_of=as_of,
            earliest_execution=execution_session,
            latest_execution=execution_session,
            strategy_version_id=self.version.strategy_version_id,
            feature_set_version=self.version.feature_set_version,
            source_snapshot_id=source_snapshot_id,
            entry_lower=lower,
            entry_upper=upper,
        )

    def _rejected_signal(
        self,
        *,
        signal_id: str,
        security_id: str,
        symbol: str,
        as_of: date,
        execution_session: date,
        source_snapshot_id: str,
        reason: str,
    ) -> Signal:
        return Signal(
            signal_id=signal_id,
            security_id=security_id,
            symbol=symbol,
            as_of=as_of,
            earliest_execution=execution_session,
            latest_execution=execution_session,
            strategy_version_id=self.version.strategy_version_id,
            feature_set_version=self.version.feature_set_version,
            source_snapshot_id=source_snapshot_id,
            rejection_reason=reason,
        )


def _required_feature(features: Mapping[str, Decimal], name: str) -> Decimal:
    value = features.get(name)
    if value is None:
        raise StrategyExpressionError(f"unknown or unavailable feature: {name}")
    return value
