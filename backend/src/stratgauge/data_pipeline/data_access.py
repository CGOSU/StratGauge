"""Explicit local-cache versus remote-fetch decisions."""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict

from stratgauge.domain.models import DomainModel


class DataAccessDecision(StrEnum):
    CACHE_HIT = "cache_hit"
    CACHE_MISS = "cache_miss"
    LOCAL_ONLY = "local_only"
    REMOTE_FETCH = "remote_fetch"
    FORCED_REFRESH = "forced_refresh"
    BLOCKED = "blocked"


class DataAccessPlan(DomainModel):
    """Auditable decision made before a data request is executed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_kind: str
    provider: str
    cache_key: str
    local_snapshot_id: str | None = None
    local_available: bool = False
    remote_allowed: bool = False
    decision: DataAccessDecision
    reason: str


def build_data_access_plan(
    *,
    request_kind: str,
    provider: str,
    cache_key: str,
    local_snapshot_id: str | None = None,
    remote_allowed: bool,
    force_refresh: bool = False,
) -> DataAccessPlan:
    """Create the single decision used by cache-aware pipeline steps."""

    local_available = local_snapshot_id is not None
    if force_refresh:
        decision = (
            DataAccessDecision.FORCED_REFRESH if remote_allowed else DataAccessDecision.BLOCKED
        )
        reason = (
            "forced refresh is allowed"
            if remote_allowed
            else "forced refresh requested but remote access is disabled"
        )
    elif local_available:
        decision = DataAccessDecision.CACHE_HIT
        reason = "approved local snapshot matches the request"
    elif remote_allowed:
        decision = DataAccessDecision.REMOTE_FETCH
        reason = "approved local snapshot is absent and remote access is allowed"
    else:
        decision = DataAccessDecision.LOCAL_ONLY
        reason = "approved local snapshot is absent and remote access is disabled"
    return DataAccessPlan(
        request_kind=request_kind,
        provider=provider,
        cache_key=cache_key,
        local_snapshot_id=local_snapshot_id,
        local_available=local_available,
        remote_allowed=remote_allowed,
        decision=decision,
        reason=reason,
    )
