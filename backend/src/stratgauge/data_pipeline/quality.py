"""Deterministic data-quality checks and cross-source comparison."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from stratgauge.domain.models import DailyBar


class QualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: str
    code: str
    message: str
    symbol: str | None = None
    session_date: date | None = None
    details: dict[str, str] = Field(default_factory=dict)


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    checked_rows: int = Field(ge=0)
    issues: tuple[QualityIssue, ...] = ()

    @property
    def approved(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def errors(self) -> tuple[QualityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[QualityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")


def check_bars(
    bars: Sequence[DailyBar],
    *,
    snapshot_id: str,
    expected_sessions: Iterable[date] = (),
    expected_security_ids: Iterable[str] = (),
) -> QualityReport:
    issues: list[QualityIssue] = []
    expected_session_set = set(expected_sessions)
    expected_security_set = set(expected_security_ids)
    keys: set[tuple[str, date, str]] = set()
    actual_sessions = {bar.session_date for bar in bars}
    actual_security_ids = {bar.security_id for bar in bars}

    for bar in bars:
        key = (bar.security_id, bar.session_date, bar.provider)
        if key in keys:
            issues.append(
                QualityIssue(
                    severity="error",
                    code="DUPLICATE_BAR",
                    message="同一证券、交易日和数据源出现重复日线。",
                    symbol=bar.symbol,
                    session_date=bar.session_date,
                )
            )
        keys.add(key)
        if bar.suspended and bar.tradable:
            issues.append(
                QualityIssue(
                    severity="error",
                    code="SUSPENDED_TRADABLE",
                    message="停牌证券被标记为可交易。",
                    symbol=bar.symbol,
                    session_date=bar.session_date,
                )
            )

    for missing_session in sorted(expected_session_set - actual_sessions):
        issues.append(
            QualityIssue(
                severity="error",
                code="MISSING_SESSION",
                message="请求范围内缺少预期交易日。",
                session_date=missing_session,
            )
        )
    for missing_security_id in sorted(expected_security_set - actual_security_ids):
        issues.append(
            QualityIssue(
                severity="error",
                code="MISSING_SECURITY",
                message="预期证券没有返回日线。",
                details={"security_id": missing_security_id},
            )
        )

    return QualityReport(snapshot_id=snapshot_id, checked_rows=len(bars), issues=tuple(issues))


def compare_bars(
    primary: Sequence[DailyBar],
    fallback: Sequence[DailyBar],
    *,
    snapshot_id: str,
    tolerance: Decimal = Decimal("0.01"),
    relative_tolerance: Decimal = Decimal("0.00001"),
) -> QualityReport:
    """Report disagreements without choosing one provider's value.

    Prices use the absolute tolerance; volume and amount also allow a tiny relative
    tolerance for provider unit conversion and integer rounding.
    """

    fallback_by_key = {(bar.security_id, bar.session_date): bar for bar in fallback}
    issues: list[QualityIssue] = []
    for primary_bar in primary:
        fallback_bar = fallback_by_key.get((primary_bar.security_id, primary_bar.session_date))
        if fallback_bar is None:
            issues.append(
                QualityIssue(
                    severity="error",
                    code="CROSS_SOURCE_MISSING",
                    message="备用数据源缺少对应日线。",
                    symbol=primary_bar.symbol,
                    session_date=primary_bar.session_date,
                )
            )
            continue
        for field in ("open", "high", "low", "close", "volume", "amount"):
            left = getattr(primary_bar, field)
            right = getattr(fallback_bar, field)
            allowed_difference = _allowed_difference(
                field,
                right,
                tolerance,
                relative_tolerance,
            )
            if abs(left - right) > allowed_difference:
                issues.append(
                    QualityIssue(
                        severity="error",
                        code="CROSS_SOURCE_DISAGREEMENT",
                        message=f"主备数据源的 {field} 超出容差。",
                        symbol=primary_bar.symbol,
                        session_date=primary_bar.session_date,
                        details={
                            "field": field,
                            "primary": str(left),
                            "fallback": str(right),
                            "tolerance": str(allowed_difference),
                        },
                    )
                )
    return QualityReport(snapshot_id=snapshot_id, checked_rows=len(primary), issues=tuple(issues))


def _allowed_difference(
    field: str,
    reference: Decimal,
    absolute_tolerance: Decimal,
    relative_tolerance: Decimal,
) -> Decimal:
    if field not in {"volume", "amount"}:
        return absolute_tolerance
    return max(absolute_tolerance, abs(reference) * relative_tolerance)


def quality_run_id(report: QualityReport) -> str:
    payload = report.model_dump_json()
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"quality-{digest[:20]}"
