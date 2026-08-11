"""Backfill approved daily history for point-in-time selection features."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator

from stratgauge.data_pipeline.data_access import DataAccessPlan
from stratgauge.data_pipeline.ingest import ingest
from stratgauge.data_pipeline.quality import QualityIssue
from stratgauge.data_pipeline.store import SnapshotStore, SnapshotStoreError
from stratgauge.data_sources.base import AdapterError, DailyBarRequest, MarketDataAdapter
from stratgauge.domain.models import DomainModel, Exchange, Security, canonical_symbol
from stratgauge.domain.selections import TdxSelectionImport

HISTORY_BACKFILL_REPORT_VERSION = "selection-history-backfill-v1"


class HistoryTargetRole(StrEnum):
    """The role a security plays in a selection feature set."""

    CANDIDATE = "candidate"
    BENCHMARK = "benchmark"
    INDUSTRY = "industry"


class HistoryBackfillStatus(StrEnum):
    """Materialized state of one requested security history."""

    READY = "ready"
    MISSING_SECURITY = "missing_security"
    INSUFFICIENT_HISTORY = "insufficient_history"
    QUALITY_FAILED = "quality_failed"
    FETCH_FAILED = "fetch_failed"


class HistoryBackfillRequest(DomainModel):
    """Required feature sessions ending strictly before a selection date."""

    selection_date: date
    lookback_sessions: int = Field(gt=0)
    expected_sessions: tuple[date, ...]

    @model_validator(mode="after")
    def validate_sessions(self) -> HistoryBackfillRequest:
        if not self.expected_sessions:
            raise ValueError("expected_sessions must not be empty")
        if len(set(self.expected_sessions)) != len(self.expected_sessions):
            raise ValueError("expected_sessions must not contain duplicates")
        if tuple(sorted(self.expected_sessions)) != self.expected_sessions:
            raise ValueError("expected_sessions must be sorted in ascending order")
        if any(session >= self.selection_date for session in self.expected_sessions):
            raise ValueError("expected_sessions must precede selection_date")
        if len(self.expected_sessions) < self.lookback_sessions:
            raise ValueError("expected_sessions do not cover the requested lookback")
        return self

    @property
    def required_sessions(self) -> tuple[date, ...]:
        """Return the exact sessions that the feature engine may consume."""

        return self.expected_sessions[-self.lookback_sessions :]


class HistoryBackfillTarget(DomainModel):
    """One explicit candidate, benchmark, or industry security to backfill."""

    security: Security
    role: HistoryTargetRole


class HistoryBackfillTargetReport(DomainModel):
    """Evidence and failures for one backfill target."""

    target_id: str
    role: HistoryTargetRole
    security_id: str
    symbol: str
    status: HistoryBackfillStatus
    snapshot_id: str | None = None
    snapshot_approved: bool = False
    required_session_count: int = Field(ge=0)
    observed_session_count: int = Field(ge=0)
    observed_sessions: tuple[date, ...] = ()
    missing_sessions: tuple[date, ...] = ()
    quality_errors: tuple[QualityIssue, ...] = ()
    quality_warnings: tuple[QualityIssue, ...] = ()
    access_plan: DataAccessPlan | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_snapshot_state(self) -> HistoryBackfillTargetReport:
        if self.snapshot_approved and not self.snapshot_id:
            raise ValueError("an approved snapshot requires snapshot_id")
        if self.status is HistoryBackfillStatus.READY:
            if not self.snapshot_approved:
                raise ValueError("ready history requires an approved snapshot")
            if self.missing_sessions:
                raise ValueError("ready history cannot have missing sessions")
        return self


class HistoryBackfillReport(DomainModel):
    """Immutable report consumed by the future feature-contract stage."""

    report_version: str = HISTORY_BACKFILL_REPORT_VERSION
    provider: str
    remote_allowed: bool = True
    selection_date: date
    lookback_sessions: int = Field(gt=0)
    history_start_date: date
    history_end_date: date
    required_sessions: tuple[date, ...]
    target_reports: tuple[HistoryBackfillTargetReport, ...]
    approved_snapshot_ids: tuple[str, ...] = ()
    rejected_snapshot_ids: tuple[str, ...] = ()
    missing_security_ids: tuple[str, ...] = ()
    missing_sessions: dict[str, tuple[date, ...]] = Field(default_factory=dict)
    insufficient_history_security_ids: tuple[str, ...] = ()
    quality_failure_security_ids: tuple[str, ...] = ()
    fetch_failure_security_ids: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    ready: bool = False

    @model_validator(mode="after")
    def validate_report_dates(self) -> HistoryBackfillReport:
        if self.history_end_date >= self.selection_date:
            raise ValueError("history_end_date must precede selection_date")
        if self.history_start_date > self.history_end_date:
            raise ValueError("history_start_date must not follow history_end_date")
        if not self.required_sessions:
            raise ValueError("required_sessions must not be empty")
        return self


def build_history_backfill_targets(
    imported: TdxSelectionImport,
    *,
    benchmark_securities: Iterable[Security] = (),
    industry_securities: Iterable[Security] = (),
) -> tuple[HistoryBackfillTarget, ...]:
    """Build targets without inferring benchmark or industry membership."""

    targets = [
        HistoryBackfillTarget(
            security=_history_security(
                candidate.local_code,
                candidate.exchange,
                candidate.name,
            ),
            role=HistoryTargetRole.CANDIDATE,
        )
        for candidate in imported.candidates
    ]
    targets.extend(
        HistoryBackfillTarget(security=security, role=HistoryTargetRole.BENCHMARK)
        for security in benchmark_securities
    )
    targets.extend(
        HistoryBackfillTarget(security=security, role=HistoryTargetRole.INDUSTRY)
        for security in industry_securities
    )
    _validate_unique_targets(targets)
    return tuple(targets)


def backfill_selection_history(
    request: HistoryBackfillRequest,
    targets: Iterable[HistoryBackfillTarget],
    *,
    adapter: MarketDataAdapter,
    market_store: SnapshotStore,
    remote_allowed: bool = True,
    force_refresh: bool = False,
) -> HistoryBackfillReport:
    """Fetch each target and return a report without making rejected data usable."""

    target_list = tuple(targets)
    _validate_unique_targets(target_list)
    required_sessions = request.required_sessions
    target_reports: list[HistoryBackfillTargetReport] = []
    approved_snapshot_ids: list[str] = []
    rejected_snapshot_ids: list[str] = []

    for target in target_list:
        target_id = f"{target.role.value}:{target.security.symbol}"
        try:
            result = ingest(
                adapter,
                _request_for_target(target.security, required_sessions),
                market_store,
                expected_sessions=required_sessions,
                expected_security_ids=(target.security.security_id,),
                remote_allowed=remote_allowed,
                force_refresh=force_refresh,
            )
        except (AdapterError, SnapshotStoreError) as exc:
            target_reports.append(
                HistoryBackfillTargetReport(
                    target_id=target_id,
                    role=target.role,
                    security_id=target.security.security_id,
                    symbol=target.security.symbol,
                    status=HistoryBackfillStatus.FETCH_FAILED,
                    required_session_count=len(required_sessions),
                    observed_session_count=0,
                    error=str(exc),
                )
            )
            continue

        observed_sessions = tuple(sorted({bar.session_date for bar in result.bars}))
        observed_session_set = set(observed_sessions)
        missing_sessions = tuple(
            session for session in required_sessions if session not in observed_session_set
        )
        approved = result.quality.approved
        if approved:
            _append_unique(approved_snapshot_ids, result.manifest.snapshot_id)
        else:
            _append_unique(rejected_snapshot_ids, result.manifest.snapshot_id)

        has_missing_security = not observed_sessions or any(
            issue.code == "MISSING_SECURITY" for issue in result.quality.errors
        )
        insufficient = len(observed_sessions) < len(required_sessions)
        if has_missing_security:
            status = HistoryBackfillStatus.MISSING_SECURITY
        elif not approved:
            status = HistoryBackfillStatus.QUALITY_FAILED
        elif insufficient:
            status = HistoryBackfillStatus.INSUFFICIENT_HISTORY
        else:
            status = HistoryBackfillStatus.READY

        target_reports.append(
            HistoryBackfillTargetReport(
                target_id=target_id,
                role=target.role,
                security_id=target.security.security_id,
                symbol=target.security.symbol,
                status=status,
                snapshot_id=result.manifest.snapshot_id,
                snapshot_approved=approved,
                required_session_count=len(required_sessions),
                observed_session_count=len(observed_sessions),
                observed_sessions=observed_sessions,
                missing_sessions=missing_sessions,
                quality_errors=result.quality.errors,
                quality_warnings=result.quality.warnings,
                access_plan=result.access_plan,
            )
        )

    missing_security_ids = tuple(
        report.security_id
        for report in target_reports
        if report.status is HistoryBackfillStatus.MISSING_SECURITY
    )
    insufficient_history_security_ids = tuple(
        report.security_id
        for report in target_reports
        if report.status
        in {
            HistoryBackfillStatus.MISSING_SECURITY,
            HistoryBackfillStatus.INSUFFICIENT_HISTORY,
            HistoryBackfillStatus.QUALITY_FAILED,
        }
        and report.observed_session_count < report.required_session_count
    )
    quality_failure_security_ids = tuple(
        report.security_id for report in target_reports if report.quality_errors
    )
    fetch_failure_security_ids = tuple(
        report.security_id
        for report in target_reports
        if report.status is HistoryBackfillStatus.FETCH_FAILED
    )
    missing_sessions_by_target = {
        report.target_id: report.missing_sessions
        for report in target_reports
        if report.missing_sessions
    }
    blockers: list[str] = []
    if not target_reports:
        blockers.append("no history backfill targets were provided")
    if any(report.status is not HistoryBackfillStatus.READY for report in target_reports):
        blockers.append("one or more history backfill targets are not ready")

    return HistoryBackfillReport(
        provider=adapter.provider,
        remote_allowed=remote_allowed,
        selection_date=request.selection_date,
        lookback_sessions=request.lookback_sessions,
        history_start_date=required_sessions[0],
        history_end_date=required_sessions[-1],
        required_sessions=required_sessions,
        target_reports=tuple(target_reports),
        approved_snapshot_ids=tuple(approved_snapshot_ids),
        rejected_snapshot_ids=tuple(rejected_snapshot_ids),
        missing_security_ids=missing_security_ids,
        missing_sessions=missing_sessions_by_target,
        insufficient_history_security_ids=insufficient_history_security_ids,
        quality_failure_security_ids=quality_failure_security_ids,
        fetch_failure_security_ids=fetch_failure_security_ids,
        blockers=tuple(blockers),
        ready=bool(target_reports) and not blockers,
    )


def read_trading_sessions(path: str | Path) -> tuple[date, ...]:
    """Read an explicit, sorted trading-session calendar from one text file."""

    sessions: list[date] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"failed to read trading sessions: {path}") from exc
    for line_number, raw_line in enumerate(lines, start=1):
        value = raw_line.split("#", 1)[0].strip()
        if not value:
            continue
        try:
            sessions.append(date.fromisoformat(value))
        except ValueError as exc:
            raise ValueError(f"invalid trading session at line {line_number}: {value!r}") from exc
    if not sessions:
        raise ValueError("trading session file is empty")
    if len(set(sessions)) != len(sessions):
        raise ValueError("trading session file contains duplicates")
    if tuple(sorted(sessions)) != tuple(sessions):
        raise ValueError("trading session file must be sorted in ascending order")
    return tuple(sessions)


def write_history_backfill_report(path: str | Path, report: HistoryBackfillReport) -> None:
    """Write a report once, preserving the evidence of an earlier run."""

    destination = Path(path)
    if destination.exists():
        raise FileExistsError(f"history backfill report already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    try:
        temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _history_security(local_code: str, exchange: Exchange, name: str | None) -> Security:
    symbol = canonical_symbol(local_code, exchange)
    return Security(
        security_id=f"history-{symbol}",
        local_code=local_code,
        symbol=symbol,
        exchange=exchange,
        name=name,
    )


def _request_for_target(
    security: Security,
    required_sessions: tuple[date, ...],
) -> DailyBarRequest:
    return DailyBarRequest(
        security=security,
        start_date=required_sessions[0],
        end_date=required_sessions[-1],
    )


def _validate_unique_targets(targets: Sequence[HistoryBackfillTarget]) -> None:
    keys = [(target.role.value, target.security.symbol) for target in targets]
    if len(keys) != len(set(keys)):
        raise ValueError("history backfill targets must be unique by role and symbol")


def _append_unique(destination: list[str], value: str) -> None:
    if value not in destination:
        destination.append(value)
