"""Immutable point-in-time universe snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from pydantic import field_validator, model_validator

from stratgauge.data_pipeline.store import SnapshotStatus
from stratgauge.domain.models import DomainModel, Security

UNIVERSE_SNAPSHOT_VERSION = "universe-snapshot-v1"


class UniverseSnapshot(DomainModel):
    """The securities known to belong to one universe at an explicit time."""

    universe_snapshot_id: str
    snapshot_version: str = UNIVERSE_SNAPSHOT_VERSION
    as_of: datetime
    source: str
    source_snapshot_ids: tuple[str, ...]
    securities: tuple[Security, ...]

    @field_validator("universe_snapshot_id", "source")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("universe snapshot text must not be empty")
        return value.strip()

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("universe as_of must include a timezone")
        return value

    @field_validator("source_snapshot_ids")
    @classmethod
    def validate_source_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("universe requires approved source snapshot IDs")
        return tuple(dict.fromkeys(item.strip() for item in value))

    @model_validator(mode="after")
    def validate_securities(self) -> UniverseSnapshot:
        symbols = tuple(security.symbol for security in self.securities)
        if len(symbols) != len(set(symbols)):
            raise ValueError("universe cannot contain duplicate symbols")
        return self


def securities_at(
    securities: Sequence[Security],
    *,
    as_of: date,
) -> tuple[Security, ...]:
    """Filter reference securities to membership visible on one calendar date."""

    effective: list[Security] = []
    for security in securities:
        if security.listed_at is not None and security.listed_at > as_of:
            continue
        if security.delisted_at is not None and security.delisted_at <= as_of:
            continue
        effective.append(security.model_copy(update={"listed": True}))
    return tuple(sorted(effective, key=lambda security: security.symbol))


def build_universe_snapshot(
    *,
    as_of: datetime,
    source: str,
    source_snapshot_ids: Sequence[str],
    securities: Sequence[Security],
) -> UniverseSnapshot:
    """Build a content-addressed universe snapshot from explicit membership."""

    _require_aware(as_of)
    ordered_securities = tuple(sorted(securities, key=lambda security: security.symbol))
    payload = {
        "as_of": as_of.astimezone(UTC).isoformat(),
        "securities": [security.model_dump(mode="json") for security in ordered_securities],
        "source": source.strip(),
        "source_snapshot_ids": tuple(dict.fromkeys(source_snapshot_ids)),
        "snapshot_version": UNIVERSE_SNAPSHOT_VERSION,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return UniverseSnapshot(
        universe_snapshot_id=f"universe-{digest[:20]}",
        as_of=as_of,
        source=source,
        source_snapshot_ids=tuple(source_snapshot_ids),
        securities=ordered_securities,
    )


class ApprovedSnapshotManifest(Protocol):
    status: SnapshotStatus


class ApprovedSnapshotReader(Protocol):
    def load_manifest(self, snapshot_id: str) -> ApprovedSnapshotManifest: ...


class UniverseSnapshotStoreError(RuntimeError):
    """A universe snapshot cannot be safely persisted or resolved."""


class UniverseSnapshotStore:
    """Persist immutable universe membership and resolve it without future leakage."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.snapshot_dir = self.root / "universe" / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        snapshot: UniverseSnapshot,
        snapshot_store: ApprovedSnapshotReader,
    ) -> UniverseSnapshot:
        for source_snapshot_id in snapshot.source_snapshot_ids:
            manifest = snapshot_store.load_manifest(source_snapshot_id)
            if manifest.status is not SnapshotStatus.APPROVED:
                raise UniverseSnapshotStoreError(
                    f"universe source snapshot is not approved: {source_snapshot_id}"
                )
        path = self.snapshot_dir / f"{snapshot.universe_snapshot_id}.json"
        if path.exists():
            existing = self.load(snapshot.universe_snapshot_id)
            if existing != snapshot:
                raise UniverseSnapshotStoreError(
                    "universe snapshot identity already has different content"
                )
            return existing
        try:
            path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8", newline="\n")
        except OSError as exc:
            raise UniverseSnapshotStoreError("failed to write universe snapshot") from exc
        return snapshot

    def load(self, universe_snapshot_id: str) -> UniverseSnapshot:
        path = self.snapshot_dir / f"{universe_snapshot_id}.json"
        if not path.exists():
            raise UniverseSnapshotStoreError(
                f"universe snapshot does not exist: {universe_snapshot_id}"
            )
        try:
            return UniverseSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise UniverseSnapshotStoreError(
                f"universe snapshot is invalid: {universe_snapshot_id}"
            ) from exc

    def latest_at(self, as_of: datetime) -> UniverseSnapshot | None:
        """Return the latest snapshot whose membership was known by ``as_of``."""

        _require_aware(as_of)
        snapshots = tuple(
            self.load(path.stem) for path in self.snapshot_dir.glob("universe-*.json")
        )
        eligible = tuple(snapshot for snapshot in snapshots if snapshot.as_of <= as_of)
        if not eligible:
            return None
        return max(eligible, key=lambda snapshot: (snapshot.as_of, snapshot.universe_snapshot_id))


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
