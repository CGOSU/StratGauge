"""API for explicit main-board market close synchronization."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.application.job_runner import JobRecord, JobStatus, LocalJobError
from stratgauge.application.market_sync_jobs import MarketSyncJobPayload
from stratgauge.data_pipeline.adjustment_factor_sync import (
    sync_adjustment_factor_session,
)
from stratgauge.data_pipeline.market_daily_sync import sync_market_sessions
from stratgauge.data_pipeline.quality import QualityIssue
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore, SnapshotStoreError
from stratgauge.data_pipeline.universe import UniverseSnapshotStore
from stratgauge.data_sources.base import AdapterError, is_retryable_adapter_exception
from stratgauge.data_sources.tushare import TushareAdapter


class MarketSyncApiError(Exception):
    """Expected API-facing market synchronization failure."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class MarketSyncQualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: str
    code: str
    label: str
    message: str
    symbol: str | None = None
    session_date: date | None = None
    details: dict[str, str] = Field(default_factory=dict)
    action: str


class MarketSyncStageDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: Literal["market_daily", "adjustment_factor"]
    phase_label: str
    session_date: date
    snapshot_id: str
    status: SnapshotStatus
    quality_run_id: str | None
    expected_securities: int = Field(ge=1)
    actual_securities: int = Field(ge=0)
    coverage: Decimal = Field(ge=0, le=1)
    minimum_coverage: Decimal = Field(ge=0, le=1)
    issues: tuple[MarketSyncQualityIssue, ...] = ()
    summary: str
    action: str


class MarketSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_date: date
    minimum_coverage: Decimal = Field(default=Decimal("0.90"), ge=0, le=1)
    lookback_days: int = Field(default=10, ge=1, le=366)

    @field_validator("session_date")
    @classmethod
    def require_session_date(cls, value: date) -> date:
        if value > datetime.now(UTC).date():
            raise ValueError("session_date cannot be in the future")
        return value


class MarketSyncSessionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    market_snapshot_id: str
    market_status: SnapshotStatus
    adjustment_factor_snapshot_id: str | None
    adjustment_factor_status: SnapshotStatus | None
    skipped: bool
    market_diagnostic: MarketSyncStageDiagnostic
    adjustment_factor_diagnostic: MarketSyncStageDiagnostic | None = None


class MarketSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_through: date
    universe_snapshot_id: str
    security_count: int = Field(ge=1)
    research_ready: bool
    sessions: tuple[MarketSyncSessionResult, ...]


class MarketSyncTaskCreateRequest(MarketSyncRequest):
    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(default=2, ge=0, le=10)
    timeout_seconds: float = Field(default=900, gt=0, le=86_400)


class MarketSyncTaskRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = Field(default=1, ge=1, le=20)


class MarketSyncTaskView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    kind: str
    status: JobStatus
    request: MarketSyncRequest
    attempt_count: int = Field(ge=0)
    max_retries: int = Field(ge=0)
    timeout_seconds: float = Field(gt=0)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: MarketSyncResponse | None
    error: str | None


class MarketSyncTaskPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[MarketSyncTaskView, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    total: int = Field(ge=0)
    has_next: bool


class MarketSyncTaskRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    jobs: tuple[MarketSyncTaskView, ...]


def to_market_sync_task_view(job: JobRecord) -> MarketSyncTaskView:
    """Expose a job without storage roots or arbitrary payload fields."""

    payload = MarketSyncJobPayload.model_validate(job.payload)
    request = MarketSyncRequest.model_validate(payload.request.model_dump(mode="json"))
    result = None
    if job.result is not None:
        response_payload = job.result.get("response")
        if isinstance(response_payload, dict):
            result = MarketSyncResponse.model_validate(response_payload)
    return MarketSyncTaskView(
        job_id=job.job_id,
        kind=job.kind,
        status=job.status,
        request=request,
        attempt_count=job.attempt_count,
        max_retries=job.max_retries,
        timeout_seconds=job.timeout_seconds,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        result=result,
        error=job.error,
    )


def paginate_market_sync_tasks(
    jobs: tuple[JobRecord, ...],
    *,
    page: int,
    page_size: int,
) -> MarketSyncTaskPage:
    if page < 1:
        raise ValueError("page must be at least 1")
    if not 1 <= page_size <= 200:
        raise ValueError("page_size must be between 1 and 200")
    total = len(jobs)
    start = (page - 1) * page_size
    items = tuple(to_market_sync_task_view(job) for job in jobs[start : start + page_size])
    return MarketSyncTaskPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_next=start + page_size < total,
    )


def to_market_sync_task_api_error(error: BaseException) -> MarketSyncApiError:
    if isinstance(error, LocalJobError):
        message = str(error)
        if "does not exist" in message:
            return MarketSyncApiError(
                "MARKET_SYNC_TASK_NOT_FOUND",
                "行情同步任务不存在。",
                404,
                {"suggestion": "检查任务 ID 后重试。"},
            )
        if "only queued jobs" in message:
            return MarketSyncApiError(
                "MARKET_SYNC_TASK_NOT_CANCELLABLE",
                "只有 queued 状态的行情同步任务可以取消。",
                409,
                {"suggestion": "等待任务完成，或查看任务结果后再处理。"},
            )
        if "different configuration" in message:
            return MarketSyncApiError(
                "MARKET_SYNC_TASK_IDEMPOTENCY_CONFLICT",
                "相同任务身份已经使用了不同的执行配置。",
                409,
                {"suggestion": "使用原任务的重试和超时配置，或等待原任务完成。"},
            )
        return MarketSyncApiError(
            "MARKET_SYNC_TASK_STORAGE_FAILED",
            "行情同步任务账本无法安全读写。",
            500,
            {"suggestion": "检查数据目录权限和磁盘空间后重试。"},
        )
    return MarketSyncApiError(
        "MARKET_SYNC_TASK_FAILED",
        "行情同步任务操作失败。",
        500,
        {"suggestion": "检查任务账本和服务日志后重试。"},
    )


def sync_main_board_close(
    request: MarketSyncRequest,
    *,
    market_root: str | Path,
    universe_root: str | Path,
    adapter: TushareAdapter | None = None,
) -> MarketSyncResponse:
    """Synchronize the missing weekday sessions through an explicit date.

    The universe snapshot is the board-membership boundary. We reject BJ and
    any other exchange rather than silently broadening the requested pool.
    """

    # Do not make the market sync unusable when the local calendar cache ends
    # before the current session. Tushare is the source of truth for whether
    # this explicit date has rows; its quality gate rejects empty/non-session
    # responses. The cached calendar remains useful for historical batch jobs.

    universe_store = UniverseSnapshotStore(universe_root)
    universe = universe_store.latest_at(
        datetime.combine(request.session_date, datetime.min.time(), tzinfo=UTC)
    )
    if universe is None:
        raise ValueError("no universe snapshot is available for the requested session")
    if not universe.securities or any(
        not _is_main_board_security(security.exchange.value, security.local_code)
        for security in universe.securities
    ):
        raise ValueError("the selected universe is not an SH/SZ main-board universe")

    market_store = SnapshotStore(market_root)
    adapter = adapter or TushareAdapter()
    start_date = max(
        request.session_date - timedelta(days=request.lookback_days),
        universe.as_of.date(),
    )
    candidate_dates = tuple(
        start_date + timedelta(days=offset)
        for offset in range((request.session_date - start_date).days + 1)
        if (start_date + timedelta(days=offset)).weekday() < 5
    )
    approved_snapshots = market_store.find_approved_market_snapshots(
        provider=adapter.provider,
        session_dates=candidate_dates,
        universe_snapshot_id=universe.universe_snapshot_id,
    )
    session_dates = tuple(
        session_date for session_date in candidate_dates if session_date not in approved_snapshots
    )
    if not session_dates:
        existing = approved_snapshots.get(request.session_date)
        if existing is None:
            raise ValueError(
                "no missing market sessions found in the recent lookback window; "
                "requested date has no approved snapshot"
            )
        factor = market_store.find_approved_adjustment_factor_snapshot(
            provider=adapter.provider,
            session_date=request.session_date,
            universe_snapshot_id=universe.universe_snapshot_id,
            source_daily_snapshot_id=existing.snapshot_id,
        )
        market_diagnostic = _build_stage_diagnostic(
            phase="market_daily",
            session_date=existing.start_date,
            snapshot_id=existing.snapshot_id,
            status=existing.status,
            quality_run_id=existing.quality_run_id,
            expected_securities=len(universe.securities),
            actual_securities=existing.row_count,
            minimum_coverage=request.minimum_coverage,
            skipped=True,
            store=market_store,
        )
        factor_diagnostic = (
            _build_stage_diagnostic(
                phase="adjustment_factor",
                session_date=factor.start_date,
                snapshot_id=factor.snapshot_id,
                status=factor.status,
                quality_run_id=factor.quality_run_id,
                expected_securities=len(universe.securities),
                actual_securities=factor.row_count,
                minimum_coverage=request.minimum_coverage,
                skipped=True,
                store=market_store,
            )
            if factor
            else None
        )
        session = MarketSyncSessionResult(
            session_date=request.session_date,
            market_snapshot_id=existing.snapshot_id,
            market_status=existing.status,
            adjustment_factor_snapshot_id=factor.snapshot_id if factor else None,
            adjustment_factor_status=factor.status if factor else None,
            skipped=True,
            market_diagnostic=market_diagnostic,
            adjustment_factor_diagnostic=factor_diagnostic,
        )
        return MarketSyncResponse(
            requested_through=request.session_date,
            universe_snapshot_id=universe.universe_snapshot_id,
            security_count=len(universe.securities),
            research_ready=_session_is_research_ready(session),
            sessions=(session,),
        )

    results = sync_market_sessions(
        adapter=adapter,
        session_dates=session_dates,
        universe=universe,
        store=market_store,
        minimum_security_coverage=request.minimum_coverage,
    )
    session_results: list[MarketSyncSessionResult] = []
    for result in results:
        factor_result = None
        if result.status is SnapshotStatus.APPROVED:
            factor_result = sync_adjustment_factor_session(
                adapter=adapter,
                session_date=result.session_date,
                universe=universe,
                source_daily_snapshot_id=result.snapshot_id,
                store=market_store,
                minimum_security_coverage=request.minimum_coverage,
            )
        market_diagnostic = _build_stage_diagnostic(
            phase="market_daily",
            session_date=result.session_date,
            snapshot_id=result.snapshot_id,
            status=result.status,
            quality_run_id=result.quality_run_id,
            expected_securities=result.expected_securities,
            actual_securities=result.actual_securities,
            coverage=result.coverage,
            minimum_coverage=request.minimum_coverage,
            skipped=result.skipped,
            store=market_store,
        )
        factor_diagnostic = (
            _build_stage_diagnostic(
                phase="adjustment_factor",
                session_date=factor_result.session_date,
                snapshot_id=factor_result.snapshot_id,
                status=factor_result.status,
                quality_run_id=factor_result.quality_run_id,
                expected_securities=factor_result.expected_securities,
                actual_securities=factor_result.actual_securities,
                coverage=factor_result.coverage,
                minimum_coverage=request.minimum_coverage,
                skipped=factor_result.skipped,
                store=market_store,
            )
            if factor_result
            else None
        )
        session_results.append(
            MarketSyncSessionResult(
                session_date=result.session_date,
                market_snapshot_id=result.snapshot_id,
                market_status=result.status,
                adjustment_factor_snapshot_id=factor_result.snapshot_id if factor_result else None,
                adjustment_factor_status=factor_result.status if factor_result else None,
                skipped=result.skipped,
                market_diagnostic=market_diagnostic,
                adjustment_factor_diagnostic=factor_diagnostic,
            )
        )
    return MarketSyncResponse(
        requested_through=request.session_date,
        universe_snapshot_id=universe.universe_snapshot_id,
        security_count=len(universe.securities),
        research_ready=all(_session_is_research_ready(session) for session in session_results),
        sessions=tuple(session_results),
    )


_STAGE_LABELS = {
    "market_daily": "日线行情",
    "adjustment_factor": "复权因子",
}
_STATUS_LABELS = {
    SnapshotStatus.PENDING: "待处理",
    SnapshotStatus.APPROVED: "已批准",
    SnapshotStatus.REJECTED: "已拒绝",
    SnapshotStatus.SUPERSEDED: "已被替代",
}
_QUALITY_ISSUE_LABELS = {
    "DUPLICATE_BAR": "日线重复",
    "SUSPENDED_TRADABLE": "停牌状态异常",
    "MISSING_SESSION": "交易日缺失",
    "MISSING_SECURITY": "证券数据缺失",
    "LOW_SECURITY_COVERAGE": "证券覆盖率不足",
    "DUPLICATE_ADJUSTMENT_FACTOR": "复权因子重复",
    "NON_POSITIVE_ADJUSTMENT_FACTOR": "复权因子无效",
}
_QUALITY_ISSUE_ACTIONS = {
    "DUPLICATE_BAR": "检查数据源去重规则后重试；重复记录未用于研究。",
    "SUSPENDED_TRADABLE": "核对停牌和可交易字段映射后重试。",
    "MISSING_SESSION": "确认该日期是否为交易日，并检查数据源日期参数后重试。",
    "MISSING_SECURITY": "确认数据源是否已返回该证券；需要完整覆盖时请在数据补齐后重试。",
    "LOW_SECURITY_COVERAGE": "确认数据源已发布完整证券范围后重试；未达到阈值前快照不会进入研究。",
    "DUPLICATE_ADJUSTMENT_FACTOR": "检查复权因子数据源去重规则后重试。",
    "NON_POSITIVE_ADJUSTMENT_FACTOR": "检查复权因子字段和数据源返回值后重试。",
}


def _build_stage_diagnostic(
    *,
    phase: Literal["market_daily", "adjustment_factor"],
    session_date: date,
    snapshot_id: str,
    status: SnapshotStatus,
    quality_run_id: str | None,
    expected_securities: int,
    actual_securities: int,
    minimum_coverage: Decimal,
    store: SnapshotStore,
    skipped: bool,
    coverage: Decimal | None = None,
) -> MarketSyncStageDiagnostic:
    report = _load_quality_report(store, quality_run_id)
    issues = tuple(_public_quality_issue(issue) for issue in report or ())
    phase_label = _STAGE_LABELS[phase]
    effective_coverage = (
        coverage
        if coverage is not None
        else Decimal(actual_securities) / Decimal(expected_securities)
    )
    if status is SnapshotStatus.REJECTED:
        summary = f"{phase_label}未通过质量门，快照已保存但不会进入研究。"
        action = issues[0].action if issues else "打开质量报告核对失败原因，修复数据后重新同步。"
    elif status is SnapshotStatus.APPROVED and issues:
        summary = f"{phase_label}已通过质量门，但有 {len(issues)} 条提示。"
        action = "核对提示项；当前快照已批准，可以用于研究。"
    elif status is SnapshotStatus.APPROVED and skipped:
        summary = f"已使用本地已批准的{phase_label}快照，未重复下载。"
        action = "无需处理；该阶段数据可以用于研究。"
    elif status is SnapshotStatus.APPROVED:
        summary = f"{phase_label}已通过质量门，可以用于研究。"
        action = "无需处理；该阶段数据可以用于研究。"
    else:
        summary = f"{phase_label}当前状态为{_STATUS_LABELS[status]}，暂不可用于研究。"
        action = "等待快照完成质量检查后再进行研究。"
    return MarketSyncStageDiagnostic(
        phase=phase,
        phase_label=phase_label,
        session_date=session_date,
        snapshot_id=snapshot_id,
        status=status,
        quality_run_id=quality_run_id,
        expected_securities=expected_securities,
        actual_securities=actual_securities,
        coverage=effective_coverage,
        minimum_coverage=minimum_coverage,
        issues=issues,
        summary=summary,
        action=action,
    )


def _load_quality_report(
    store: SnapshotStore,
    quality_run_id: str | None,
) -> tuple[QualityIssue, ...] | None:
    if not quality_run_id:
        return None
    try:
        return store.load_quality_report(quality_run_id).issues
    except SnapshotStoreError:
        return None


def _public_quality_issue(issue: QualityIssue) -> MarketSyncQualityIssue:
    return MarketSyncQualityIssue(
        severity=issue.severity,
        code=issue.code,
        label=_QUALITY_ISSUE_LABELS.get(issue.code, "数据质量问题"),
        message=issue.message,
        symbol=issue.symbol,
        session_date=issue.session_date,
        details=dict(issue.details),
        action=_QUALITY_ISSUE_ACTIONS.get(
            issue.code,
            "检查该质量问题的原始数据和字段映射后重试。",
        ),
    )


def _session_is_research_ready(session: MarketSyncSessionResult) -> bool:
    return (
        session.market_status is SnapshotStatus.APPROVED
        and session.adjustment_factor_status is SnapshotStatus.APPROVED
    )


def to_market_sync_api_error(error: BaseException) -> MarketSyncApiError:
    """Translate sync failures without exposing provider or credential details."""

    if isinstance(error, (AdapterError, ConnectionError, TimeoutError)):
        retryable = is_retryable_adapter_exception(error)
        if retryable:
            return MarketSyncApiError(
                code="MARKET_SYNC_PROVIDER_UNAVAILABLE",
                message="行情数据源暂时不可用，主板收盘行情尚未完成同步。",
                status_code=503,
                details={
                    "reason": "数据源请求超时、网络中断或暂时触发访问限制。",
                    "suggestion": "检查数据源服务、网络连接和访问配置，稍后重试。",
                    "retryable": True,
                },
            )
        return MarketSyncApiError(
            code="MARKET_SYNC_PROVIDER_DATA_INVALID",
            message="行情数据源返回的数据无法通过字段或格式校验。",
            status_code=422,
            details={
                "reason": "数据源字段缺失、格式异常或无法与证券范围匹配。",
                "suggestion": "检查数据源返回字段和交易日参数，修复后重新同步。",
                "retryable": False,
            },
        )
    if isinstance(error, SnapshotStoreError) or isinstance(error, OSError):
        return MarketSyncApiError(
            code="MARKET_SYNC_STORAGE_FAILED",
            message="行情快照无法安全保存或读取，本次同步未完成。",
            status_code=500,
            details={
                "reason": "本地快照文件、质量报告或索引读写失败。",
                "suggestion": "检查数据目录权限和磁盘空间后重试；不要使用未批准快照进行研究。",
                "retryable": False,
            },
        )
    if isinstance(error, ValueError):
        return MarketSyncApiError(
            code="MARKET_SYNC_PREREQUISITE_FAILED",
            message="行情同步缺少可用的主板证券范围或前置快照。",
            status_code=422,
            details={
                "reason": "请求日期没有可用的主板范围快照，或已选范围不符合 SH/SZ 主板约束。",
                "suggestion": "先准备并批准对应日期的主板证券范围，再重新同步。",
                "retryable": False,
            },
        )
    if isinstance(error, RuntimeError):
        return MarketSyncApiError(
            code="MARKET_SYNC_FAILED",
            message="主板收盘行情同步未完成。",
            status_code=422,
            details={
                "reason": "同步流程在数据校验或快照处理阶段失败。",
                "suggestion": "检查同步前置条件和服务日志，确认问题后重试。",
                "retryable": False,
            },
        )
    return MarketSyncApiError(
        code="MARKET_SYNC_FAILED",
        message="主板收盘行情同步未完成。",
        status_code=422,
        details={
            "reason": "同步过程中发生未分类错误。",
            "suggestion": "检查服务日志和数据源状态，确认问题后重试。",
            "retryable": False,
        },
    )


def _is_main_board_security(exchange: str, local_code: str) -> bool:
    if exchange == "SH":
        return local_code.startswith(("600", "601", "603", "605"))
    if exchange == "SZ":
        return local_code.startswith(("000", "001", "002", "003"))
    return False
