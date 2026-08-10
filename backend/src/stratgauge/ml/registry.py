"""Immutable local registration and manual lifecycle for selection models."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import field_validator

from stratgauge.domain.models import DomainModel, ModelLifecycle
from stratgauge.ml.lightgbm_training import LightGBMModel
from stratgauge.ml.selection_training import BaselineModel

RegisteredModelMetadata = BaselineModel | LightGBMModel

MODEL_REGISTRY_VERSION = "selection-model-registry-v1"


class ModelRegistryError(RuntimeError):
    """A model registration or lifecycle event cannot be safely persisted."""


class RegisteredModel(DomainModel):
    """Immutable model metadata materialized with its lifecycle events."""

    registry_version: Literal["selection-model-registry-v1"] = "selection-model-registry-v1"
    model_version_id: str
    model_type: str
    lifecycle: ModelLifecycle = ModelLifecycle.CHALLENGER
    feature_set_version_id: str
    tracking_policy_id: str
    source_file_sha256: str
    artifact_sha256: str
    runtime_artifact_sha256: str | None = None
    evaluation_sha256: str
    training_manifest_sha256: str
    random_seed: int
    registered_at: datetime
    registered_by: str

    @field_validator(
        "model_version_id",
        "model_type",
        "feature_set_version_id",
        "tracking_policy_id",
        "source_file_sha256",
        "artifact_sha256",
        "evaluation_sha256",
        "training_manifest_sha256",
        "registered_by",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("registered model text must not be empty")
        return value.strip()

    @field_validator("registered_at")
    @classmethod
    def validate_registered_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("registered_at must include a timezone")
        return value


class ModelLifecycleEvent(DomainModel):
    """One append-only, manually attributable model transition."""

    event_id: str
    model_version_id: str
    from_lifecycle: ModelLifecycle
    to_lifecycle: ModelLifecycle
    changed_at: datetime
    reason: str
    operator: str
    evaluation_sha256: str | None = None

    @field_validator("event_id", "model_version_id", "reason", "operator")
    @classmethod
    def validate_event_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model lifecycle event text must not be empty")
        return value.strip()

    @field_validator("changed_at")
    @classmethod
    def validate_changed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("model lifecycle timestamp must include a timezone")
        return value


_ALLOWED_TRANSITIONS: Mapping[ModelLifecycle, frozenset[ModelLifecycle]] = {
    ModelLifecycle.CHALLENGER: frozenset({ModelLifecycle.SHADOW, ModelLifecycle.RETIRED}),
    ModelLifecycle.SHADOW: frozenset({ModelLifecycle.ACTIVE, ModelLifecycle.RETIRED}),
    ModelLifecycle.ACTIVE: frozenset({ModelLifecycle.SHADOW, ModelLifecycle.RETIRED}),
    ModelLifecycle.RETIRED: frozenset(),
}


class ModelRegistryStore:
    """Persist model artifacts once and materialize manual lifecycle events."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.registration_dir = self.root / "models" / "registry"
        self.event_dir = self.root / "models" / "lifecycle"
        self.artifact_dir = self.root / "models" / "artifacts"
        self.runtime_artifact_dir = self.root / "models" / "runtime-artifacts"
        self.evaluation_dir = self.root / "models" / "evaluations"
        self.training_manifest_dir = self.root / "models" / "training-manifests"
        for directory in (
            self.registration_dir,
            self.event_dir,
            self.artifact_dir,
            self.runtime_artifact_dir,
            self.evaluation_dir,
            self.training_manifest_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def register(
        self,
        model: RegisteredModelMetadata,
        *,
        model_artifact: bytes,
        runtime_artifact: bytes | None = None,
        evaluation_report: bytes,
        training_manifest: bytes,
        registered_at: datetime,
        registered_by: str,
    ) -> RegisteredModel:
        """Register one validated model and its immutable evidence files."""

        _require_aware(registered_at, "registered_at")
        _require_text(registered_by, "registered_by")
        _validate_model_artifact(model, model_artifact, runtime_artifact)
        _validate_json_object(training_manifest, "training manifest")
        _validate_json(evaluation_report, "evaluation report")
        manifest = _parse_json_object(training_manifest, "training manifest")
        _validate_training_manifest(manifest, model)
        registration = RegisteredModel(
            model_version_id=model.model_version_id,
            model_type=model.model_type,
            feature_set_version_id=model.feature_set_version_id,
            tracking_policy_id=model.tracking_policy_id,
            source_file_sha256=model.source_file_sha256,
            artifact_sha256=_sha256(model_artifact),
            runtime_artifact_sha256=(
                _sha256(runtime_artifact) if runtime_artifact is not None else None
            ),
            evaluation_sha256=_sha256(evaluation_report),
            training_manifest_sha256=_sha256(training_manifest),
            random_seed=model.random_seed,
            registered_at=registered_at,
            registered_by=registered_by,
        )
        registration_path = self._registration_path(model.model_version_id)
        if registration_path.exists():
            existing = self.load(model.model_version_id)
            if existing.model_copy(update={"lifecycle": ModelLifecycle.CHALLENGER}) != registration:
                raise ModelRegistryError(
                    "model version identity already has different registration content"
                )
            return existing
        self._write_once(
            self._artifact_path(model.model_version_id),
            model_artifact,
            "model artifact",
        )
        if runtime_artifact is not None:
            self._write_once(
                self._runtime_artifact_path(model.model_version_id),
                runtime_artifact,
                "model runtime artifact",
            )
        self._write_once(
            self._evaluation_path(model.model_version_id),
            evaluation_report,
            "model evaluation report",
        )
        self._write_once(
            self._training_manifest_path(model.model_version_id),
            training_manifest,
            "model training manifest",
        )
        self._write_once(
            registration_path,
            registration.model_dump_json(indent=2).encode("utf-8"),
            "model registration",
        )
        return registration

    def load(self, model_version_id: str) -> RegisteredModel:
        """Load a registration and verify every referenced immutable artifact."""

        registration_path = self._registration_path(model_version_id)
        if not registration_path.exists():
            raise ModelRegistryError(f"model registration does not exist: {model_version_id}")
        try:
            base = RegisteredModel.model_validate_json(
                registration_path.read_text(encoding="utf-8")
            )
            self._verify_artifact(
                self._artifact_path(model_version_id),
                base.artifact_sha256,
                "model artifact",
            )
            if base.runtime_artifact_sha256 is not None:
                self._verify_artifact(
                    self._runtime_artifact_path(model_version_id),
                    base.runtime_artifact_sha256,
                    "model runtime artifact",
                )
            self._verify_artifact(
                self._evaluation_path(model_version_id),
                base.evaluation_sha256,
                "model evaluation report",
            )
            manifest_bytes = self._training_manifest_path(model_version_id).read_bytes()
            if _sha256(manifest_bytes) != base.training_manifest_sha256:
                raise ModelRegistryError("model training manifest hash does not match registration")
            _validate_json_object(manifest_bytes, "model training manifest")
            events = self.list_events(model_version_id)
        except ModelRegistryError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise ModelRegistryError(f"model registration is invalid: {model_version_id}") from exc
        lifecycle = base.lifecycle
        for event in events:
            if event.from_lifecycle is not lifecycle:
                raise ModelRegistryError("model lifecycle event chain is inconsistent")
            if (
                event.to_lifecycle is ModelLifecycle.ACTIVE
                and event.evaluation_sha256 != base.evaluation_sha256
            ):
                raise ModelRegistryError(
                    "active lifecycle event cites a different evaluation report"
                )
            lifecycle = event.to_lifecycle
        return base.model_copy(update={"lifecycle": lifecycle})

    def load_model(self, model_version_id: str) -> tuple[RegisteredModel, RegisteredModelMetadata]:
        """Load a registered baseline or LightGBM metadata artifact."""

        registration = self.load(model_version_id)
        try:
            content = self._artifact_path(model_version_id).read_bytes()
            model: RegisteredModelMetadata
            if registration.model_type == "constant_probability":
                model = BaselineModel.model_validate_json(content)
            elif registration.model_type == "lightgbm":
                model = LightGBMModel.model_validate_json(content)
            else:
                raise ModelRegistryError(
                    f"registered model type is unsupported: {registration.model_type}"
                )
        except ModelRegistryError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise ModelRegistryError("registered model artifact metadata is invalid") from exc
        if (
            model.model_version_id != registration.model_version_id
            or model.feature_set_version_id != registration.feature_set_version_id
            or model.tracking_policy_id != registration.tracking_policy_id
        ):
            raise ModelRegistryError(
                "registered model artifact metadata does not match registration"
            )
        if isinstance(model, LightGBMModel):
            if registration.runtime_artifact_sha256 is None:
                raise ModelRegistryError("registered LightGBM model has no runtime artifact")
            if model.artifact_sha256 != registration.runtime_artifact_sha256:
                raise ModelRegistryError("LightGBM runtime hash does not match model metadata")
        return registration, model

    def load_baseline_model(self, model_version_id: str) -> tuple[RegisteredModel, BaselineModel]:
        """Load a registered constant baseline only after artifact verification."""

        registration, model = self.load_model(model_version_id)
        if not isinstance(model, BaselineModel):
            raise ModelRegistryError("registered model is not a constant baseline")
        return registration, model

    def read_runtime_artifact(self, model_version_id: str) -> bytes:
        """Read a registered executable model artifact after hash verification."""

        registration = self.load(model_version_id)
        if registration.runtime_artifact_sha256 is None:
            raise ModelRegistryError("registered model has no runtime artifact")
        try:
            return self._runtime_artifact_path(model_version_id).read_bytes()
        except OSError as exc:
            raise ModelRegistryError("model runtime artifact is missing") from exc

    def read_evaluation_report(self, model_version_id: str) -> bytes:
        """Read a registered evaluation report only after registry verification."""

        self.load(model_version_id)
        try:
            return self._evaluation_path(model_version_id).read_bytes()
        except OSError as exc:
            raise ModelRegistryError("model evaluation report is missing") from exc

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
        event_id = f"model-event-{_sha256(_canonical_json(identity))[:20]}"
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
        if _sha256(content) != expected_sha256:
            raise ModelRegistryError(f"{artifact_name} hash does not match registration")


def _validate_model_artifact(
    model: RegisteredModelMetadata,
    content: bytes,
    runtime_artifact: bytes | None,
) -> None:
    if isinstance(model, BaselineModel):
        if runtime_artifact is not None:
            raise ModelRegistryError("baseline model must not have a runtime artifact")
        try:
            parsed = BaselineModel.model_validate_json(content)
        except (TypeError, ValueError) as exc:
            raise ModelRegistryError("model artifact is not a valid baseline model") from exc
        if parsed != model:
            raise ModelRegistryError("model artifact does not match model metadata")
        return
    if runtime_artifact is None:
        raise ModelRegistryError("LightGBM model requires a runtime artifact")
    if _sha256(runtime_artifact) != model.artifact_sha256:
        raise ModelRegistryError("LightGBM runtime artifact hash does not match model metadata")
    try:
        parsed_lightgbm = LightGBMModel.model_validate_json(content)
    except (TypeError, ValueError) as exc:
        raise ModelRegistryError("model artifact is not valid LightGBM metadata") from exc
    if parsed_lightgbm != model:
        raise ModelRegistryError("model artifact does not match model metadata")


def _validate_training_manifest(
    manifest: Mapping[str, Any], model: RegisteredModelMetadata
) -> None:
    expected = {
        "model_version_id": model.model_version_id,
        "feature_set_version_id": model.feature_set_version_id,
        "tracking_policy_id": model.tracking_policy_id,
        "source_file_sha256": model.source_file_sha256,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ModelRegistryError(f"training manifest does not match model: {key}")


def _validate_json_object(content: bytes, name: str) -> None:
    payload = _parse_json(content, name)
    if not isinstance(payload, dict):
        raise ModelRegistryError(f"{name} must be a JSON object")


def _validate_json(content: bytes, name: str) -> None:
    _parse_json(content, name)


def _parse_json_object(content: bytes, name: str) -> dict[str, Any]:
    payload = _parse_json(content, name)
    if not isinstance(payload, dict):
        raise ModelRegistryError(f"{name} must be a JSON object")
    return payload


def _parse_json(content: bytes, name: str) -> Any:
    try:
        return json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ModelRegistryError(f"{name} is not valid JSON") from exc


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(
        character in normalized for character in ("/", "\\", "\0", "\r", "\n")
    ):
        raise ModelRegistryError("model version identifier contains an invalid path character")
    return normalized


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ModelRegistryError(f"{name} must not be empty")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ModelRegistryError(f"{name} must include a timezone")
