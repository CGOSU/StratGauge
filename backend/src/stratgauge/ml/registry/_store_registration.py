"""Registration and artifact read methods for the model registry."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from stratgauge.domain.models import ModelLifecycle
from stratgauge.ml._hashing import sha256_bytes
from stratgauge.ml.lightgbm_training import LightGBMModel
from stratgauge.ml.registry._store_base import ModelRegistryBase
from stratgauge.ml.registry.core import (
    ModelRegistryError,
)
from stratgauge.ml.registry.helpers import (
    _parse_json_object,
    _require_aware,
    _require_text,
    _validate_json,
    _validate_json_object,
    _validate_model_artifact,
    _validate_training_manifest,
)
from stratgauge.ml.registry.models import (
    RegisteredModel,
    RegisteredModelMetadata,
)
from stratgauge.ml.selection_training import BaselineModel


class RegistrationMixin(ModelRegistryBase):
    """Register models and read registered artifacts."""

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
            artifact_sha256=sha256_bytes(model_artifact),
            runtime_artifact_sha256=(
                sha256_bytes(runtime_artifact) if runtime_artifact is not None else None
            ),
            evaluation_sha256=sha256_bytes(evaluation_report),
            training_manifest_sha256=sha256_bytes(training_manifest),
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
            if sha256_bytes(manifest_bytes) != base.training_manifest_sha256:
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

    def read_training_manifest(self, model_version_id: str) -> bytes:
        """Read a registered training manifest after registry verification."""

        self.load(model_version_id)
        try:
            return self._training_manifest_path(model_version_id).read_bytes()
        except OSError as exc:
            raise ModelRegistryError("model training manifest is missing") from exc

    def read_evaluation_report(self, model_version_id: str) -> bytes:
        """Read a registered evaluation report only after registry verification."""

        self.load(model_version_id)
        try:
            return self._evaluation_path(model_version_id).read_bytes()
        except OSError as exc:
            raise ModelRegistryError("model evaluation report is missing") from exc
