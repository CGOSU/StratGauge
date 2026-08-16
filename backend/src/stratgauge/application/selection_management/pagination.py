"""Pagination helpers shared by selection APIs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stratgauge.domain.selections import (
    SelectionLifecycleEvent,
    SelectionLifecycleStatus,
)

if TYPE_CHECKING:
    pass


def validate_page(page: int, page_size: int) -> None:
    if page < 1:
        raise ValueError("page must be positive")
    if page_size < 1 or page_size > 200:
        raise ValueError("page_size must be between 1 and 200")


def page_slice[ItemT](
    items: tuple[ItemT, ...],
    page: int,
    page_size: int,
) -> tuple[tuple[ItemT, ...], int, bool]:
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end], total, end < total


def is_entity_active(events: tuple[SelectionLifecycleEvent, ...]) -> bool:
    return not events or events[-1].status is SelectionLifecycleStatus.ACTIVE
