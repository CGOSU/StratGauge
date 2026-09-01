"""Export isolated, point-in-time evidence for manual position tracking."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from stratgauge.data_pipeline.portfolio_store import ManualPortfolioStore
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore, SnapshotStoreError
from stratgauge.domain.models import DailyBar, Exchange
from stratgauge.domain.portfolio import (
    MANUAL_POSITION_TRACKING_POLICY_ID,
    ManualFill,
    ManualOrder,
    ManualOrderSide,
    ManualPositionTrackingCase,
    ManualSellReason,
    PortfolioReview,
)

MANUAL_POSITION_TRACKING_DATASET_SCHEMA_VERSION = "manual-position-tracking-dataset-v1"
MANUAL_POSITION_TRACKING_REPORT_SCHEMA_VERSION = "manual-position-tracking-report-v1"

ManualTrackingEvidenceType = Literal["order", "fill", "review", "market"]
ManualTrackingEvidenceSource = Literal[
    "manual_order",
    "manual_fill",
    "portfolio_review",
    "approved_market_daily",
]
ManualTrackingQualificationStatus = Literal["eligible", "not_eligible"]
ManualTrackingCoverageStatus = Literal["empty", "partial", "complete"]


class ManualPositionTrackingEvidenceRow(BaseModel):
    """One post-start fact that is safe to pass to an isolated evidence review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = MANUAL_POSITION_TRACKING_DATASET_SCHEMA_VERSION
    tracking_policy_id: str = MANUAL_POSITION_TRACKING_POLICY_ID
    tracking_policy_version: int = Field(default=1, gt=0)
    tracking_case_id: str
    security_id: str
    local_code: str
    symbol: str
    exchange: Exchange
    tracking_started_at: datetime
    evaluated_at: datetime
    evidence_type: ManualTrackingEvidenceType
    source: ManualTrackingEvidenceSource
    source_id: str
    event_at: datetime
    source_fact_sha256: str
    qualification_status: ManualTrackingQualificationStatus = "eligible"
    order_id: str | None = None
    fill_id: str | None = None
    review_id: str | None = None
    snapshot_id: str | None = None
    session_date: date | None = None
    event_time: datetime | None = None
    available_at: datetime | None = None
    side: ManualOrderSide | None = None
    sell_reason: ManualSellReason | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    commission: Decimal | None = None
    stamp_duty: Decimal | None = None
    note: str | None = None

    @field_validator("tracking_policy_id")
    @classmethod
    def validate_tracking_policy_id(cls, value: str) -> str:
        if value != MANUAL_POSITION_TRACKING_POLICY_ID:
            raise ValueError("only manual-position-tracking-v1 is supported")
        return value

    @field_validator("tracking_policy_version")
    @classmethod
    def validate_tracking_policy_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("only manual position tracking policy version 1 is supported")
        return value

    @field_validator(
        "tracking_started_at",
        "evaluated_at",
        "event_at",
        "event_time",
        "available_at",
    )
    @classmethod
    def validate_aware_times(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("manual tracking evidence timestamps must include a timezone")
        return value

    @field_validator("source_fact_sha256")
    @classmethod
    def validate_source_fact_sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("source_fact_sha256 must be a SHA-256 digest")
        return normalized

    @field_validator(
        "tracking_case_id",
        "security_id",
        "local_code",
        "symbol",
        "source_id",
    )
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("manual tracking evidence identifiers must not be empty")
        return normalized

    @field_validator("quantity", "price", "commission", "stamp_duty", mode="before")
    @classmethod
    def validate_decimals(cls, value: Decimal | str | int | float | None) -> Decimal | None:
        if value is None:
            return None
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("manual tracking evidence decimals must be finite")
        return result

    @model_validator(mode="after")
    def validate_evidence_window(self) -> ManualPositionTrackingEvidenceRow:
        started_at = self.tracking_started_at.astimezone(UTC)
        event_at = self.event_at.astimezone(UTC)
        evaluated_at = self.evaluated_at.astimezone(UTC)
        if evaluated_at < started_at:
            raise ValueError("evaluated_at cannot precede tracking_started_at")
        if event_at < started_at or event_at > evaluated_at:
            raise ValueError("evidence event must be within the tracking evaluation window")
        return self

    @property
    def occurred_at(self) -> datetime:
        """Compatibility name for consumers that call events occurrences."""

        return self.event_at

    @property
    def fact_sha256(self) -> str:
        """Short compatibility name for the immutable source fact digest."""

        return self.source_fact_sha256

    @property
    def ml_eligible(self) -> bool:
        return self.qualification_status == "eligible"


class ManualPositionTrackingExcludedEvidence(BaseModel):
    """A source fact deliberately excluded from the training evidence rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tracking_case_id: str
    evidence_type: ManualTrackingEvidenceType
    source_id: str
    source_fact_sha256: str
    event_at: datetime | None = None
    reason: str

    @field_validator("source_fact_sha256")
    @classmethod
    def validate_source_fact_sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("excluded source_fact_sha256 must be a SHA-256 digest")
        return normalized

    @field_validator("event_at")
    @classmethod
    def validate_event_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("excluded evidence event_at must include a timezone")
        return value

    @field_validator("tracking_case_id", "source_id", "reason")
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("excluded evidence text must not be empty")
        return normalized


class ManualPositionTrackingCaseReport(BaseModel):
    """Coverage and qualification decision for one immutable tracking case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tracking_policy_id: str = MANUAL_POSITION_TRACKING_POLICY_ID
    tracking_policy_version: int = Field(default=1, gt=0)
    tracking_case_id: str
    security_id: str
    local_code: str
    symbol: str
    exchange: Exchange
    tracking_started_at: datetime
    evaluated_at: datetime
    source_order_ids: tuple[str, ...] = ()
    source_fill_ids: tuple[str, ...] = ()
    evidence_row_count: int = Field(default=0, ge=0)
    order_count: int = Field(default=0, ge=0)
    fill_count: int = Field(default=0, ge=0)
    review_count: int = Field(default=0, ge=0)
    market_count: int = Field(default=0, ge=0)
    market_snapshot_ids: tuple[str, ...] = ()
    excluded_evidence_count: int = Field(default=0, ge=0)
    excluded_evidence_ids: tuple[str, ...] = ()
    missing_reasons: tuple[str, ...] = ()
    qualification_status: ManualTrackingQualificationStatus
    qualification_reasons: tuple[str, ...] = ()
    source_fact_sha256: str

    @field_validator("tracking_policy_id")
    @classmethod
    def validate_tracking_policy_id(cls, value: str) -> str:
        if value != MANUAL_POSITION_TRACKING_POLICY_ID:
            raise ValueError("only manual-position-tracking-v1 is supported")
        return value

    @field_validator("tracking_policy_version")
    @classmethod
    def validate_tracking_policy_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("only manual position tracking policy version 1 is supported")
        return value

    @field_validator("tracking_started_at", "evaluated_at")
    @classmethod
    def validate_aware_times(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("tracking case report timestamps must include a timezone")
        return value

    @field_validator("source_fact_sha256")
    @classmethod
    def validate_source_fact_sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("case source_fact_sha256 must be a SHA-256 digest")
        return normalized


class ManualPositionTrackingEvidenceReport(BaseModel):
    """Immutable coverage report kept separate from all four selection ML inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_version: str = MANUAL_POSITION_TRACKING_REPORT_SCHEMA_VERSION
    tracking_policy_id: str = MANUAL_POSITION_TRACKING_POLICY_ID
    tracking_policy_version: int = Field(default=1, gt=0)
    evaluated_at: datetime
    case_count: int = Field(default=0, ge=0)
    evidence_row_count: int = Field(default=0, ge=0)
    eligible_case_count: int = Field(default=0, ge=0)
    not_eligible_case_count: int = Field(default=0, ge=0)
    dataset_sha256: str
    coverage_status: ManualTrackingCoverageStatus
    ready: bool = False
    cases: tuple[ManualPositionTrackingCaseReport, ...] = ()
    excluded_evidence: tuple[ManualPositionTrackingExcludedEvidence, ...] = ()
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @field_validator("tracking_policy_id")
    @classmethod
    def validate_tracking_policy_id(cls, value: str) -> str:
        if value != MANUAL_POSITION_TRACKING_POLICY_ID:
            raise ValueError("only manual-position-tracking-v1 is supported")
        return value

    @field_validator("tracking_policy_version")
    @classmethod
    def validate_tracking_policy_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("only manual position tracking policy version 1 is supported")
        return value

    @field_validator("evaluated_at")
    @classmethod
    def validate_evaluated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("manual tracking report evaluated_at must include a timezone")
        return value

    @field_validator("dataset_sha256")
    @classmethod
    def validate_dataset_sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("dataset_sha256 must be a SHA-256 digest")
        return normalized

    @model_validator(mode="after")
    def validate_counts(self) -> ManualPositionTrackingEvidenceReport:
        if self.case_count != len(self.cases):
            raise ValueError("manual tracking report case_count does not match cases")
        if self.evidence_row_count != sum(case.evidence_row_count for case in self.cases):
            raise ValueError("manual tracking report evidence count does not match cases")
        if self.eligible_case_count + self.not_eligible_case_count != self.case_count:
            raise ValueError("manual tracking report qualification counts do not match cases")
        return self

    @property
    def source_dataset_sha256(self) -> str:
        return self.dataset_sha256


@dataclass(frozen=True, slots=True)
class ManualPositionTrackingDatasetExport:
    """Rows and their matching report, built from one read-only ledger view."""

    rows: tuple[ManualPositionTrackingEvidenceRow, ...]
    report: ManualPositionTrackingEvidenceReport


@dataclass(frozen=True, slots=True)
class _CollectedEvidence:
    rows: tuple[ManualPositionTrackingEvidenceRow, ...]
    cases: tuple[ManualPositionTrackingCaseReport, ...]
    excluded: tuple[ManualPositionTrackingExcludedEvidence, ...]


def build_manual_position_tracking_dataset(
    portfolio_store: ManualPortfolioStore,
    market_store: SnapshotStore | None = None,
    *,
    evaluated_at: datetime,
) -> tuple[ManualPositionTrackingEvidenceRow, ...]:
    """Read manual cases and return only post-start, case-linked evidence rows."""

    return _collect_evidence(portfolio_store, market_store, evaluated_at=evaluated_at).rows


def build_manual_position_tracking_evidence_report(
    portfolio_store: ManualPortfolioStore,
    market_store: SnapshotStore | None = None,
    *,
    evaluated_at: datetime,
) -> ManualPositionTrackingEvidenceReport:
    """Build a coverage report without training, scoring, registration, or writes."""

    return _build_export(portfolio_store, market_store, evaluated_at=evaluated_at).report


def build_manual_position_tracking_export(
    portfolio_store: ManualPortfolioStore,
    market_store: SnapshotStore | None = None,
    *,
    evaluated_at: datetime,
) -> ManualPositionTrackingDatasetExport:
    """Build rows and report together so the report hash covers the exact JSONL bytes."""

    return _build_export(portfolio_store, market_store, evaluated_at=evaluated_at)


def build_manual_position_tracking_report(
    portfolio_store: ManualPortfolioStore,
    market_store: SnapshotStore | None = None,
    *,
    evaluated_at: datetime,
) -> ManualPositionTrackingEvidenceReport:
    """Compatibility alias for the public report builder."""

    return build_manual_position_tracking_evidence_report(
        portfolio_store,
        market_store,
        evaluated_at=evaluated_at,
    )


def read_manual_position_tracking_dataset(
    content: bytes,
) -> tuple[ManualPositionTrackingEvidenceRow, ...]:
    """Parse UTF-8 JSONL and reject rows from another evidence schema."""

    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("manual tracking dataset is not valid UTF-8") from exc
    rows: list[ManualPositionTrackingEvidenceRow] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("dataset row must be a JSON object")
            if payload.get("schema_version") != MANUAL_POSITION_TRACKING_DATASET_SCHEMA_VERSION:
                raise ValueError("unsupported manual tracking dataset schema")
            rows.append(ManualPositionTrackingEvidenceRow.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid manual tracking dataset row at line {line_number}: {exc}"
            ) from exc
    return tuple(rows)


def serialize_manual_position_tracking_dataset(
    rows: Iterable[ManualPositionTrackingEvidenceRow],
) -> str:
    """Serialize the isolated evidence rows as deterministic JSONL."""

    return "".join(f"{row.model_dump_json()}\n" for row in rows)


def write_manual_position_tracking_dataset(
    path: str | Path,
    rows: Iterable[ManualPositionTrackingEvidenceRow],
) -> None:
    """Write evidence JSONL once and reject every later overwrite."""

    _write_exclusive(Path(path), serialize_manual_position_tracking_dataset(rows))


def read_manual_position_tracking_report(
    content: bytes,
) -> ManualPositionTrackingEvidenceReport:
    """Parse a versioned manual tracking coverage report."""

    try:
        return ManualPositionTrackingEvidenceReport.model_validate_json(content)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid manual tracking evidence report: {exc}") from exc


def serialize_manual_position_tracking_report(
    report: ManualPositionTrackingEvidenceReport,
) -> str:
    return report.model_dump_json(indent=2) + "\n"


def write_manual_position_tracking_report(
    path: str | Path,
    report: ManualPositionTrackingEvidenceReport,
) -> None:
    """Write the report once without replacing an existing report."""

    _write_exclusive(Path(path), serialize_manual_position_tracking_report(report))


def write_manual_position_tracking_evidence_report(
    path: str | Path,
    report: ManualPositionTrackingEvidenceReport,
) -> None:
    """Compatibility alias for the report writer."""

    write_manual_position_tracking_report(path, report)


def _build_export(
    portfolio_store: ManualPortfolioStore,
    market_store: SnapshotStore | None,
    *,
    evaluated_at: datetime,
) -> ManualPositionTrackingDatasetExport:
    collected = _collect_evidence(portfolio_store, market_store, evaluated_at=evaluated_at)
    rows = collected.rows
    content = serialize_manual_position_tracking_dataset(rows).encode("utf-8")
    report = _make_report(
        collected,
        evaluated_at=_aware_utc(evaluated_at, "evaluated_at"),
        dataset_sha256=hashlib.sha256(content).hexdigest(),
    )
    return ManualPositionTrackingDatasetExport(rows=rows, report=report)


def _collect_evidence(
    portfolio_store: ManualPortfolioStore,
    market_store: SnapshotStore | None,
    *,
    evaluated_at: datetime,
) -> _CollectedEvidence:
    cutoff = _aware_utc(evaluated_at, "evaluated_at")
    orders = portfolio_store.list_orders()
    fills = portfolio_store.list_fills()
    reviews = portfolio_store.list_reviews()
    cases = portfolio_store.list_tracking_cases()
    rows: list[ManualPositionTrackingEvidenceRow] = []
    case_reports: list[ManualPositionTrackingCaseReport] = []
    excluded: list[ManualPositionTrackingExcludedEvidence] = []
    for case in cases:
        case_rows, case_excluded, missing = _collect_case_evidence(
            case,
            orders=orders,
            fills=fills,
            reviews=reviews,
            all_cases=cases,
            market_store=market_store,
            evaluated_at=cutoff,
        )
        rows.extend(case_rows)
        excluded.extend(case_excluded)
        case_reports.append(_case_report(case, cutoff, case_rows, case_excluded, missing))
    rows.sort(
        key=lambda row: (row.event_at, row.tracking_case_id, row.evidence_type, row.source_id)
    )
    case_reports.sort(key=lambda report: (report.tracking_started_at, report.tracking_case_id))
    excluded.sort(
        key=lambda item: (
            item.event_at or datetime.min.replace(tzinfo=UTC),
            item.tracking_case_id,
            item.evidence_type,
            item.source_id,
        )
    )
    return _CollectedEvidence(tuple(rows), tuple(case_reports), tuple(excluded))


def _collect_case_evidence(
    case: ManualPositionTrackingCase,
    *,
    orders: tuple[ManualOrder, ...],
    fills: tuple[ManualFill, ...],
    reviews: tuple[PortfolioReview, ...],
    all_cases: tuple[ManualPositionTrackingCase, ...],
    market_store: SnapshotStore | None,
    evaluated_at: datetime,
) -> tuple[
    tuple[ManualPositionTrackingEvidenceRow, ...],
    tuple[ManualPositionTrackingExcludedEvidence, ...],
    tuple[str, ...],
]:
    started_at = _aware_utc(case.tracking_started_at, "tracking_started_at")
    rows: list[ManualPositionTrackingEvidenceRow] = []
    excluded: list[ManualPositionTrackingExcludedEvidence] = []
    missing: list[str] = []
    orders_by_id = {order.order_id: order for order in orders}
    fills_by_id = {fill.fill_id: fill for fill in fills}
    for source_order_id in case.source_order_ids:
        source_order = orders_by_id.get(source_order_id)
        if source_order is None:
            missing.append(f"tracking case source order does not exist: {source_order_id}")
        elif source_order.security.symbol != case.security.symbol:
            missing.append(
                f"tracking case source order has a different security: {source_order_id}"
            )
    for source_fill_id in case.source_fill_ids:
        source_fill = fills_by_id.get(source_fill_id)
        if source_fill is None:
            missing.append(f"tracking case source fill does not exist: {source_fill_id}")
        elif source_fill.order_id not in orders_by_id:
            missing.append(
                f"tracking case source fill references a missing order: {source_fill_id}"
            )
        elif orders_by_id[source_fill.order_id].security.symbol != case.security.symbol:
            missing.append(f"tracking case source fill has a different security: {source_fill_id}")

    for order in orders:
        if order.security.symbol != case.security.symbol:
            continue
        explicitly_linked = order.tracking_case_id == case.tracking_case_id
        source_linked = order.order_id in case.source_order_ids
        if not explicitly_linked and not source_linked:
            continue
        fact_hash = _fact_hash("manual_order", order.order_id, order)
        event_at = _aware_utc(order.ordered_at, "ordered_at")
        if (
            order.selection_candidate_id is not None
            or order.strategy_version_id is not None
            or order.recommendation_id is not None
        ):
            excluded.append(
                _excluded(
                    case,
                    "order",
                    order.order_id,
                    fact_hash,
                    event_at,
                    "order is associated with a candidate, strategy, or recommendation",
                )
            )
        elif event_at < started_at:
            excluded.append(
                _excluded(
                    case,
                    "order",
                    order.order_id,
                    fact_hash,
                    event_at,
                    "order occurred before tracking_started_at",
                )
            )
        elif event_at > evaluated_at:
            excluded.append(
                _excluded(
                    case,
                    "order",
                    order.order_id,
                    fact_hash,
                    event_at,
                    "order is after evaluated_at",
                )
            )
        elif not explicitly_linked:
            excluded.append(
                _excluded(
                    case,
                    "order",
                    order.order_id,
                    fact_hash,
                    event_at,
                    "post-start order is not explicitly linked to the tracking case",
                )
            )
        else:
            rows.append(_order_row(case, order, evaluated_at, fact_hash))

    for fill in fills:
        fill_order = orders_by_id.get(fill.order_id)
        if fill_order is None:
            if (
                fill.tracking_case_id != case.tracking_case_id
                and fill.fill_id not in case.source_fill_ids
            ):
                continue
            fact_hash = _fact_hash("manual_fill", fill.fill_id, fill)
            excluded.append(
                _excluded(
                    case,
                    "fill",
                    fill.fill_id,
                    fact_hash,
                    _aware_utc(fill.filled_at, "filled_at"),
                    "fill references a missing order",
                )
            )
            continue
        if fill_order.security.symbol != case.security.symbol:
            continue
        explicitly_linked = (
            fill.tracking_case_id == case.tracking_case_id
            or fill_order.tracking_case_id == case.tracking_case_id
        )
        source_linked = fill.fill_id in case.source_fill_ids
        if not explicitly_linked and not source_linked:
            continue
        fact_hash = _fact_hash("manual_fill", fill.fill_id, fill)
        event_at = _aware_utc(fill.filled_at, "filled_at")
        if (
            fill_order.selection_candidate_id is not None
            or fill_order.strategy_version_id is not None
            or fill_order.recommendation_id is not None
        ):
            excluded.append(
                _excluded(
                    case,
                    "fill",
                    fill.fill_id,
                    fact_hash,
                    event_at,
                    "fill belongs to an order associated with a candidate, "
                    "strategy, or recommendation",
                )
            )
        elif event_at < started_at:
            excluded.append(
                _excluded(
                    case,
                    "fill",
                    fill.fill_id,
                    fact_hash,
                    event_at,
                    "fill occurred before tracking_started_at",
                )
            )
        elif event_at > evaluated_at:
            excluded.append(
                _excluded(
                    case,
                    "fill",
                    fill.fill_id,
                    fact_hash,
                    event_at,
                    "fill is after evaluated_at",
                )
            )
        elif not explicitly_linked:
            excluded.append(
                _excluded(
                    case,
                    "fill",
                    fill.fill_id,
                    fact_hash,
                    event_at,
                    "post-start fill is not explicitly linked to the tracking case",
                )
            )
        else:
            rows.append(_fill_row(case, fill_order, fill, evaluated_at, fact_hash))

    symbol_cases = tuple(
        other for other in all_cases if other.security.symbol == case.security.symbol
    )
    for review in reviews:
        if review.symbol != case.security.symbol:
            continue
        event_at = _aware_utc(review.reviewed_at, "reviewed_at")
        fact_hash = _fact_hash("portfolio_review", review.review_id, review)
        if event_at < started_at:
            excluded.append(
                _excluded(
                    case,
                    "review",
                    review.review_id,
                    fact_hash,
                    event_at,
                    "review occurred before tracking_started_at",
                )
            )
        elif event_at > evaluated_at:
            excluded.append(
                _excluded(
                    case,
                    "review",
                    review.review_id,
                    fact_hash,
                    event_at,
                    "review is after evaluated_at",
                )
            )
        elif review.recommendation_id is not None:
            excluded.append(
                _excluded(
                    case,
                    "review",
                    review.review_id,
                    fact_hash,
                    event_at,
                    "review has a recommendation association",
                )
            )
        else:
            matching_cases = tuple(
                other
                for other in symbol_cases
                if _aware_utc(other.tracking_started_at, "tracking_started_at") <= event_at
                and event_at <= evaluated_at
            )
            if len(matching_cases) != 1:
                if case in matching_cases:
                    excluded.append(
                        _excluded(
                            case,
                            "review",
                            review.review_id,
                            fact_hash,
                            event_at,
                            "review cannot be assigned to one tracking case unambiguously",
                        )
                    )
                continue
            if matching_cases[0].tracking_case_id == case.tracking_case_id:
                rows.append(_review_row(case, review, evaluated_at, fact_hash))

    market_rows, market_missing = _market_rows(case, market_store, evaluated_at)
    rows.extend(market_rows)
    missing.extend(market_missing)
    if not any(row.evidence_type in {"order", "fill", "review"} for row in rows):
        missing.append("no post-start case-linked manual order, fill, or review evidence")
    qualification_status: ManualTrackingQualificationStatus = (
        "eligible" if _case_is_eligible(case, evaluated_at, rows) else "not_eligible"
    )
    qualified_rows = tuple(
        row.model_copy(update={"qualification_status": qualification_status}) for row in rows
    )
    return qualified_rows, tuple(excluded), tuple(dict.fromkeys(missing))


def _market_rows(
    case: ManualPositionTrackingCase,
    market_store: SnapshotStore | None,
    evaluated_at: datetime,
) -> tuple[tuple[ManualPositionTrackingEvidenceRow, ...], tuple[str, ...]]:
    if market_store is None:
        return (), ("no market store was provided",)
    try:
        manifests = tuple(
            sorted(
                (
                    manifest
                    for manifest in market_store.list_manifests(status=SnapshotStatus.APPROVED)
                    if manifest.status is SnapshotStatus.APPROVED
                    and manifest.request_kind == "market_daily"
                ),
                key=lambda item: (item.start_date, item.end_date, item.snapshot_id),
                reverse=True,
            )
        )
    except (OSError, SnapshotStoreError) as exc:
        return (), (f"approved market snapshots could not be listed: {exc}",)
    if not manifests:
        return (), ("no approved market_daily snapshot is available",)

    started_at = _aware_utc(case.tracking_started_at, "tracking_started_at")
    selected: dict[date, tuple[str, DailyBar]] = {}
    pre_start_count = 0
    future_count = 0
    load_failures: list[str] = []
    for manifest in manifests:
        try:
            bars = market_store.load_bars(manifest.snapshot_id, symbols=(case.security.symbol,))
        except SnapshotStoreError as exc:
            load_failures.append(f"{manifest.snapshot_id}: {exc}")
            continue
        for bar in bars:
            if bar.symbol != case.security.symbol:
                continue
            event_time = _aware_utc(bar.event_time, "event_time")
            available_at = _aware_utc(bar.available_at, "available_at")
            if bar.session_date < started_at.date() or event_time < started_at:
                pre_start_count += 1
                continue
            if (
                event_time > evaluated_at
                or available_at > evaluated_at
                or bar.session_date > evaluated_at.date()
            ):
                future_count += 1
                continue
            selected.setdefault(bar.session_date, (manifest.snapshot_id, bar))
    rows: list[ManualPositionTrackingEvidenceRow] = []
    for session_date, (snapshot_id, bar) in sorted(selected.items()):
        event_time = _aware_utc(bar.event_time, "event_time")
        available_at = _aware_utc(bar.available_at, "available_at")
        fact_hash = _fact_hash(
            "approved_market_daily",
            f"{snapshot_id}:{session_date.isoformat()}",
            bar,
        )
        rows.append(
            ManualPositionTrackingEvidenceRow(
                tracking_case_id=case.tracking_case_id,
                security_id=case.security.security_id,
                local_code=case.security.local_code,
                symbol=case.security.symbol,
                exchange=case.security.exchange,
                tracking_started_at=started_at,
                evaluated_at=evaluated_at,
                evidence_type="market",
                source="approved_market_daily",
                source_id=f"{snapshot_id}:{session_date.isoformat()}",
                event_at=event_time,
                source_fact_sha256=fact_hash,
                snapshot_id=snapshot_id,
                session_date=bar.session_date,
                event_time=event_time,
                available_at=available_at,
                price=bar.close,
            )
        )
    if rows:
        return tuple(rows), ()
    reasons = list(load_failures)
    if pre_start_count:
        reasons.append("approved market evidence exists only before tracking_started_at")
    if future_count:
        reasons.append("approved market evidence is after evaluated_at")
    if not reasons:
        reasons.append("no approved market evidence is available after tracking_started_at")
    return (), tuple(reasons)


def _order_row(
    case: ManualPositionTrackingCase,
    order: ManualOrder,
    evaluated_at: datetime,
    fact_hash: str,
) -> ManualPositionTrackingEvidenceRow:
    return ManualPositionTrackingEvidenceRow(
        tracking_case_id=case.tracking_case_id,
        security_id=case.security.security_id,
        local_code=case.security.local_code,
        symbol=case.security.symbol,
        exchange=case.security.exchange,
        tracking_started_at=_aware_utc(case.tracking_started_at, "tracking_started_at"),
        evaluated_at=evaluated_at,
        evidence_type="order",
        source="manual_order",
        source_id=order.order_id,
        event_at=_aware_utc(order.ordered_at, "ordered_at"),
        source_fact_sha256=fact_hash,
        order_id=order.order_id,
        side=order.side,
        sell_reason=order.sell_reason,
        quantity=order.quantity,
        price=order.limit_price,
        note=order.note,
    )


def _fill_row(
    case: ManualPositionTrackingCase,
    order: ManualOrder,
    fill: ManualFill,
    evaluated_at: datetime,
    fact_hash: str,
) -> ManualPositionTrackingEvidenceRow:
    return ManualPositionTrackingEvidenceRow(
        tracking_case_id=case.tracking_case_id,
        security_id=case.security.security_id,
        local_code=case.security.local_code,
        symbol=case.security.symbol,
        exchange=case.security.exchange,
        tracking_started_at=_aware_utc(case.tracking_started_at, "tracking_started_at"),
        evaluated_at=evaluated_at,
        evidence_type="fill",
        source="manual_fill",
        source_id=fill.fill_id,
        event_at=_aware_utc(fill.filled_at, "filled_at"),
        source_fact_sha256=fact_hash,
        order_id=order.order_id,
        fill_id=fill.fill_id,
        side=order.side,
        sell_reason=order.sell_reason,
        quantity=fill.quantity,
        price=fill.price,
        commission=fill.commission,
        stamp_duty=fill.stamp_duty,
        note=fill.note,
    )


def _review_row(
    case: ManualPositionTrackingCase,
    review: PortfolioReview,
    evaluated_at: datetime,
    fact_hash: str,
) -> ManualPositionTrackingEvidenceRow:
    return ManualPositionTrackingEvidenceRow(
        tracking_case_id=case.tracking_case_id,
        security_id=case.security.security_id,
        local_code=case.security.local_code,
        symbol=case.security.symbol,
        exchange=case.security.exchange,
        tracking_started_at=_aware_utc(case.tracking_started_at, "tracking_started_at"),
        evaluated_at=evaluated_at,
        evidence_type="review",
        source="portfolio_review",
        source_id=review.review_id,
        event_at=_aware_utc(review.reviewed_at, "reviewed_at"),
        source_fact_sha256=fact_hash,
        review_id=review.review_id,
        note=review.note,
    )


def _case_is_eligible(
    case: ManualPositionTrackingCase,
    evaluated_at: datetime,
    rows: Iterable[ManualPositionTrackingEvidenceRow],
) -> bool:
    evidence_rows = tuple(rows)
    return (
        evaluated_at >= _aware_utc(case.tracking_started_at, "tracking_started_at")
        and any(row.evidence_type in {"order", "fill", "review"} for row in evidence_rows)
        and any(row.evidence_type == "market" for row in evidence_rows)
    )


def _case_report(
    case: ManualPositionTrackingCase,
    evaluated_at: datetime,
    rows: tuple[ManualPositionTrackingEvidenceRow, ...],
    excluded: tuple[ManualPositionTrackingExcludedEvidence, ...],
    missing: tuple[str, ...],
) -> ManualPositionTrackingCaseReport:
    evidence_hash = _hash_values(row.source_fact_sha256 for row in rows)
    market_count = sum(row.evidence_type == "market" for row in rows)
    reasons = list(missing)
    if evaluated_at < _aware_utc(case.tracking_started_at, "tracking_started_at"):
        reasons.append("evaluated_at precedes tracking_started_at")
    reasons = list(dict.fromkeys(reasons))
    qualification_reasons = tuple(reasons)
    status: ManualTrackingQualificationStatus = (
        "eligible" if _case_is_eligible(case, evaluated_at, rows) else "not_eligible"
    )
    return ManualPositionTrackingCaseReport(
        tracking_case_id=case.tracking_case_id,
        security_id=case.security.security_id,
        local_code=case.security.local_code,
        symbol=case.security.symbol,
        exchange=case.security.exchange,
        tracking_started_at=_aware_utc(case.tracking_started_at, "tracking_started_at"),
        evaluated_at=evaluated_at,
        source_order_ids=case.source_order_ids,
        source_fill_ids=case.source_fill_ids,
        evidence_row_count=len(rows),
        order_count=sum(row.evidence_type == "order" for row in rows),
        fill_count=sum(row.evidence_type == "fill" for row in rows),
        review_count=sum(row.evidence_type == "review" for row in rows),
        market_count=market_count,
        market_snapshot_ids=tuple(
            sorted({row.snapshot_id for row in rows if row.snapshot_id is not None})
        ),
        excluded_evidence_count=len(excluded),
        excluded_evidence_ids=tuple(item.source_id for item in excluded),
        missing_reasons=tuple(dict.fromkeys(missing)),
        qualification_status=status,
        qualification_reasons=qualification_reasons,
        source_fact_sha256=evidence_hash,
    )


def _make_report(
    collected: _CollectedEvidence,
    *,
    evaluated_at: datetime,
    dataset_sha256: str,
) -> ManualPositionTrackingEvidenceReport:
    eligible = sum(case.qualification_status == "eligible" for case in collected.cases)
    not_eligible = len(collected.cases) - eligible
    if not collected.cases:
        coverage: ManualTrackingCoverageStatus = "empty"
    elif not_eligible:
        coverage = "partial"
    else:
        coverage = "complete"
    blockers = tuple(
        f"{case.tracking_case_id}: {reason}"
        for case in collected.cases
        if case.qualification_status == "not_eligible"
        for reason in case.qualification_reasons
    )
    return ManualPositionTrackingEvidenceReport(
        evaluated_at=evaluated_at,
        case_count=len(collected.cases),
        evidence_row_count=len(collected.rows),
        eligible_case_count=eligible,
        not_eligible_case_count=not_eligible,
        dataset_sha256=dataset_sha256,
        coverage_status=coverage,
        ready=bool(collected.cases) and not not_eligible,
        cases=collected.cases,
        excluded_evidence=collected.excluded,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=(
            "manual tracking evidence is isolated and is not a candidate_success, "
            "annotation_decision, buy_timing, or sell_timing training input",
        ),
    )


def _excluded(
    case: ManualPositionTrackingCase,
    evidence_type: ManualTrackingEvidenceType,
    source_id: str,
    fact_hash: str,
    event_at: datetime,
    reason: str,
) -> ManualPositionTrackingExcludedEvidence:
    return ManualPositionTrackingExcludedEvidence(
        tracking_case_id=case.tracking_case_id,
        evidence_type=evidence_type,
        source_id=source_id,
        source_fact_sha256=fact_hash,
        event_at=event_at,
        reason=reason,
    )


def _fact_hash(source: str, source_id: str, record: BaseModel) -> str:
    payload = {
        "source": source,
        "source_id": source_id,
        "record": record.model_dump(mode="json"),
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _hash_values(values: Iterable[str]) -> str:
    return hashlib.sha256(_canonical_json(tuple(values))).hexdigest()


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value.astimezone(UTC)


def _write_exclusive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"evidence output already exists: {path}")
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        if path.exists():
            raise FileExistsError(f"evidence output already exists: {path}")
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "MANUAL_POSITION_TRACKING_DATASET_SCHEMA_VERSION",
    "MANUAL_POSITION_TRACKING_REPORT_SCHEMA_VERSION",
    "ManualPositionTrackingCaseReport",
    "ManualPositionTrackingDatasetExport",
    "ManualPositionTrackingEvidenceReport",
    "ManualPositionTrackingEvidenceRow",
    "ManualPositionTrackingExcludedEvidence",
    "build_manual_position_tracking_dataset",
    "build_manual_position_tracking_evidence_report",
    "build_manual_position_tracking_export",
    "build_manual_position_tracking_report",
    "read_manual_position_tracking_dataset",
    "read_manual_position_tracking_report",
    "serialize_manual_position_tracking_dataset",
    "serialize_manual_position_tracking_report",
    "write_manual_position_tracking_dataset",
    "write_manual_position_tracking_evidence_report",
    "write_manual_position_tracking_report",
]
