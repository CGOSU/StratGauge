"""Durable main-board market synchronization jobs and explicit runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratgauge.application.job_runner import (
    JobRecord,
    JobStatus,
    LocalJobStore,
    job_id,
)
from stratgauge.data_pipeline.locking import LocalWriteLockError, local_write_lock
from stratgauge.data_sources.base import AdapterError, is_retryable_adapter_exception
from stratgauge.data_sources.tushare import TushareAdapter

MAIN_BOARD_MARKET_SYNC_JOB_KIND = "market_sync_main_board"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MarketSyncJobRequest(BaseModel):
    """The immutable request persisted inside a main-board job payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    minimum_coverage: Decimal = Field(default=Decimal("0.90"), ge=0, le=1)
    lookback_days: int = Field(default=10, ge=1, le=366)

    @field_validator("session_date")
    @classmethod
    def require_session_date(cls, value: date) -> date:
        if value > datetime.now(UTC).date():
            raise ValueError("session_date cannot be in the future")
        return value


class MarketSyncJobPayload(BaseModel):
    """Durable execution configuration, including the evidence roots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: MarketSyncJobRequest
    market_root: str
    universe_root: str

    @field_validator("market_root", "universe_root")
    @classmethod
    def require_root(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("job storage roots must not be empty")
        return value


@dataclass(frozen=True)
class MarketSyncEnqueueResult:
    """The durable job and whether this enqueue created a new record."""

    job: JobRecord
    created: bool


class MarketSyncCallable(Protocol):
    def __call__(
        self,
        request: MarketSyncJobRequest,
        *,
        market_root: str,
        universe_root: str,
        adapter: TushareAdapter,
    ) -> Any: ...


class MarketSyncAdapterFactory(Protocol):
    def __call__(self, job: JobRecord) -> TushareAdapter: ...


def enqueue_main_board_market_sync(
    job_store: LocalJobStore,
    request: BaseModel | Mapping[str, object],
    *,
    market_root: str | Path,
    universe_root: str | Path,
    max_retries: int = 2,
    timeout_seconds: float = 900,
    now: datetime | None = None,
) -> MarketSyncEnqueueResult:
    """Create or return one idempotent main-board synchronization job."""

    if max_retries < 0:
        raise ValueError("max_retries must not be negative")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    normalized_request = _normalize_request(request)
    payload = MarketSyncJobPayload(
        request=normalized_request,
        market_root=str(Path(market_root).expanduser().resolve()),
        universe_root=str(Path(universe_root).expanduser().resolve()),
    ).model_dump(mode="json")
    created_at = now or datetime.now(UTC)
    record = JobRecord(
        job_id=job_id(kind=MAIN_BOARD_MARKET_SYNC_JOB_KIND, payload=payload),
        kind=MAIN_BOARD_MARKET_SYNC_JOB_KIND,
        payload=payload,
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
        created_at=created_at,
        updated_at=created_at,
    )
    created = job_store.create_with_result(record)
    return MarketSyncEnqueueResult(job=created.job, created=created.created)


class MainBoardMarketSyncJobRunner:
    """Run queued jobs sequentially outside FastAPI's request process."""

    def __init__(
        self,
        job_store: LocalJobStore,
        *,
        adapter_factory: MarketSyncAdapterFactory | None = None,
        sync_function: MarketSyncCallable | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._job_store = job_store
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._sync_function = sync_function or _sync_main_board_close
        self._clock = clock

    def run_pending(self, *, limit: int | None = None) -> tuple[JobRecord, ...]:
        """Recover abandoned work and execute queued work in creation order."""

        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive when provided")
        recovered = self._job_store.recover_running(
            now=self._clock(),
            kind=MAIN_BOARD_MARKET_SYNC_JOB_KIND,
        )
        pending = tuple(
            sorted(
                self._job_store.list(
                    status=JobStatus.QUEUED,
                    kind=MAIN_BOARD_MARKET_SYNC_JOB_KIND,
                )
                + self._job_store.list(
                    status=JobStatus.RETRYING,
                    kind=MAIN_BOARD_MARKET_SYNC_JOB_KIND,
                ),
                key=lambda job: (job.created_at, job.job_id),
            )
        )
        if limit is not None:
            pending = pending[:limit]
        recovered_terminal = tuple(
            job
            for job in recovered
            if job.status
            in {
                JobStatus.COMPLETED,
                JobStatus.PARTIAL,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }
        )
        return recovered_terminal + tuple(self.run_job(job.job_id) for job in pending)

    def run_job(self, job_id_value: str) -> JobRecord:
        """Claim and execute one queued/retrying job, with bounded retry state."""

        job = self._job_store.load(job_id_value)
        if job.kind != MAIN_BOARD_MARKET_SYNC_JOB_KIND:
            raise RuntimeError(f"job kind is not supported by this runner: {job.kind}")
        if job.status in {
            JobStatus.COMPLETED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            return job
        if job.status not in {JobStatus.QUEUED, JobStatus.RETRYING}:
            raise RuntimeError(f"job is not runnable: {job.job_id}")
        running = self._job_store.start(job.job_id, now=self._clock())
        if running.status in {
            JobStatus.COMPLETED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            return running
        adapter: TushareAdapter | None = None
        try:
            payload = MarketSyncJobPayload.model_validate(running.payload)
            adapter = self._adapter_factory(running)
            with local_write_lock(
                payload.market_root,
                name="market-sync",
                timeout_seconds=running.timeout_seconds,
            ):
                response = self._sync_function(
                    payload.request,
                    market_root=payload.market_root,
                    universe_root=payload.universe_root,
                    adapter=adapter,
                )
        except Exception as exc:
            retryable = (
                isinstance(exc, LocalWriteLockError) or _is_retryable_sync_error(exc)
            ) and running.attempt_count <= running.max_retries
            return self._job_store.finish(
                running.job_id,
                status=JobStatus.RETRYING if retryable else JobStatus.FAILED,
                now=self._clock(),
                error=_safe_error(exc),
            )
        finally:
            _close_adapter(adapter)

        response_payload = _response_payload(response)
        ready = _research_ready(response)
        return self._job_store.finish(
            running.job_id,
            status=JobStatus.COMPLETED if ready else JobStatus.PARTIAL,
            now=self._clock(),
            result={"response": response_payload, "research_ready": ready},
        )


def run_main_board_market_sync_jobs(
    job_store: LocalJobStore,
    *,
    limit: int | None = None,
    adapter_factory: MarketSyncAdapterFactory | None = None,
    sync_function: MarketSyncCallable | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> tuple[JobRecord, ...]:
    """Convenience entry point used by an explicit CLI/OS runner."""

    return MainBoardMarketSyncJobRunner(
        job_store,
        adapter_factory=adapter_factory,
        sync_function=sync_function,
        clock=clock,
    ).run_pending(limit=limit)


def _sync_main_board_close(
    request: MarketSyncJobRequest,
    *,
    market_root: str,
    universe_root: str,
    adapter: TushareAdapter,
) -> Any:
    # Keep the application job core independent from the API module at import
    # time; the existing synchronous service remains the single writer path.
    from stratgauge.api.market_sync import MarketSyncRequest, sync_main_board_close

    return sync_main_board_close(
        MarketSyncRequest.model_validate(request.model_dump(mode="json")),
        market_root=market_root,
        universe_root=universe_root,
        adapter=adapter,
    )


def _default_adapter_factory(_: JobRecord) -> TushareAdapter:
    return TushareAdapter()


def _normalize_request(request: BaseModel | Mapping[str, object]) -> MarketSyncJobRequest:
    if isinstance(request, MarketSyncJobRequest):
        return request
    if isinstance(request, BaseModel):
        return MarketSyncJobRequest.model_validate(request.model_dump(mode="json"))
    return MarketSyncJobRequest.model_validate(dict(request))


def _close_adapter(adapter: TushareAdapter | None) -> None:
    if adapter is None:
        return
    try:
        adapter.close()
    except Exception:
        pass


def _response_payload(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        payload = response.model_dump(mode="json")
    elif isinstance(response, Mapping):
        payload = dict(response)
    else:
        payload = {"value": str(response)}
    return dict(payload) if isinstance(payload, Mapping) else {"value": str(payload)}


def _research_ready(response: Any) -> bool:
    if isinstance(response, Mapping):
        return bool(response.get("research_ready", True))
    return bool(getattr(response, "research_ready", True))


def _is_retryable_sync_error(error: BaseException) -> bool:
    return isinstance(
        error, (AdapterError, ConnectionError, TimeoutError)
    ) and is_retryable_adapter_exception(error)


def _safe_error(error: BaseException) -> str:
    try:
        from stratgauge.api.market_sync import to_market_sync_api_error

        translated = to_market_sync_api_error(error)
        return f"{translated.code}: {translated.message}"
    except Exception:
        return "MARKET_SYNC_FAILED: main-board market synchronization failed"
