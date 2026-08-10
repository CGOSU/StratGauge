"""Immutable point-in-time universe snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from pydantic import Field, field_validator, model_validator

from stratgauge.data_pipeline.store import SnapshotStatus
from stratgauge.domain.models import DomainModel, Exchange, Security, canonical_symbol

UNIVERSE_SNAPSHOT_VERSION = "universe-snapshot-v1"


class SecurityOverride(DomainModel):
    """User annotation for display; it never changes source membership facts."""

    override_id: str
    symbol: str
    effective_at: datetime
    name: str | None = None
    note: str = Field(min_length=1)
    operator: str = Field(min_length=1)

    @field_validator("override_id", "symbol", "note", "operator")
    @classmethod
    def validate_override_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("security override text must not be empty")
        return normalized

    @field_validator("override_id")
    @classmethod
    def validate_override_id_path(cls, value: str) -> str:
        if any(character in value for character in ("/", "\\", "\0", "\r", "\n")):
            raise ValueError("security override identifier contains an invalid path character")
        return value

    @field_validator("effective_at")
    @classmethod
    def validate_effective_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("security override effective_at must include a timezone")
        return value

    @field_validator("name")
    @classmethod
    def normalize_override_name(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.upper()
        parts = normalized.split(".")
        if len(parts) != 2:
            raise ValueError("security override symbol must use CODE.EXCHANGE")
        try:
            if canonical_symbol(parts[0], Exchange(parts[1])) != normalized:
                raise ValueError("security override symbol must use CODE.EXCHANGE")
        except ValueError as exc:
            raise ValueError("security override symbol must use CODE.EXCHANGE") from exc
        return normalized


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


class UniverseOverrideStoreError(RuntimeError):
    """A user security override cannot be safely persisted or read."""


class UniverseOverrideStore:
    """Append-only user annotations kept outside immutable source snapshots."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.override_dir = self.root / "universe" / "overrides"
        self.override_dir.mkdir(parents=True, exist_ok=True)

    def write(self, override: SecurityOverride) -> SecurityOverride:
        path = self.override_dir / f"{override.override_id}.json"
        if path.exists():
            existing = self.load(override.override_id)
            if existing != override:
                raise UniverseOverrideStoreError(
                    "security override identity already has different content"
                )
            return existing
        try:
            path.write_text(override.model_dump_json(indent=2), encoding="utf-8", newline="\n")
        except OSError as exc:
            raise UniverseOverrideStoreError("failed to write security override") from exc
        return override

    def load(self, override_id: str) -> SecurityOverride:
        path = self.override_dir / f"{override_id}.json"
        if not path.exists():
            raise UniverseOverrideStoreError(f"security override does not exist: {override_id}")
        try:
            return SecurityOverride.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise UniverseOverrideStoreError(
                f"security override is invalid: {override_id}"
            ) from exc

    def latest_for(self, symbol: str, as_of: datetime) -> SecurityOverride | None:
        overrides = tuple(self.load(path.stem) for path in self.override_dir.glob("*.json"))
        eligible = tuple(
            item for item in overrides if item.symbol == symbol and item.effective_at <= as_of
        )
        return max(eligible, key=lambda item: (item.effective_at, item.override_id), default=None)


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
