"""Lifecycle transitions for the model registry."""

from __future__ import annotations

from datetime import datetime

from stratgauge.domain.models import ModelLifecycle
from stratgauge.ml.registry._store_base import ModelRegistryBase
from stratgauge.ml.registry.core import (
    ModelRegistryError,
)
from stratgauge.ml.registry.helpers import (
    _require_aware,
    _require_text,
)
from stratgauge.ml.registry.models import (
    ModelLifecycleEvent,
    RegisteredModel,
)


class LifecycleMixin(ModelRegistryBase):
    """List, transition, promote and rollback registered models."""

    def list_models(self) -> tuple[RegisteredModel, ...]:
        return tuple(
            sorted(
                (self.load(path.stem) for path in self.registration_dir.glob("*.json")),
                key=lambda model: model.model_version_id,
            )
        )

    def list_events(self, model_version_id: str) -> tuple[ModelLifecycleEvent, ...]:
        events: list[ModelLifecycleEvent] = []
        for path in self.event_dir.glob("*.json"):
            try:
                event = ModelLifecycleEvent.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ModelRegistryError(f"model lifecycle event is invalid: {path.name}") from exc
            if event.model_version_id == model_version_id:
                events.append(event)
        return tuple(sorted(events, key=lambda event: (event.changed_at, event.event_id)))

    def active_models(
        self, *, tracking_policy_id: str | None = None
    ) -> tuple[RegisteredModel, ...]:
        models = tuple(
            model for model in self.list_models() if model.lifecycle is ModelLifecycle.ACTIVE
        )
        if tracking_policy_id is not None:
            models = tuple(
                model for model in models if model.tracking_policy_id == tracking_policy_id
            )
        if tracking_policy_id is not None and len(models) > 1:
            raise ModelRegistryError("more than one active model exists for the tracking policy")
        return models

    def transition(
        self,
        model_version_id: str,
        to_lifecycle: ModelLifecycle,
        *,
        changed_at: datetime,
        reason: str,
        operator: str,
    ) -> RegisteredModel:
        """Append one manual transition; activation requires the registered report hash."""

        current = self.load(model_version_id)
        if current.lifecycle is to_lifecycle:
            return current
        if to_lifecycle is ModelLifecycle.ACTIVE:
            raise ModelRegistryError(
                "activation requires promote() with explicit evaluation evidence hash"
            )
        self._validate_transition_inputs(current, to_lifecycle, changed_at, reason, operator)
        self._append_event(
            current,
            to_lifecycle,
            changed_at=changed_at,
            reason=reason,
            operator=operator,
        )
        return self.load(model_version_id)

    def promote(
        self,
        model_version_id: str,
        *,
        changed_at: datetime,
        reason: str,
        operator: str,
        evaluation_sha256: str,
        comparison_report_id: str | None = None,
        comparison_report_sha256: str | None = None,
    ) -> RegisteredModel:
        """Manually activate a shadow model after a passing comparison gate."""

        current = self.load(model_version_id)
        _require_aware(changed_at, "changed_at")
        _require_text(reason, "reason")
        _require_text(operator, "operator")
        if current.lifecycle is not ModelLifecycle.SHADOW:
            raise ModelRegistryError("only a shadow model can be promoted to active")
        if evaluation_sha256.strip() != current.evaluation_sha256:
            raise ModelRegistryError("promotion evaluation hash does not match registration")
        if comparison_report_id is None or comparison_report_sha256 is None:
            raise ModelRegistryError("promotion requires a comparison report")
        self._validate_comparison_for_promotion(
            current,
            changed_at=changed_at,
            comparison_report_id=comparison_report_id,
            comparison_report_sha256=comparison_report_sha256,
        )
        self._append_event(
            current,
            ModelLifecycle.ACTIVE,
            changed_at=changed_at,
            reason=reason,
            operator=operator,
            evaluation_sha256=evaluation_sha256,
        )
        return self.load(model_version_id)

    def rollback(
        self,
        model_version_id: str,
        *,
        changed_at: datetime,
        reason: str,
        operator: str,
        evaluation_sha256: str,
    ) -> RegisteredModel:
        """Retire the active model and activate a previously retired model manually."""

        target = self.load(model_version_id)
        self._validate_transition_inputs(
            target,
            ModelLifecycle.ACTIVE,
            changed_at,
            reason,
            operator,
        )
        if target.lifecycle is not ModelLifecycle.RETIRED:
            raise ModelRegistryError("rollback target must be retired")
        if evaluation_sha256.strip() != target.evaluation_sha256:
            raise ModelRegistryError("rollback evaluation hash does not match registration")
        active = self.active_models(tracking_policy_id=target.tracking_policy_id)
        if len(active) != 1:
            raise ModelRegistryError("rollback requires exactly one active model")
        if active[0].model_version_id == target.model_version_id:
            raise ModelRegistryError("rollback target is already active")
        self._append_event(
            active[0],
            ModelLifecycle.RETIRED,
            changed_at=changed_at,
            reason=f"rollback replaced by {target.model_version_id}: {reason}",
            operator=operator,
        )
        self._append_event(
            target,
            ModelLifecycle.ACTIVE,
            changed_at=changed_at,
            reason=reason,
            operator=operator,
            evaluation_sha256=evaluation_sha256,
        )
        return self.load(target.model_version_id)

    def _validate_comparison_for_promotion(
        self,
        current: RegisteredModel,
        *,
        changed_at: datetime,
        comparison_report_id: str,
        comparison_report_sha256: str,
    ) -> None:
        from stratgauge.ml.comparison import ModelComparisonError, ModelComparisonStore

        try:
            report, actual_sha256 = ModelComparisonStore(self.root).load_with_sha256(
                comparison_report_id
            )
        except (ModelComparisonError, OSError, ValueError) as exc:
            raise ModelRegistryError("promotion comparison report cannot be verified") from exc
        if actual_sha256 != comparison_report_sha256.strip():
            raise ModelRegistryError("promotion comparison report hash does not match")
        if report.compared_at > changed_at:
            raise ModelRegistryError("promotion cannot use a comparison from the future")
        if not report.gate_passed:
            raise ModelRegistryError("promotion comparison gate did not pass")
        if report.challenger_model_version_id != current.model_version_id:
            raise ModelRegistryError("comparison report cites a different challenger")
        if report.challenger_evaluation_sha256 != current.evaluation_sha256:
            raise ModelRegistryError("comparison report cites a different challenger evaluation")
        if report.tracking_policy_id != current.tracking_policy_id:
            raise ModelRegistryError("comparison report tracking policy does not match model")
        active = self.active_models(tracking_policy_id=current.tracking_policy_id)
        if report.incumbent_model_version_id is None:
            if active:
                raise ModelRegistryError(
                    "comparison report has no incumbent while an active model exists"
                )
            return
        if active:
            raise ModelRegistryError(
                "retire the compared active model before promoting its challenger"
            )
        try:
            incumbent = self.load(report.incumbent_model_version_id)
        except ModelRegistryError as exc:
            raise ModelRegistryError("comparison incumbent model cannot be verified") from exc
        if incumbent.lifecycle is not ModelLifecycle.RETIRED:
            raise ModelRegistryError("comparison incumbent must be retired before promotion")
        if incumbent.tracking_policy_id != current.tracking_policy_id:
            raise ModelRegistryError("comparison incumbent tracking policy does not match model")
        if report.incumbent_evaluation_sha256 != incumbent.evaluation_sha256:
            raise ModelRegistryError("comparison report cites a different incumbent evaluation")
