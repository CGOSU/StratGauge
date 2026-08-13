"""API for explicit main-board market close synchronization."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.data_pipeline.adjustment_factor_sync import sync_adjustment_factor_session
from stratgauge.data_pipeline.market_daily_sync import sync_market_sessions
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.universe import UniverseSnapshotStore
from stratgauge.data_sources.tushare import TushareAdapter


class MarketSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_date: date
    minimum_coverage: Decimal = Field(default=Decimal("0.90"), ge=0, le=1)
    lookback_days: int = Field(default=10, ge=1, le=366)

    @field_validator("session_date")
    @classmethod
    def require_session_date(cls, value: date) -> date:
        if value > datetime.now(UTC).date():
            raise ValueError("session_date cannot be in the future")
        return value


class MarketSyncSessionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    market_snapshot_id: str
    market_status: SnapshotStatus
    adjustment_factor_snapshot_id: str | None
    adjustment_factor_status: SnapshotStatus | None
    skipped: bool


class MarketSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_through: date
    universe_snapshot_id: str
    security_count: int = Field(ge=1)
    sessions: tuple[MarketSyncSessionResult, ...]


def sync_main_board_close(
    request: MarketSyncRequest,
    *,
    market_root: str | Path,
    universe_root: str | Path,
) -> MarketSyncResponse:
    """Synchronize the missing weekday sessions through an explicit date.

    The universe snapshot is the board-membership boundary. We reject BJ and
    any other exchange rather than silently broadening the requested pool.
    """

    # Do not make the market sync unusable when the local calendar cache ends
    # before the current session. Tushare is the source of truth for whether
    # this explicit date has rows; its quality gate rejects empty/non-session
    # responses. The cached calendar remains useful for historical batch jobs.

    universe_store = UniverseSnapshotStore(universe_root)
    universe = universe_store.latest_at(
        datetime.combine(request.session_date, datetime.min.time(), tzinfo=UTC)
    )
    if universe is None:
        raise ValueError("no universe snapshot is available for the requested session")
    if not universe.securities or any(
        not _is_main_board_security(security.exchange.value, security.local_code)
        for security in universe.securities
    ):
        raise ValueError("the selected universe is not an SH/SZ main-board universe")

    market_store = SnapshotStore(market_root)
    adapter = TushareAdapter()
    start_date = max(
        request.session_date - timedelta(days=request.lookback_days),
        universe.as_of.date(),
    )
    candidate_dates = tuple(
        start_date + timedelta(days=offset)
        for offset in range((request.session_date - start_date).days + 1)
        if (start_date + timedelta(days=offset)).weekday() < 5
    )
    session_dates = tuple(
        session_date
        for session_date in candidate_dates
        if market_store.find_approved_market_snapshot(
            provider=adapter.provider,
            session_date=session_date,
            universe_snapshot_id=universe.universe_snapshot_id,
        )
        is None
    )
    if not session_dates:
        existing = market_store.find_approved_market_snapshot(
            provider=adapter.provider,
            session_date=request.session_date,
            universe_snapshot_id=universe.universe_snapshot_id,
        )
        if existing is None:
            raise ValueError(
                "no missing market sessions found in the recent lookback window; "
                "requested date has no approved snapshot"
            )
        factor = market_store.find_approved_adjustment_factor_snapshot(
            provider=adapter.provider,
            session_date=request.session_date,
            universe_snapshot_id=universe.universe_snapshot_id,
            source_daily_snapshot_id=existing.snapshot_id,
        )
        return MarketSyncResponse(
            requested_through=request.session_date,
            universe_snapshot_id=universe.universe_snapshot_id,
            security_count=len(universe.securities),
            sessions=(
                MarketSyncSessionResult(
                    session_date=request.session_date,
                    market_snapshot_id=existing.snapshot_id,
                    market_status=existing.status,
                    adjustment_factor_snapshot_id=factor.snapshot_id if factor else None,
                    adjustment_factor_status=factor.status if factor else None,
                    skipped=True,
                ),
            ),
        )

    results = sync_market_sessions(
        adapter=adapter,
        session_dates=session_dates,
        universe=universe,
        store=market_store,
        minimum_security_coverage=request.minimum_coverage,
    )
    session_results: list[MarketSyncSessionResult] = []
    for result in results:
        factor_result = None
        if result.status is SnapshotStatus.APPROVED:
            factor_result = sync_adjustment_factor_session(
                adapter=adapter,
                session_date=result.session_date,
                universe=universe,
                source_daily_snapshot_id=result.snapshot_id,
                store=market_store,
                minimum_security_coverage=request.minimum_coverage,
            )
        session_results.append(
            MarketSyncSessionResult(
                session_date=result.session_date,
                market_snapshot_id=result.snapshot_id,
                market_status=result.status,
                adjustment_factor_snapshot_id=factor_result.snapshot_id if factor_result else None,
                adjustment_factor_status=factor_result.status if factor_result else None,
                skipped=result.skipped,
            )
        )
    return MarketSyncResponse(
        requested_through=request.session_date,
        universe_snapshot_id=universe.universe_snapshot_id,
        security_count=len(universe.securities),
        sessions=tuple(session_results),
    )


def _is_main_board_security(exchange: str, local_code: str) -> bool:
    if exchange == "SH":
        return local_code.startswith(("600", "601", "603", "605"))
    if exchange == "SZ":
        return local_code.startswith(("000", "001", "002", "003"))
    return False
