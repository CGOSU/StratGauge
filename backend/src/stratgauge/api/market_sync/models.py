"""Market sync API models and view mapping."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.application.job_runner import JobRecord, JobStatus, LocalJobError
from stratgauge.application.market_sync_jobs import MarketSyncJobPayload
from stratgauge.data_pipeline.store import SnapshotStatus


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
