"""Deterministic reports for theoretical backtest results."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.backtest.models import BacktestResult, Trade

BACKTEST_REPORT_VERSION = "backtest-report-v1"
UNSPECIFIED_MARKET_STATE = "unspecified"


class BacktestReportStatistics(BaseModel):
    """Descriptive trade statistics available from theoretical fills."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_count: int = Field(ge=0)
    profitable_trade_count: int = Field(ge=0)
    losing_trade_count: int = Field(ge=0)
    flat_trade_count: int = Field(ge=0)
    win_rate: Decimal | None = Field(default=None, ge=0, le=1)
    average_gross_return: Decimal | None = None
    average_net_return: Decimal | None = None
    compounded_net_return: Decimal | None = None
    max_drawdown: Decimal | None = Field(default=None, ge=0)


class BacktestReportGroup(BaseModel):
    """One explicit strategy, market-state, and entry-date report slice."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_version_id: str
    cost_schedule_id: str
    market_state: str
    entry_session: date
    statistics: BacktestReportStatistics

    @field_validator("strategy_version_id", "cost_schedule_id", "market_state")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("backtest report group text must not be empty")
        return normalized


class BacktestReport(BaseModel):
    """A content-addressed report of theory-only backtest evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str
    report_version: Literal["backtest-report-v1"] = "backtest-report-v1"
    evidence_kind: Literal["theoretical_backtest"] = "theoretical_backtest"
    run_id: str
    source_snapshot_ids: tuple[str, ...]
    feature_set_version_ids: tuple[str, ...]
    strategy_version_ids: tuple[str, ...]
    cost_schedule_ids: tuple[str, ...]
    market_state_labels: tuple[str, ...]
    unfilled_count: int = Field(ge=0)
    unresolved_signal_count: int = Field(ge=0)
    statistics: BacktestReportStatistics
    groups: tuple[BacktestReportGroup, ...]

    @field_validator(
        "report_id",
        "run_id",
        "source_snapshot_ids",
        "feature_set_version_ids",
        "strategy_version_ids",
        "cost_schedule_ids",
        "market_state_labels",
    )
    @classmethod
    def validate_text_fields(cls, value: str | tuple[str, ...]) -> str | tuple[str, ...]:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("backtest report text must not be empty")
            return normalized
        if any(not item.strip() for item in value):
            raise ValueError("backtest report identifiers must not be empty")
        return tuple(item.strip() for item in value)


def build_backtest_report(
    result: BacktestResult,
    *,
    market_states: Mapping[date, str] | None = None,
) -> BacktestReport:
    """Build a reproducible report without adding recommendation or fill evidence."""

    normalized_states = _normalize_market_states(market_states or {})
    trades = tuple(sorted(result.trades, key=_trade_sort_key))
    groups = _build_groups(trades, normalized_states)
    cost_schedule_ids = tuple(sorted({trade.cost_schedule_id for trade in trades}))
    if not cost_schedule_ids and result.cost_schedule_id:
        cost_schedule_ids = (result.cost_schedule_id,)
    market_state_labels = tuple(
        sorted({group.market_state for group in groups} or {UNSPECIFIED_MARKET_STATE})
    )
    identity = {
        "market_states": [
            [session.isoformat(), state] for session, state in normalized_states.items()
        ],
        "result": result.model_dump(mode="json"),
        "report_version": BACKTEST_REPORT_VERSION,
    }
    report_id = (
        "backtest-report-"
        + hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()[:20]
    )
    return BacktestReport(
        report_id=report_id,
        run_id=result.run_id,
        source_snapshot_ids=result.source_snapshot_ids,
        feature_set_version_ids=result.feature_set_version_ids,
        strategy_version_ids=result.strategy_version_ids,
        cost_schedule_ids=cost_schedule_ids,
        market_state_labels=market_state_labels,
        unfilled_count=len(result.unfilled),
        unresolved_signal_count=len(result.unresolved_signal_ids),
        statistics=_statistics(trades),
        groups=groups,
    )


def _normalize_market_states(states: Mapping[date, str]) -> dict[date, str]:
    normalized: dict[date, str] = {}
    for session, state in states.items():
        if not isinstance(session, date):
            raise ValueError("market state keys must be dates")
        value = state.strip()
        if not value:
            raise ValueError(f"market state must not be empty: {session.isoformat()}")
        normalized[session] = value
    return dict(sorted(normalized.items()))


def _build_groups(
    trades: tuple[Trade, ...],
    market_states: Mapping[date, str],
) -> tuple[BacktestReportGroup, ...]:
    grouped: defaultdict[tuple[str, str, str, date], list[Trade]] = defaultdict(list)
    for trade in trades:
        entry_session = date.fromisoformat(trade.entry_session)
        grouped[
            (
                trade.strategy_version_id,
                trade.cost_schedule_id,
                market_states.get(entry_session, UNSPECIFIED_MARKET_STATE),
                entry_session,
            )
        ].append(trade)
    return tuple(
        BacktestReportGroup(
            strategy_version_id=strategy_version_id,
            cost_schedule_id=cost_schedule_id,
            market_state=market_state,
            entry_session=entry_session,
            statistics=_statistics(tuple(sorted(rows, key=_trade_sort_key))),
        )
        for (strategy_version_id, cost_schedule_id, market_state, entry_session), rows in sorted(
            grouped.items()
        )
    )


def _statistics(trades: tuple[Trade, ...]) -> BacktestReportStatistics:
    profitable = sum(trade.net_return > 0 for trade in trades)
    losing = sum(trade.net_return < 0 for trade in trades)
    flat = len(trades) - profitable - losing
    returns = tuple(trade.net_return for trade in trades)
    gross_returns = tuple(trade.gross_return for trade in trades)
    return BacktestReportStatistics(
        trade_count=len(trades),
        profitable_trade_count=profitable,
        losing_trade_count=losing,
        flat_trade_count=flat,
        win_rate=(Decimal(profitable) / Decimal(len(trades)) if trades else None),
        average_gross_return=(sum(gross_returns, Decimal("0")) / len(trades) if trades else None),
        average_net_return=(sum(returns, Decimal("0")) / len(trades) if trades else None),
        compounded_net_return=_compounded_return(returns),
        max_drawdown=_max_drawdown(returns),
    )


def _compounded_return(returns: tuple[Decimal, ...]) -> Decimal | None:
    if not returns:
        return None
    equity = Decimal("1")
    for value in returns:
        equity *= Decimal("1") + value
    return equity - Decimal("1")


def _max_drawdown(returns: tuple[Decimal, ...]) -> Decimal | None:
    if not returns:
        return None
    equity = Decimal("1")
    peak = equity
    drawdown = Decimal("0")
    for value in returns:
        equity *= Decimal("1") + value
        if equity > peak:
            peak = equity
        elif peak > 0:
            drawdown = max(drawdown, (peak - equity) / peak)
    return drawdown


def _trade_sort_key(trade: Trade) -> tuple[str, str, str]:
    return (trade.exit_session, trade.entry_session, trade.signal_id)
