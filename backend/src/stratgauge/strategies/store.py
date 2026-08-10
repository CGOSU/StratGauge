"""Immutable strategy-version storage with append-only lifecycle events."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from pydantic import field_validator

from stratgauge.domain.models import DomainModel, StrategyLifecycle, StrategyVersion


class StrategyStoreError(RuntimeError):
    """A strategy version or lifecycle transition is invalid."""


class StrategyLifecycleEvent(DomainModel):
    """One append-only strategy lifecycle transition."""

    event_id: str
    strategy_version_id: str
    from_lifecycle: StrategyLifecycle
    to_lifecycle: StrategyLifecycle
    changed_at: datetime
    reason: str

    @field_validator("event_id", "strategy_version_id", "reason")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("strategy lifecycle text must not be empty")
        return value.strip()

    @field_validator("changed_at")
    @classmethod
    def validate_changed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("strategy lifecycle timestamp must include a timezone")
        return value


_ALLOWED_TRANSITIONS: Mapping[StrategyLifecycle, frozenset[StrategyLifecycle]] = {
    StrategyLifecycle.DRAFT: frozenset({StrategyLifecycle.VALIDATED, StrategyLifecycle.RETIRED}),
    StrategyLifecycle.VALIDATED: frozenset({StrategyLifecycle.SHADOW, StrategyLifecycle.RETIRED}),
    StrategyLifecycle.SHADOW: frozenset({StrategyLifecycle.ACTIVE, StrategyLifecycle.RETIRED}),
    StrategyLifecycle.ACTIVE: frozenset({StrategyLifecycle.RETIRED}),
    StrategyLifecycle.RETIRED: frozenset(),
}


class StrategyVersionStore:
    """Persist versions once and materialize lifecycle from immutable events."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.version_dir = self.root / "strategies" / "versions"
        self.event_dir = self.root / "strategies" / "lifecycle"
        self.version_dir.mkdir(parents=True, exist_ok=True)
        self.event_dir.mkdir(parents=True, exist_ok=True)

    def create(self, strategy: StrategyVersion) -> StrategyVersion:
        path = self._version_path(strategy.strategy_version_id)
        if path.exists():
            try:
                existing_base = StrategyVersion.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise StrategyStoreError("strategy version is invalid") from exc
            if existing_base != strategy:
                raise StrategyStoreError("strategy version identity already has different content")
            return self.load(strategy.strategy_version_id)
        try:
            path.write_text(strategy.model_dump_json(indent=2), encoding="utf-8", newline="\n")
        except OSError as exc:
            raise StrategyStoreError("failed to write strategy version") from exc
        return strategy

    def load(self, strategy_version_id: str) -> StrategyVersion:
        path = self._version_path(strategy_version_id)
        if not path.exists():
            raise StrategyStoreError(f"strategy version does not exist: {strategy_version_id}")
        try:
            base = StrategyVersion.model_validate_json(path.read_text(encoding="utf-8"))
            events = self.list_events(strategy_version_id)
        except StrategyStoreError:
            raise
        except Exception as exc:
            raise StrategyStoreError(f"strategy version is invalid: {strategy_version_id}") from exc
        if not events:
            return base
        return base.model_copy(update={"lifecycle": events[-1].to_lifecycle})

    def list_versions(self) -> tuple[StrategyVersion, ...]:
        return tuple(
            sorted(
                (self.load(path.stem) for path in self.version_dir.glob("*.json")),
                key=lambda strategy: strategy.strategy_version_id,
            )
        )

    def list_events(self, strategy_version_id: str) -> tuple[StrategyLifecycleEvent, ...]:
        events: list[StrategyLifecycleEvent] = []
        for path in self.event_dir.glob("*.json"):
            try:
                event = StrategyLifecycleEvent.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise StrategyStoreError(
                    f"strategy lifecycle event is invalid: {path.name}"
                ) from exc
            if event.strategy_version_id == strategy_version_id:
                events.append(event)
        return tuple(sorted(events, key=lambda event: (event.changed_at, event.event_id)))

    def _version_path(self, strategy_version_id: str) -> Path:
        normalized = strategy_version_id.strip()
        if not normalized or any(
            character in normalized for character in ("/", "\\", "\0", "\r", "\n")
        ):
            raise StrategyStoreError(
                "strategy version identifier contains an invalid path character"
            )
        return self.version_dir / f"{normalized}.json"

    def transition(
        self,
        strategy_version_id: str,
        to_lifecycle: StrategyLifecycle,
        *,
        changed_at: datetime,
        reason: str,
    ) -> StrategyVersion:
        current = self.load(strategy_version_id)
        if changed_at.tzinfo is None or changed_at.utcoffset() is None:
            raise StrategyStoreError("strategy lifecycle timestamp must include a timezone")
        if not reason.strip():
            raise StrategyStoreError("strategy lifecycle reason must not be empty")
        if current.lifecycle is to_lifecycle:
            return current
        events = self.list_events(strategy_version_id)
        if events and changed_at <= events[-1].changed_at:
            raise StrategyStoreError("strategy lifecycle timestamp must advance")
        if to_lifecycle not in _ALLOWED_TRANSITIONS[current.lifecycle]:
            raise StrategyStoreError(
                f"invalid strategy lifecycle transition: "
                f"{current.lifecycle.value}->{to_lifecycle.value}"
            )
        identity = {
            "changed_at": changed_at.isoformat(),
            "from_lifecycle": current.lifecycle.value,
            "reason": reason.strip(),
            "strategy_version_id": strategy_version_id,
            "to_lifecycle": to_lifecycle.value,
        }
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        event = StrategyLifecycleEvent(
            event_id=f"strategy-event-{digest[:20]}",
            strategy_version_id=strategy_version_id,
            from_lifecycle=current.lifecycle,
            to_lifecycle=to_lifecycle,
            changed_at=changed_at,
            reason=reason,
        )
        path = self.event_dir / f"{event.event_id}.json"
        if path.exists():
            existing = StrategyLifecycleEvent.model_validate_json(path.read_text(encoding="utf-8"))
            if existing != event:
                raise StrategyStoreError("strategy lifecycle event identity has different content")
        else:
            try:
                path.write_text(event.model_dump_json(indent=2), encoding="utf-8", newline="\n")
            except OSError as exc:
                raise StrategyStoreError("failed to write strategy lifecycle event") from exc
        return self.load(strategy_version_id)
