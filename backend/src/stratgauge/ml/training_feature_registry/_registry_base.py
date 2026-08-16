"""Feature registry base: attributes and private infrastructure."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from datetime import datetime
from pathlib import Path

from stratgauge.ml._hashing import sha256_json
from stratgauge.ml.training_feature_lightgbm.models import FEATURE_LIGHTGBM_MODEL_TYPE
from stratgauge.ml.training_feature_lightgbm_calibration import (
    FeatureCalibrationError,
    FeatureCalibrationOutput,
    FeatureLightGBMOutput,
    read_feature_calibration_output,
    read_feature_lightgbm_output,
)
from stratgauge.ml.training_feature_registry.core import (
    FeatureModelRegistryError,
)
from stratgauge.ml.training_feature_registry.models import (
    FeatureModelLifecycle,
    FeatureModelLifecycleEvent,
    FeatureModelRegistration,
    _registration_id,
)


class FeatureRegistryBase:
    """Attributes and shared private helpers for feature registry mixins."""

    root: Path
    registry_root: Path
    registration_dir: Path
    event_dir: Path
    evidence_dir: Path

    # Cross-mixin surface: implementations live in registration/lifecycle mixins.
    def load(self, registration_id: str) -> FeatureModelRegistration:
        raise NotImplementedError

    def list_events(self, registration_id: str) -> tuple[FeatureModelLifecycleEvent, ...]:
        raise NotImplementedError

    def _load_verified(
        self,
        registration_id: str,
    ) -> tuple[FeatureModelRegistration, FeatureLightGBMOutput, FeatureCalibrationOutput | None]:
        path = self._registration_path(registration_id)
        if not path.exists():
            raise FeatureModelRegistryError(
                f"feature model registration does not exist: {registration_id}"
            )
        try:
            registration = FeatureModelRegistration.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            if registration.lifecycle != "challenger":
                raise FeatureModelRegistryError(
                    "feature model registration base lifecycle must remain challenger"
                )
            training = read_feature_lightgbm_output(self._training_evidence_path(registration_id))
            calibration = None
            calibration_path = self._calibration_evidence_path(registration_id)
            if registration.calibrated:
                calibration = read_feature_calibration_output(calibration_path)
            elif calibration_path.exists():
                raise FeatureModelRegistryError(
                    "uncalibrated feature model registration has unexpected calibration evidence"
                )
            _validate_registration(registration, training, calibration)
            events = self.list_events(registration_id)
            lifecycle: FeatureModelLifecycle = (
                "challenger" if not events else events[-1].to_lifecycle
            )
            return registration.model_copy(update={"lifecycle": lifecycle}), training, calibration
        except FeatureModelRegistryError:
            raise
        except (FeatureCalibrationError, OSError, TypeError, ValueError) as exc:
            raise FeatureModelRegistryError(
                f"feature model registration is invalid: {registration_id}"
            ) from exc

    def _write_evidence(
        self,
        registration_id: str,
        training_output_dir: str | Path,
        calibration_output_dir: str | Path | None,
    ) -> None:
        training_destination = self._training_evidence_path(registration_id)
        _copy_once(
            training_destination,
            Path(training_output_dir),
            ("model.json", "model.txt", "metrics.json", "predictions.jsonl", "manifest.json"),
        )
        if calibration_output_dir is not None:
            _copy_once(
                self._calibration_evidence_path(registration_id),
                Path(calibration_output_dir),
                (
                    "calibration.json",
                    "metrics.json",
                    "calibrated_predictions.jsonl",
                    "manifest.json",
                ),
            )

    def _registration_path(self, registration_id: str) -> Path:
        return self.registration_dir / f"{_safe_identifier(registration_id)}.json"

    def _evidence_path(self, registration_id: str) -> Path:
        return self.evidence_dir / _safe_identifier(registration_id)

    def _training_evidence_path(self, registration_id: str) -> Path:
        return self._evidence_path(registration_id) / "training"

    def _calibration_evidence_path(self, registration_id: str) -> Path:
        return self._evidence_path(registration_id) / "calibration"

    @staticmethod
    def _write_once(path: Path, content: bytes, artifact_name: str) -> None:
        if path.exists():
            try:
                if path.read_bytes() != content:
                    raise FeatureModelRegistryError(
                        f"{artifact_name} identity already has different content"
                    )
            except OSError as exc:
                raise FeatureModelRegistryError(f"failed to read existing {artifact_name}") from exc
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise FeatureModelRegistryError(f"failed to write {artifact_name}") from exc


def _build_registration(
    training: FeatureLightGBMOutput,
    calibration: FeatureCalibrationOutput | None,
    registered_at: datetime,
    registered_by: str,
) -> FeatureModelRegistration:
    model = training.model
    calibration_model = None if calibration is None else calibration.model
    return FeatureModelRegistration(
        registration_id=_registration_id(
            model.model_version_id,
            None if calibration_model is None else calibration_model.calibration_id,
        ),
        model_version_id=model.model_version_id,
        purpose=model.purpose,
        label_contract_id=model.label_contract_id,
        feature_set_version_id=model.feature_set_version_id,
        feature_names=model.feature_names,
        class_labels=model.class_labels,
        calibrated=calibration_model is not None,
        calibration_id=(None if calibration_model is None else calibration_model.calibration_id),
        calibrated_at=(None if calibration_model is None else calibration_model.calibrated_at),
        calibration_date_start=(
            None if calibration_model is None else calibration_model.calibration_date_start
        ),
        calibration_date_end=(
            None if calibration_model is None else calibration_model.calibration_date_end
        ),
        training_model_sha256=training.model_sha256,
        training_runtime_artifact_sha256=model.artifact_sha256,
        training_metrics_sha256=training.metrics_sha256,
        training_predictions_sha256=training.predictions_sha256,
        training_manifest_sha256=training.manifest_sha256,
        calibration_sha256=(None if calibration is None else calibration.calibration_sha256),
        calibration_metrics_sha256=(None if calibration is None else calibration.metrics_sha256),
        calibrated_predictions_sha256=(
            None if calibration is None else calibration.calibrated_predictions_sha256
        ),
        calibration_manifest_sha256=(None if calibration is None else calibration.manifest_sha256),
        evaluation_sha256=(
            training.metrics_sha256 if calibration is None else calibration.metrics_sha256
        ),
        feature_dataset_manifest_sha256=model.feature_dataset_manifest_sha256,
        source_label_dataset_sha256=model.source_label_dataset_sha256,
        label_split_manifest_sha256=model.label_split_manifest_sha256,
        registered_at=registered_at,
        registered_by=registered_by,
    )


def _copy_once(destination: Path, source: Path, names: tuple[str, ...]) -> None:
    if not source.is_dir():
        raise FeatureModelRegistryError(
            f"feature model evidence directory does not exist: {source}"
        )
    for name in names:
        try:
            content = (source / name).read_bytes()
        except OSError as exc:
            raise FeatureModelRegistryError(
                f"feature model evidence file does not exist: {source / name}"
            ) from exc
        FeatureRegistryBase._write_once(
            destination / name, content, f"feature model evidence {name}"
        )


def _lifecycle_event_id(event: FeatureModelLifecycleEvent) -> str:
    identity = {
        "changed_at": event.changed_at.isoformat(),
        "from_lifecycle": event.from_lifecycle,
        "operator": event.operator.strip(),
        "reason": event.reason.strip(),
        "registration_id": event.registration_id,
        "to_lifecycle": event.to_lifecycle,
    }
    return f"feature-model-event-{sha256_json(identity)[:20]}"


def _read_source_outputs(
    training_output_dir: str | Path,
    calibration_output_dir: str | Path | None,
) -> tuple[FeatureLightGBMOutput, FeatureCalibrationOutput | None]:
    try:
        training = read_feature_lightgbm_output(training_output_dir)
        calibration = (
            None
            if calibration_output_dir is None
            else read_feature_calibration_output(calibration_output_dir)
        )
        if calibration is not None:
            _validate_registration_compatibility(training, calibration)
        return training, calibration
    except (FeatureCalibrationError, OSError, TypeError, ValueError) as exc:
        raise FeatureModelRegistryError(f"feature model source evidence is invalid: {exc}") from exc


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeatureModelRegistryError(f"{name} must include a timezone")


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise FeatureModelRegistryError(f"{name} must not be empty")


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(
        character in normalized for character in ("/", "\\", "\0", "\r", "\n")
    ):
        raise FeatureModelRegistryError(
            "feature model identifier contains an invalid path character"
        )
    return normalized


def _sort_key(registration: FeatureModelRegistration) -> tuple[datetime, str]:
    return registration.registered_at, registration.registration_id


def _validate_registration(
    registration: FeatureModelRegistration,
    training: FeatureLightGBMOutput,
    calibration: FeatureCalibrationOutput | None,
) -> None:
    _validate_registration_compatibility(training, calibration)
    model = training.model
    registration_fields: tuple[tuple[str, object, object], ...] = (
        ("model_version_id", registration.model_version_id, model.model_version_id),
        ("purpose", registration.purpose, model.purpose),
        ("label_contract_id", registration.label_contract_id, model.label_contract_id),
        (
            "feature_set_version_id",
            registration.feature_set_version_id,
            model.feature_set_version_id,
        ),
        ("feature_names", registration.feature_names, model.feature_names),
        ("class_labels", registration.class_labels, model.class_labels),
        ("training_model_sha256", registration.training_model_sha256, training.model_sha256),
        (
            "training_runtime_artifact_sha256",
            registration.training_runtime_artifact_sha256,
            model.artifact_sha256,
        ),
        ("training_metrics_sha256", registration.training_metrics_sha256, training.metrics_sha256),
        (
            "training_predictions_sha256",
            registration.training_predictions_sha256,
            training.predictions_sha256,
        ),
        (
            "training_manifest_sha256",
            registration.training_manifest_sha256,
            training.manifest_sha256,
        ),
        (
            "feature_dataset_manifest_sha256",
            registration.feature_dataset_manifest_sha256,
            model.feature_dataset_manifest_sha256,
        ),
        (
            "source_label_dataset_sha256",
            registration.source_label_dataset_sha256,
            model.source_label_dataset_sha256,
        ),
        (
            "label_split_manifest_sha256",
            registration.label_split_manifest_sha256,
            model.label_split_manifest_sha256,
        ),
    )
    for name, actual, expected in registration_fields:
        if actual != expected:
            raise FeatureModelRegistryError(
                f"feature model registration field does not match: {name}"
            )
    if calibration is None:
        if registration.evaluation_sha256 != training.metrics_sha256:
            raise FeatureModelRegistryError("feature model evaluation hash does not match training")
        return
    calibration_model = calibration.model
    calibration_fields: tuple[tuple[str, object, object], ...] = (
        ("calibration_id", registration.calibration_id, calibration_model.calibration_id),
        ("calibrated_at", registration.calibrated_at, calibration_model.calibrated_at),
        (
            "calibration_date_start",
            registration.calibration_date_start,
            calibration_model.calibration_date_start,
        ),
        (
            "calibration_date_end",
            registration.calibration_date_end,
            calibration_model.calibration_date_end,
        ),
        ("calibration_sha256", registration.calibration_sha256, calibration.calibration_sha256),
        (
            "calibration_metrics_sha256",
            registration.calibration_metrics_sha256,
            calibration.metrics_sha256,
        ),
        (
            "calibrated_predictions_sha256",
            registration.calibrated_predictions_sha256,
            calibration.calibrated_predictions_sha256,
        ),
        (
            "calibration_manifest_sha256",
            registration.calibration_manifest_sha256,
            calibration.manifest_sha256,
        ),
    )
    for name, actual, expected in calibration_fields:
        if actual != expected:
            raise FeatureModelRegistryError(
                f"feature model registration field does not match: {name}"
            )
    if registration.evaluation_sha256 != calibration.metrics_sha256:
        raise FeatureModelRegistryError("feature model evaluation hash does not match calibration")


def _validate_registration_compatibility(
    training: FeatureLightGBMOutput,
    calibration: FeatureCalibrationOutput | None,
) -> None:
    if training.model.model_type != FEATURE_LIGHTGBM_MODEL_TYPE:
        raise FeatureModelRegistryError("feature model registration requires LightGBM output")
    if calibration is None:
        return
    model = training.model
    calibrated = calibration.model
    compatibility_fields: tuple[tuple[str, object, object], ...] = (
        ("base_model_version_id", calibrated.base_model_version_id, model.model_version_id),
        ("purpose", calibrated.purpose, model.purpose),
        ("label_contract_id", calibrated.label_contract_id, model.label_contract_id),
        ("feature_set_version_id", calibrated.feature_set_version_id, model.feature_set_version_id),
        ("feature_names", calibrated.feature_names, model.feature_names),
        (
            "source_prediction_sha256",
            calibrated.source_prediction_sha256,
            training.predictions_sha256,
        ),
        ("source_model_sha256", calibrated.source_model_sha256, training.model_sha256),
        ("source_manifest_sha256", calibrated.source_manifest_sha256, training.manifest_sha256),
    )
    for name, actual, expected in compatibility_fields:
        if actual != expected:
            raise FeatureModelRegistryError(
                f"feature calibration does not match training evidence: {name}"
            )
