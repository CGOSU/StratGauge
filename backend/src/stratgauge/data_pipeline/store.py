"""Immutable raw/normalized snapshot storage."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import duckdb
import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from stratgauge._files import atomic_write_text
from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.data_pipeline.quality import QualityReport
from stratgauge.data_sources.base import (
    AdapterResult,
    AdjustmentFactorAdapterResult,
    DailyBarRequest,
    MarketAdapterResult,
)
from stratgauge.domain.models import AdjustmentFactor, DailyBar


class SnapshotStoreError(RuntimeError):
    """A snapshot cannot be safely read or written."""


class SnapshotStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class SnapshotVerificationReport(BaseModel):
    """Read-only consistency report for one snapshot storage root."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    checked_manifests: int = Field(ge=0)
    invalid_manifests: tuple[str, ...] = ()
    missing_files: tuple[str, ...] = ()
    catalog_missing: tuple[str, ...] = ()
    catalog_orphans: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return not (
            self.invalid_manifests
            or self.missing_files
            or self.catalog_missing
            or self.catalog_orphans
        )


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
    source_daily_snapshot_id: str | None = None
    request_sha256: str | None = None


_SNAPSHOT_CATALOG_COLUMNS = (
    "snapshot_id",
    "provider",
    "adapter_version",
    "start_date",
    "end_date",
    "row_count",
    "content_sha256",
    "raw_path",
    "normalized_path",
    "status",
    "quality_run_id",
    "created_at",
    "request_kind",
    "universe_snapshot_id",
    "source_daily_snapshot_id",
    "request_sha256",
)


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
        with local_write_lock(self.root):
            self._initialize_catalog()

    def write(
        self,
        result: AdapterResult | MarketAdapterResult | AdjustmentFactorAdapterResult,
    ) -> SnapshotManifest:
        with local_write_lock(self.root):
            return self._write_unlocked(result)

    def _write_unlocked(
        self,
        result: AdapterResult | MarketAdapterResult | AdjustmentFactorAdapterResult,
    ) -> SnapshotManifest:
        request_payload = result.request.model_dump(mode="json")
        payload = {
            "provider": result.provider,
            "adapter_version": result.adapter_version,
            "request": request_payload,
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
        _write_snapshot_file(raw_path, canonical_payload)
        normalized_rows: tuple[Any, ...]
        if isinstance(result, AdjustmentFactorAdapterResult):
            normalized_rows = result.factors
        else:
            normalized_rows = cast(AdapterResult, result).bars
        if normalized_rows:
            normalized_frame = pl.DataFrame([row.model_dump() for row in normalized_rows])
        else:
            model_type = (
                AdjustmentFactor if isinstance(result, AdjustmentFactorAdapterResult) else DailyBar
            )
            normalized_frame = pl.DataFrame(schema=list(model_type.model_fields))
        normalized_frame.write_parquet(normalized_path)
        manifest = SnapshotManifest(
            snapshot_id=snapshot_id,
            provider=result.provider,
            adapter_version=result.adapter_version,
            start_date=result.request.start_date,
            end_date=result.request.end_date,
            row_count=len(normalized_rows),
            content_sha256=content_sha256,
            raw_path=str(raw_path.relative_to(self.root)),
            normalized_path=str(normalized_path.relative_to(self.root)),
            created_at=result.fetched_at,
            request_kind=result.request.request_kind,
            universe_snapshot_id=getattr(result.request, "universe_snapshot_id", None),
            source_daily_snapshot_id=getattr(result.request, "source_daily_snapshot_id", None),
            request_sha256=_canonical_sha256(request_payload),
        )
        _write_snapshot_file(
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

    def load_bars(
        self,
        snapshot_id: str,
        *,
        symbols: Iterable[str] | None = None,
    ) -> tuple[DailyBar, ...]:
        """Load normalized bars only from an approved snapshot.

        When ``symbols`` is provided, predicate pushdown keeps workbench reads from
        materializing unrelated securities from a full-market Parquet snapshot.
        """

        manifest = self.load_manifest(snapshot_id)
        if manifest.status is not SnapshotStatus.APPROVED:
            raise SnapshotStoreError(f"snapshot is not approved: {snapshot_id}")
        path = self.root / manifest.normalized_path
        if not path.exists():
            raise SnapshotStoreError(f"normalized snapshot does not exist: {snapshot_id}")
        requested_symbols = None if symbols is None else tuple(dict.fromkeys(symbols))
        if requested_symbols == ():
            return ()
        try:
            frame = pl.scan_parquet(path)
            if requested_symbols is not None:
                frame = frame.filter(pl.col("symbol").is_in(requested_symbols))
            rows = frame.collect().to_dicts()
            return tuple(DailyBar.model_validate(row) for row in rows)
        except Exception as exc:
            raise SnapshotStoreError(f"normalized snapshot is invalid: {snapshot_id}") from exc

    def load_adjustment_factors(self, snapshot_id: str) -> tuple[AdjustmentFactor, ...]:
        """Load normalized factors only from an approved snapshot."""

        manifest = self.load_manifest(snapshot_id)
        if manifest.status is not SnapshotStatus.APPROVED:
            raise SnapshotStoreError(f"snapshot is not approved: {snapshot_id}")
        if manifest.request_kind != "adjustment_factor":
            raise SnapshotStoreError(
                f"snapshot is not an adjustment-factor snapshot: {snapshot_id}"
            )
        path = self.root / manifest.normalized_path
        if not path.exists():
            raise SnapshotStoreError(f"normalized snapshot does not exist: {snapshot_id}")
        try:
            rows = pl.read_parquet(path).to_dicts()
            return tuple(AdjustmentFactor.model_validate(row) for row in rows)
        except Exception as exc:
            raise SnapshotStoreError(
                f"normalized adjustment factors are invalid: {snapshot_id}"
            ) from exc

    def update_status(
        self,
        snapshot_id: str,
        status: SnapshotStatus,
        quality_run_id: str | None = None,
    ) -> SnapshotManifest:
        with local_write_lock(self.root):
            return self._update_status_unlocked(snapshot_id, status, quality_run_id)

    def _update_status_unlocked(
        self,
        snapshot_id: str,
        status: SnapshotStatus,
        quality_run_id: str | None = None,
    ) -> SnapshotManifest:
        manifest = self.load_manifest(snapshot_id)
        updated = manifest.model_copy(update={"status": status, "quality_run_id": quality_run_id})
        _write_snapshot_file(
            self.manifest_dir / f"{snapshot_id}.json",
            updated.model_dump_json(indent=2),
        )
        self._register_manifest(updated)
        return updated

    def save_quality_report(self, report: QualityReport, quality_run_id: str) -> str:
        with local_write_lock(self.root):
            return self._save_quality_report_unlocked(report, quality_run_id)

    def _save_quality_report_unlocked(self, report: QualityReport, quality_run_id: str) -> str:
        path = self.quality_dir / f"{quality_run_id}.json"
        _write_snapshot_file(path, report.model_dump_json(indent=2))
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

    def load_quality_report(self, quality_run_id: str) -> QualityReport:
        path = self.quality_dir / f"{quality_run_id}.json"
        if not path.exists():
            raise SnapshotStoreError(f"quality report does not exist: {quality_run_id}")
        try:
            return QualityReport.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SnapshotStoreError(f"quality report is invalid: {quality_run_id}") from exc

    def verify_fast(self) -> SnapshotVerificationReport:
        """Check catalog membership and file presence without reading file contents.

        This is a cheap operational health check. It deliberately does not validate
        raw hashes, manifest JSON contents, or Parquet row counts; use ``verify``
        for that deep integrity check.
        """

        with local_write_lock(self.root):
            catalog_manifests = self._query_catalog_manifests()
            catalog_ids = {manifest.snapshot_id for manifest in catalog_manifests}
            manifest_ids = {path.stem for path in self.manifest_dir.glob("snapshot-*.json")}
            missing_files: list[str] = []
            for manifest in catalog_manifests:
                if not (self.root / manifest.raw_path).is_file():
                    missing_files.append(f"{manifest.snapshot_id}:raw")
                if not (self.root / manifest.normalized_path).is_file():
                    missing_files.append(f"{manifest.snapshot_id}:normalized")
            return SnapshotVerificationReport(
                checked_manifests=len(catalog_manifests),
                missing_files=tuple(sorted(missing_files)),
                catalog_missing=tuple(sorted(manifest_ids - catalog_ids)),
                catalog_orphans=tuple(sorted(catalog_ids - manifest_ids)),
            )

    def verify(self) -> SnapshotVerificationReport:
        """Check immutable files and the DuckDB catalog without changing them."""

        with local_write_lock(self.root):
            manifest_ids: set[str] = set()
            invalid: list[str] = []
            missing_files: list[str] = []
            for path in sorted(self.manifest_dir.glob("snapshot-*.json")):
                snapshot_id = path.stem
                manifest_ids.add(snapshot_id)
                try:
                    manifest = self.load_manifest(snapshot_id)
                    raw_path = self.root / manifest.raw_path
                    normalized_path = self.root / manifest.normalized_path
                    if not raw_path.exists():
                        missing_files.append(f"{snapshot_id}:raw")
                    elif (
                        hashlib.sha256(raw_path.read_bytes()).hexdigest() != manifest.content_sha256
                    ):
                        invalid.append(f"{snapshot_id}:raw_hash")
                    if not normalized_path.exists():
                        missing_files.append(f"{snapshot_id}:normalized")
                    else:
                        if pl.read_parquet(normalized_path).height != manifest.row_count:
                            invalid.append(f"{snapshot_id}:row_count")
                except Exception:
                    invalid.append(snapshot_id)

            with duckdb.connect(str(self.catalog_path), read_only=True) as connection:
                catalog_ids = {
                    row[0]
                    for row in connection.execute(
                        "SELECT snapshot_id FROM snapshot_catalog"
                    ).fetchall()
                }
            return SnapshotVerificationReport(
                checked_manifests=len(manifest_ids),
                invalid_manifests=tuple(sorted(invalid)),
                missing_files=tuple(sorted(missing_files)),
                catalog_missing=tuple(sorted(manifest_ids - catalog_ids)),
                catalog_orphans=tuple(sorted(catalog_ids - manifest_ids)),
            )

    def repair_catalog(self) -> tuple[str, ...]:
        """Register valid manifests that are missing from the local index."""

        with local_write_lock(self.root):
            repaired: list[str] = []
            for path in sorted(self.manifest_dir.glob("snapshot-*.json")):
                manifest = self.load_manifest(path.stem)
                self._register_manifest(manifest)
                repaired.append(manifest.snapshot_id)
            return tuple(repaired)

    def find_approved_adjustment_factor_snapshot(
        self,
        *,
        provider: str,
        session_date: date,
        universe_snapshot_id: str,
        source_daily_snapshot_id: str,
    ) -> SnapshotManifest | None:
        matches = self._query_catalog_manifests(
            """
            WHERE status = ?
              AND provider = ?
              AND request_kind = ?
              AND start_date = ?
              AND end_date = ?
              AND universe_snapshot_id = ?
              AND source_daily_snapshot_id = ?
            """,
            (
                SnapshotStatus.APPROVED.value,
                provider,
                "adjustment_factor",
                session_date,
                session_date,
                universe_snapshot_id,
                source_daily_snapshot_id,
            ),
        )
        return matches[0] if matches else None

    def find_approved_market_snapshots(
        self,
        *,
        provider: str,
        session_dates: Iterable[date],
        universe_snapshot_id: str,
    ) -> dict[date, SnapshotManifest]:
        """Find approved market snapshots for several sessions in one catalog query."""

        dates = tuple(dict.fromkeys(session_dates))
        if not dates:
            return {}
        placeholders = ", ".join("?" for _ in dates)
        matches = self._query_catalog_manifests(
            f"""
            WHERE status = ?
              AND provider = ?
              AND request_kind = ?
              AND start_date = end_date
              AND universe_snapshot_id = ?
              AND start_date IN ({placeholders})
            """,
            (
                SnapshotStatus.APPROVED.value,
                provider,
                "market_daily",
                universe_snapshot_id,
                *dates,
            ),
        )
        snapshots: dict[date, SnapshotManifest] = {}
        for manifest in matches:
            snapshots.setdefault(manifest.start_date, manifest)
        return snapshots

    def find_approved_market_snapshot(
        self,
        *,
        provider: str,
        session_date: date,
        universe_snapshot_id: str,
    ) -> SnapshotManifest | None:
        return self.find_approved_market_snapshots(
            provider=provider,
            session_dates=(session_date,),
            universe_snapshot_id=universe_snapshot_id,
        ).get(session_date)

    def find_approved_security_snapshot(
        self,
        *,
        provider: str,
        request: DailyBarRequest,
    ) -> SnapshotManifest | None:
        """Find an approved single-security snapshot with the exact request."""

        request_sha256 = _canonical_sha256(request.model_dump(mode="json"))
        matches = self._query_catalog_manifests(
            """
            WHERE status = ?
              AND provider = ?
              AND request_kind = ?
              AND (request_sha256 = ? OR request_sha256 IS NULL)
            """,
            (
                SnapshotStatus.APPROVED.value,
                provider,
                request.request_kind,
                request_sha256,
            ),
        )
        for manifest in matches:
            if manifest.request_sha256 == request_sha256:
                return manifest
            if _raw_request_sha256(manifest, self.root) == request_sha256:
                return manifest
        return None

    def list_manifests(self, status: SnapshotStatus | None = None) -> tuple[SnapshotManifest, ...]:
        if status is None:
            return self._query_catalog_manifests()
        return self._query_catalog_manifests("WHERE status = ?", (status.value,))

    def _query_catalog_manifests(
        self,
        where_clause: str = "",
        parameters: tuple[Any, ...] = (),
    ) -> tuple[SnapshotManifest, ...]:
        columns = ", ".join(_SNAPSHOT_CATALOG_COLUMNS)
        query = f"SELECT {columns} FROM snapshot_catalog"
        if where_clause:
            query += f" {where_clause.strip()}"
        query += " ORDER BY created_at, snapshot_id"
        with duckdb.connect(str(self.catalog_path), read_only=True) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_manifest_from_catalog_row(row) for row in rows)

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
                    universe_snapshot_id VARCHAR,
                    source_daily_snapshot_id VARCHAR,
                    request_sha256 VARCHAR
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
                "ALTER TABLE snapshot_catalog "
                "ADD COLUMN IF NOT EXISTS source_daily_snapshot_id VARCHAR"
            )
            connection.execute(
                "ALTER TABLE snapshot_catalog ADD COLUMN IF NOT EXISTS request_sha256 VARCHAR"
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS snapshot_catalog_market_lookup
                ON snapshot_catalog (
                    status, provider, request_kind, start_date, end_date,
                    universe_snapshot_id
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS snapshot_catalog_factor_lookup
                ON snapshot_catalog (
                    status, provider, request_kind, start_date, end_date,
                    universe_snapshot_id, source_daily_snapshot_id
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS snapshot_catalog_request_lookup
                ON snapshot_catalog (status, provider, request_kind, request_sha256)
                """
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
                 request_kind, universe_snapshot_id, source_daily_snapshot_id, request_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    manifest.source_daily_snapshot_id,
                    manifest.request_sha256,
                ],
            )


def _manifest_from_catalog_row(row: tuple[Any, ...]) -> SnapshotManifest:
    return SnapshotManifest.model_validate(dict(zip(_SNAPSHOT_CATALOG_COLUMNS, row, strict=True)))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value) if not isinstance(value, (str, int, float, bool, type(None))) else value


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _raw_request_sha256(manifest: SnapshotManifest, root: Path) -> str | None:
    try:
        payload = json.loads((root / manifest.raw_path).read_text(encoding="utf-8"))
        request = payload["request"]
        return _canonical_sha256(request)
    except (OSError, KeyError, TypeError, ValueError):
        return None


def _write_snapshot_file(path: Path, content: str) -> None:
    try:
        atomic_write_text(path, content)
    except OSError as exc:
        raise SnapshotStoreError(f"failed to write snapshot file: {path}") from exc
