"""Deterministic entry-window construction for after-close signals."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from stratgauge.backtest.models import EntryExecutionPolicy


class EntryWindowError(ValueError):
    """The supplied trading sessions cannot build an entry window."""


def build_entry_window(
    *,
    as_of: date,
    trading_sessions: Sequence[date],
    policy: EntryExecutionPolicy,
) -> tuple[date, date]:
    """Return the first and last valid sessions after an after-close signal."""

    sessions = tuple(sorted({session for session in trading_sessions if session > as_of}))
    if len(sessions) < policy.validity_sessions:
        raise EntryWindowError(
            f"entry window requires {policy.validity_sessions} sessions after {as_of}"
        )
    return sessions[0], sessions[policy.validity_sessions - 1]
