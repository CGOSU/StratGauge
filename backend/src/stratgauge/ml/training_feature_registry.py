"""Immutable registration evidence for purpose-aware feature LightGBM models."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.domain.models import DomainModel
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    TrainingLabel,
    label_contract_id_for,
)
from stratgauge.ml.training_feature_lightgbm import FEATURE_LIGHTGBM_MODEL_TYPE
from stratgauge.ml.training_feature_lightgbm_calibration import (
    FeatureCalibrationError,
    FeatureCalibrationOutput,
    FeatureLightGBMOutput,
    read_feature_calibration_output,
    read_feature_lightgbm_output,
)

FEATURE_MODEL_REGISTRY_VERSION: Literal["selection-purpose-feature-registry-v1"] = (
    "selection-purpose-feature-registry-v1"
)
FEATURE_MODEL_REGISTRATION_TYPE: Literal["feature_lightgbm"] = "feature_lightgbm"
FEATURE_MODEL_LIFECYCLE_VERSION: Literal["selection-purpose-feature-lifecycle-v1"] = (
    "selection-purpose-feature-lifecycle-v1"
)
FeatureModelLifecycle = Literal["challenger", "shadow", "retired"]


class FeatureModelRegistryError(RuntimeError):
    """A purpose-aware feature model cannot be registered or verified."""


class FeatureModelRegistration(DomainModel):
    """Immutable challenger registration bound to training and calibration evidence."""

    registry_version: Literal["selection-purpose-feature-registry-v1"] = (
        FEATURE_MODEL_REGISTRY_VERSION
    )
    registration_id: str
    registration_type: Literal["feature_lightgbm"] = FEATURE_MODEL_REGISTRATION_TYPE
    model_version_id: str
    model_type: Literal["lightgbm"] = FEATURE_LIGHTGBM_MODEL_TYPE
    lifecycle: FeatureModelLifecycle = "challenger"
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_set_version_id: str
    feature_names: tuple[str, ...]
    class_labels: tuple[TrainingLabel, ...]
    calibrated: bool = False
    calibration_id: str | None = None
    calibrated_at: datetime | None = None
    calibration_date_start: date | None = None
    calibration_date_end: date | None = None
    training_model_sha256: str
    training_runtime_artifact_sha256: str
    training_metrics_sha256: str
    training_predictions_sha256: str
    training_manifest_sha256: str
    calibration_sha256: str | None = None
    calibration_metrics_sha256: str | None = None
    calibrated_predictions_sha256: str | None = None
    calibration_manifest_sha256: str | None = None
    evaluation_sha256: str
    feature_dataset_manifest_sha256: str
    source_label_dataset_sha256: str
    label_split_manifest_sha256: str
    registered_at: datetime
    registered_by: str

    @field_validator(
        "registration_id",
        "model_version_id",
        "label_contract_id",
        "feature_set_version_id",
        "registered_by",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature model registration text must not be empty")
        return normalized

    @field_validator(
        "training_model_sha256",
        "training_runtime_artifact_sha256",
        "training_metrics_sha256",
        "training_predictions_sha256",
        "training_manifest_sha256",
        "evaluation_sha256",
        "feature_dataset_manifest_sha256",
        "source_label_dataset_sha256",
        "label_split_manifest_sha256",
        "calibration_sha256",
        "calibration_metrics_sha256",
        "calibrated_predictions_sha256",
        "calibration_manifest_sha256",
    )
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not _is_sha256(normalized):
            raise ValueError("feature model registration digest must be a SHA-256 value")
        return normalized

    @field_validator("registered_at", "calibrated_at")
    @classmethod
    def validate_aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("feature model registration timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> FeatureModelRegistration:
        if self.registry_version != FEATURE_MODEL_REGISTRY_VERSION:
            raise ValueError("unsupported feature model registry version")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("feature model registration label contract does not match purpose")
        if self.registration_id != _registration_id(self.model_version_id, self.calibration_id):
            raise ValueError("feature model registration identity does not match model identity")
        if not self.feature_names or tuple(sorted(set(self.feature_names))) != self.feature_names:
            raise ValueError(
                "feature model registration feature names must be sorted and non-empty"
            )
        if self.class_labels != _labels_for(self.purpose):
            raise ValueError("feature model registration classes do not match purpose")
        calibration_values = (
            self.calibration_id,
            self.calibrated_at,
            self.calibration_date_start,
            self.calibration_date_end,
            self.calibration_sha256,
            self.calibration_metrics_sha256,
            self.calibrated_predictions_sha256,
            self.calibration_manifest_sha256,
        )
        if self.calibrated:
            if any(value is None for value in calibration_values):
                raise ValueError("calibrated registration is missing calibration evidence")
            if self.calibrated_at is None or self.calibrated_at > self.registered_at:
                raise ValueError("registration cannot precede calibration time")
            if (
                self.calibration_date_start is None
                or self.calibration_date_end is None
                or self.calibration_date_end < self.calibration_date_start
            ):
                raise ValueError("feature model calibration dates are reversed")
        elif any(value is not None for value in calibration_values):
            raise ValueError("uncalibrated registration must not contain calibration evidence")
        return self


class FeatureModelLifecycleEvent(DomainModel):
    """One manually authorized, append-only purpose-model lifecycle event."""

    lifecycle_version: Literal["selection-purpose-feature-lifecycle-v1"] = (
        FEATURE_MODEL_LIFECYCLE_VERSION
    )
    event_id: str
    registration_id: str
    from_lifecycle: FeatureModelLifecycle
    to_lifecycle: FeatureModelLifecycle
    changed_at: datetime
    reason: str
    operator: str

    @field_validator("event_id", "registration_id", "reason", "operator")
    @classmethod
    def validate_event_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature model lifecycle event text must not be empty")
        return normalized

    @field_validator("changed_at")
    @classmethod
    def validate_changed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("feature model lifecycle event timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_transition(self) -> FeatureModelLifecycleEvent:
        if self.lifecycle_version != FEATURE_MODEL_LIFECYCLE_VERSION:
            raise ValueError("unsupported feature model lifecycle version")
        if self.from_lifecycle == self.to_lifecycle:
            raise ValueError("feature model lifecycle event must change state")
        if (self.from_lifecycle, self.to_lifecycle) not in {
            ("challenger", "shadow"),
            ("shadow", "retired"),
            ("challenger", "retired"),
        }:
            raise ValueError("invalid feature model lifecycle transition")
        return self


class FeatureModelRegistryStore:
    """Persist purpose-aware model evidence without reusing the legacy registry."""

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
                event_id=f"feature-model-event-{_sha256_json(identity)[:20]}",
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

    def read_runtime_artifact(self, registration_id: str) -> bytes:
        """Read and hash-check the copied LightGBM runtime artifact."""

        registration = self.load(registration_id)
        path = self._training_evidence_path(registration_id) / "model.txt"
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise FeatureModelRegistryError("feature model runtime artifact is missing") from exc
        if hashlib.sha256(content).hexdigest() != registration.training_runtime_artifact_sha256:
            raise FeatureModelRegistryError(
                "feature model runtime artifact hash does not match registration"
            )
        return content

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
        FeatureModelRegistryStore._write_once(
            destination / name, content, f"feature model evidence {name}"
        )


def _labels_for(purpose: TrainingDatasetPurpose) -> tuple[TrainingLabel, ...]:
    return {
        TrainingDatasetPurpose.CANDIDATE_SUCCESS: (
            TrainingLabel.NOT_SUCCESS,
            TrainingLabel.SUCCESS,
        ),
        TrainingDatasetPurpose.ANNOTATION_DECISION: (
            TrainingLabel.DISCARD,
            TrainingLabel.WATCH,
            TrainingLabel.PREPARE_BUY,
        ),
        TrainingDatasetPurpose.BUY_TIMING: (
            TrainingLabel.NOT_PURCHASED,
            TrainingLabel.PURCHASED,
        ),
        TrainingDatasetPurpose.SELL_TIMING: (TrainingLabel.NOT_SOLD, TrainingLabel.SOLD),
    }[purpose]


def _registration_id(model_version_id: str, calibration_id: str | None) -> str:
    identity = {
        "calibration_id": calibration_id,
        "model_version_id": model_version_id,
        "registry_version": FEATURE_MODEL_REGISTRY_VERSION,
    }
    return f"feature-registration-{_sha256_json(identity)[:20]}"


def _sort_key(registration: FeatureModelRegistration) -> tuple[datetime, str]:
    return registration.registered_at, registration.registration_id


def _lifecycle_event_id(event: FeatureModelLifecycleEvent) -> str:
    identity = {
        "changed_at": event.changed_at.isoformat(),
        "from_lifecycle": event.from_lifecycle,
        "operator": event.operator.strip(),
        "reason": event.reason.strip(),
        "registration_id": event.registration_id,
        "to_lifecycle": event.to_lifecycle,
    }
    return f"feature-model-event-{_sha256_json(identity)[:20]}"


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(
        character in normalized for character in ("/", "\\", "\0", "\r", "\n")
    ):
        raise FeatureModelRegistryError(
            "feature model identifier contains an invalid path character"
        )
    return normalized


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise FeatureModelRegistryError(f"{name} must not be empty")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeatureModelRegistryError(f"{name} must include a timezone")


def _is_sha256(value: str) -> bool:
    normalized = value.strip().lower()
    return len(normalized) == 64 and all(char in "0123456789abcdef" for char in normalized)


def _sha256_json(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
