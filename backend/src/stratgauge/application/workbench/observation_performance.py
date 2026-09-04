"""Pure daily percentage-performance projection for one observation cycle."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from stratgauge.application.workbench.stock_performance import SHANGHAI
from stratgauge.application.workbench.views import (
    MarketEvidenceStatus,
    WorkbenchObservationPerformanceHistory,
    WorkbenchObservationPerformancePoint,
)
from stratgauge.domain.models import DailyBar


def build_observation_performance_history(
    *,
    candidate_id: str,
    symbol: str,
    started_at: datetime | None,
    as_of: datetime,
    market_bars: dict[date, tuple[str, DailyBar]],
    market_session_dates: Iterable[date],
    invalid_snapshot_ids: Iterable[str] = (),
) -> WorkbenchObservationPerformanceHistory:
    """Project one continuous observation cycle without inventing missing closes."""

    if started_at is None:
        return WorkbenchObservationPerformanceHistory(
            candidate_id=candidate_id,
            symbol=symbol,
            as_of=as_of,
            status=MarketEvidenceStatus.UNAVAILABLE,
            reason="没有当前连续观察周期。",
        )

    visible_date = as_of.astimezone(SHANGHAI).date()
    invalid_ids = tuple(sorted(set(invalid_snapshot_ids)))
    anchor_candidates = tuple(
        (session_date, snapshot_id, bar)
        for session_date, (snapshot_id, bar) in market_bars.items()
        if session_date <= started_at.astimezone(SHANGHAI).date()
        and bar.available_at <= started_at
        and bar.available_at <= as_of
    )
    if not anchor_candidates:
        return WorkbenchObservationPerformanceHistory(
            candidate_id=candidate_id,
            symbol=symbol,
            as_of=as_of,
            status=(
                MarketEvidenceStatus.INCOMPLETE if invalid_ids else MarketEvidenceStatus.UNAVAILABLE
            ),
            started_at=started_at,
            reason=(
                "已批准行情快照无法读取：" + ", ".join(invalid_ids)
                if invalid_ids
                else "没有在观察开始时可见的已批准 market_daily 收盘价。"
            ),
        )

    anchor_date, anchor_snapshot_id, anchor_bar = max(anchor_candidates, key=lambda item: item[0])
    if anchor_bar.close == 0:
        return WorkbenchObservationPerformanceHistory(
            candidate_id=candidate_id,
            symbol=symbol,
            as_of=as_of,
            status=MarketEvidenceStatus.INCOMPLETE,
            started_at=started_at,
            anchor_snapshot_id=anchor_snapshot_id,
            anchor_session_date=anchor_date,
            anchor_available_at=anchor_bar.available_at,
            anchor_close=anchor_bar.close,
            reason="观察开始收盘价为 0，无法计算观察期涨跌幅。",
        )

    session_dates = {
        session_date
        for session_date in market_session_dates
        if anchor_date <= session_date <= visible_date
    }
    session_dates.update(
        session_date for session_date in market_bars if anchor_date <= session_date <= visible_date
    )
    points: list[WorkbenchObservationPerformancePoint] = []
    previous_close = None
    previous_point_complete = True
    for session_date in sorted(session_dates):
        evidence = market_bars.get(session_date)
        if evidence is None:
            reason = (
                "已批准行情快照无法读取：" + ", ".join(invalid_ids)
                if invalid_ids
                else f"{session_date.isoformat()} 缺少该证券的 approved market_daily 行情。"
            )
            points.append(
                WorkbenchObservationPerformancePoint(
                    session_date=session_date,
                    status=MarketEvidenceStatus.INCOMPLETE,
                    reason=reason,
                )
            )
            previous_close = None
            previous_point_complete = False
            continue
        snapshot_id, bar = evidence
        if bar.available_at > as_of:
            points.append(
                WorkbenchObservationPerformancePoint(
                    session_date=session_date,
                    status=MarketEvidenceStatus.INCOMPLETE,
                    market_snapshot_id=snapshot_id,
                    available_at=bar.available_at,
                    close=bar.close,
                    reason="该交易日已批准行情在当前 as_of 时点尚不可用。",
                )
            )
            previous_close = None
            previous_point_complete = False
            continue
        cumulative_return = (bar.close - anchor_bar.close) / anchor_bar.close
        if session_date == anchor_date:
            points.append(
                WorkbenchObservationPerformancePoint(
                    session_date=session_date,
                    status=MarketEvidenceStatus.AVAILABLE,
                    market_snapshot_id=snapshot_id,
                    available_at=bar.available_at,
                    close=bar.close,
                    cumulative_return=cumulative_return,
                )
            )
            previous_close = bar.close
            previous_point_complete = True
            continue
        if previous_close is None or not previous_point_complete:
            points.append(
                WorkbenchObservationPerformancePoint(
                    session_date=session_date,
                    status=MarketEvidenceStatus.INCOMPLETE,
                    market_snapshot_id=snapshot_id,
                    available_at=bar.available_at,
                    close=bar.close,
                    cumulative_return=cumulative_return,
                    reason="上一交易日行情证据缺失，日涨跌幅保持为空。",
                )
            )
            previous_close = bar.close
            previous_point_complete = True
            continue
        if previous_close == 0:
            points.append(
                WorkbenchObservationPerformancePoint(
                    session_date=session_date,
                    status=MarketEvidenceStatus.INCOMPLETE,
                    market_snapshot_id=snapshot_id,
                    available_at=bar.available_at,
                    close=bar.close,
                    cumulative_return=cumulative_return,
                    reason="上一交易日收盘价为 0，日涨跌幅保持为空。",
                )
            )
            previous_close = bar.close
            previous_point_complete = True
            continue
        points.append(
            WorkbenchObservationPerformancePoint(
                session_date=session_date,
                status=MarketEvidenceStatus.AVAILABLE,
                market_snapshot_id=snapshot_id,
                available_at=bar.available_at,
                close=bar.close,
                daily_return=(bar.close - previous_close) / previous_close,
                cumulative_return=cumulative_return,
            )
        )
        previous_close = bar.close
        previous_point_complete = True

    incomplete_points = tuple(
        point for point in points if point.status is not MarketEvidenceStatus.AVAILABLE
    )
    latest = next((point for point in reversed(points) if point.close is not None), None)
    return WorkbenchObservationPerformanceHistory(
        candidate_id=candidate_id,
        symbol=symbol,
        as_of=as_of,
        status=(
            MarketEvidenceStatus.INCOMPLETE
            if incomplete_points or invalid_ids
            else MarketEvidenceStatus.AVAILABLE
        ),
        started_at=started_at,
        anchor_snapshot_id=anchor_snapshot_id,
        anchor_session_date=anchor_date,
        anchor_available_at=anchor_bar.available_at,
        anchor_close=anchor_bar.close,
        latest_session_date=latest.session_date if latest is not None else None,
        latest_daily_return=latest.daily_return if latest is not None else None,
        latest_cumulative_return=latest.cumulative_return if latest is not None else None,
        points=tuple(points),
        reason=(
            incomplete_points[0].reason
            if incomplete_points
            else ("已批准行情快照无法读取：" + ", ".join(invalid_ids) if invalid_ids else None)
        ),
    )
