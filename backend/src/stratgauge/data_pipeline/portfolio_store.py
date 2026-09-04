"""Append-only local manual portfolio ledger."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel

from stratgauge._files import atomic_write_text
from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.domain.models import Security
from stratgauge.domain.portfolio import (
    MANUAL_POSITION_TRACKING_POLICY_ID,
    ManualFill,
    ManualOrder,
    ManualOrderSide,
    ManualPositionTrackingCase,
    ManualPositionTrackingOrigin,
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
        self.tracking_case_dir = self.root / "portfolio" / "tracking_cases"
        for directory in (
            self.order_dir,
            self.fill_dir,
            self.review_dir,
            self.tracking_case_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def create_order(self, order: ManualOrder) -> ManualOrder:
        with local_write_lock(self.root):
            self._validate_order_tracking_unlocked(order)
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
        *,
        auto_track_manual_buy: bool = False,
        auto_resolve_manual_sell: bool = False,
    ) -> tuple[ManualOrder, ManualFill]:
        with local_write_lock(self.root):
            return self._record_order_and_fill_unlocked(
                order,
                fill,
                auto_track_manual_buy=auto_track_manual_buy,
                auto_resolve_manual_sell=auto_resolve_manual_sell,
            )

    def _record_order_and_fill_unlocked(
        self,
        order: ManualOrder,
        fill: ManualFill,
        *,
        auto_track_manual_buy: bool,
        auto_resolve_manual_sell: bool,
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
        if existing_order is not None and existing_fill is not None:
            existing_case_id = existing_order.tracking_case_id or existing_fill.tracking_case_id
            if order.tracking_case_id is None and existing_case_id is not None:
                order = order.model_copy(update={"tracking_case_id": existing_case_id})
            if fill.tracking_case_id is None and existing_case_id is not None:
                fill = fill.model_copy(update={"tracking_case_id": existing_case_id})
            if existing_order != order:
                raise PortfolioStoreError("order identity already has different content")
            if existing_fill != fill:
                raise PortfolioStoreError("fill identity already has different content")
            return existing_order, existing_fill
        if existing_order is not None or existing_fill is not None:
            raise PortfolioStoreError("atomic candidate trade has a partial existing record")

        order, fill, new_case = self._prepare_trade_tracking_unlocked(
            order,
            fill,
            auto_track_manual_buy=auto_track_manual_buy,
            auto_resolve_manual_sell=auto_resolve_manual_sell,
        )
        self._validate_trade_tracking_unlocked(order, fill, new_case=new_case)
        if order.side is ManualOrderSide.SELL:
            held = self._quantity_for_symbol(order.security.symbol)
            if fill.quantity > held:
                raise PortfolioStoreError("sell fill exceeds current position")

        case_written = False
        try:
            if new_case is not None:
                self._create_tracking_case_unlocked(new_case)
                case_written = True
            _write_portfolio_record(order_path, order.model_dump_json(indent=2))
            _write_portfolio_record(fill_path, fill.model_dump_json(indent=2))
        except Exception:
            order_path.unlink(missing_ok=True)
            if case_written and new_case is not None:
                self._tracking_case_path(new_case.tracking_case_id).unlink(missing_ok=True)
            raise
        return order, fill

    def _create_tracking_case_unlocked(
        self,
        case: ManualPositionTrackingCase,
    ) -> ManualPositionTrackingCase:
        path = self._tracking_case_path(case.tracking_case_id)
        if path.exists():
            existing = self._load_tracking_case(case.tracking_case_id)
            if existing != case:
                raise PortfolioStoreError("tracking case identity already has different content")
            return existing
        _write_portfolio_record(path, case.model_dump_json(indent=2))
        return case

    def _enroll_position_unlocked(
        self,
        security: Security,
        *,
        tracking_case_id: str | None,
        tracking_started_at: datetime,
    ) -> ManualPositionTrackingCase:
        requested_case_id = tracking_case_id.strip() if tracking_case_id is not None else None
        if requested_case_id == "":
            requested_case_id = None
        if requested_case_id is not None:
            existing = self._existing_tracking_case(requested_case_id)
            if existing is not None:
                self._validate_case_security(existing, security)
                return existing

        orders = self.list_orders()
        orders_by_id = {order.order_id: order for order in orders}
        existing_cases = self.list_tracking_cases()
        tracked_source_order_ids = {
            order_id for case in existing_cases for order_id in case.source_order_ids
        }
        tracked_source_fill_ids = {
            fill_id for case in existing_cases for fill_id in case.source_fill_ids
        }
        eligible_fills = tuple(
            fill
            for fill in self.list_fills()
            if (
                (order := orders_by_id.get(fill.order_id)) is not None
                and order.security.symbol == security.symbol
                and order.selection_candidate_id is None
                and order.tracking_case_id is None
                and fill.tracking_case_id is None
                and fill.order_id not in tracked_source_order_ids
                and fill.fill_id not in tracked_source_fill_ids
            )
        )
        quantity = sum(
            (
                fill.quantity
                if orders_by_id[fill.order_id].side is ManualOrderSide.BUY
                else -fill.quantity
                for fill in eligible_fills
            ),
            Decimal("0"),
        )
        if not eligible_fills or quantity <= 0:
            raise PortfolioStoreError(
                "no untracked manual position is available for explicit enrollment"
            )

        source_order_ids = tuple(sorted({fill.order_id for fill in eligible_fills}))
        source_fill_ids = tuple(sorted({fill.fill_id for fill in eligible_fills}))
        case_id = requested_case_id or _generated_enrollment_case_id(
            security.symbol,
            source_order_ids,
            source_fill_ids,
        )
        existing = self._existing_tracking_case(case_id)
        if existing is not None:
            self._validate_case_security(existing, security)
            return existing

        source_security = orders_by_id[source_order_ids[0]].security
        case = ManualPositionTrackingCase(
            tracking_case_id=case_id,
            tracking_policy_id=MANUAL_POSITION_TRACKING_POLICY_ID,
            security=source_security,
            tracking_started_at=tracking_started_at,
            created_at=datetime.now(UTC),
            origin=ManualPositionTrackingOrigin.HISTORICAL_ENROLLMENT,
            source_order_ids=source_order_ids,
            source_fill_ids=source_fill_ids,
        )
        return self._create_tracking_case_unlocked(case)

    def _prepare_trade_tracking_unlocked(
        self,
        order: ManualOrder,
        fill: ManualFill,
        *,
        auto_track_manual_buy: bool,
        auto_resolve_manual_sell: bool,
    ) -> tuple[ManualOrder, ManualFill, ManualPositionTrackingCase | None]:
        case_id = _merge_tracking_case_id(order, fill)
        if order.selection_candidate_id is not None and case_id is not None:
            raise PortfolioStoreError(
                "selection candidate trades cannot reference manual tracking cases"
            )
        new_case: ManualPositionTrackingCase | None = None
        if (
            auto_track_manual_buy
            and order.side is ManualOrderSide.BUY
            and order.selection_candidate_id is None
        ):
            if case_id is None:
                active_cases = self._active_tracking_cases_unlocked(order.security.symbol)
                if len(active_cases) > 1:
                    raise PortfolioStoreError(
                        "multiple active tracking cases require an explicit tracking case"
                    )
                case_id = active_cases[0].tracking_case_id if active_cases else None
            if case_id is None:
                case_id = _generated_trade_case_id(order.order_id)
            existing_case = self._existing_tracking_case(case_id)
            if existing_case is None:
                new_case = ManualPositionTrackingCase(
                    tracking_case_id=case_id,
                    tracking_policy_id=MANUAL_POSITION_TRACKING_POLICY_ID,
                    security=order.security,
                    tracking_started_at=fill.filled_at,
                    created_at=datetime.now(UTC),
                    origin=ManualPositionTrackingOrigin.CONFIRMED_TRADE,
                    source_order_ids=(order.order_id,),
                    source_fill_ids=(fill.fill_id,),
                )
            else:
                self._validate_case_security(existing_case, order.security)
                if self._tracking_case_quantity_unlocked(existing_case) <= 0:
                    raise PortfolioStoreError("tracking case is closed")
        elif (
            auto_resolve_manual_sell
            and order.side is ManualOrderSide.SELL
            and order.selection_candidate_id is None
            and case_id is None
        ):
            active_cases = self._active_tracking_cases_unlocked(order.security.symbol)
            if len(active_cases) == 1:
                case = active_cases[0]
                if self._tracking_case_quantity_unlocked(case) == self._quantity_for_symbol(
                    order.security.symbol
                ):
                    case_id = case.tracking_case_id

        if case_id is not None:
            order = order.model_copy(update={"tracking_case_id": case_id})
            fill = fill.model_copy(update={"tracking_case_id": case_id})
        return order, fill, new_case

    def _bind_fill_tracking_unlocked(
        self,
        order: ManualOrder,
        fill: ManualFill,
    ) -> ManualFill:
        case_id = _merge_tracking_case_id(order, fill)
        if order.selection_candidate_id is not None and case_id is not None:
            raise PortfolioStoreError(
                "selection candidate trades cannot reference manual tracking cases"
            )
        if case_id is None:
            return fill
        return fill.model_copy(update={"tracking_case_id": case_id})

    def _validate_order_tracking_unlocked(self, order: ManualOrder) -> None:
        case_id = order.tracking_case_id
        if case_id is None:
            return
        case = self._existing_tracking_case(case_id)
        if case is None:
            raise PortfolioStoreError("tracking case does not exist")
        self._validate_case_security(case, order.security)
        if self._tracking_case_quantity_unlocked(case) <= 0:
            raise PortfolioStoreError("tracking case is closed")
        if (
            order.side is ManualOrderSide.SELL
            and order.quantity > self._tracking_case_quantity_unlocked(case)
        ):
            raise PortfolioStoreError("sell order exceeds tracking case position")

    def _validate_trade_tracking_unlocked(
        self,
        order: ManualOrder,
        fill: ManualFill,
        *,
        new_case: ManualPositionTrackingCase | None = None,
    ) -> None:
        case_id = _merge_tracking_case_id(order, fill)
        if case_id is None:
            return
        case = new_case or self._existing_tracking_case(case_id)
        if case is None:
            raise PortfolioStoreError("tracking case does not exist")
        self._validate_case_security(case, order.security)
        if order.tracking_case_id is None and order.order_id not in case.source_order_ids:
            raise PortfolioStoreError("tracking case does not include the order")
        if order.side is ManualOrderSide.SELL:
            available = self._tracking_case_quantity_unlocked(case)
            if fill.quantity > available:
                raise PortfolioStoreError("sell fill exceeds tracking case position")
        elif new_case is None and self._tracking_case_quantity_unlocked(case) <= 0:
            raise PortfolioStoreError("tracking case is closed")

    def _active_tracking_cases_unlocked(
        self,
        symbol: str,
    ) -> tuple[ManualPositionTrackingCase, ...]:
        return tuple(
            case
            for case in self.list_tracking_cases(symbol=symbol)
            if self._tracking_case_quantity_unlocked(case) > 0
        )

    def _tracking_case_quantity_unlocked(
        self,
        case: ManualPositionTrackingCase,
    ) -> Decimal:
        orders = {order.order_id: order for order in self.list_orders()}
        quantity = Decimal("0")
        for fill in self.list_fills():
            order = orders.get(fill.order_id)
            if order is None:
                raise PortfolioStoreError(f"fill references missing order: {fill.order_id}")
            if order.security.symbol != case.security.symbol:
                continue
            if not _fill_belongs_to_case(case, order, fill):
                continue
            if order.side is ManualOrderSide.BUY:
                quantity += fill.quantity
            else:
                quantity -= fill.quantity
        return quantity

    def _existing_tracking_case(
        self,
        tracking_case_id: str,
    ) -> ManualPositionTrackingCase | None:
        path = self._tracking_case_path(tracking_case_id)
        return self._load_tracking_case(tracking_case_id) if path.exists() else None

    def _validate_case_security(
        self,
        case: ManualPositionTrackingCase,
        security: Security,
    ) -> None:
        if (
            case.security.symbol != security.symbol
            or case.security.local_code != security.local_code
            or case.security.exchange != security.exchange
        ):
            raise PortfolioStoreError("tracking case security does not match the trade")

    def create_tracking_case(
        self,
        case: ManualPositionTrackingCase,
    ) -> ManualPositionTrackingCase:
        with local_write_lock(self.root):
            return self._create_tracking_case_unlocked(case)

    def enroll_position(
        self,
        security: Security,
        *,
        tracking_case_id: str | None = None,
        tracking_started_at: datetime | None = None,
    ) -> ManualPositionTrackingCase:
        started_at = tracking_started_at or datetime.now(UTC)
        _require_aware_tracking_time(started_at)
        with local_write_lock(self.root):
            return self._enroll_position_unlocked(
                security,
                tracking_case_id=tracking_case_id,
                tracking_started_at=started_at,
            )

    def list_tracking_cases(
        self,
        *,
        symbol: str | None = None,
    ) -> tuple[ManualPositionTrackingCase, ...]:
        cases = tuple(
            self._load_tracking_case(path.stem) for path in self.tracking_case_dir.glob("*.json")
        )
        if symbol is not None:
            cases = tuple(case for case in cases if case.security.symbol == symbol)
        return tuple(
            sorted(cases, key=lambda case: (case.tracking_started_at, case.tracking_case_id))
        )

    def record_fill(self, fill: ManualFill) -> ManualFill:
        with local_write_lock(self.root):
            return self._record_fill_unlocked(fill)

    def _record_fill_unlocked(self, fill: ManualFill) -> ManualFill:
        order = self._load_order(fill.order_id)
        path = self.fill_dir / f"{fill.fill_id}.json"
        if path.exists():
            existing = self._load_fill(fill.fill_id)
            if fill.tracking_case_id is None and existing.tracking_case_id is not None:
                fill = fill.model_copy(update={"tracking_case_id": existing.tracking_case_id})
            if existing != fill:
                raise PortfolioStoreError("fill identity already has different content")
            return existing
        if fill.filled_at < order.ordered_at:
            raise PortfolioStoreError("fill cannot precede order")
        fill = self._bind_fill_tracking_unlocked(order, fill)
        existing_fills = self.list_fills(order_id=order.order_id)
        filled_quantity = sum((item.quantity for item in existing_fills), Decimal("0"))
        if filled_quantity + fill.quantity > order.quantity:
            raise PortfolioStoreError("fills exceed order quantity")
        self._validate_trade_tracking_unlocked(order, fill)
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

    def _load_tracking_case(self, tracking_case_id: str) -> ManualPositionTrackingCase:
        return _load(
            self._tracking_case_path(tracking_case_id),
            ManualPositionTrackingCase,
            "tracking case",
        )

    def _tracking_case_path(self, tracking_case_id: str) -> Path:
        normalized = tracking_case_id.strip()
        if not normalized or any(
            character in normalized for character in ("/", "\\", "\0", "\r", "\n")
        ):
            raise PortfolioStoreError("tracking case identifier contains an invalid path character")
        return self.tracking_case_dir / f"{normalized}.json"


def _merge_tracking_case_id(order: ManualOrder, fill: ManualFill) -> str | None:
    if (
        order.tracking_case_id is not None
        and fill.tracking_case_id is not None
        and order.tracking_case_id != fill.tracking_case_id
    ):
        raise PortfolioStoreError("order and fill reference different tracking cases")
    return order.tracking_case_id or fill.tracking_case_id


def _fill_belongs_to_case(
    case: ManualPositionTrackingCase,
    order: ManualOrder,
    fill: ManualFill,
) -> bool:
    return (
        fill.tracking_case_id == case.tracking_case_id
        or order.tracking_case_id == case.tracking_case_id
        or fill.fill_id in case.source_fill_ids
        or order.order_id in case.source_order_ids
    )


def _generated_trade_case_id(order_id: str) -> str:
    digest = hashlib.sha256(order_id.encode("utf-8")).hexdigest()[:20]
    return f"manual-tracking-case-{digest}"


def _generated_enrollment_case_id(
    symbol: str,
    source_order_ids: tuple[str, ...],
    source_fill_ids: tuple[str, ...],
) -> str:
    identity = {
        "source_fill_ids": source_fill_ids,
        "source_order_ids": source_order_ids,
        "symbol": symbol,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:20]
    return f"manual-tracking-case-{digest}"


def _require_aware_tracking_time(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PortfolioStoreError("tracking timestamp must include a timezone")


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
