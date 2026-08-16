"""Registration methods for the feature model registry."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.ml._hashing import sha256_bytes
from stratgauge.ml.training_feature_lightgbm_calibration import (
    read_feature_calibration_output,
    read_feature_lightgbm_output,
)
from stratgauge.ml.training_feature_registry._registry_base import (
    FeatureRegistryBase,
    _build_registration,
    _read_source_outputs,
    _require_aware,
    _require_text,
    _validate_registration,
)
from stratgauge.ml.training_feature_registry.core import (
    FeatureModelRegistryError,
)
from stratgauge.ml.training_feature_registry.models import (
    FeatureModelRegistration,
)


class RegistrationMixin(FeatureRegistryBase):
    """Register models and read copied evidence."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.registry_root = self.root / "models" / "feature-registry"
        self.registration_dir = self.registry_root / "registrations"
        self.event_dir = self.registry_root / "lifecycle"
        self.evidence_dir = self.registry_root / "evidence"
        self.registration_dir.mkdir(parents=True, exist_ok=True)
        self.event_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def register(
        self,
        training_output_dir: str | Path,
        *,
        calibration_output_dir: str | Path | None = None,
        registered_at: datetime,
        registered_by: str,
    ) -> FeatureModelRegistration:
        """Register verified training evidence and optional derived calibration evidence."""

        _require_aware(registered_at, "registered_at")
        _require_text(registered_by, "registered_by")
        training, calibration = _read_source_outputs(
            training_output_dir,
            calibration_output_dir,
        )
        try:
            registration = _build_registration(training, calibration, registered_at, registered_by)
        except (TypeError, ValueError) as exc:
            raise FeatureModelRegistryError(
                f"feature model registration metadata is invalid: {exc}"
            ) from exc
        with local_write_lock(self.root, name="feature-model-registry"):
            registration_path = self._registration_path(registration.registration_id)
            if registration_path.exists():
                existing = self._load_verified(registration.registration_id)[0]
                if existing.model_copy(update={"lifecycle": "challenger"}) != registration:
                    raise FeatureModelRegistryError(
                        "feature model registration identity already has different content"
                    )
                return existing
            self._write_evidence(
                registration.registration_id, training_output_dir, calibration_output_dir
            )
            copied_training = read_feature_lightgbm_output(
                self._training_evidence_path(registration.registration_id)
            )
            copied_calibration = (
                None
                if calibration_output_dir is None
                else read_feature_calibration_output(
                    self._calibration_evidence_path(registration.registration_id)
                )
            )
            _validate_registration(registration, copied_training, copied_calibration)
            self._write_once(
                registration_path,
                (registration.model_dump_json(indent=2) + "\n").encode("utf-8"),
                "feature model registration",
            )
        return self.load(registration.registration_id)

    def read_runtime_artifact(self, registration_id: str) -> bytes:
        """Read and hash-check the copied LightGBM runtime artifact."""

        registration = self.load(registration_id)
        path = self._training_evidence_path(registration_id) / "model.txt"
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise FeatureModelRegistryError("feature model runtime artifact is missing") from exc
        if sha256_bytes(content) != registration.training_runtime_artifact_sha256:
            raise FeatureModelRegistryError(
                "feature model runtime artifact hash does not match registration"
            )
        return content
