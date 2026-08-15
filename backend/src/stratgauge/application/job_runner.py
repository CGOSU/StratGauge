"""Durable local job records for Windows Task Scheduler and the API."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from stratgauge._files import atomic_write_text
from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.domain.models import DomainModel


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.PARTIAL,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
)


class LocalJobError(RuntimeError):
    """A local job record cannot be safely created or advanced."""


class JobRecord(DomainModel):
    """One durable execution record; configuration is immutable by identity."""

    job_id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = JobStatus.QUEUED
    attempt_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=0, ge=0)
    timeout_seconds: float = Field(default=900, gt=0)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class JobCreateResult:
    """The durable record and whether this call created it."""

    job: JobRecord
    created: bool


class LocalJobStore:
    """Persist job state under one local data root with a separate job lock."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.job_dir = self.root / "jobs"
        self.job_dir.mkdir(parents=True, exist_ok=True)

    def create(self, job: JobRecord) -> JobRecord:
        return self.create_with_result(job).job

    def create_with_result(self, job: JobRecord) -> JobCreateResult:
        with local_write_lock(self.root, name="jobs"):
            path = self._path(job.job_id)
            if path.exists():
                existing = self.load(job.job_id)
                if not _same_configuration(existing, job):
                    raise LocalJobError("job identity already exists with different configuration")
                return JobCreateResult(job=existing, created=False)
            _write_job_record(path, job.model_dump_json(indent=2))
            return JobCreateResult(job=job, created=True)

    def load(self, job_id: str) -> JobRecord:
        path = self._path(job_id)
        if not path.exists():
            raise LocalJobError(f"job does not exist: {job_id}")
        try:
            return JobRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise LocalJobError(f"job is invalid: {job_id}") from exc

    def list(
        self,
        *,
        status: JobStatus | None = None,
        kind: str | None = None,
    ) -> tuple[JobRecord, ...]:
        jobs = tuple(self.load(path.stem) for path in self.job_dir.glob("job-*.json"))
        if status is not None:
            jobs = tuple(job for job in jobs if job.status is status)
        if kind is not None:
            jobs = tuple(job for job in jobs if job.kind == kind)
        return tuple(sorted(jobs, key=lambda job: (job.created_at, job.job_id)))

    def start(self, job_id: str, *, now: datetime) -> JobRecord:
        with local_write_lock(self.root, name="jobs"):
            job = self.load(job_id)
            if job.status in _TERMINAL_STATUSES:
                return job
            if job.status is JobStatus.RUNNING:
                raise LocalJobError(f"job is already running: {job_id}")
            updated = job.model_copy(
                update={
                    "status": JobStatus.RUNNING,
                    "attempt_count": job.attempt_count + 1,
                    "started_at": now,
                    "updated_at": now,
                    "finished_at": None,
                    "result": None,
                    "error": None,
                }
            )
            return self._save(updated)

    def finish(
        self,
        job_id: str,
        *,
        status: JobStatus,
        now: datetime,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> JobRecord:
        if status not in _TERMINAL_STATUSES and status is not JobStatus.RETRYING:
            raise LocalJobError("job finish status must be terminal or retrying")
        with local_write_lock(self.root, name="jobs"):
            job = self.load(job_id)
            updated = job.model_copy(
                update={
                    "status": status,
                    "updated_at": now,
                    "finished_at": now if status in _TERMINAL_STATUSES else None,
                    "result": result,
                    "error": error,
                }
            )
            return self._save(updated)

    def cancel(
        self, job_id: str, *, now: datetime, reason: str = "cancelled by operator"
    ) -> JobRecord:
        """Cancel a job only while it is still queued."""

        with local_write_lock(self.root, name="jobs"):
            job = self.load(job_id)
            if job.status is not JobStatus.QUEUED:
                raise LocalJobError("only queued jobs may be cancelled")
            return self._save(
                job.model_copy(
                    update={
                        "status": JobStatus.CANCELLED,
                        "updated_at": now,
                        "finished_at": now,
                        "error": reason,
                    }
                )
            )

    def recover_running(
        self,
        *,
        now: datetime,
        kind: str | None = None,
    ) -> tuple[JobRecord, ...]:
        """Requeue or fail jobs abandoned by a previous runner process."""

        recovered: list[JobRecord] = []
        with local_write_lock(self.root, name="jobs"):
            for job in self.list(status=JobStatus.RUNNING, kind=kind):
                if job.started_at is not None:
                    elapsed = (now - job.started_at).total_seconds()
                    if elapsed < job.timeout_seconds:
                        continue
                retryable = job.attempt_count <= job.max_retries
                recovered.append(
                    self._save(
                        job.model_copy(
                            update={
                                "status": JobStatus.RETRYING if retryable else JobStatus.FAILED,
                                "updated_at": now,
                                "finished_at": None if retryable else now,
                                "error": "runner process stopped before job completion",
                            }
                        )
                    )
                )
        return tuple(recovered)

    def _save(self, job: JobRecord) -> JobRecord:
        _write_job_record(self._path(job.job_id), job.model_dump_json(indent=2))
        return job

    def _path(self, job_id: str) -> Path:
        if not job_id.startswith("job-"):
            raise LocalJobError(f"invalid job id: {job_id}")
        return self.job_dir / f"{job_id}.json"


def job_id(*, kind: str, payload: dict[str, Any]) -> str:
    """Build a deterministic job identity from its immutable input payload."""

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{kind}\0{encoded}".encode()).hexdigest()[:20]
    return f"job-{digest}"


def _same_configuration(first: JobRecord, second: JobRecord) -> bool:
    return (
        first.job_id == second.job_id
        and first.kind == second.kind
        and first.payload == second.payload
        and first.max_retries == second.max_retries
        and first.timeout_seconds == second.timeout_seconds
    )


def _write_job_record(path: Path, content: str) -> None:
    try:
        atomic_write_text(path, content)
    except OSError as exc:
        raise LocalJobError(f"failed to write job: {path.name}") from exc
