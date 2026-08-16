"""Registry store base: attributes and private infrastructure."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from stratgauge.domain.models import ModelLifecycle
from stratgauge.ml._hashing import sha256_bytes, sha256_json
from stratgauge.ml.registry.core import (
    ModelRegistryError,
)
from stratgauge.ml.registry.helpers import (
    _require_aware,
    _require_text,
    _safe_identifier,
)
from stratgauge.ml.registry.models import (
    _ALLOWED_TRANSITIONS,
    ModelLifecycleEvent,
    RegisteredModel,
)


class ModelRegistryBase:
    """Attributes and shared private helpers for registry mixins."""

    root: Path
    registration_dir: Path
    event_dir: Path
    artifact_dir: Path
    runtime_artifact_dir: Path
    evaluation_dir: Path
    training_manifest_dir: Path

    # Cross-mixin surface: implementations live in registration/lifecycle
    # mixins; declared here for type checking.
    def load(self, model_version_id: str) -> RegisteredModel:
        raise NotImplementedError

    def list_events(self, model_version_id: str) -> tuple[ModelLifecycleEvent, ...]:
        raise NotImplementedError

    def _append_event(
        self,
        current: RegisteredModel,
        to_lifecycle: ModelLifecycle,
        *,
        changed_at: datetime,
        reason: str,
        operator: str,
        evaluation_sha256: str | None = None,
    ) -> ModelLifecycleEvent:
        events = self.list_events(current.model_version_id)
        if events and changed_at <= events[-1].changed_at:
            raise ModelRegistryError("model lifecycle timestamp must advance")
        if to_lifecycle not in _ALLOWED_TRANSITIONS[current.lifecycle] and not (
            current.lifecycle is ModelLifecycle.RETIRED and to_lifecycle is ModelLifecycle.ACTIVE
        ):
            raise ModelRegistryError(
                f"invalid model lifecycle transition: "
                f"{current.lifecycle.value}->{to_lifecycle.value}"
            )
        identity = {
            "changed_at": changed_at.isoformat(),
            "evaluation_sha256": evaluation_sha256,
            "from_lifecycle": current.lifecycle.value,
            "model_version_id": current.model_version_id,
            "operator": operator.strip(),
            "reason": reason.strip(),
            "to_lifecycle": to_lifecycle.value,
        }
        event_id = f"model-event-{sha256_json(identity)[:20]}"
        event = ModelLifecycleEvent(
            event_id=event_id,
            model_version_id=current.model_version_id,
            from_lifecycle=current.lifecycle,
            to_lifecycle=to_lifecycle,
            changed_at=changed_at,
            reason=reason,
            operator=operator,
            evaluation_sha256=evaluation_sha256,
        )
        self._write_once(
            self.event_dir / f"{event.event_id}.json",
            event.model_dump_json(indent=2).encode("utf-8"),
            "model lifecycle event",
        )
        return event

    @staticmethod
    def _validate_transition_inputs(
        current: RegisteredModel,
        to_lifecycle: ModelLifecycle,
        changed_at: datetime,
        reason: str,
        operator: str,
    ) -> None:
        _require_aware(changed_at, "changed_at")
        _require_text(reason, "reason")
        _require_text(operator, "operator")
        if to_lifecycle not in _ALLOWED_TRANSITIONS[current.lifecycle] and not (
            current.lifecycle is ModelLifecycle.RETIRED and to_lifecycle is ModelLifecycle.ACTIVE
        ):
            raise ModelRegistryError(
                f"invalid model lifecycle transition: "
                f"{current.lifecycle.value}->{to_lifecycle.value}"
            )

    def _registration_path(self, model_version_id: str) -> Path:
        return self.registration_dir / f"{_safe_identifier(model_version_id)}.json"

    def _artifact_path(self, model_version_id: str) -> Path:
        return self.artifact_dir / f"{_safe_identifier(model_version_id)}.json"

    def _runtime_artifact_path(self, model_version_id: str) -> Path:
        return self.runtime_artifact_dir / f"{_safe_identifier(model_version_id)}.bin"

    def _evaluation_path(self, model_version_id: str) -> Path:
        return self.evaluation_dir / f"{_safe_identifier(model_version_id)}.json"

    def _training_manifest_path(self, model_version_id: str) -> Path:
        return self.training_manifest_dir / f"{_safe_identifier(model_version_id)}.json"

    @staticmethod
    def _write_once(path: Path, content: bytes, artifact_name: str) -> None:
        if path.exists():
            try:
                existing = path.read_bytes()
            except OSError as exc:
                raise ModelRegistryError(f"failed to read existing {artifact_name}") from exc
            if existing != content:
                raise ModelRegistryError(f"{artifact_name} identity already has different content")
            return
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ModelRegistryError(f"failed to write {artifact_name}") from exc

    @staticmethod
    def _verify_artifact(path: Path, expected_sha256: str, artifact_name: str) -> None:
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ModelRegistryError(f"{artifact_name} is missing") from exc
        if sha256_bytes(content) != expected_sha256:
            raise ModelRegistryError(f"{artifact_name} hash does not match registration")
