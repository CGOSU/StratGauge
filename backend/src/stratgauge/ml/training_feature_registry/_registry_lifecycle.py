"""Lifecycle and query methods for the feature model registry."""

from __future__ import annotations

from datetime import datetime

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.ml._hashing import sha256_json
from stratgauge.ml.training_feature_lightgbm_calibration import (
    FeatureCalibrationOutput,
    FeatureLightGBMOutput,
)
from stratgauge.ml.training_feature_registry._registry_base import (
    FeatureRegistryBase,
    _lifecycle_event_id,
    _require_aware,
    _require_text,
    _safe_identifier,
    _sort_key,
)
from stratgauge.ml.training_feature_registry.core import (
    FeatureModelRegistryError,
)
from stratgauge.ml.training_feature_registry.models import (
    FeatureModelLifecycle,
    FeatureModelLifecycleEvent,
    FeatureModelRegistration,
)


class LifecycleMixin(FeatureRegistryBase):
    """List registrations and manage lifecycle transitions."""

    def load(self, registration_id: str) -> FeatureModelRegistration:
        """Load a registration and verify every copied source artifact."""

        return self._load_verified(registration_id)[0]

    def list_events(self, registration_id: str) -> tuple[FeatureModelLifecycleEvent, ...]:
        """Read the append-only lifecycle history for one registration."""

        _safe_identifier(registration_id)
        events: list[FeatureModelLifecycleEvent] = []
        try:
            for path in self.event_dir.glob("*.json"):
                event = FeatureModelLifecycleEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if event.event_id != _lifecycle_event_id(event):
                    raise FeatureModelRegistryError(
                        f"feature model lifecycle event identity is invalid: {path.name}"
                    )
                if event.registration_id == registration_id:
                    events.append(event)
        except FeatureModelRegistryError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise FeatureModelRegistryError(
                f"feature model lifecycle event is invalid: {registration_id}"
            ) from exc
        ordered = tuple(sorted(events, key=lambda item: (item.changed_at, item.event_id)))
        current: FeatureModelLifecycle = "challenger"
        previous_changed_at: datetime | None = None
        for event in ordered:
            if event.from_lifecycle != current:
                raise FeatureModelRegistryError(
                    "feature model lifecycle event chain is inconsistent"
                )
            if previous_changed_at is not None and event.changed_at <= previous_changed_at:
                raise FeatureModelRegistryError("feature model lifecycle timestamps must advance")
            current = event.to_lifecycle
            previous_changed_at = event.changed_at
        return ordered

    def transition(
        self,
        registration_id: str,
        to_lifecycle: FeatureModelLifecycle,
        *,
        changed_at: datetime,
        reason: str,
        operator: str,
    ) -> FeatureModelRegistration:
        """Manually authorize a challenger for shadow, or retire a shadow model."""

        _require_aware(changed_at, "changed_at")
        _require_text(reason, "reason")
        _require_text(operator, "operator")
        with local_write_lock(self.root, name="feature-model-registry"):
            current = self.load(registration_id)
            if current.lifecycle == to_lifecycle:
                return current
            allowed = {
                ("challenger", "shadow"),
                ("challenger", "retired"),
                ("shadow", "retired"),
            }
            if (current.lifecycle, to_lifecycle) not in allowed:
                raise FeatureModelRegistryError(
                    f"invalid feature model lifecycle transition: "
                    f"{current.lifecycle}->{to_lifecycle}"
                )
            events = self.list_events(registration_id)
            if events and changed_at <= events[-1].changed_at:
                raise FeatureModelRegistryError("feature model lifecycle timestamp must advance")
            identity = {
                "changed_at": changed_at.isoformat(),
                "from_lifecycle": current.lifecycle,
                "operator": operator.strip(),
                "reason": reason.strip(),
                "registration_id": registration_id,
                "to_lifecycle": to_lifecycle,
            }
            event = FeatureModelLifecycleEvent(
                event_id=f"feature-model-event-{sha256_json(identity)[:20]}",
                registration_id=registration_id,
                from_lifecycle=current.lifecycle,
                to_lifecycle=to_lifecycle,
                changed_at=changed_at,
                reason=reason,
                operator=operator,
            )
            self._write_once(
                self.event_dir / f"{event.event_id}.json",
                (event.model_dump_json(indent=2) + "\n").encode("utf-8"),
                "feature model lifecycle event",
            )
        return self.load(registration_id)

    def load_outputs(
        self,
        registration_id: str,
    ) -> tuple[FeatureModelRegistration, FeatureLightGBMOutput, FeatureCalibrationOutput | None]:
        """Load registration metadata together with all verified copied evidence."""

        return self._load_verified(registration_id)

    def list_registrations(self) -> tuple[FeatureModelRegistration, ...]:
        """List verified purpose-aware challenger registrations."""

        try:
            registration_ids = tuple(path.stem for path in self.registration_dir.glob("*.json"))
            return tuple(sorted((self.load(item) for item in registration_ids), key=_sort_key))
        except FeatureModelRegistryError:
            raise
        except OSError as exc:
            raise FeatureModelRegistryError("failed to list feature model registrations") from exc
