"""Append-only local manual portfolio ledger."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel

from stratgauge._files import atomic_write_text
from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.domain.portfolio import (
    ManualFill,
    ManualOrder,
    ManualOrderSide,
    PortfolioPosition,
    PortfolioReview,
)


class PortfolioStoreError(RuntimeError):
    """A manual portfolio record cannot be safely persisted."""


class ManualPortfolioStore:
    """Persist orders, fills, and reviews; derive positions from fills."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.order_dir = self.root / "portfolio" / "orders"
        self.fill_dir = self.root / "portfolio" / "fills"
        self.review_dir = self.root / "portfolio" / "reviews"
        for directory in (self.order_dir, self.fill_dir, self.review_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def create_order(self, order: ManualOrder) -> ManualOrder:
        with local_write_lock(self.root):
            return self._create_order_unlocked(order)

    def _create_order_unlocked(self, order: ManualOrder) -> ManualOrder:
        path = self.order_dir / f"{order.order_id}.json"
        if path.exists():
            existing = self._load_order(order.order_id)
            if existing != order:
                raise PortfolioStoreError("order identity already has different content")
            return existing
        _write_portfolio_record(path, order.model_dump_json(indent=2))
        return order

    def record_order_and_fill(
        self,
        order: ManualOrder,
        fill: ManualFill,
    ) -> tuple[ManualOrder, ManualFill]:
        with local_write_lock(self.root):
            return self._record_order_and_fill_unlocked(order, fill)

    def _record_order_and_fill_unlocked(
        self,
        order: ManualOrder,
        fill: ManualFill,
    ) -> tuple[ManualOrder, ManualFill]:
        if fill.order_id != order.order_id:
            raise PortfolioStoreError("fill must reference the submitted order")
        if fill.filled_at < order.ordered_at:
            raise PortfolioStoreError("fill cannot precede order")
        if fill.quantity > order.quantity:
            raise PortfolioStoreError("fill exceeds order quantity")
        order_path = self.order_dir / f"{order.order_id}.json"
        fill_path = self.fill_dir / f"{fill.fill_id}.json"
        existing_order = self._load_order(order.order_id) if order_path.exists() else None
        existing_fill = self._load_fill(fill.fill_id) if fill_path.exists() else None
        if existing_order is not None and existing_order != order:
            raise PortfolioStoreError("order identity already has different content")
        if existing_fill is not None and existing_fill != fill:
            raise PortfolioStoreError("fill identity already has different content")
        if existing_order is not None and existing_fill is not None:
            return existing_order, existing_fill
        if existing_order is not None or existing_fill is not None:
            raise PortfolioStoreError("atomic candidate trade has a partial existing record")
        if order.side is ManualOrderSide.SELL:
            held = self._quantity_for_symbol(order.security.symbol)
            if fill.quantity > held:
                raise PortfolioStoreError("sell fill exceeds current position")
        _write_portfolio_record(order_path, order.model_dump_json(indent=2))
        try:
            _write_portfolio_record(fill_path, fill.model_dump_json(indent=2))
        except Exception:
            order_path.unlink(missing_ok=True)
            raise
        return order, fill

    def record_fill(self, fill: ManualFill) -> ManualFill:
        with local_write_lock(self.root):
            return self._record_fill_unlocked(fill)

    def _record_fill_unlocked(self, fill: ManualFill) -> ManualFill:
        order = self._load_order(fill.order_id)
        path = self.fill_dir / f"{fill.fill_id}.json"
        if path.exists():
            existing = self._load_fill(fill.fill_id)
            if existing != fill:
                raise PortfolioStoreError("fill identity already has different content")
            return existing
        if fill.filled_at < order.ordered_at:
            raise PortfolioStoreError("fill cannot precede order")
        existing_fills = self.list_fills(order_id=order.order_id)
        filled_quantity = sum((item.quantity for item in existing_fills), Decimal("0"))
        if filled_quantity + fill.quantity > order.quantity:
            raise PortfolioStoreError("fills exceed order quantity")
        if order.side is ManualOrderSide.SELL:
            held = self._quantity_for_symbol(order.security.symbol)
            if fill.quantity > held:
                raise PortfolioStoreError("sell fill exceeds current position")
        _write_portfolio_record(path, fill.model_dump_json(indent=2))
        return fill

    def write_review(self, review: PortfolioReview) -> PortfolioReview:
        with local_write_lock(self.root):
            return self._write_review_unlocked(review)

    def _write_review_unlocked(self, review: PortfolioReview) -> PortfolioReview:
        path = self.review_dir / f"{review.review_id}.json"
        if path.exists():
            existing = self._load_review(review.review_id)
            if existing != review:
                raise PortfolioStoreError("review identity already has different content")
            return existing
        _write_portfolio_record(path, review.model_dump_json(indent=2))
        return review

    def list_orders(self) -> tuple[ManualOrder, ...]:
        return tuple(
            sorted(
                (self._load_order(path.stem) for path in self.order_dir.glob("*.json")),
                key=lambda order: (order.ordered_at, order.order_id),
            )
        )

    def list_fills(self, *, order_id: str | None = None) -> tuple[ManualFill, ...]:
        fills = tuple(self._load_fill(path.stem) for path in self.fill_dir.glob("*.json"))
        if order_id is not None:
            fills = tuple(fill for fill in fills if fill.order_id == order_id)
        return tuple(sorted(fills, key=lambda fill: (fill.filled_at, fill.fill_id)))

    def list_reviews(self, *, symbol: str | None = None) -> tuple[PortfolioReview, ...]:
        reviews = tuple(self._load_review(path.stem) for path in self.review_dir.glob("*.json"))
        if symbol is not None:
            reviews = tuple(review for review in reviews if review.symbol == symbol)
        return tuple(sorted(reviews, key=lambda review: (review.reviewed_at, review.review_id)))

    def positions(self) -> tuple[PortfolioPosition, ...]:
        orders = {order.order_id: order for order in self.list_orders()}
        quantity_by_symbol: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        cost_by_symbol: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        security_by_symbol = {order.security.symbol: order.security for order in orders.values()}
        last_fill_at: dict[str, datetime] = {}
        for fill in self.list_fills():
            order = orders.get(fill.order_id)
            if order is None:
                raise PortfolioStoreError(f"fill references missing order: {fill.order_id}")
            symbol = order.security.symbol
            quantity = quantity_by_symbol[symbol]
            cost = cost_by_symbol[symbol]
            buy_cost = fill.quantity * fill.price + fill.commission + fill.stamp_duty
            if order.side is ManualOrderSide.BUY:
                quantity += fill.quantity
                cost += buy_cost
            else:
                if fill.quantity > quantity:
                    raise PortfolioStoreError("sell fills produce a negative position")
                average_cost = cost / quantity if quantity else Decimal("0")
                quantity -= fill.quantity
                cost -= average_cost * fill.quantity
                if quantity == 0:
                    cost = Decimal("0")
            quantity_by_symbol[symbol] = quantity
            cost_by_symbol[symbol] = cost
            last_fill_at[symbol] = fill.filled_at

        positions: list[PortfolioPosition] = []
        for symbol, quantity in sorted(quantity_by_symbol.items()):
            security = security_by_symbol[symbol]
            cost = cost_by_symbol[symbol]
            average_cost = cost / quantity if quantity else Decimal("0")
            positions.append(
                PortfolioPosition(
                    security=security,
                    quantity=quantity,
                    average_cost=average_cost,
                    invested_notional=cost,
                    last_fill_at=last_fill_at.get(symbol),
                )
            )
        return tuple(positions)

    def _quantity_for_symbol(self, symbol: str) -> Decimal:
        for position in self.positions():
            if position.security.symbol == symbol:
                return position.quantity
        return Decimal("0")

    def _load_order(self, order_id: str) -> ManualOrder:
        return _load(self.order_dir / f"{order_id}.json", ManualOrder, "order")

    def _load_fill(self, fill_id: str) -> ManualFill:
        return _load(self.fill_dir / f"{fill_id}.json", ManualFill, "fill")

    def _load_review(self, review_id: str) -> PortfolioReview:
        return _load(self.review_dir / f"{review_id}.json", PortfolioReview, "review")


def _load[ModelT: BaseModel](path: Path, model: type[ModelT], label: str) -> ModelT:
    if not path.exists():
        raise PortfolioStoreError(f"{label} does not exist: {path.stem}")
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PortfolioStoreError(f"{label} is invalid: {path.stem}") from exc


def _write_portfolio_record(path: Path, content: str) -> None:
    try:
        atomic_write_text(path, content)
    except OSError as exc:
        raise PortfolioStoreError(f"failed to write portfolio record: {path.name}") from exc
