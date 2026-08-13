"""Build purpose-specific, point-in-time datasets for selection research models."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore, PortfolioStoreError
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.domain.portfolio import ManualFill, ManualOrder, ManualOrderSide
from stratgauge.domain.selections import (
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
)

BUY_TIMING_DATASET_SCHEMA_VERSION = "selection-buy-timing-dataset-v1"
SELL_TIMING_DATASET_SCHEMA_VERSION = "selection-sell-timing-dataset-v1"


class SelectionBuyTimingRow(BaseModel):
    """One pre-buy annotation with a future purchase-window label."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = BUY_TIMING_DATASET_SCHEMA_VERSION
    annotation_id: str
    candidate_id: str
    batch_id: str
    symbol: str
    selection_date: date
    annotation_reviewed_at: datetime
    annotation_recorded_at: datetime
    overall_score: int = Field(ge=1, le=5)
    outlook: SelectionAnnotationOutlook
    decision: SelectionAnnotationDecision
    observation_end: date
    window_sessions: int = Field(gt=0)
    purchased_within_window: bool | None
    order_id: str | None = None
    fill_id: str | None = None
    filled_at: datetime | None = None
    fill_price: Decimal | None = None
    fill_quantity: Decimal | None = None
    source_order_ids: tuple[str, ...] = ()

    @field_validator("annotation_reviewed_at", "annotation_recorded_at")
    @classmethod
    def validate_aware_annotation_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("buy timing annotation timestamps must include a timezone")
        return value

    @field_validator("filled_at")
    @classmethod
    def validate_aware_fill_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("buy timing fill time must include a timezone")
        return value

    @field_validator("fill_price", "fill_quantity", mode="before")
    @classmethod
    def validate_fill_decimals(cls, value: Decimal | str | int | float | None) -> Decimal | None:
        if value is None:
            return None
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0:
            raise ValueError("buy timing fill decimals must be positive and finite")
        return result


class SelectionSellTimingRow(BaseModel):
    """One holding-session exposure with a future sale-window label."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SELL_TIMING_DATASET_SCHEMA_VERSION
    candidate_id: str
    batch_id: str
    symbol: str
    selection_date: date
    exposure_session: date
    evaluated_at: datetime
    sell_window_end: date
    window_sessions: int = Field(gt=0)
    sold_within_window: bool | None
    exposure_quantity: Decimal = Field(gt=0)
    average_cost: Decimal = Field(gt=0)
    buy_order_ids: tuple[str, ...]
    buy_fill_ids: tuple[str, ...]
    sell_order_ids: tuple[str, ...] = ()
    sell_fill_ids: tuple[str, ...] = ()

    @field_validator("evaluated_at")
    @classmethod
    def validate_aware_evaluated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("sell timing evaluated_at must include a timezone")
        return value

    @field_validator("exposure_quantity", "average_cost", mode="before")
    @classmethod
    def validate_position_decimals(cls, value: Decimal | str | int | float) -> Decimal:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0:
            raise ValueError("sell timing position decimals must be positive and finite")
        return result


def build_buy_timing_dataset(
    selection_store: SelectionImportStore,
    portfolio_store: ManualPortfolioStore,
    batch_id: str,
    *,
    trading_sessions: Sequence[date],
    evaluated_at: datetime,
    window_sessions: int = 3,
) -> tuple[SelectionBuyTimingRow, ...]:
    """Build pre-buy labels using an explicit trading-session calendar."""

    _validate_sessions(trading_sessions)
    _validate_aware(evaluated_at, "evaluated_at")
    if window_sessions <= 0:
        raise ValueError("window_sessions must be positive")
    imported = selection_store.load_import(batch_id)
    orders, fills = _portfolio_records(portfolio_store)
    rows: list[SelectionBuyTimingRow] = []
    for candidate in imported.candidates:
        candidate_orders = tuple(
            order
            for order in orders
            if order.selection_candidate_id == candidate.candidate_id
            and order.side is ManualOrderSide.BUY
        )
        candidate_fills = tuple(
            (order, fill)
            for order in candidate_orders
            for fill in fills
            if fill.order_id == order.order_id
        )
        for annotation in selection_store.list_annotations(candidate.candidate_id):
            if any(fill.filled_at <= annotation.recorded_at for _, fill in candidate_fills):
                continue
            window = _future_sessions(
                trading_sessions,
                after=annotation.reviewed_at.date(),
                count=window_sessions,
            )
            if not window:
                continue
            window_end = window[-1]
            matching_fill = _first_fill_in_window(
                candidate_fills,
                window,
            )
            known_matching_fill = (
                matching_fill
                if matching_fill is not None and matching_fill[1].filled_at <= evaluated_at
                else None
            )
            complete = evaluated_at.date() >= window_end
            rows.append(
                SelectionBuyTimingRow(
                    annotation_id=annotation.annotation_id,
                    candidate_id=candidate.candidate_id,
                    batch_id=batch_id,
                    symbol=candidate.symbol,
                    selection_date=imported.batch.selection_date,
                    annotation_reviewed_at=annotation.reviewed_at,
                    annotation_recorded_at=annotation.recorded_at,
                    overall_score=annotation.overall_score,
                    outlook=annotation.outlook,
                    decision=annotation.decision,
                    observation_end=window_end,
                    window_sessions=window_sessions,
                    purchased_within_window=(
                        known_matching_fill is not None
                        if complete or known_matching_fill is not None
                        else None
                    ),
                    order_id=known_matching_fill[0].order_id if known_matching_fill else None,
                    fill_id=known_matching_fill[1].fill_id if known_matching_fill else None,
                    filled_at=known_matching_fill[1].filled_at if known_matching_fill else None,
                    fill_price=known_matching_fill[1].price if known_matching_fill else None,
                    fill_quantity=known_matching_fill[1].quantity if known_matching_fill else None,
                    source_order_ids=tuple(order.order_id for order in candidate_orders),
                )
            )
    return tuple(sorted(rows, key=lambda row: (row.annotation_recorded_at, row.annotation_id)))


def build_sell_timing_dataset(
    selection_store: SelectionImportStore,
    portfolio_store: ManualPortfolioStore,
    batch_id: str,
    *,
    trading_sessions: Sequence[date],
    evaluated_at: datetime,
    window_sessions: int = 1,
) -> tuple[SelectionSellTimingRow, ...]:
    """Build one row per holding session and expose ambiguous unlinked sells."""

    _validate_sessions(trading_sessions)
    _validate_aware(evaluated_at, "evaluated_at")
    if window_sessions <= 0:
        raise ValueError("window_sessions must be positive")
    imported = selection_store.load_import(batch_id)
    orders, fills = _portfolio_records(portfolio_store)
    fills_by_order = {
        order.order_id: tuple(fill for fill in fills if fill.order_id == order.order_id)
        for order in orders
    }
    rows: list[SelectionSellTimingRow] = []
    for candidate in imported.candidates:
        linked_orders = tuple(
            order for order in orders if order.selection_candidate_id == candidate.candidate_id
        )
        buy_orders = tuple(order for order in linked_orders if order.side is ManualOrderSide.BUY)
        if not buy_orders:
            continue
        matching_symbol_orders = tuple(
            order for order in orders if order.security.symbol == candidate.symbol
        )
        if any(
            order.side is ManualOrderSide.SELL and order.selection_candidate_id is None
            for order in matching_symbol_orders
        ):
            raise ValueError(
                "unlinked sell order makes candidate sell timing ambiguous: "
                f"{candidate.candidate_id}"
            )
        candidate_fills = tuple(
            sorted(
                (
                    (order, fill)
                    for order in linked_orders
                    for fill in fills_by_order[order.order_id]
                ),
                key=lambda item: (item[1].filled_at, item[1].fill_id),
            )
        )
        buy_fill_ids = tuple(
            fill.fill_id for order, fill in candidate_fills if order.side is ManualOrderSide.BUY
        )
        buy_order_ids = tuple(order.order_id for order in buy_orders)
        first_buy_date = min(
            fill.filled_at.date()
            for order, fill in candidate_fills
            if order.side is ManualOrderSide.BUY
        )
        for exposure_session in trading_sessions:
            if exposure_session < first_buy_date or exposure_session > evaluated_at.date():
                continue
            quantity, cost = _position_at(candidate_fills, exposure_session)
            if quantity <= 0:
                continue
            future_sessions = _future_sessions(
                trading_sessions,
                after=exposure_session,
                count=window_sessions,
            )
            if not future_sessions:
                continue
            window_end = future_sessions[-1]
            future_sells = tuple(
                (order, fill)
                for order, fill in candidate_fills
                if order.side is ManualOrderSide.SELL
                and exposure_session < fill.filled_at.date() <= window_end
            )
            known_future_sells = tuple(
                item for item in future_sells if item[1].filled_at <= evaluated_at
            )
            complete = evaluated_at.date() >= window_end
            rows.append(
                SelectionSellTimingRow(
                    candidate_id=candidate.candidate_id,
                    batch_id=batch_id,
                    symbol=candidate.symbol,
                    selection_date=imported.batch.selection_date,
                    exposure_session=exposure_session,
                    evaluated_at=evaluated_at,
                    sell_window_end=window_end,
                    window_sessions=window_sessions,
                    sold_within_window=(
                        bool(known_future_sells) if complete or known_future_sells else None
                    ),
                    exposure_quantity=quantity,
                    average_cost=cost / quantity,
                    buy_order_ids=buy_order_ids,
                    buy_fill_ids=buy_fill_ids,
                    sell_order_ids=tuple(order.order_id for order, _ in known_future_sells),
                    sell_fill_ids=tuple(fill.fill_id for _, fill in known_future_sells),
                )
            )
    return tuple(sorted(rows, key=lambda row: (row.exposure_session, row.candidate_id)))


def read_buy_timing_dataset(content: bytes) -> tuple[SelectionBuyTimingRow, ...]:
    return _read_jsonl(content, BUY_TIMING_DATASET_SCHEMA_VERSION, SelectionBuyTimingRow)


def read_sell_timing_dataset(content: bytes) -> tuple[SelectionSellTimingRow, ...]:
    return _read_jsonl(content, SELL_TIMING_DATASET_SCHEMA_VERSION, SelectionSellTimingRow)


def serialize_buy_timing_dataset(rows: Iterable[SelectionBuyTimingRow]) -> str:
    return _serialize_jsonl(rows)


def serialize_sell_timing_dataset(rows: Iterable[SelectionSellTimingRow]) -> str:
    return _serialize_jsonl(rows)


def write_buy_timing_dataset(path: str | Path, rows: Iterable[SelectionBuyTimingRow]) -> None:
    _write_non_overwriting(path, serialize_buy_timing_dataset(rows))


def write_sell_timing_dataset(path: str | Path, rows: Iterable[SelectionSellTimingRow]) -> None:
    _write_non_overwriting(path, serialize_sell_timing_dataset(rows))


def _portfolio_records(
    store: ManualPortfolioStore,
) -> tuple[tuple[ManualOrder, ...], tuple[ManualFill, ...]]:
    try:
        return store.list_orders(), store.list_fills()
    except PortfolioStoreError:
        raise


def _validate_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")


def _validate_sessions(sessions: Sequence[date]) -> None:
    if not sessions:
        raise ValueError("trading_sessions must not be empty")
    if tuple(sessions) != tuple(sorted(set(sessions))):
        raise ValueError("trading_sessions must be unique and sorted")


def _future_sessions(sessions: Sequence[date], *, after: date, count: int) -> tuple[date, ...]:
    return tuple(session for session in sessions if session > after)[:count]


def _first_fill_in_window(
    fills: Sequence[tuple[ManualOrder, ManualFill]],
    window: Sequence[date],
) -> tuple[ManualOrder, ManualFill] | None:
    window_dates = set(window)
    candidates = tuple(item for item in fills if item[1].filled_at.date() in window_dates)
    return (
        min(candidates, key=lambda item: (item[1].filled_at, item[1].fill_id))
        if candidates
        else None
    )


def _position_at(
    candidate_fills: Sequence[tuple[ManualOrder, ManualFill]],
    session: date,
) -> tuple[Decimal, Decimal]:
    quantity = Decimal("0")
    cost = Decimal("0")
    for order, fill in candidate_fills:
        if fill.filled_at.date() > session:
            continue
        if order.side is ManualOrderSide.BUY:
            quantity += fill.quantity
            cost += fill.quantity * fill.price + fill.commission + fill.stamp_duty
        else:
            if fill.quantity > quantity:
                raise ValueError("candidate-linked sell fills produce a negative position")
            average_cost = cost / quantity if quantity else Decimal("0")
            quantity -= fill.quantity
            cost -= average_cost * fill.quantity
            if quantity == 0:
                cost = Decimal("0")
    return quantity, cost


def _read_jsonl[
    RowT: BaseModel,
](content: bytes, schema_version: str, row_type: type[RowT]) -> tuple[RowT, ...]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("dataset is not valid UTF-8") from exc
    rows: list[RowT] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict) or payload.get("schema_version") != schema_version:
                raise ValueError("unsupported dataset schema")
            rows.append(row_type.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid dataset row at line {line_number}: {exc}") from exc
    return tuple(rows)


def _serialize_jsonl(rows: Iterable[BaseModel]) -> str:
    return "".join(f"{row.model_dump_json()}\n" for row in rows)


def _write_non_overwriting(path: str | Path, content: str) -> None:
    destination = Path(path)
    if destination.exists():
        raise FileExistsError(f"dataset already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
