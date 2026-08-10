"""Immutable raw/normalized snapshot storage."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from stratgauge.data_pipeline.quality import QualityReport
from stratgauge.data_sources.base import AdapterResult, MarketAdapterResult
from stratgauge.domain.models import DailyBar


class SnapshotStoreError(RuntimeError):
    """A snapshot cannot be safely read or written."""


class SnapshotStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class SnapshotManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    provider: str
    adapter_version: str
    start_date: date
    end_date: date
    row_count: int = Field(ge=0)
    content_sha256: str
    raw_path: str
    normalized_path: str
    status: SnapshotStatus = SnapshotStatus.PENDING
    quality_run_id: str | None = None
    created_at: datetime
    request_kind: str = "security_daily"
    universe_snapshot_id: str | None = None


class SnapshotStore:
    """Store each content-addressed snapshot once and never overwrite it."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.normalized_dir = self.root / "normalized"
        self.manifest_dir = self.root / "manifests"
        self.quality_dir = self.root / "quality"
        self.catalog_path = self.root / "catalog.duckdb"
        for directory in (
            self.raw_dir,
            self.normalized_dir,
            self.manifest_dir,
            self.quality_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._initialize_catalog()

    def write(self, result: AdapterResult | MarketAdapterResult) -> SnapshotManifest:
        payload = {
            "provider": result.provider,
            "adapter_version": result.adapter_version,
            "request": result.request.model_dump(mode="json"),
            "records": [_json_safe(record) for record in result.raw_records],
        }
        canonical_payload = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        content_sha256 = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        snapshot_id = f"snapshot-{content_sha256[:20]}"
        manifest_path = self.manifest_dir / f"{snapshot_id}.json"
        if manifest_path.exists():
            manifest = self.load_manifest(snapshot_id)
            self._register_manifest(manifest)
            return manifest

        raw_path = self.raw_dir / f"{snapshot_id}.json"
        normalized_path = self.normalized_dir / f"{snapshot_id}.parquet"
        _atomic_write_text(raw_path, canonical_payload)
        pl.DataFrame([bar.model_dump() for bar in result.bars]).write_parquet(normalized_path)
        manifest = SnapshotManifest(
            snapshot_id=snapshot_id,
            provider=result.provider,
            adapter_version=result.adapter_version,
            start_date=result.request.start_date,
            end_date=result.request.end_date,
            row_count=len(result.bars),
            content_sha256=content_sha256,
            raw_path=str(raw_path.relative_to(self.root)),
            normalized_path=str(normalized_path.relative_to(self.root)),
            created_at=result.fetched_at,
            request_kind=result.request.request_kind,
            universe_snapshot_id=getattr(result.request, "universe_snapshot_id", None),
        )
        _atomic_write_text(
            manifest_path,
            manifest.model_dump_json(indent=2),
        )
        self._register_manifest(manifest)
        return manifest

    def load_manifest(self, snapshot_id: str) -> SnapshotManifest:
        path = self.manifest_dir / f"{snapshot_id}.json"
        if not path.exists():
            raise SnapshotStoreError(f"snapshot manifest does not exist: {snapshot_id}")
        try:
            return SnapshotManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SnapshotStoreError(f"snapshot manifest is invalid: {snapshot_id}") from exc

    def load_bars(self, snapshot_id: str) -> tuple[DailyBar, ...]:
        """Load normalized bars only from an approved snapshot."""

        manifest = self.load_manifest(snapshot_id)
        if manifest.status is not SnapshotStatus.APPROVED:
            raise SnapshotStoreError(f"snapshot is not approved: {snapshot_id}")
        path = self.root / manifest.normalized_path
        if not path.exists():
            raise SnapshotStoreError(f"normalized snapshot does not exist: {snapshot_id}")
        try:
            rows = pl.read_parquet(path).to_dicts()
            return tuple(DailyBar.model_validate(row) for row in rows)
        except Exception as exc:
            raise SnapshotStoreError(f"normalized snapshot is invalid: {snapshot_id}") from exc

    def update_status(
        self,
        snapshot_id: str,
        status: SnapshotStatus,
        quality_run_id: str | None = None,
    ) -> SnapshotManifest:
        manifest = self.load_manifest(snapshot_id)
        updated = manifest.model_copy(update={"status": status, "quality_run_id": quality_run_id})
        _atomic_write_text(
            self.manifest_dir / f"{snapshot_id}.json",
            updated.model_dump_json(indent=2),
        )
        self._register_manifest(updated)
        return updated

    def save_quality_report(self, report: QualityReport, quality_run_id: str) -> str:
        path = self.quality_dir / f"{quality_run_id}.json"
        _atomic_write_text(path, report.model_dump_json(indent=2))
        with duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO quality_catalog
                (quality_run_id, snapshot_id, approved, checked_rows, error_count,
                 warning_count, report_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    quality_run_id,
                    report.snapshot_id,
                    report.approved,
                    report.checked_rows,
                    len(report.errors),
                    len(report.warnings),
                    str(path.relative_to(self.root)),
                ],
            )
        return str(path.relative_to(self.root))

    def find_approved_market_snapshot(
        self,
        *,
        provider: str,
        session_date: date,
        universe_snapshot_id: str,
    ) -> SnapshotManifest | None:
        for manifest in self.list_manifests(SnapshotStatus.APPROVED):
            if (
                manifest.request_kind == "market_daily"
                and manifest.provider == provider
                and manifest.start_date == session_date
                and manifest.end_date == session_date
                and manifest.universe_snapshot_id == universe_snapshot_id
            ):
                return manifest
        return None

    def list_manifests(self, status: SnapshotStatus | None = None) -> tuple[SnapshotManifest, ...]:
        query = "SELECT snapshot_id FROM snapshot_catalog"
        parameters: list[str] = []
        if status is not None:
            query += " WHERE status = ?"
            parameters.append(status.value)
        query += " ORDER BY created_at, snapshot_id"
        with duckdb.connect(str(self.catalog_path), read_only=True) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(self.load_manifest(row[0]) for row in rows)

    def _initialize_catalog(self) -> None:
        with duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshot_catalog (
                    snapshot_id VARCHAR PRIMARY KEY,
                    provider VARCHAR NOT NULL,
                    adapter_version VARCHAR NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    row_count BIGINT NOT NULL,
                    content_sha256 VARCHAR NOT NULL,
                    raw_path VARCHAR NOT NULL,
                    normalized_path VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    quality_run_id VARCHAR,
                    created_at VARCHAR NOT NULL,
                    request_kind VARCHAR NOT NULL DEFAULT 'security_daily',
                    universe_snapshot_id VARCHAR
                )
                """
            )
            connection.execute(
                "ALTER TABLE snapshot_catalog "
                "ADD COLUMN IF NOT EXISTS request_kind VARCHAR DEFAULT 'security_daily'"
            )
            connection.execute(
                "ALTER TABLE snapshot_catalog ADD COLUMN IF NOT EXISTS universe_snapshot_id VARCHAR"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quality_catalog (
                    quality_run_id VARCHAR PRIMARY KEY,
                    snapshot_id VARCHAR NOT NULL,
                    approved BOOLEAN NOT NULL,
                    checked_rows BIGINT NOT NULL,
                    error_count BIGINT NOT NULL,
                    warning_count BIGINT NOT NULL,
                    report_path VARCHAR NOT NULL
                )
                """
            )

    def _register_manifest(self, manifest: SnapshotManifest) -> None:
        with duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO snapshot_catalog
                (snapshot_id, provider, adapter_version, start_date, end_date, row_count,
                 content_sha256, raw_path, normalized_path, status, quality_run_id, created_at,
                 request_kind, universe_snapshot_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    manifest.snapshot_id,
                    manifest.provider,
                    manifest.adapter_version,
                    manifest.start_date,
                    manifest.end_date,
                    manifest.row_count,
                    manifest.content_sha256,
                    manifest.raw_path,
                    manifest.normalized_path,
                    manifest.status.value,
                    manifest.quality_run_id,
                    manifest.created_at.isoformat(),
                    manifest.request_kind,
                    manifest.universe_snapshot_id,
                ],
            )


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value) if not isinstance(value, (str, int, float, bool, type(None))) else value


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SnapshotStoreError(f"failed to write snapshot file: {path}") from exc
